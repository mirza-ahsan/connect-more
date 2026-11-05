"""Tests for the coordinator (coordinator/server.py, strategies.py, run.py).

The integration tests are whole runs on this laptop: a coordinator plus two real worker
processes, each driving a real c4solve (work plan step 16, check 1). The start positions have
known results: the empty small boards from Tromp's table, and positions from Pons's 7x6 files.
"""

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
import zmq

from common import messages as m
from common.messages import parse_board
from common.runlog import log_path
from coordinator import run as run_cli
from coordinator.calibration import Position, load_positions
from coordinator.server import Config, Coordinator, CoordinatorError, Summary
from coordinator.strategies import SharedPile, SplittingPile, make_strategy, weighted_split
from coordinator.tree import GameTree, Outcome
from tests.helpers import ROOT, fake_hello, logged, needs_solver, read_log, start_worker

# --- Strategy ---------------------------------------------------------------------------------


def test_shared_pile_order_and_skipping() -> None:
    tree = GameTree("", 1)
    pile = SharedPile()
    pile.start(tree, [], {})
    assert pile.next_task("a") is tree.node("p1")
    assert pile.next_task("b") is tree.node("p2")
    tree.record("p3", Outcome.LOSS)  # decides the root: nothing else is needed
    assert pile.next_task("a") is None
    pile.add_tasks([tree.node("p5")])
    assert pile.next_task("a") is None  # still not needed


def test_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        make_strategy("V9")


# --- Whole runs -------------------------------------------------------------------------------


def in_thread(coordinator: Coordinator) -> tuple[threading.Thread, list[Summary | Exception]]:
    """Starts the coordinator's run in a thread; the list gets its summary or its exception."""
    outcome: list[Summary | Exception] = []

    def run() -> None:
        try:
            outcome.append(coordinator.run())
        except Exception as e:
            outcome.append(e)

    # A daemon thread: a coordinator that never ends fails its test instead of keeping pytest
    # from exiting.
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def whole_run(config: Config, worker_boards: list[str]) -> tuple[Summary | Exception, list[int]]:
    """Runs the coordinator in a thread and one worker process per board given."""
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    workers = [
        start_worker(f"w{i + 1}", coordinator.port, config.log_dir, board)
        for i, board in enumerate(worker_boards)
    ]
    thread.join(300)
    codes = []
    for worker in workers:
        try:
            codes.append(worker.wait(60))
        except subprocess.TimeoutExpired:
            worker.kill()
            codes.append(-1)
    assert not thread.is_alive(), "the coordinator did not finish"
    return outcome[0], codes


def check_every_task_ended(summary: Summary) -> None:
    """Every task sent was answered, stopped or cancelled; none is left unanswered."""
    for worker in summary.workers.values():
        assert worker["unanswered"] == 0
        assert worker["tasks_sent"] == worker["results"] + worker["stopped"] + worker["cancelled"]


