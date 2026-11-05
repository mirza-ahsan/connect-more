"""Tests for the coordinator (coordinator/server.py, strategies.py, run.py).

The integration tests are whole runs on this laptop: a coordinator plus two real worker
processes, each driving a real c4solve (work plan step 16, check 1). The start positions have
known results: the empty small boards from Tromp's table, and positions from Pons's 7x6 files.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
import zmq

from common import messages as m
from common.messages import parse_board
from coordinator import run as run_cli
from coordinator.server import Config, Coordinator, CoordinatorError, Summary
from coordinator.strategies import SharedPile, make_strategy
from coordinator.tree import GameTree, Outcome
from tests.helpers import needs_solver, start_worker

# --- Strategy ---------------------------------------------------------------------------------


def test_shared_pile_order_and_skipping() -> None:
    tree = GameTree("", 1)
    pile = SharedPile()
    pile.start(tree, [])
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

    thread = threading.Thread(target=run)
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
    for worker in summary.workers.values():
        assert worker["tasks_sent"] > 0
        assert worker["unanswered"] == 0
        assert worker["tasks_sent"] == worker["results"] + worker["stopped"] + worker["cancelled"]
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
    lines = (tmp_path / f"test-{board}" / "coordinator.jsonl").read_text().splitlines()
    end = json.loads(lines[-1])
    assert end["event"] == "run_end" and end["summary"]["result"] == expect
    records = [json.loads(line) for line in lines]
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

    def send(self, message: m.Message) -> None:
        self.socket.send(m.encode(message))

    def join(self, credit: int) -> list[m.Task]:
        """Says hello, waits for welcome, grants credit and returns the tasks that arrive."""
        self.send(m.Hello(self.name, 1, self.board, 16, "abc", False, 0.0, 0.0))
        self.wait_for(m.Welcome)
        self.send(m.GiveMeWork(credit))
        tasks = [self.wait_for(m.Task) for _ in range(credit)]
        return [task for task in tasks if isinstance(task, m.Task)]

    def wait_for(self, kind: type, timeout: float = 10) -> m.Message:
        """The next message of this type; clock checks on the way are answered."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.socket.poll(100):
                message = m.decode(self.socket.recv())
                if isinstance(message, m.ClockCheck):
                    self.send(m.ClockReply(message.check_id, time.time(), time.perf_counter()))
                if isinstance(message, kind):
                    return message
        raise TimeoutError(f"{self.name} got no {kind.__name__}")

    def close(self) -> None:
        self.socket.close()
        self.context.term()


def fake_config(
    tmp_path: Path,
    run_id: str = "fake",
    expect: Outcome | None = None,
    silence_limit: float = 30.0,
    bind: str = "tcp://127.0.0.1:0",
) -> Config:
    """One worker, the empty board one move deep: 7 tasks."""
    return Config(
        run_id=run_id,
        depth=1,
        workers=1,
        expect=expect,
        bind=bind,
        timeout=60,
        silence_limit=silence_limit,
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
    late.send(m.Hello("late", 1, "7x6", 16, "abc", False, 0.0, 0.0))
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
