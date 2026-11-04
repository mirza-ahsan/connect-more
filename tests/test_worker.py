"""Integration test for the worker: a fake coordinator in this test drives a real worker process
(which runs a real c4solve) through a whole run, checking every rule of docs/protocol.md part B
that the worker is responsible for."""

import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import zmq

from common import messages as m

ROOT = Path(__file__).resolve().parent.parent
SOLVER = ROOT / "build/release/bin/c4solve_7x6.exe"
pytestmark = pytest.mark.skipif(not SOLVER.exists(), reason="build the release preset first")

THREADS = 2
CAPACITY = 2 * THREADS


class FakeCoordinator:
    """A ROUTER socket that talks to one worker, keeping everything it received."""

    def __init__(self) -> None:
        self.context = zmq.Context()
        self.router = self.context.socket(zmq.ROUTER)
        self.router.setsockopt(zmq.LINGER, 0)
        self.port = self.router.bind_to_random_port("tcp://127.0.0.1")
        self.worker_id = b""
        self.received: list[m.Message] = []

    def send(self, message: m.Message) -> None:
        self.router.send_multipart([self.worker_id, m.encode(message)])

    def wait_for(self, match: Callable[[m.Message], bool], timeout: float = 60) -> m.Message:
        """The first message, received earlier or now, that matches. Answers about different
        tasks arrive in any order, so a match may already be in `received`."""
        for message in self.received:
            if match(message):
                return message
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._receive(100)
            if message is not None and match(message):
                return message
        raise TimeoutError("no matching message from the worker")

    def drain(self, quiet: float = 1.0) -> None:
        """Receives everything still on its way, until nothing arrives for `quiet` seconds."""
        while self._receive(int(quiet * 1000)) is not None:
            pass

    def _receive(self, timeout_ms: int) -> m.Message | None:
        if not self.router.poll(timeout_ms):
            return None
        worker_id, data = self.router.recv_multipart()
        self.worker_id = worker_id
        message = m.decode(data, expect=m.WORKER_TO_COORDINATOR)
        self.received.append(message)
        return message

    def close(self) -> None:
        self.router.close()
        self.context.term()


def final(task_id: str, kind: type) -> Callable[[m.Message], bool]:
    return lambda msg: isinstance(msg, kind) and getattr(msg, "task_id", None) == task_id


def credit(received: list[m.Message]) -> int:
    return sum(msg.free for msg in received if isinstance(msg, m.GiveMeWork))


@pytest.fixture
def run(tmp_path: Path) -> Iterator[tuple[FakeCoordinator, subprocess.Popen[str], Path]]:
    coordinator = FakeCoordinator()
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "worker",
            "--coordinator",
            "127.0.0.1",
            "--port",
            str(coordinator.port),
            "--name",
            "testworker",
            "--threads",
            str(THREADS),
            "--table-mb",
            "64",
            "--log-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        yield coordinator, worker, tmp_path
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(10)
        coordinator.close()