@pytest.mark.parametrize(
    ("board", "start", "depth", "expect"),
    [
        pytest.param("4x4", "", 3, Outcome.DRAW, marks=needs_solver("4x4"), id="4x4 draw"),
        pytest.param("6x4", "", 3, Outcome.LOSS, marks=needs_solver("6x4"), id="6x4 loss"),
        pytest.param("6x6", "", 2, Outcome.LOSS, marks=needs_solver("6x6"), id="6x6 loss"),
        # Pons's Middle-Medium file: a draw and a loss for the player to move.
        pytest.param(
            "7x6", "274552224131661", 2, Outcome.DRAW, marks=needs_solver("7x6"), id="7x6 draw"
        ),
        pytest.param(
            "7x6", "5455174361263362", 3, Outcome.LOSS, marks=needs_solver("7x6"), id="7x6 loss"
        ),
    ],
)
def test_whole_run_gives_the_known_result(
    board: str, start: str, depth: int, expect: Outcome, tmp_path: Path
) -> None:
    config = Config(
        run_id=f"test-{board}",
        start=start,
        depth=depth,
        workers=2,
        board=board,
        expect=expect,
        bind="tcp://127.0.0.1:0",
        timeout=240,
        log_dir=tmp_path,
    )
    summary, codes = whole_run(config, [board, board])
    assert isinstance(summary, Summary), summary
    assert summary.result == expect and summary.correct is True and not summary.timed_out
    assert summary.solve_seconds is not None and summary.solve_seconds > 0
    assert codes == [0, 0]  # both workers exited cleanly after run_over

    # Both laptops took part, and every task sent was answered, stopped or cancelled.
    assert all(worker["tasks_sent"] > 0 for worker in summary.workers.values())
    check_every_task_ended(summary)
    assert summary.tasks_sent == sum(w["tasks_sent"] for w in summary.workers.values())

    # Counts describe the tree as it was at the start (once the result is known, no task is
    # "needed" any more, so counting at the end would give 0).
    fresh = GameTree(start, depth, *parse_board(board))
    assert summary.tasks_at_start == len(fresh.tasks())
    assert summary.copied_tasks == sum(len(group) - 1 for group in fresh.copies().values())
    if start == "" and depth >= 3:
        assert summary.copied_tasks > 0  # from the empty board, copies appear three moves deep
    for worker in summary.workers.values():
        assert worker["positions"] >= worker["positions_lost"] + worker["positions_unneeded"]

    # Logs: the coordinator's run log ends with the summary; one log per worker.
    records = read_log(log_path(config.run_id, "coordinator", tmp_path))
    end = records[-1]
    assert end["event"] == "run_end" and end["summary"]["result"] == expect
    events = {record["event"] for record in records}
    assert {"coordinator_start", "run_start", "sent", "received", "clock", "run_end"} <= events
    clocks = [record["worker"] for record in records if record["event"] == "clock"]
    for name in summary.workers:
        assert clocks.count(name) >= 2  # one when joining, one at the end of the run
    assert len(list((tmp_path / "workers").glob("*.jsonl"))) == 2


@needs_solver("4x4")
def test_wrong_board_stops_the_run(tmp_path: Path) -> None:
    config = Config(
        run_id="test-wrong-board",
        workers=1,
        board="6x4",
        bind="tcp://127.0.0.1:0",
        timeout=60,
        log_dir=tmp_path,
    )
    error, codes = whole_run(config, ["4x4"])
    assert isinstance(error, CoordinatorError) and "4x4" in str(error)
    assert codes == [0]  # the worker was told the run is over and exited


def test_timeout_while_waiting_for_workers(tmp_path: Path) -> None:
    config = Config(
        run_id="test-timeout", workers=1, bind="tcp://127.0.0.1:0", timeout=0.5, log_dir=tmp_path
    )
    summary = Coordinator(config).run()
    assert summary.timed_out and summary.result is None and summary.solve_seconds is None


def test_command_line(tmp_path: Path) -> None:
    config = run_cli.parse_args(
        ["--start", "44", "--depth", "4", "--expect", "win", "--port", "6000", "--run-id", "r1"]
    )
    assert (config.start, config.depth, config.expect, config.bind, config.run_id) == (
        "44",
        4,
        Outcome.WIN,
        "tcp://*:6000",
        "r1",
    )
    assert run_cli.main(["--start", "1212121", "--log-dir", str(tmp_path)]) == 2  # game over


# --- Misbehaving workers (fake workers driven by the test) ------------------------------------


