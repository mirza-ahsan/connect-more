"""Tests for the analysis scripts (analysis/): exact numbers on a small hand-made coordinator log,
then the whole chain (tables and figures) on a real run of this machine with a synthetic sensor log.
"""

import json
from pathlib import Path

import pytest

from analysis import best_fixed_split, figures, metrics, tables
from analysis.load_runs import Run, load_run, load_runs, runs_table
from scripts import run_experiment as rx
from tests.helpers import free_port, needs_solver, write_sensor_log
from tests.test_run_experiment import settings

T0 = 10.0  # coordinator clock when the first task is sent
OFFSET = -100.0  # coordinator clock minus the worker's clock


def record(event: str, mono: float, **fields: object) -> dict:
    return {"mono": mono, "wall": 1_800_000_000.0 + mono, "local": "", "event": event, **fields}


def message(kind: str, **fields: object) -> dict:
    return {"type": kind, **fields}


def received(mono: float, kind: str, **fields: object) -> dict:
    return record("received", mono, worker="w1", message=message(kind, **fields))


def sent(mono: float, kind: str, **fields: object) -> dict:
    return record("sent", mono, worker="w1", message=message(kind, **fields))


def solver(t: float) -> float:
    """A solver time (from the worker's ready) for a coordinator time t from t0. The worker's
    ready was at 100.0 on its own clock, which is 0.0 on the coordinator's (OFFSET)."""
    return T0 + t


def hand_made_run(folder: Path) -> Run:
    """One worker with 2 threads; p123 and p321 are the same position. Thread 0 solves p123 from
    0.5 to 2.5 s, thread 1 p321 from 0.5 to 1.5 s; the result is known at 3.0 s."""
    hello = {
        "worker": "w1",
        "threads": 2,
        "board": "7x6",
        "table_mb": 16,
        "commit": "abc",
        "dirty": False,
        "wall_time": 0.0,
        "mono_time": 100.0,
    }
    records = [
        record("coordinator_start", 0.0, config={"strategy": "V1", "depth": "3", "start": ""}),
        received(1.0, "hello", **hello),
        record(
            "clock",
            1.0,
            worker="w1",
            round_trip=0.002,
            coordinator_mono=1.0,
            worker_mono=1.0 - OFFSET,
            worker_wall=1_800_000_001.5,
        ),
        sent(T0, "task", task_id="p123", moves="123"),
        sent(T0, "task", task_id="p321", moves="321"),
        received(T0 + 0.5, "started", task_id="p123", thread=0, time=solver(0.5)),
        received(T0 + 0.5, "started", task_id="p321", thread=1, time=solver(0.5)),
        received(T0 + 1.0, "progress", positions=50, time=solver(1.0)),
        received(
            T0 + 1.5,
            "result",
            task_id="p321",
            outcome="win",
            positions=40,
            seconds=1.0,
            time=solver(1.5),
        ),
        received(T0 + 2.0, "progress", positions=150, time=solver(2.0)),
        received(
            T0 + 2.5,
            "result",
            task_id="p123",
            outcome="win",
            positions=100,
            seconds=2.0,
            time=solver(2.5),
        ),
        record(
            "run_end",
            T0 + 3.0,
            summary={
                "solve_seconds": 3.0,
                "correct": True,
                "result": "loss",
                "workers": {"w1": {"positions": 140, "positions_lost": 0, "positions_unneeded": 0}},
            },
        ),
    ]
    folder.mkdir(parents=True)
    lines = "".join(json.dumps(r) + "\n" for r in records)
    (folder / "coordinator.jsonl").write_bytes(lines.encode())
    return load_run(folder)


def test_tasks_progress_and_clocks(tmp_path: Path) -> None:
    run = hand_made_run(tmp_path / "r1")
    assert run.t0 == T0 and run.clock_offsets == {"w1": OFFSET}
    assert run.wall_offsets() == {"w1": pytest.approx(0.5)}
    tasks = {row["task_id"]: row for row in run.tasks().to_dict("records")}
    p123 = tasks["p123"]
    assert (p123["started"], p123["ended"], p123["thread"], p123["end"]) == (0.5, 2.5, 0, "result")
    assert tasks["p321"]["positions"] == 40
    assert run.progress()["time"].tolist() == [1.0, 2.0]
    messages = run.messages()
    assert len(messages) == 2 + 7 and (messages["bytes"] > 0).all()