def test_whole_run(run: tuple[FakeCoordinator, subprocess.Popen[str], Path]) -> None:
    coordinator, worker, log_dir = run

    # Start-up: hello with the solver's details, then a credit of 2 x threads after welcome.
    hello = coordinator.wait_for(lambda msg: isinstance(msg, m.Hello))
    assert isinstance(hello, m.Hello)
    assert (hello.worker, hello.threads, hello.board, hello.table_mb) == (
        "testworker",
        2,
        "7x6",
        64,
    )
    assert len(hello.commit) >= 7
    coordinator.send(m.Welcome("testworker", "test-run", "V1"))
    coordinator.wait_for(lambda msg: isinstance(msg, m.GiveMeWork))
    assert credit(coordinator.received) == CAPACITY

    # Tasks with known answers: the first three positions of Pons's End-Easy test file (a loss,
    # a win and a draw for the player to move). Each ending grants one more task of credit.
    easy = {
        "e1": ("2252576253462244111563365343671351441", "loss"),
        "e2": ("7422341735647741166133573473242566", "win"),
        "e3": ("23163416124767223154467471272416755633", "draw"),
    }
    for task_id, (moves, _) in easy.items():
        coordinator.send(m.Task(task_id, moves))
    for task_id, (_, outcome) in easy.items():
        result = coordinator.wait_for(final(task_id, m.Result))
        assert isinstance(result, m.Result) and result.outcome == outcome
        started = [msg for msg in coordinator.received if final(task_id, m.Started)(msg)]
        assert len(started) == 1 and coordinator.received.index(started[0]) < (
            coordinator.received.index(result)
        )
    coordinator.wait_for(lambda msg: credit(coordinator.received) == CAPACITY + len(easy), 10)

    # Clock check: answered with the same id and the worker's clocks.
    coordinator.send(m.ClockCheck(7, 1.0))
    reply = coordinator.wait_for(lambda msg: isinstance(msg, m.ClockReply))
    assert isinstance(reply, m.ClockReply) and reply.check_id == 7
    assert abs(reply.wall_time - time.time()) < 5

    # Fill the credit exactly: two long tasks run, two wait. One more is refused.
    for task_id, moves in [("long1", ""), ("long2", "4"), ("wait1", "44"), ("wait2", "44")]:
        coordinator.send(m.Task(task_id, moves))
    coordinator.wait_for(final("long2", m.Started))
    coordinator.send(m.Task("extra", "44"))
    refused = coordinator.wait_for(final("extra", m.Error), 10)
    assert isinstance(refused, m.Error) and "credit" in refused.message

    # Cancel a waiting task, stop a running one.
    coordinator.send(m.Cancel("wait1"))
    cancelled = coordinator.wait_for(final("wait1", m.Cancelled), 10)
    assert isinstance(cancelled, m.Cancelled) and cancelled.positions == 0
    coordinator.send(m.Stop("long1"))
    stopped = coordinator.wait_for(final("long1", m.Stopped), 10)
    assert isinstance(stopped, m.Stopped) and stopped.positions > 0

    # An invalid position comes back as an error for that task and frees its credit.
    before = credit(coordinator.received)
    coordinator.wait_for(lambda msg: credit(coordinator.received) >= before, 10)
    coordinator.send(m.Task("bad", "1111111"))
    error = coordinator.wait_for(final("bad", m.Error), 10)
    assert isinstance(error, m.Error) and "full" in error.message

    # A malformed message is answered with an error, not a crash.
    coordinator.router.send_multipart([coordinator.worker_id, b'{"type": "task", "v": 1}'])
    coordinator.wait_for(lambda msg: isinstance(msg, m.Error) and msg.task_id is None, 10)

    # Progress arrives while tasks run.
    coordinator.wait_for(lambda msg: isinstance(msg, m.Progress), 10)

    # Run over: the running and waiting tasks end, and the worker exits cleanly.
    coordinator.send(m.RunOver())
    coordinator.wait_for(final("long2", m.Stopped), 30)
    assert worker.wait(30) == 0
    coordinator.drain()  # answers sent just before the worker exited

    # Every task got exactly one final answer.
    finals: dict[str, int] = {}
    for msg in coordinator.received:
        if isinstance(msg, m.Result | m.Stopped | m.Cancelled):
            finals[msg.task_id] = finals.get(msg.task_id, 0) + 1
    assert all(count == 1 for count in finals.values()), finals
    assert set(finals) >= {"e1", "e2", "e3", "long1", "long2", "wait1", "wait2"}

    # The log has every line with both clocks, and the key events.
    logs = list((log_dir / "workers").glob("*_testworker.jsonl"))
    assert len(logs) == 1
    lines = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    events = [line["event"] for line in lines]
    for event in ("worker_start", "solver_ready", "sent", "received", "solver_bye", "worker_end"):
        assert event in events
    assert lines[-1]["event"] == "worker_end" and lines[-1]["clean"] is True
    assert all("mono" in line and "wall" in line for line in lines)