class FakeWorker:
    """A worker played by the test: a DEALER socket that sends what the test tells it to."""

    def __init__(self, port: int, name: str, board: str = "7x6") -> None:
        self.name, self.board = name, board
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.DEALER)
        self.socket.setsockopt(zmq.ROUTING_ID, name.encode())
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(f"tcp://127.0.0.1:{port}")
        self.error: Exception | None = None  # what went wrong in serve(), if anything

    def finished_cleanly(self, server: threading.Thread) -> bool:
        """True once serve() ended because the run was over (it got run_over), without errors."""
        server.join(10)
        return not server.is_alive() and self.error is None

    def send(self, message: m.Message) -> None:
        self.socket.send(m.encode(message))

    def _hello(self, credit: int) -> None:
        """Says hello, waits for welcome and grants credit."""
        self.send(fake_hello(self.name, self.board))
        self.wait_for(m.Welcome)
        self.send(m.GiveMeWork(credit))

    def join(self, credit: int) -> list[m.Task]:
        """Joins, and returns the tasks that arrive for the credit granted."""
        self._hello(credit)
        tasks = [self.wait_for(m.Task) for _ in range(credit)]
        return [task for task in tasks if isinstance(task, m.Task)]

    def wait_for(self, kind: type | tuple[type, ...], timeout: float = 10) -> m.Message:
        """The next message of this type; clock checks on the way are answered."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.socket.poll(100):
                message = m.decode(self.socket.recv())
                if isinstance(message, m.ClockCheck):
                    self.send(m.ClockReply(message.check_id, time.time(), time.perf_counter()))
                if isinstance(message, kind):
                    return message
        raise TimeoutError(f"{self.name} got no {kind}")

    def serve(
        self, answer: Callable[[m.Task], str | None], credit: int = 1, delay: float = 0.0
    ) -> threading.Thread:
        """In a thread: joins, then takes each task as it arrives, like a real worker: sends
        `started`, waits `delay` seconds (how fast this worker seems) and answers answer(task),
        10 positions searched. If answer(task) is None, the task keeps "running" until the
        coordinator stops or cancels it, which is then answered with 50 positions searched.
        Ends when the run is over."""

        def loop() -> None:
            try:
                self._hello(credit)
                running: set[str] = set()
                kinds = (m.Task, m.Stop, m.Cancel, m.RunOver)
                while not isinstance(message := self.wait_for(kinds, 60), m.RunOver):
                    if isinstance(message, m.Task):
                        self.send(m.Started(message.task_id, 0, 0.0))
                        if (outcome := answer(message)) is None:
                            running.add(message.task_id)
                            continue
                        time.sleep(delay)
                        self.send(m.Result(message.task_id, outcome, 10, delay, 1.0))
                    elif isinstance(message, m.Stop | m.Cancel) and message.task_id in running:
                        running.discard(message.task_id)
                        ended = m.Stopped if isinstance(message, m.Stop) else m.Cancelled
                        self.send(ended(message.task_id, 50, 1.0, 1.0))
                    else:
                        continue  # a stop or cancel for a task already answered
                    self.send(m.GiveMeWork(1))  # one more task of credit after every answer
            except Exception as e:  # kept for the test to check, instead of lost in the thread
                self.error = e

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        # destroy() does not wait: term() would block forever if serve() still held the socket.
        self.context.destroy(linger=0)


def fake_config(
    tmp_path: Path,
    run_id: str = "fake",
    expect: Outcome | None = None,
    silence_limit: float = 30.0,
    bind: str = "tcp://127.0.0.1:0",
    strategy: str = "V1",
    workers: int = 1,
    depth: int = 1,
    calibration: tuple[Position, ...] = (),
    timeout: float = 60,
    board: str = "7x6",
    split_after: float = 60.0,
) -> Config:
    """For runs with fake workers; by default one worker and the empty board one move deep
    (7 tasks)."""
    return Config(
        run_id=run_id,
        depth=depth,
        strategy=strategy,
        workers=workers,
        expect=expect,
        bind=bind,
        board=board,
        timeout=timeout,
        split_after=split_after,
        silence_limit=silence_limit,
        calibration=calibration,
        log_dir=tmp_path,
    )


def test_a_worker_error_ends_the_run_at_once(tmp_path: Path) -> None:
    coordinator = Coordinator(fake_config(tmp_path))
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    worker.join(credit=2)
    worker.send(m.Error(None, "solver exited unexpectedly (3)"))  # what a crashed worker sends
    worker.wait_for(m.RunOver)  # it is told the run is over ...
    thread.join(10)
    assert not thread.is_alive()  # ... and the coordinator stops instead of waiting for hours
    assert isinstance(outcome[0], CoordinatorError) and "solver exited" in str(outcome[0])
    worker.close()


def test_a_silent_worker_ends_the_run(tmp_path: Path) -> None:
    coordinator = Coordinator(fake_config(tmp_path, silence_limit=1.0))
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    worker.join(credit=2)  # then says nothing at all, like a worker whose laptop went to sleep
    thread.join(10)
    assert not thread.is_alive()
    assert isinstance(outcome[0], CoordinatorError) and "sent nothing" in str(outcome[0])
    worker.close()


def test_unexpected_answers_and_late_workers_do_not_spoil_a_run(tmp_path: Path) -> None:
    coordinator = Coordinator(fake_config(tmp_path, expect=Outcome.LOSS))
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    tasks = worker.join(credit=7)

    # An answer for a task it was never sent: logged and ignored.
    worker.send(m.Result("p77", "win", 5, 0.1, 1.0))
    # A second worker turning up during the run: sent away, and the run goes on.
    late = FakeWorker(coordinator.port, "late")
    late.send(fake_hello("late"))
    late.wait_for(m.RunOver)
    late.close()

    # Every move is a win for the opponent, so the start position is a loss.
    for task in tasks:
        worker.send(m.Result(task.task_id, "win", 10, 0.1, 1.0))
    worker.wait_for(m.RunOver)
    thread.join(30)
    summary = outcome[0]
    assert isinstance(summary, Summary), summary
    assert summary.result == "loss" and summary.correct is True
    w1 = summary.workers["w1"]
    assert (w1["results"], w1["positions"], w1["unanswered"]) == (7, 70, 0)
    worker.close()


def test_results_that_change_nothing_count_as_unneeded(tmp_path: Path) -> None:
    coordinator = Coordinator(fake_config(tmp_path))
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    tasks = worker.join(credit=7)
    worker.send(m.Result(tasks[0].task_id, "loss", 10, 0.1, 1.0))  # decides the start position
    for task in tasks[1:]:  # answers that were already on their way
        worker.send(m.Result(task.task_id, "win", 100, 0.1, 1.0))
    worker.wait_for(m.RunOver)
    thread.join(30)
    summary = outcome[0]
    assert isinstance(summary, Summary) and summary.result == "win"
    w1 = summary.workers["w1"]
    assert (w1["positions"], w1["positions_unneeded"]) == (610, 600)
    worker.close()


def test_port_already_in_use(tmp_path: Path) -> None:
    first = Coordinator(fake_config(tmp_path, run_id="first"))
    try:
        with pytest.raises(CoordinatorError, match="cannot listen"):
            Coordinator(fake_config(tmp_path, run_id="second", bind=first.endpoint))
    finally:
        first.router.close()
        first.context.term()
        first.log.close()


# --- V0: calibrate, then split once by speed --------------------------------------------------

# Fake calibration positions: the fake workers look the answers up, so any valid moves will do.
FAKE_SET = (Position("1", Outcome.WIN), Position("2", Outcome.WIN), Position("3", Outcome.WIN))


def calibrated_answer(task: m.Task) -> str:
    """Right answers for FAKE_SET (all wins); every task of the run is a draw, so every task is
    needed until the end and the start position comes out as a draw."""
    return "win" if task.task_id.startswith("c") else "draw"


def test_v0_gives_the_faster_laptop_the_bigger_share(tmp_path: Path) -> None:
    config = fake_config(
        tmp_path, "v0-fake", Outcome.DRAW, strategy="V0", workers=2, depth=2, calibration=FAKE_SET
    )  # depth 2: 49 tasks
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    fast, slow = FakeWorker(coordinator.port, "fast"), FakeWorker(coordinator.port, "slow")
    # About 3:1 in speed: calibration takes 3 x 0.01 s on one and 3 x 0.03 s on the other.
    servers = [fast.serve(calibrated_answer, delay=0.01), slow.serve(calibrated_answer, delay=0.03)]
    thread.join(60)
    assert fast.finished_cleanly(servers[0]) and slow.finished_cleanly(servers[1])
    summary = outcome[0]
    assert isinstance(summary, Summary), summary

    # Calibration measured the slow one as slower, and the split followed the measurement.
    assert summary.calibration is not None
    shares = {name: summary.calibration[name]["share"] for name in ("fast", "slow")}
    assert shares["fast"] > 0.6 and shares["fast"] + shares["slow"] == pytest.approx(1.0)
    sent = {name: summary.workers[name]["tasks_sent"] for name in ("fast", "slow")}
    assert sent["fast"] + sent["slow"] == 49
    assert abs(sent["fast"] - 49 * shares["fast"]) < 1  # dealt in proportion to speed

    # Calibration tasks are not part of the run: not counted as sent tasks or as positions.
    assert summary.tasks_sent == 49
    for name in ("fast", "slow"):
        assert summary.workers[name]["positions"] == 10 * sent[name]

    # No work moved: each laptop was sent exactly the tasks dealt to it at the start, in order.
    speeds = {name: summary.calibration[name]["speed"] for name in ("fast", "slow")}
    dealt = weighted_split([t.task_id for t in GameTree("", 2).tasks()], speeds)
    records = read_log(log_path(config.run_id, "coordinator", tmp_path))
    for name in ("fast", "slow"):
        got = [
            r["message"]["task_id"]
            for r in logged(records, "sent", "task")
            if r["worker"] == name and r["message"]["task_id"].startswith("p")
        ]
        assert got == dealt[name]
    fast.close()
    slow.close()


def test_wrong_calibration_answer_ends_the_run(tmp_path: Path) -> None:
    config = fake_config(tmp_path, "v0-wrong", strategy="V0", calibration=FAKE_SET)
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    server = worker.serve(lambda task: "loss")  # wrong: the set says win
    thread.join(30)
    assert isinstance(outcome[0], CoordinatorError) and "calibration" in str(outcome[0])
    assert worker.finished_cleanly(server)  # it was told the run is over
    worker.close()


def test_worker_error_during_calibration_ends_the_run(tmp_path: Path) -> None:
    coordinator = Coordinator(
        fake_config(tmp_path, "v0-crash", strategy="V0", calibration=FAKE_SET)
    )
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    worker.join(credit=1)  # gets a calibration task ...
    worker.send(m.Error(None, "solver exited unexpectedly (3)"))  # ... and its solver crashes
    worker.wait_for(m.RunOver)
    thread.join(10)
    assert isinstance(outcome[0], CoordinatorError) and "solver exited" in str(outcome[0])
    worker.close()


def test_timeout_during_calibration(tmp_path: Path) -> None:
    config = fake_config(tmp_path, "v0-slow", strategy="V0", calibration=FAKE_SET, timeout=1.0)
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    worker.join(credit=2)  # takes two calibration tasks and never answers
    worker.wait_for(m.RunOver)
    thread.join(30)
    summary = outcome[0]
    assert isinstance(summary, Summary) and summary.timed_out and summary.result is None
    # The unanswered calibration tasks are not reported as unanswered run tasks.
    assert summary.calibration is None and summary.workers["w1"]["unanswered"] == 0
    worker.close()


def test_calibration_set_for_another_board_is_refused_at_once(tmp_path: Path) -> None:
    seven_columns = (Position("7", Outcome.WIN),)  # column 7 does not exist on a 6x4 board
    with pytest.raises(ValueError, match="does not fit a 6x4 board"):
        Coordinator(
            fake_config(tmp_path, "v0-board", strategy="V0", calibration=seven_columns, board="6x4")
        )


def test_v0_needs_a_calibration_set(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="calibration set"):
        Coordinator(fake_config(tmp_path, "no-set", strategy="V0"))


@needs_solver("7x6")
def test_v0_whole_run_with_real_workers(tmp_path: Path) -> None:
    calibration = load_positions(ROOT / "solver/tests/data/7x6/Test_L2_R2.txt", 20)
    config = Config(
        run_id="v0-real",
        start="5455174361263362",  # Pons Middle-Medium: a loss for the player to move
        depth=3,
        strategy="V0",
        workers=2,
        expect=Outcome.LOSS,
        bind="tcp://127.0.0.1:0",
        timeout=240,
        calibration=calibration,
        log_dir=tmp_path,
    )
    summary, codes = whole_run(config, ["7x6", "7x6"])
    assert isinstance(summary, Summary), summary
    assert summary.correct is True and codes == [0, 0]
    assert summary.calibration is not None and set(summary.calibration) == {"w1", "w2"}
    assert sum(w["share"] for w in summary.calibration.values()) == pytest.approx(1.0)
    assert all(w["seconds"] > 0 and w["positions"] > 0 for w in summary.calibration.values())

    # Every calibration task was sent before the run started, and solve time starts after it.
    records = read_log(log_path(config.run_id, "coordinator", tmp_path))
    start = next(r for r in records if r["event"] == "run_start")
    calibration_sends = [
        r["mono"]
        for r in logged(records, "sent", "task")
        if r["message"]["task_id"].startswith("c")
    ]
    assert len(calibration_sends) == 2 * 20 and max(calibration_sends) < start["mono"]
    # The split covers every task; a laptop is sent at most its share (fewer once the result
    # makes the rest unneeded), never a task from the other laptop's share.
    shares = start["shares"]
    assert sum(shares.values()) == summary.tasks_at_start
    for name in ("w1", "w2"):
        assert 0 < summary.workers[name]["tasks_sent"] <= shares[name]


# --- V2: split long tasks, cancel unneeded ones -----------------------------------------------


def test_v2_strategy_flags_and_pile_order() -> None:
    assert not SharedPile.splits_long_tasks and not SharedPile.cancels_unneeded
    assert SplittingPile.splits_long_tasks and SplittingPile.cancels_unneeded
    tree = GameTree("", 1)
    pile = SplittingPile()
    pile.start(tree, [], {})
    assert [pile.next_task("a"), pile.next_task("a")] == [tree.node("p1"), tree.node("p2")]
    children, _ = tree.split("p2")  # p2 was sent, ran too long and was stopped
    pile.add_tasks(children)  # its children go first, in their own order, then the rest
    expected = [*children, tree.node("p3")]
    assert [pile.next_task("a") for _ in range(len(expected))] == expected

    # A task that was split is never handed out itself.
    pile.add_tasks([tree.node("p2")])
    assert pile.next_task("a") is tree.node("p4")


def test_v2_splits_a_task_past_the_time_limit(tmp_path: Path) -> None:
    config = fake_config(tmp_path, "v2-split", Outcome.DRAW, strategy="V2", split_after=0.3)
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")
    # p4 keeps running until it is stopped; every other task (its children too) is a draw.
    server = worker.serve(lambda task: None if task.task_id == "p4" else "draw", credit=7)
    thread.join(30)
    assert worker.finished_cleanly(server)
    summary = outcome[0]
    assert isinstance(summary, Summary), summary
    assert summary.result == "draw" and summary.tasks_split == 1 and summary.cancels_sent == 0
    w1 = summary.workers["w1"]
    assert (w1["stopped"], w1["positions_lost"], w1["unanswered"]) == (1, 50, 0)
    assert summary.tasks_sent == 7 + 7  # p1..p7, then p41..p47 in p4's place

    records = read_log(log_path(config.run_id, "coordinator", tmp_path))
    split = next(r for r in records if r["event"] == "split")
    assert split["task_id"] == "p4" and split["children"] == [f"p4{c}" for c in "1234567"]
    # It was stopped only once it had run for the time limit, counted from its `started`.
    started = next(
        r["mono"] for r in logged(records, "received", "started") if r["message"]["task_id"] == "p4"
    )
    stopped = next(r["mono"] for r in records if r["event"] == "stop_for_split")
    assert stopped - started >= 0.3
    worker.close()


def test_v2_cancels_tasks_an_answer_made_unneeded(tmp_path: Path) -> None:
    config = fake_config(tmp_path, "v2-cancel", Outcome.LOSS, strategy="V2", depth=2)
    coordinator = Coordinator(config)
    thread, outcome = in_thread(coordinator)
    worker = FakeWorker(coordinator.port, "w1")

    # The first 7 tasks are p11..p17. p11 is a loss for the player to move, so p1 is a win and
    # p12..p17 (still running) can no longer matter. Every later task is a loss too, so each
    # position one move deep is a win, and the start position a loss.
    def answer(task: m.Task) -> str | None:
        return None if task.task_id.startswith("p1") and task.task_id != "p11" else "loss"

    server = worker.serve(answer, credit=7)
    thread.join(30)
    assert worker.finished_cleanly(server)
    summary = outcome[0]
    assert isinstance(summary, Summary), summary
    assert summary.result == "loss" and summary.correct is True
    assert summary.tasks_split == 0
    # The 6 tasks still running under p1 were cancelled and answered "cancelled". Tasks that
    # were sent but answered before their cancel arrived count as results (some of them
    # unneeded); tasks never needed were never sent.
    w1 = summary.workers["w1"]
    assert (w1["cancelled"], w1["positions_lost"], w1["unanswered"]) == (6, 6 * 50, 0)
    assert summary.tasks_sent < 49

    # Every cancel was for a task that had been sent and not yet answered.
    records = read_log(log_path(config.run_id, "coordinator", tmp_path))
    sent: set[str] = set()
    answered: set[str] = set()
    cancelled: list[str] = []
    for r in records:
        message = r.get("message", {})
        if r["event"] == "sent" and message["type"] == "task":
            sent.add(message["task_id"])
        elif r["event"] == "sent" and message["type"] == "cancel":
            assert message["task_id"] in sent and message["task_id"] not in answered
            cancelled.append(message["task_id"])
        elif r["event"] == "received" and message["type"] in ("result", "cancelled", "stopped"):
            answered.add(message["task_id"])
    assert set(cancelled) >= {f"p1{c}" for c in "234567"}
    assert len(cancelled) == len(set(cancelled)) == summary.cancels_sent  # never twice
    worker.close()


def test_v2_refuses_a_time_limit_of_zero(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="time limit"):
        Coordinator(fake_config(tmp_path, "v2-zero", strategy="V2", split_after=0))


@needs_solver("7x6")
def test_v2_whole_run_with_real_workers(tmp_path: Path) -> None:
    config = Config(
        run_id="v2-real",
        start="751321",  # Pons Begin-Hard: a win for the player to move, about 1.5 s on 1 thread
        depth=1,
        strategy="V2",
        workers=2,
        expect=Outcome.WIN,
        bind="tcp://127.0.0.1:0",
        timeout=240,
        split_after=0.1,  # short, so that tasks really are split during the test
        log_dir=tmp_path,
    )
    summary, codes = whole_run(config, ["7x6", "7x6"])
    assert isinstance(summary, Summary), summary
    assert summary.correct is True and codes == [0, 0]
    assert summary.tasks_split > 0
    check_every_task_ended(summary)
