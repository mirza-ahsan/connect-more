"""The coordinator: runs one experiment run on Laptop 1 (docs/protocol.md, part B).

A run has three phases:
1. **Joining:** the task tree is built, a ROUTER socket is bound on the messaging port, and each
   worker that says `hello` gets `welcome`.
2. **Running:** once every expected worker has joined, tasks go out, chosen by the strategy, but
   only as far as each worker's credit allows (one running and one waiting task per solver
   thread). Every answer is recorded in the tree, until the start position's result is known.
3. **Draining:** every worker gets `run_over`, and the coordinator waits a short while for the
   answers of tasks still in flight (their positions count as lost work) and for the last clock
   checks. A summary is written to the run log and returned.

However the coordinator stops (finished, failed, or interrupted), every worker that joined is
told the run is over, so no solver is left running.

A run that cannot be measured properly fails instead of waiting: a worker that reports an error
of its own, cannot be reached, or sends nothing for SILENCE_LIMIT seconds (it sends progress
every second while working) ends the run with CoordinatorError.

Clocks: the solve time runs from the first task sent until the result is known, on this laptop's
steady clock. Each worker is sent `clock_check` when it joins, every CLOCK_EVERY seconds and at
the end; the round trips (network delay) and the worker's clock readings go into the log, so both
laptops' logs can be lined up afterwards.
"""

import time
from dataclasses import asdict, dataclass
from pathlib import Path

import zmq

from common import messages as m
from common.runlog import LOGS_DIR, RunLog, log_path
from coordinator.strategies import make_strategy
from coordinator.tree import GameTree, Outcome

CLOCK_EVERY = 10.0  # seconds between clock checks during a run
END_GRACE = 15.0  # seconds to wait for the last answers after run_over
POLL_MS = 200  # longest wait for a message; a message ends the wait at once


@dataclass(frozen=True)
class Config:
    run_id: str
    start: str = ""  # start position, as a move string
    depth: int = 3  # split depth
    strategy: str = "V1"
    workers: int = 2  # how many workers must join before the run starts
    board: str = "7x6"
    expect: Outcome | None = None  # the known result of the start position, if any
    bind: str = "tcp://*:5555"  # "tcp://127.0.0.1:0" binds a free port (tests)
    timeout: float = 24 * 3600.0  # seconds before the run is given up
    silence_limit: float = 30.0  # seconds without any message before a worker counts as gone
    log_dir: Path = LOGS_DIR


@dataclass
class WorkerState:
    name: str
    routing_id: bytes
    hello: m.Hello
    last_heard: float  # steady clock, when its last message arrived
    credit: int = 0
    tasks_sent: int = 0
    results: int = 0
    stopped: int = 0
    cancelled: int = 0
    positions: int = 0  # in every final answer, also stopped and cancelled ones
    positions_lost: int = 0  # in stopped and cancelled tasks
    positions_unneeded: int = 0  # in results that arrived when they could no longer change anything


@dataclass
class Assignment:
    """A task that was sent to a worker and has no final answer yet."""

    worker: WorkerState
    sent_at: float  # steady clock
    started_at: float | None = None  # when its `started` arrived
    thread: int | None = None


@dataclass
class Summary:
    run_id: str
    strategy: str
    start: str
    depth: int
    board: str
    result: str | None
    expected: str | None
    correct: bool | None
    solve_seconds: float | None
    tasks_at_start: int  # tasks in the tree when the run started
    copied_tasks: int  # of those, tasks that are the same position as an earlier task
    tasks_sent: int
    workers: dict[str, dict[str, int]]
    timed_out: bool = False


class CoordinatorError(RuntimeError):
    """The run cannot go on, or cannot be measured properly (for example a worker crashed)."""