def test_metrics(tmp_path: Path) -> None:
    run = hand_made_run(tmp_path / "r1")
    idle = metrics.idle_time(run).iloc[0]
    assert (idle["busy"], idle["idle"], idle["last_task_end"]) == (3.0, 3.0, 2.5)
    assert idle["tail_idle"] == 2 * 0.5 and idle["thread_tail_idle"] == 0.5 + 1.5
    assert metrics.speedup(3.0, {"T1": 6.0, "T2": 12.0}) == {
        "S1": 2.0,
        "S2": 4.0,
        "T_ideal": 4.0,
        "E": pytest.approx(4 / 3),
    }
    # p321 was sent while p123 (the same position) was still being solved: repeated work.
    assert metrics.repeated_work(run) == {"repeated_sent": 1, "repeated_positions": 40}
    speed = metrics.speed_over_time(run, window=1.0)
    assert speed["positions_per_s"].tolist() == [100.0] and speed["time"].tolist() == [1.5]
    assert metrics.work(run, {"P1": 70.0}, unsplit=280.0) == {
        "positions": 140,
        "positions_lost": 0,
        "positions_unneeded": 0,
        "extra_vs_laptop1": 2.0,
        "extra_vs_unsplit": 0.5,
    }
    assert runs_table([run]).loc[0, "positions"] == 140


def test_best_split() -> None:
    # 8 tasks of 10 positions, two machines of 2 positions/s with one thread each: 4 tasks each.
    even = best_fixed_split.best_split([10] * 8, {"a": 2.0, "b": 2.0}, {"a": 1, "b": 1})
    assert even["seconds"] == 20.0
    # With 4 threads, a single task runs on one thread at a quarter of the speed: 10 / 0.5 s.
    threads = best_fixed_split.best_split([10, 10], {"a": 2.0, "b": 2.0}, {"a": 4, "b": 4})
    assert threads["seconds"] == 20.0
    # One task larger than everything else: one thread of a 4-thread machine does 1/4 of its
    # speed, so the big task alone takes 100 / (8 / 4) = 50 s on the faster machine.
    big = best_fixed_split.best_split([100, 1], {"a": 8.0, "b": 1.0}, {"a": 4, "b": 1})
    assert big["seconds"] == 50.0 and big["shares"]["a"]["tasks"] == 1


def test_set_aside_sensor_copies_are_not_read(tmp_path: Path) -> None:
    folder = tmp_path / "pc/sensors"
    write_sensor_log(folder / "s.jsonl", [1.0], [50.0])
    write_sensor_log(folder / "s.rewritten-2026-11-07_10-00-00.jsonl", [1.0], [99.0])
    (frame,) = figures.read_sensors(tmp_path)["pc"]
    assert frame["package_c"].tolist() == [50.0]


@needs_solver("7x6")
def test_tables_and_figures_on_a_real_run(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    argv = ["--pilot", "--settings", str(settings(tmp_path)), "--logs", str(logs)]
    argv += ["--port", str(free_port()), "alone", "--machine", "laptop1", "--repeat", "1"]
    assert rx.main(argv) == 0
    (run,) = load_runs(logs, pilot=True)
    assert run.valid is True and run.info["experiment"] == "E1"
    assert run.t0_wall is not None
    walls = [run.t0_wall - 5 + i for i in range(15)]  # one sample a second around the run
    write_sensor_log(logs / "sensors/session.jsonl", walls, [50.0 + i for i in range(15)])

    out = tmp_path / "out"
    assert tables.main(["--logs", str(logs), "--out", str(out), "--pilot"]) == 0
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "E1" in summary and "solve_seconds_mean" in summary
    assert figures.main(["--logs", str(logs), "--out", str(out), "--pilot"]) == 0
    assert (out / f"timeline_{run.run_id}.png").stat().st_size > 0
    assert (out / f"sensors_{run.run_id}.png").stat().st_size > 0
    assert tables.main(["--logs", str(tmp_path / "empty"), "--out", str(out)]) == 1
