"""Tests for worker/solver.py: reading the solver's answer lines, and driving a real c4solve."""

import queue
from pathlib import Path

import pytest

from worker import solver as s

SOLVER = Path(__file__).resolve().parent.parent / "build/release/bin/c4solve_7x6.exe"
needs_solver = pytest.mark.skipif(not SOLVER.exists(), reason="build the release preset first")


@pytest.mark.parametrize(
    ("line", "answer"),
    [
        ("# commit abc, dirty false\n", s.Info("commit abc, dirty false")),
        ("ready 1 7x6 20 1024\n", s.Ready(1, "7x6", 20, 1024)),
        ("started t1 3 0.412000\n", s.Started("t1", 3, 0.412)),
        ("result t1 loss 31799719 2.541 2.953\r\n", s.Result("t1", "loss", 31799719, 2.541, 2.953)),
        ("stopped t2 400 0.5 1.5", s.Stopped("t2", 400, 0.5, 1.5)),
        ("cancelled t3 0 0.000000 0.000716", s.Cancelled("t3", 0, 0.0, 0.000716)),
        ("progress 8104577 1.0", s.Progress(8104577, 1.0)),
        ("error t9 no task with this id", s.Error("t9", "no task with this id")),
        ('error - unknown command "dance"', s.Error(None, 'unknown command "dance"')),
        ("bye 3.5", s.Bye(3.5)),
    ],
)
def test_parse_answer(line: str, answer: s.Answer) -> None:
    assert s.parse_answer(line) == answer


@pytest.mark.parametrize(
    "line",
    [
        "",
        "hello",
        "ready 1 7x6 20",
        "result t1 maybe 1 1.0 1.0",
        "result t1 win -5 1.0 1.0",
        "result t1 win 5 nan 1.0",
        "started t1 x 1.0",
        "progress 1",
        "error t1",
        "bye",
    ],
)
def test_parse_bad_answer(line: str) -> None:
    with pytest.raises(s.AnswerError):
        s.parse_answer(line)


def collect_until(
    answers: queue.Queue[s.Answer], done: type, timeout: float = 60
) -> list[s.Answer]:
    """Answers up to and including the first one of type `done`."""
    seen: list[s.Answer] = []
    while True:
        answer = answers.get(timeout=timeout)
        seen.append(answer)
        if isinstance(answer, done):
            return seen


@needs_solver
def test_real_solver_session() -> None:
    answers: queue.Queue[s.Answer] = queue.Queue()
    solver = s.SolverProcess(SOLVER, answers.put, threads=2, table_mb=64)

    start = collect_until(answers, s.Ready)
    assert start[-1] == s.Ready(1, "7x6", 2, 64)
    assert any(isinstance(a, s.Info) and a.text.startswith("commit ") for a in start)

    solver.solve("easy", "4453")
    solver.solve("full", "1111111")
    solver.solve("long", "")  # the empty board, written "-" on the line
    seen = collect_until(answers, s.Result)
    result = seen[-1]
    assert isinstance(result, s.Result)
    assert (result.task_id, result.outcome) == ("easy", "loss")
    assert any(isinstance(a, s.Error) and a.task_id == "full" for a in seen)

    solver.stop("long")
    stopped = collect_until(answers, s.Stopped)[-1]
    assert isinstance(stopped, s.Stopped) and stopped.task_id == "long" and stopped.positions > 0

    solver.quit()
    end = collect_until(answers, s.Exited)
    assert any(isinstance(a, s.Bye) for a in end)
    assert end[-1] == s.Exited(0)
    assert solver.wait(10) == 0