class Coordinator:
    def __init__(self, config: Config) -> None:
        self.config = config
        width, height = m.parse_board(config.board)
        self.tree = GameTree(config.start, config.depth, width, height)
        self.strategy = make_strategy(config.strategy)
        # Counted now: once answers come in, tasks stop being "needed" and would not be counted.
        self.tasks_at_start = len(self.tree.tasks())
        self.copied_tasks = sum(len(group) - 1 for group in self.tree.copies().values())

        self.context = zmq.Context()
        self.router = self.context.socket(zmq.ROUTER)
        self.router.setsockopt(zmq.ROUTER_MANDATORY, 1)  # an unreachable worker raises, not drops
        self.router.setsockopt(zmq.LINGER, 2000)
        try:
            self.router.bind(config.bind)
        except zmq.ZMQError as e:
            self.router.close()
            self.context.term()
            raise CoordinatorError(f"cannot listen on {config.bind}: {e}") from e
        self.endpoint = self.router.getsockopt_string(zmq.LAST_ENDPOINT)
        self.port = int(self.endpoint.rsplit(":", 1)[1])
        self.log = RunLog(log_path(config.run_id, "coordinator", config.log_dir))

        self.phase = "joining"  # then "running", then "draining"
        self.workers: dict[bytes, WorkerState] = {}
        self.in_flight: dict[str, Assignment] = {}  # task id -> who has it, since when
        self.first_task_at: float | None = None
        self.result_at: float | None = None
        self.clock_checks: dict[int, tuple[bytes, float]] = {}  # check id -> (worker, sent at)
        self.final_checks: set[int] = set()  # the end-of-run clock checks still unanswered
        self.next_check_id = 1
        self.dispatch_turn = 0  # which worker is offered tasks first, taking turns

        self.log.write(
            "coordinator_start",
            config={k: str(v) for k, v in asdict(config).items()},
            endpoint=self.endpoint,
            tasks=self.tasks_at_start,
            copied_tasks=self.copied_tasks,
            already_decided=self.tree.result,
        )

    # --- Sending --------------------------------------------------------------------------

    def _send(self, routing_id: bytes, name: str, message: m.Message) -> bool:
        """Sends a message; False if the worker cannot be reached (it has gone)."""
        try:
            self.router.send_multipart([routing_id, m.encode(message)])
        except zmq.ZMQError as e:
            self.log.write("send_failed", worker=name, type=message.TYPE, error=str(e))
            return False
        self.log.write("sent", worker=name, message=m.as_dict(message))
        return True

    def _send_to(self, worker: WorkerState, message: m.Message) -> bool:
        return self._send(worker.routing_id, worker.name, message)

    def _clock_check(self, worker: WorkerState) -> int:
        check_id = self.next_check_id
        self.next_check_id += 1
        sent = time.perf_counter()
        self.clock_checks[check_id] = (worker.routing_id, sent)
        self._send_to(worker, m.ClockCheck(check_id, sent))
        return check_id

    def _dispatch(self) -> None:
        """Sends tasks to every worker with credit, as long as the strategy has some. Workers
        take turns at being offered tasks first, so neither gets first pick every time."""
        if self.phase != "running" or not self.workers:
            return
        workers = list(self.workers.values())
        first = self.dispatch_turn % len(workers)
        self.dispatch_turn += 1
        for worker in workers[first:] + workers[:first]:
            while worker.credit > 0 and (task := self.strategy.next_task(worker.name)):
                now = time.perf_counter()
                if self.first_task_at is None:
                    self.first_task_at = now
                worker.credit -= 1
                worker.tasks_sent += 1
                self.in_flight[task.task_id] = Assignment(worker, now)
                if not self._send_to(worker, m.Task(task.task_id, task.moves)):
                    raise CoordinatorError(f"{worker.name} cannot be reached")

    # --- Receiving ------------------------------------------------------------------------

    def _poll_once(self) -> None:
        """Handles every message that arrives within POLL_MS, then sends what they made possible."""
        if self.router.poll(POLL_MS):
            while True:
                try:
                    routing_id, data = self.router.recv_multipart(zmq.NOBLOCK)
                except zmq.Again:
                    break
                self._handle(routing_id, data, time.perf_counter())
        self._dispatch()

    def _handle(self, routing_id: bytes, data: bytes, arrived: float) -> None:
        try:
            message = m.decode(data, expect=m.WORKER_TO_COORDINATOR)
        except m.MessageError as e:
            self.log.write("bad_message", routing_id=routing_id.hex(), error=str(e))
            return
        worker = self.workers.get(routing_id)
        name = worker.name if worker else routing_id.decode("utf-8", "replace")
        self.log.write("received", worker=name, message=m.as_dict(message))

        if isinstance(message, m.Hello):
            self._hello(routing_id, message, arrived)
            return
        if worker is None:
            self.log.write("unknown_worker", routing_id=routing_id.hex(), type=message.TYPE)
            return
        worker.last_heard = arrived

        match message:
            case m.GiveMeWork():
                worker.credit += message.free
            case m.Started():
                if (assignment := self.in_flight.get(message.task_id)) is not None:
                    assignment.started_at = arrived
                    assignment.thread = message.thread
            case m.Result():
                if self._final(worker, message):
                    decided = self.tree.record(message.task_id, Outcome(message.outcome))
                    if decided:
                        self.log.write(
                            "decided", nodes={n.task_id: str(n.outcome) for n in decided}
                        )
                    else:  # the answer could no longer change anything
                        worker.positions_unneeded += message.positions
                    if self.tree.result is not None and self.result_at is None:
                        self.result_at = arrived
            case m.Stopped() | m.Cancelled():
                if self._final(worker, message):
                    self._returned(message.task_id)
            case m.ClockReply():
                sent_by, sent = self.clock_checks.pop(message.check_id, (b"", 0.0))
                self.final_checks.discard(message.check_id)
                if sent_by == routing_id:
                    self.log.write(
                        "clock",
                        worker=worker.name,
                        round_trip=arrived - sent,
                        coordinator_mono=(sent + arrived) / 2,
                        worker_mono=message.mono_time,
                        worker_wall=message.wall_time,
                    )
            case m.Error():
                if message.task_id is None or message.task_id in self.in_flight:
                    # The worker itself failed (its solver crashed), or it could not take a task
                    # we sent: either way this run can no longer be measured properly.
                    raise CoordinatorError(f"{worker.name} reported: {message.message}")
            case m.Progress():
                pass  # logged above; it also shows the worker is alive

    def _hello(self, routing_id: bytes, hello: m.Hello, arrived: float) -> None:
        names = [w.name for w in self.workers.values()]
        if self.phase != "joining":
            # A stray or restarted worker must not end a run that is going well.
            self._refuse(routing_id, hello, f"{hello.worker} joined after the run started")
            return
        if routing_id in self.workers or hello.worker in names:
            self._refuse(routing_id, hello, f"a second worker called {hello.worker!r} joined")
            raise CoordinatorError(f"two workers are called {hello.worker!r}")
        if hello.board != self.config.board:
            self._refuse(routing_id, hello, "wrong board")
            raise CoordinatorError(f"{hello.worker} solves {hello.board}, not {self.config.board}")

        worker = WorkerState(hello.worker, routing_id, hello, last_heard=arrived)
        self.workers[routing_id] = worker
        self._check_settings(hello)
        print(
            f"coordinator: {hello.worker} joined ({hello.threads} threads, commit {hello.commit})"
        )
        self._send_to(worker, m.Welcome(hello.worker, self.config.run_id, self.config.strategy))
        self._clock_check(worker)

    def _check_settings(self, hello: m.Hello) -> None:
        """Warns if this worker's solver differs from the first one's in a way that matters for
        comparing the laptops (CLAUDE.md: same code, same table size on both)."""
        problems = []
        if hello.dirty:
            problems.append("built with uncommitted changes")
        first = next(iter(self.workers.values())).hello
        if hello.commit != first.commit:
            problems.append(f"commit {hello.commit}, but {first.worker} has {first.commit}")
        if hello.table_mb != first.table_mb:
            problems.append(f"table {hello.table_mb} MB, but {first.worker} has {first.table_mb}")
        for problem in problems:
            print(f"warning: {hello.worker}'s solver: {problem}")
            self.log.write("settings_warning", worker=hello.worker, problem=problem)

    def _refuse(self, routing_id: bytes, hello: m.Hello, reason: str) -> None:
        """Turns a worker away. It is not one of our workers, so the end of the run would not
        reach it: it is told now that the run is over."""
        self.log.write("refused", worker=hello.worker, reason=reason)
        self._send(routing_id, hello.worker, m.RunOver())

    def _final(self, worker: WorkerState, answer: m.Result | m.Stopped | m.Cancelled) -> bool:
        """Book-keeping for a task's final answer. False (and the answer is ignored) if this
        worker was not holding that task."""
        assignment = self.in_flight.get(answer.task_id)
        if assignment is None or assignment.worker is not worker:
            self.log.write("unexpected_answer", worker=worker.name, task_id=answer.task_id)
            return False
        del self.in_flight[answer.task_id]
        worker.positions += answer.positions
        if isinstance(answer, m.Result):
            worker.results += 1
            return True
        worker.positions_lost += answer.positions
        if isinstance(answer, m.Stopped):
            worker.stopped += 1
        else:
            worker.cancelled += 1
        return True

    def _returned(self, task_id: str) -> None:
        """A task came back unanswered. If it is still needed, it goes back to the pile."""
        node = self.tree.node(task_id)
        if self.phase == "running" and node.is_leaf() and node.is_needed():
            self.strategy.add_tasks([node])

    def _check_silence(self, now: float) -> None:
        for worker in self.workers.values():
            if now - worker.last_heard > self.config.silence_limit:
                raise CoordinatorError(
                    f"{worker.name} sent nothing for {self.config.silence_limit:.0f} s"
                )

    # --- The run --------------------------------------------------------------------------

    def run(self) -> Summary:
        try:
            return self._run()
        except BaseException as e:
            # Whatever went wrong (including Ctrl+C), tell the workers that joined, so no solver
            # is left running.
            self.log.write("run_failed", error=f"{type(e).__name__}: {e}")
            self._end_run()
            raise
        finally:
            self.router.close()
            self.context.term()
            self.log.close()

    def _run(self) -> Summary:
        config = self.config
        give_up = time.perf_counter() + config.timeout
        print(
            f"coordinator: run {config.run_id}, {config.strategy}, depth {config.depth}, "
            f"{self.tasks_at_start} tasks; waiting for {config.workers} workers "
            f"on {self.endpoint}"
        )

        # Joining.
        while len(self.workers) < config.workers:
            if time.perf_counter() > give_up:
                return self._drain(timed_out=True)
            self._poll_once()

        # Running.
        self.phase = "running"
        self.strategy.start(self.tree, [w.hello for w in self.workers.values()])
        self.log.write("run_start", workers=[w.name for w in self.workers.values()])
        print("coordinator: all workers joined, run started")
        next_clock_round = time.perf_counter() + CLOCK_EVERY
        self._dispatch()
        while self.tree.result is None:
            now = time.perf_counter()
            if now > give_up:
                return self._drain(timed_out=True)
            self._check_silence(now)
            if now >= next_clock_round:
                for worker in self.workers.values():
                    self._clock_check(worker)
                next_clock_round = now + CLOCK_EVERY
            self._poll_once()
        if self.result_at is None:
            self.result_at = time.perf_counter()  # decided by the rules alone
        print(f"coordinator: result {self.tree.result}")
        return self._drain(timed_out=False)

    def _drain(self, timed_out: bool) -> Summary:
        """Draining: tells every worker the run is over, waits for the tasks still in flight and
        the last clock checks, and writes the summary."""
        self._end_run()
        end_by = time.perf_counter() + END_GRACE
        while (self.in_flight or self.final_checks) and time.perf_counter() < end_by:
            self._poll_once()
        summary = self._summary(timed_out)
        self.log.write("run_end", summary=asdict(summary))
        return summary

    def _end_run(self) -> None:
        if self.phase == "draining":
            return
        self.phase = "draining"
        for worker in self.workers.values():
            self.final_checks.add(self._clock_check(worker))
            self._send_to(worker, m.RunOver())

    # --- Results --------------------------------------------------------------------------

    def _summary(self, timed_out: bool) -> Summary:
        config = self.config
        result = None if timed_out else self.tree.result
        solve = None
        if result is not None and self.first_task_at is not None and self.result_at is not None:
            solve = self.result_at - self.first_task_at
        unanswered = {w.name: 0 for w in self.workers.values()}
        for assignment in self.in_flight.values():
            unanswered[assignment.worker.name] += 1
        return Summary(
            run_id=config.run_id,
            strategy=config.strategy,
            start=config.start,
            depth=config.depth,
            board=config.board,
            result=None if result is None else str(result),
            expected=None if config.expect is None else str(config.expect),
            correct=None if config.expect is None or result is None else result == config.expect,
            solve_seconds=solve,
            tasks_at_start=self.tasks_at_start,
            copied_tasks=self.copied_tasks,
            tasks_sent=sum(w.tasks_sent for w in self.workers.values()),
            workers={
                w.name: {
                    "threads": w.hello.threads,
                    "table_mb": w.hello.table_mb,
                    "tasks_sent": w.tasks_sent,
                    "results": w.results,
                    "stopped": w.stopped,
                    "cancelled": w.cancelled,
                    "positions": w.positions,
                    "positions_lost": w.positions_lost,
                    "positions_unneeded": w.positions_unneeded,
                    "unanswered": unanswered[w.name],
                }
                for w in self.workers.values()
            },
            timed_out=timed_out,
        )
