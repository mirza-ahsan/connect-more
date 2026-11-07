"""Tests for scripts/collect_logs.py: the PC's side is served from a thread on this machine."""

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts import collect_logs as cl
from tests.helpers import free_port


@pytest.fixture
def pc(tmp_path: Path) -> Iterator[tuple[Path, int]]:
    """A logs folder offered by `serve` in a daemon thread, as on the PC."""
    root = tmp_path / "pc-logs"
    (root / "workers").mkdir(parents=True)
    (root / "workers/2026-11-05_10-00-00_pc.jsonl").write_bytes(b'{"event":"a"}\n')
    (root / "sensors").mkdir()
    (root / "sensors/session.jsonl").write_bytes(bytes(range(256)) * 40_000)  # > 2 pieces
    port = free_port()
    stop = threading.Event()
    thread = threading.Thread(target=cl.serve, args=(root, port, stop), daemon=True)
    thread.start()
    yield root, port
    stop.set()
    thread.join(5)


def test_pull_copies_new_and_grown_files_only(
    pc: tuple[Path, int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, port = pc
    monkeypatch.setattr(cl, "CHUNK", 1 << 20)  # the sensor log comes in several pieces
    logs = tmp_path / "laptop1-logs"
    report = cl.pull("127.0.0.1", logs, port)
    assert sorted(report.copied) == [
        "sensors/session.jsonl",
        "workers/2026-11-05_10-00-00_pc.jsonl",
    ]
    assert report.problems == []
    for relative in report.copied:
        assert (logs / "pc" / relative).read_bytes() == (root / relative).read_bytes()

    # Nothing new: nothing copied. Then a log grows (still being written): copied again.
    assert cl.pull("127.0.0.1", logs, port).unchanged == 2
    with (root / "sensors/session.jsonl").open("ab") as f:
        f.write(b"more rows\n")
    report = cl.pull("127.0.0.1", logs, port)
    assert report.copied == ["sensors/session.jsonl"] and report.unchanged == 1
    assert (logs / "pc/sensors/session.jsonl").read_bytes() == (
        root / "sensors/session.jsonl"
    ).read_bytes()
    assert not list(logs.rglob("*.partial"))


def test_a_larger_copy_here_is_left_alone(pc: tuple[Path, int], tmp_path: Path) -> None:
    _, port = pc
    logs = tmp_path / "laptop1-logs"
    mine = logs / "pc/workers/2026-11-05_10-00-00_pc.jsonl"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"x" * 1000)
    report = cl.pull("127.0.0.1", logs, port)
    assert any("larger" in p for p in report.problems)
    assert mine.read_bytes() == b"x" * 1000


BAD_PATHS = [
    "../secret.txt",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "",
    r"..\CLAUDE.md",
    r"\Windows\win.ini",
    "workers/log.jsonl.partial",
]


@pytest.mark.parametrize("bad", BAD_PATHS)
def test_paths_outside_the_logs_folder_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        cl.inside(tmp_path, bad)
    request = json.dumps({"op": "get", "path": bad, "offset": 0, "size": 1}).encode()
    header, data = cl._answer(tmp_path, request, [])
    assert "error" in header and data == b""


def test_bad_requests_get_an_error_not_a_crash(tmp_path: Path) -> None:
    for request in (b"[1]", b"not json", b'{"op": "get"}', b'{"op": "nothing"}'):
        header, data = cl._answer(tmp_path, request, [])
        assert "error" in header and data == b""
    assert cl._answer(tmp_path, b'{"op": "echo"}', [b"hello"]) == ({}, b"hello")


def test_a_rewritten_original_keeps_the_old_copy(pc: tuple[Path, int], tmp_path: Path) -> None:
    root, port = pc
    logs = tmp_path / "laptop1-logs"
    cl.pull("127.0.0.1", logs, port)
    original = root / "workers/2026-11-05_10-00-00_pc.jsonl"
    first = original.read_bytes()
    original.write_bytes(b'{"event":"other"}\n' * 3)  # not a longer version of the first
    report = cl.pull("127.0.0.1", logs, port)
    assert any("rewritten" in p for p in report.problems)
    # The new copy takes the name; the old one is kept beside it.
    mine = logs / "pc/workers/2026-11-05_10-00-00_pc.jsonl"
    (kept,) = (logs / "pc/workers").glob("*.rewritten-*.jsonl")
    assert mine.read_bytes() == original.read_bytes() and kept.read_bytes() == first
    assert not list(logs.rglob("*.partial"))
    # Later pulls carry on as usual: nothing more is copied or set aside.
    later = cl.pull("127.0.0.1", logs, port)
    assert later.problems == [] and later.copied == []


def test_backup_copies_missing_files_and_never_deletes(tmp_path: Path) -> None:
    logs, to = tmp_path / "logs", tmp_path / "backup"
    (logs / "run1").mkdir(parents=True)
    (logs / "run1/coordinator.jsonl").write_bytes(b"one\n")
    (to / "old").mkdir(parents=True)
    (to / "old/kept.txt").write_bytes(b"kept")
    report = cl.backup(logs, to)
    assert report.copied == ["run1/coordinator.jsonl"] and report.problems == []
    assert (to / "run1/coordinator.jsonl").read_bytes() == b"one\n"
    assert (to / "old/kept.txt").exists()
    assert cl.backup(logs, to).unchanged == 1


def test_pull_from_nobody_fails_clearly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cl, "TIMEOUT_MS", 300)
    assert (
        cl.main(
            ["--logs", str(tmp_path), "--port", str(free_port()), "pull", "--peer", "127.0.0.1"]
        )
        == 1
    )
