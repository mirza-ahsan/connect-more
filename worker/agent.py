"""The worker: drives one c4solve and connects it to the coordinator (docs/protocol.md).

Start-up: start the solver, wait for `ready`, connect to the coordinator, send `hello`, wait for
`welcome`, then grant credit for 2 x threads tasks (one running and one waiting per thread).

Then, until `run_over`: pass tasks, stop and cancel to the solver; pass the solver's answers to
the coordinator; grant one more task of credit each time a task ends; answer clock checks.
Everything is written to the worker's run log, with both clocks.
"""

import contextlib
import queue
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import zmq

from common import messages as m
from common.build import ROOT, git_state, parse_commit
from common.runlog import LOGS_DIR, RunLog, log_path, stamp
from worker import solver as s

READY_TIMEOUT = 300.0  # seconds; allocating a large table takes a moment
QUIT_TIMEOUT = 60.0  # seconds to wait for the solver to say bye after quit


@dataclass(frozen=True)
class Config:
    coordinator: str = "127.0.0.1"
    port: int = 5555
    name: str = socket.gethostname()
    solver: Path = ROOT / "build/release/bin/c4solve_7x6.exe"
    threads: int | None = None  # None: one per logical processor (the solver decides)
    table_mb: int | None = None  # None: the solver's default (CM_DEFAULT_TT_MB)
    log_dir: Path = LOGS_DIR
    # Seconds without any message from the coordinator, once welcomed, before the worker gives
    # up: the coordinator sends a clock check every 10 s in every phase, so this means it has gone
    # (its laptop crashed or its console was closed), and the solver must not work for nobody.
    silence_limit: float = 90.0


class WorkerError(RuntimeError):
    """The worker cannot go on (the solver failed or exited)."""


