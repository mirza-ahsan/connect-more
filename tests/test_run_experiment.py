"""Tests for scripts/run_experiment.py: the checks before a run, the cool-down, and whole runs on
this machine (pilot mode, since the tests run from a working tree with changes)."""

import threading
import time
from pathlib import Path

import pytest

from common.runlog import RunLog
from coordinator.server import Config, Summary
from scripts import collect_logs
from scripts import run_experiment as rx
from tests.helpers import free_port, needs_solver, read_log, start_worker, write_sensor_log

HEADER_OK = [
    "c4solve 0.1.0",
    "commit abc123def456, dirty false",
    "build Release, GNU 16.2.1, thread model posix",
    "board 7x6",
]


def settings(tmp_path: Path) -> Path:
    """The real settings file with test values: start 751321 (Pons Begin-Hard, a win), no rest."""
    text = rx.SETTINGS.read_text(encoding="utf-8")
    text = text.replace('start = ""', 'start = "751321"').replace('expect = ""', 'expect = "win"')
    text = text.replace("rest_seconds = 120.0", "rest_seconds = 0.0")
    path = tmp_path / "experiment.toml"
    path.write_bytes(text.encode())
    return path


def test_the_settings_file_is_valid() -> None:
    loaded = rx.load_settings(rx.SETTINGS)
    assert {"run", "cooldown", "network"} <= set(loaded)
    assert set(loaded["cooldown"]["max_package_c"]) == set(rx.MACHINES)
    assert set(loaded["network"]) == set(rx.MACHINES)


@pytest.mark.parametrize(
    ("header", "repository", "problem"),
    [
        (HEADER_OK, {"commit": "abc123def456", "dirty": False}, None),
        (HEADER_OK, {"commit": "abc123def456", "dirty": True}, "repository has uncommitted"),
        (HEADER_OK, {"commit": "abc123def456", "dirty": None}, "git cannot read"),
        (HEADER_OK, {"commit": "999999999999", "dirty": False}, "rebuild release"),
        (
            [HEADER_OK[0], "commit abc123def456, dirty true", *HEADER_OK[2:]],
            {"commit": "abc123def456", "dirty": False},
            "built with",
        ),
        (
            [*HEADER_OK[:2], "build Debug, GNU 16.2.1", HEADER_OK[3]],
            {"commit": "abc123def456", "dirty": False},
            "not a release",
        ),
        ("not available: no such file", {"commit": "abc123def456", "dirty": False}, "cannot start"),
    ],
)
def test_check_build(
    monkeypatch: pytest.MonkeyPatch, header: object, repository: dict, problem: str | None
) -> None:
    info = {"repository": repository, "solver": "c4solve", "solver_header": header}
    monkeypatch.setattr(rx, "build_info", lambda solver: info)
    checks = rx.Checks()
    rx.check_build(checks, Path("c4solve"))
    if problem is None:
        assert checks.problems == []
    else:
        assert any(problem in p for p in checks.problems), checks.problems


def live_sensors(logs: Path, temperatures: list[float | None], age: float = 0.0) -> Path:
    """A sensor log still being written, one sample a second, the last one `age` seconds old."""
    last = time.time() - age
    walls = [last - (len(temperatures) - 1 - i) for i in range(len(temperatures))]
    return write_sensor_log(logs / "sensors/session.jsonl", walls, temperatures, partial='{"mo')


def test_package_reading(tmp_path: Path) -> None:
    package, about = rx.package_reading(tmp_path, 10)
    assert package is None and "no sensor log" in about

    live_sensors(tmp_path, [60.0, 48.0])
    assert rx.package_reading(tmp_path, 10) == (48.0, "session.jsonl")

    live_sensors(tmp_path / "old", [48.0], age=60)  # the sensor log stopped a minute ago
    package, about = rx.package_reading(tmp_path / "old", 10)
    assert package is None and "no sample from the last" in about

    live_sensors(tmp_path / "blind", [None])  # no temperature sensor found
    package, about = rx.package_reading(tmp_path / "blind", 10)
    assert package is None and "no CPU package temperature" in about


def test_cool_down_waits_for_a_fresh_low_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = iter([(70.0, "a"), (None, "stale"), (60.0, "a"), (50.0, "a")])
    monkeypatch.setattr(rx, "package_reading", lambda logs, age: next(readings))
    rule = {
        "rest_seconds": 0.0,
        "max_package_c": {"laptop1": 55.0},
        "poll_seconds": 0.0,
        "sensor_max_age": 10.0,
    }
    with RunLog(tmp_path / "log.jsonl") as log:
        rx.cool_down("laptop1", {"cooldown": rule}, tmp_path, False, log)
    events = read_log(tmp_path / "log.jsonl")
    readings_logged = [r["package_c"] for r in events if r["event"] == "cooldown_reading"]
    assert readings_logged == [70, None, 60, 50] and events[-1]["event"] == "cooldown_end"


