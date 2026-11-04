"""Tests for common/runlog.py: run logs with two clocks that are never overwritten."""

import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from common.runlog import RunLog, log_path


def read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_lines_have_both_clocks_and_fields(tmp_path: Path) -> None:
    path = tmp_path / "run" / "worker.jsonl"
    with RunLog(path) as log:
        log.write("started", task_id="t1", thread=3)
        log.write("result", task_id="t1", outcome="win", positions=12)

    lines = read_lines(path)
    assert [line["event"] for line in lines] == ["started", "result"]
    assert lines[0]["task_id"] == "t1"
    assert lines[0]["thread"] == 3
    assert lines[1]["positions"] == 12
    for line in lines:
        assert isinstance(line["mono"], float)
        assert isinstance(line["wall"], float)
        # The local time is the same moment as the wall clock, to the millisecond.
        local = datetime.fromisoformat(str(line["local"]))
        assert abs(local.timestamp() - float(line["wall"])) < 0.001  # pyrefly: ignore[bad-argument-type]
    assert float(lines[1]["mono"]) >= float(lines[0]["mono"])  # pyrefly: ignore[bad-argument-type]


def test_a_log_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "worker.jsonl"
    with RunLog(path) as log:
        log.write("first")
    before = path.read_bytes()

    with pytest.raises(FileExistsError):
        RunLog(path)
    assert path.read_bytes() == before


def test_reserved_names_are_refused(tmp_path: Path) -> None:
    with RunLog(tmp_path / "log.jsonl") as log:
        for name in ("mono", "wall", "local", "event"):
            with pytest.raises(ValueError, match="reserved"):
                log.write("x", **{name: 1})


def test_line_ends_are_plain_newlines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    with RunLog(path) as log:
        log.write("a")
        log.write("b")
    assert b"\r" not in path.read_bytes()


def test_many_threads_write_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    threads, per_thread = 8, 500
    with RunLog(path) as log:

        def write_many(n: int) -> None:
            for i in range(per_thread):
                log.write("tick", thread=n, i=i, padding="x" * 200)

        workers = [threading.Thread(target=write_many, args=(n,)) for n in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

    lines = read_lines(path)  # every line must be valid JSON on its own
    assert len(lines) == threads * per_thread
    for n in range(threads):
        assert [line["i"] for line in lines if line["thread"] == n] == list(range(per_thread))


def test_log_path(tmp_path: Path) -> None:
    assert log_path("2026-11-04_V1_r1", "worker-laptop2", tmp_path) == (
        tmp_path / "2026-11-04_V1_r1" / "worker-laptop2.jsonl"
    )
    for bad in ("../escape", "a b", ""):
        with pytest.raises(ValueError):
            log_path(bad, "worker", tmp_path)