class Worker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.log = RunLog(log_path("workers", f"{stamp()}_{config.name}", config.log_dir))
        self.log.write(
            "worker_start",
            config={k: str(v) for k, v in asdict(config).items()},
            repository=git_state(),
        )

        # Answers from the solver's reader threads are queued, and each one pings the main
        # loop through an inproc socket, so the loop wakes at once without polling.
        self.context = zmq.Context()
        self.wake_address = f"inproc://solver-answers-{id(self)}"
        self.wake_pull = self.context.socket(zmq.PULL)
        self.wake_pull.bind(self.wake_address)
        # One push socket for both reader threads; the lock makes them take turns with it.
        self.wake_push = self.context.socket(zmq.PUSH)
        self.wake_push.setsockopt(zmq.LINGER, 0)
        self.wake_push.connect(self.wake_address)
        self.wake_lock = threading.Lock()
        self.wake_closed = False
        self.answers: queue.Queue[s.Answer] = queue.Queue()

        self.dealer: zmq.Socket | None = None
        self.solver: s.SolverProcess | None = None
        self.capacity = 0
        self.credit_owed = 0  # credit the coordinator has granted us that is not used yet
        self.held: set[str] = set()  # tasks given to the solver whose final answer is not out
        self.welcomed = False
        self.quitting = False
        self.solver_done = False
        self.exit_code: int | None = None

    # --- Solver answers, from the reader threads ------------------------------------------

    def _on_answer(self, answer: s.Answer) -> None:
        self.answers.put(answer)
        with self.wake_lock:
            if not self.wake_closed:
                # If the pipe is full, the loop is already awake with pings to read.
                with contextlib.suppress(zmq.Again):
                    self.wake_push.send(b"", zmq.NOBLOCK)

    def _next_answer(self, timeout: float) -> s.Answer | None:
        try:
            return self.answers.get(timeout=timeout)
        except queue.Empty:
            return None

    # --- Sending to the coordinator -------------------------------------------------------

    def _send(self, message: m.Message) -> None:
        assert self.dealer is not None
        self.dealer.send(m.encode(message))
        self.log.write("sent", message=m.as_dict(message))

    def _grant(self, tasks: int) -> None:
        if tasks > 0 and not self.quitting:
            self.credit_owed += tasks
            self._send(m.GiveMeWork(tasks))

    # --- Start-up -------------------------------------------------------------------------

    def _start_solver(self) -> s.Ready:
        config = self.config
        if not config.solver.exists():
            raise WorkerError(f"solver not found: {config.solver} (build the release preset)")
        self.solver = s.SolverProcess(
            config.solver, self._on_answer, config.threads, config.table_mb
        )
        commit, dirty = "unknown", True
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            answer = self._next_answer(deadline - time.monotonic())
            if isinstance(answer, s.Info):
                self.log.write("solver_info", text=answer.text)
                if (build := parse_commit(answer.text)) is not None:
                    commit, dirty = build
            elif isinstance(answer, s.Ready):
                if answer.protocol != s.PROTOCOL_VERSION:
                    raise WorkerError(f"solver speaks protocol {answer.protocol}")
                self.ready_wall = time.time()
                self.ready_mono = time.perf_counter()
                self.commit, self.dirty = commit, dirty
                self.log.write("solver_ready", **asdict(answer), commit=commit, dirty=dirty)
                return answer
            elif isinstance(answer, s.Error | s.Exited):
                raise WorkerError(f"solver failed to start: {answer}")
        raise WorkerError(f"solver did not say ready within {READY_TIMEOUT} s")

    def _connect(self, ready: s.Ready) -> None:
        config = self.config
        self.dealer = self.context.socket(zmq.DEALER)
        self.dealer.setsockopt(zmq.ROUTING_ID, config.name.encode())
        self.dealer.setsockopt(zmq.LINGER, 2000)  # let the last messages out on close
        address = f"tcp://{config.coordinator}:{config.port}"
        self.dealer.connect(address)
        self.log.write("connecting", address=address)
        print(
            f"worker {config.name}: solver ready ({ready.threads} threads), connecting to {address}"
        )
        self.capacity = 2 * ready.threads
        self._send(
            m.Hello(
                config.name,
                ready.threads,
                ready.board,
                ready.table_mb,
                self.commit,
                self.dirty,
                self.ready_wall,
                self.ready_mono,
            )
        )

    # --- Messages from the coordinator ----------------------------------------------------

    def _handle_message(self, data: bytes) -> None:
        try:
            message = m.decode(data, expect=m.COORDINATOR_TO_WORKER)
        except m.MessageError as e:
            self.log.write("bad_message", error=str(e), data=data.decode("utf-8", "replace"))
            self._send(m.Error(None, f"bad message: {e}"))
            return
        if isinstance(message, m.ClockCheck):
            # Answered before anything else, even logging, so the round trip the coordinator
            # measures is as close to the network delay as possible.
            self._send(m.ClockReply(message.check_id, time.time(), time.perf_counter()))
        self.log.write("received", message=m.as_dict(message))
        assert self.solver is not None

        match message:
            case m.Welcome():
                if not self.welcomed:
                    self.welcomed = True
                    print(f"worker {self.config.name}: welcome to run {message.run_id}")
                    self._grant(self.capacity)
            case m.Task():
                if not self.welcomed or self.quitting:
                    self._send(m.Error(message.task_id, "not taking tasks now"))
                elif message.task_id in self.held:
                    self._send(m.Error(message.task_id, "task id already in use"))
                elif self.credit_owed <= 0:
                    self._send(m.Error(message.task_id, "no credit left for this task"))
                else:
                    self.credit_owed -= 1
                    self.held.add(message.task_id)
                    self.solver.solve(message.task_id, message.moves)
            case m.Stop():
                self.solver.stop(message.task_id)
            case m.Cancel():
                self.solver.cancel(message.task_id)
            case m.ClockCheck():
                pass  # answered above
            case m.RunOver():
                if not self.quitting:
                    self.quitting = True
                    print(f"worker {self.config.name}: run over")
                    self.solver.quit()
            case m.NoWork():
                pass  # logged above; the coordinator keeps our credit

    # --- Answers from the solver ----------------------------------------------------------

    def _task_ended(self, task_id: str) -> None:
        if task_id in self.held:
            self.held.discard(task_id)
            self._grant(1)

    def _handle_answer(self, answer: s.Answer) -> None:
        match answer:
            case s.Info():
                self.log.write("solver_info", text=answer.text)
            case s.Started():
                self._send(m.Started(answer.task_id, answer.thread, answer.time))
            case s.Result():
                self._send(
                    m.Result(
                        answer.task_id,
                        answer.outcome,
                        answer.positions,
                        answer.seconds,
                        answer.time,
                    )
                )
                self._task_ended(answer.task_id)
            case s.Stopped():
                self._send(m.Stopped(answer.task_id, answer.positions, answer.seconds, answer.time))
                self._task_ended(answer.task_id)
            case s.Cancelled():
                self._send(
                    m.Cancelled(answer.task_id, answer.positions, answer.seconds, answer.time)
                )
                self._task_ended(answer.task_id)
            case s.Progress():
                if self.welcomed:
                    self._send(m.Progress(answer.positions, answer.time))
            case s.Error():
                self._send(m.Error(answer.task_id, answer.message))
                if answer.task_id is not None:
                    self._task_ended(answer.task_id)  # e.g. an invalid position: no other answer
            case s.Bye():
                self.log.write("solver_bye", time=answer.time)
            case s.Exited():
                self.solver_done = True
                self.exit_code = answer.returncode
                self.log.write("solver_exited", returncode=answer.returncode)
                if not self.quitting:
                    self._send(m.Error(None, f"solver exited unexpectedly ({answer.returncode})"))
            case s.Ready():
                self.log.write("solver_info", text=f"unexpected second ready: {answer}")

    # --- Main loop ------------------------------------------------------------------------

    def run(self) -> int:
        """Runs until the coordinator says run_over and the solver has exited. Returns the exit
        code for the process: 0 if everything ended cleanly."""
        try:
            self._connect(self._start_solver())
            assert self.dealer is not None
            poller = zmq.Poller()
            poller.register(self.dealer, zmq.POLLIN)
            poller.register(self.wake_pull, zmq.POLLIN)
            waiting_since = last_heard = time.monotonic()

            while not self.solver_done:
                events = dict(poller.poll(1000))
                if self.wake_pull in events:
                    while True:
                        try:
                            self.wake_pull.recv(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                # Coordinator messages first: tasks reach the solver sooner, and clock checks
                # are not held up behind a batch of solver answers.
                if self.dealer in events:
                    last_heard = time.monotonic()
                    while True:
                        try:
                            data = self.dealer.recv(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                        self._handle_message(data)
                while (answer := self._next_answer(0)) is not None:
                    self._handle_answer(answer)
                if not self.welcomed and time.monotonic() - waiting_since > 10:
                    print(f"worker {self.config.name}: still waiting for the coordinator ...")
                    waiting_since = time.monotonic()
                silent = time.monotonic() - last_heard
                if self.welcomed and not self.quitting and silent > self.config.silence_limit:
                    raise WorkerError(f"the coordinator sent nothing for {silent:.0f} s")

            clean = self.quitting and self.exit_code == 0
            self.log.write("worker_end", clean=clean)
            return 0 if clean else 1
        except KeyboardInterrupt:
            self.log.write("worker_end", clean=False, reason="interrupted")
            return 130
        except WorkerError as e:
            self.log.write("worker_end", clean=False, reason=str(e))
            raise
        finally:
            self._shut_down()

    def _shut_down(self) -> None:
        if self.solver is not None and not self.solver_done:
            self.solver.quit()
            try:
                self.solver.wait(QUIT_TIMEOUT)
            except Exception:
                self.solver.kill()
        with self.wake_lock:
            self.wake_closed = True
            self.wake_push.close()
        for sock in [self.dealer, self.wake_pull]:
            if sock is not None:
                sock.close()
        self.context.term()
        self.log.close()