def test_no_measured_run_without_the_pilot_settings(tmp_path: Path) -> None:
    argv = ["--settings", str(rx.SETTINGS), "--logs", str(tmp_path), "alone"]
    assert rx.main([*argv, "--machine", "laptop1", "--repeat", "1"]) == 2  # nothing is run
    assert not list(tmp_path.iterdir())


@needs_solver("7x6")
def test_pilot_run_alone(tmp_path: Path) -> None:
    argv = ["--pilot", "--settings", str(settings(tmp_path)), "--logs", str(tmp_path / "logs")]
    argv += ["--port", str(free_port()), "alone", "--machine", "laptop1", "--repeat", "1"]
    assert rx.main(argv) == 0
    (run_dir,) = [p for p in (tmp_path / "logs").iterdir() if p.name.startswith("pilot_")]
    assert run_dir.name.endswith("_E1_V3_laptop1_d3_r1")
    records = read_log(run_dir / "experiment.jsonl")
    events = [r["event"] for r in records]
    assert events[0] == "experiment_start" and "experiment_end" in events
    assert records[0]["pilot"] is True and records[0]["checks"]["problems"]  # dirty tree here
    end = next(r for r in records if r["event"] == "experiment_end")
    assert end["summary"]["correct"] is True and list(end["summary"]["workers"]) == ["laptop1"]


@needs_solver("7x6")
def test_pilot_run_with_two_laptops_copies_the_other_logs(tmp_path: Path) -> None:
    """The PC is played by a worker process and a log server on this machine."""
    pc_logs = tmp_path / "pc-logs"
    (pc_logs / "sessions").mkdir(parents=True)
    (pc_logs / "sessions/role.jsonl").write_bytes(b'{"event":"worker_role_start"}\n')
    log_port, port = free_port(), free_port()
    stop = threading.Event()
    server = threading.Thread(
        target=collect_logs.serve, args=(pc_logs, log_port, stop), daemon=True
    )
    server.start()
    worker = start_worker("pc", port, pc_logs)
    try:
        argv = ["--pilot", "--settings", str(settings(tmp_path)), "--logs", str(tmp_path / "l1")]
        argv += ["--port", str(port), "--log-port", str(log_port), "coordinator"]
        argv += ["--strategy", "V1", "--link", "cable", "--repeat", "2", "--peer", "127.0.0.1"]
        assert rx.main(argv) == 0
        assert worker.wait(60) == 0
    finally:
        stop.set()
        if worker.poll() is None:
            worker.kill()
    (run_dir,) = [p for p in (tmp_path / "l1").iterdir() if p.name.startswith("pilot_")]
    assert "_E2_V1_cable_d3_r2" in run_dir.name
    end = next(r for r in read_log(run_dir / "experiment.jsonl") if r["event"] == "experiment_end")
    assert set(end["summary"]["workers"]) == {"laptop1", "pc"}
    assert (tmp_path / "l1/pc/sessions/role.jsonl").exists()
    assert list((tmp_path / "l1/pc/workers").glob("*_pc.jsonl"))


def test_runs_with_the_wrong_workers_or_warnings_are_invalid(tmp_path: Path) -> None:
    config = Config(run_id="r", workers=1, log_dir=tmp_path)
    (tmp_path / "r").mkdir()
    log = tmp_path / "r/coordinator.jsonl"
    log.write_text('{"event":"run_start"}\n', encoding="utf-8")
    plan = rx.Plan("E1", "laptop1", "local", ("laptop1",), config)

    def summary(*workers: str, result: str | None = "win") -> Summary:
        return Summary("r", "V3", "", 3, "7x6", result, "win", True, 1.0, 1, 0, 1,
                       {w: {} for w in workers})  # fmt: skip

    assert rx.run_problems(summary("laptop1"), plan, 0, pilot=False) == []
    assert rx.run_problems(summary("pc"), plan, 0, pilot=False)  # the PC joined instead
    assert rx.run_problems(summary("laptop1-pilot"), plan, 0, pilot=False)
    assert rx.run_problems(summary("laptop1-pilot"), plan, 0, pilot=True) == []
    assert rx.run_problems(summary("laptop1", result=None), plan, 0, pilot=False)
    assert rx.run_problems(summary("laptop1"), plan, 1, pilot=False)  # its worker failed
    log.write_text('{"event":"settings_warning","problem":"x"}\n', encoding="utf-8")
    assert rx.run_problems(summary("laptop1"), plan, 0, pilot=False)
    assert rx.run_problems(summary("laptop1"), plan, 0, pilot=True) == []
