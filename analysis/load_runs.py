"""Reads the run logs (common/runlog.py format: one JSON object per line) into tables.

A run folder logs/<run_id>/ holds coordinator.jsonl (every message sent and received, with the
coordinator's clocks, clock checks, decisions, splits and the summary) and, for runs started by
scripts/run_experiment.py, experiment.jsonl (settings, checks, cool-down, outcome). The coordinator
log already holds every worker message, with the solver's own times, so the tables below are built
from it; the worker logs (logs/workers/, and logs/laptop2/workers/ for Laptop 2) are only needed
to line up a laptop's sensor log.

Times: every table uses the coordinator's steady clock, in seconds from the first run task sent
(`t0`), which is where the solve time starts. A worker's solver times count from its `ready`
(protocol, part A); they are put on the coordinator's clock with that worker's `hello.mono_time`
and the offset measured by the clock checks (the median of coordinator - worker readings; for
Laptop 1's own worker it is close to zero, since both processes share one steady clock).

Calibration tasks (V0, ids starting with "c") are kept apart: they are not part of the run.
Nothing here changes a log; derived tables only exist in memory or in files the analysis writes.
"""

import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pandas as pd

from common import messages as m

COORDINATOR_LOG = "coordinator.jsonl"
EXPERIMENT_LOG = "experiment.jsonl"
FINAL = ("result", "stopped", "cancelled")


def read_log(path: Path) -> list[dict]:
    """A run log as a list of records. A last line cut short (a crash while writing) is left
    out."""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


@dataclass
class Run:
    run_id: str
    folder: Path
    records: list[dict]  # coordinator.jsonl
    experiment: list[dict] = field(default_factory=list)  # experiment.jsonl, if any
    _tables: dict = field(default_factory=dict, repr=False)  # tables built once, by kind

    # --- What the run was ---------------------------------------------------------------------

    @cached_property
    def config(self) -> dict:
        return next(r["config"] for r in self.records if r["event"] == "coordinator_start")

    @cached_property
    def summary(self) -> dict | None:
        """The coordinator's summary (None if the run never ended properly)."""
        return next((r["summary"] for r in self.records if r["event"] == "run_end"), None)

    @cached_property
    def info(self) -> dict:
        """experiment, laptop, link, repeat, pilot (from experiment.jsonl; {} if not there)."""
        start = next((r for r in self.experiment if r["event"] == "experiment_start"), None)
        if start is None:
            return {}
        keys = ("experiment", "laptop", "link", "repeat", "pilot")
        return {key: start.get(key) for key in keys}

    @cached_property
    def valid(self) -> bool | None:
        """Whether scripts/run_experiment.py found the run usable as a result: False if it
        failed or never ended; None if the run was not started by that script."""
        if not self.experiment:
            return None
        end = next((r for r in self.experiment if r["event"] == "experiment_end"), None)
        return end is not None and bool(end["valid"])

    @cached_property
    def hellos(self) -> dict[str, dict]:
        """Each worker's hello: threads, table, commit, and its clocks when its solver was
        ready."""
        return {
            r["worker"]: r["message"]
            for r in self.records
            if r["event"] == "received" and r["message"]["type"] == "hello"
        }

    @cached_property
    def _first_task(self) -> dict | None:
        """The record of the first run task sent (the solve time starts there)."""
        return next(
            (
                r
                for r in self.records
                if r["event"] == "sent"
                and r["message"]["type"] == "task"
                and not _calibration(r["message"]["task_id"])
            ),
            None,
        )

    @property
    def t0(self) -> float | None:
        """Coordinator clock when the first run task was sent (the solve time starts here)."""
        return None if self._first_task is None else self._first_task["mono"]

    # --- Clocks -------------------------------------------------------------------------------

    @property
    def t0_wall(self) -> float | None:
        """The normal clock (seconds since 1970) on Laptop 1 at t0, for lining up sensor logs."""
        return None if self._first_task is None else self._first_task["wall"]

    @cached_property
    def _clocks(self) -> list[dict]:
        return [r for r in self.records if r["event"] == "clock"]

    def wall_offsets(self) -> dict[str, float]:
        """Per worker: its normal clock minus Laptop 1's (seconds), the median over its clock
        checks. Subtract it from a time read on that worker's laptop (its HWiNFO log) to put it
        on Laptop 1's normal clock."""
        return _medians(
            (r["worker"], r["worker_wall"] - (r["wall"] - (r["mono"] - r["coordinator_mono"])))
            for r in self._clocks
        )

    @cached_property
    def clock_offsets(self) -> dict[str, float]:
        """Per worker: coordinator clock minus worker clock (seconds), the median over its clock
        checks."""
        return _medians(
            (r["worker"], r["coordinator_mono"] - r["worker_mono"]) for r in self._clocks
        )

    def round_trips(self) -> pd.DataFrame:
        """Every clock check: worker, time (s from t0) and round trip (s)."""
        t0 = self.t0 or 0.0
        rows = [
            {
                "worker": r["worker"],
                "time": r["coordinator_mono"] - t0,
                "round_trip": r["round_trip"],
            }
            for r in self._clocks
        ]
        return pd.DataFrame(rows, columns=["worker", "time", "round_trip"])

    def solver_clock(self, worker: str) -> float:
        """Add to a worker's solver time to get the coordinator's clock."""
        return self.hellos[worker]["mono_time"] + self.clock_offsets.get(worker, 0.0)

    @staticmethod
    def laptop(worker: str) -> str:
        """The laptop a worker ran on: its name, without the "-pilot" of a pilot-mode worker."""
        return worker.removesuffix("-pilot")

    # --- Tables -------------------------------------------------------------------------------

    def tasks(self, calibration: bool = False) -> pd.DataFrame:
        if calibration not in self._tables:
            self._tables[calibration] = self._build_tasks(calibration)
        return self._tables[calibration].copy()

    def _build_tasks(self, calibration: bool) -> pd.DataFrame:
        """One row per task sent: task, worker, sent / started / ended (s from t0, on the
        coordinator's clock; started and ended from the solver's own times), how it ended
        (result, stopped, cancelled, or None if never answered), outcome, positions searched and
        seconds the solver spent on it. Run tasks only, unless `calibration`."""
        t0 = self.t0 or 0.0
        base = {w: self.solver_clock(w) - t0 for w in self.hellos}
        rows: dict[str, dict] = {}
        for r in self.records:
            message = r.get("message")
            if message is None or "task_id" not in message:
                continue
            task_id = message["task_id"]
            if _calibration(task_id) != calibration:
                continue
            kind = message["type"]
            if r["event"] == "sent" and kind == "task":
                rows[task_id] = {
                    "task_id": task_id,
                    "worker": r["worker"],
                    "depth": len(message["moves"]),
                    "sent": r["mono"] - t0,
                    "started": None,
                    "thread": None,
                    "ended": None,
                    "end": None,
                    "outcome": None,
                    "positions": 0,
                    "seconds": 0.0,
                }
            elif r["event"] == "received" and task_id in rows:
                row = rows[task_id]
                if kind == "started":
                    row["started"] = base[r["worker"]] + message["time"]
                    row["thread"] = message["thread"]
                elif kind in FINAL and row["end"] is None:
                    row["end"] = kind
                    row["ended"] = base[r["worker"]] + message["time"]
                    row["outcome"] = message.get("outcome")
                    row["positions"] = message["positions"]
                    row["seconds"] = message["seconds"]
        columns = ["task_id", "worker", "depth", "sent", "started", "thread", "ended", "end"]
        return pd.DataFrame(
            list(rows.values()), columns=[*columns, "outcome", "positions", "seconds"]
        )

    def progress(self) -> pd.DataFrame:
        """Every `progress` report: worker, time (s from t0, solver clock put on the
        coordinator's) and positions searched by all its threads since its solver started."""
        if "progress" not in self._tables:
            self._tables["progress"] = self._build_progress()
        return self._tables["progress"].copy()

    def _build_progress(self) -> pd.DataFrame:
        t0 = self.t0 or 0.0
        base = {w: self.solver_clock(w) - t0 for w in self.hellos}
        rows = [
            {
                "worker": r["worker"],
                "time": base[r["worker"]] + r["message"]["time"],
                "positions": r["message"]["positions"],
            }
            for r in self.records
            if r["event"] == "received" and r["message"]["type"] == "progress"
        ]
        return pd.DataFrame(rows, columns=["worker", "time", "positions"])

    def messages(self) -> pd.DataFrame:
        """Every message sent or received by the coordinator: direction, worker, type, payload
        bytes (the JSON as sent; ZeroMQ adds a few bytes of framing) and time (s from t0)."""
        t0 = self.t0 or 0.0
        rows = []
        for r in self.records:
            if r["event"] in ("sent", "received") and "message" in r:
                rows.append(
                    {
                        "direction": r["event"],
                        "worker": r.get("worker"),
                        "type": r["message"]["type"],
                        "bytes": message_bytes(r["message"]),
                        "time": r["mono"] - t0,
                    }
                )
        return pd.DataFrame(rows, columns=["direction", "worker", "type", "bytes", "time"])


def _medians(pairs: Iterable[tuple[str, float]]) -> dict[str, float]:
    """The median value per name."""
    found: dict[str, list[float]] = {}
    for name, value in pairs:
        found.setdefault(name, []).append(value)
    return {name: statistics.median(values) for name, values in found.items()}


def message_bytes(logged: dict) -> int:
    """The size of a logged message as it was sent: rebuilt and encoded like common/messages.py
    does."""
    fields = {k: v for k, v in logged.items() if k != "type"}
    return len(m.encode(m.BY_TYPE[logged["type"]](**fields)))


def _calibration(task_id: str) -> bool:
    return task_id.startswith("c")


def load_run(folder: Path) -> Run:
    experiment = folder / EXPERIMENT_LOG
    return Run(
        run_id=folder.name,
        folder=folder,
        records=read_log(folder / COORDINATOR_LOG),
        experiment=read_log(experiment) if experiment.exists() else [],
    )


def load_runs(logs: Path, pilot: bool = False) -> list[Run]:
    """Every run under `logs` (folders holding a coordinator log), oldest first: Laptop 1's, and
    Laptop 2's own E1 runs, copied into logs/laptop2/. Pilot runs (ids starting with "pilot_")
    only if `pilot`."""
    found = [*logs.glob(f"*/{COORDINATOR_LOG}"), *logs.glob(f"laptop2/*/{COORDINATOR_LOG}")]
    folders = sorted({p.parent for p in found}, key=lambda f: f.name)
    return [load_run(f) for f in folders if pilot or not f.name.startswith("pilot_")]


def runs_table(runs: list[Run]) -> pd.DataFrame:
    """One row per run: what it was and its summary numbers."""
    rows = []
    for run in runs:
        summary = run.summary or {}
        workers = summary.get("workers", {})
        rows.append(
            {
                "run_id": run.run_id,
                **run.info,
                "strategy": run.config["strategy"],
                "depth": int(run.config["depth"]),
                "start": run.config["start"],
                "workers": len(workers),
                "result": summary.get("result"),
                "correct": summary.get("correct"),
                "solve_seconds": summary.get("solve_seconds"),
                "tasks_at_start": summary.get("tasks_at_start"),
                "copied_tasks": summary.get("copied_tasks"),
                "tasks_sent": summary.get("tasks_sent"),
                "tasks_split": summary.get("tasks_split"),
                "cancels_sent": summary.get("cancels_sent"),
                "answered_from_memory": summary.get("answered_from_memory"),
                "merged_copies": summary.get("merged_copies"),
                "positions": sum(w["positions"] for w in workers.values()),
                "positions_lost": sum(w["positions_lost"] for w in workers.values()),
                "positions_unneeded": sum(w["positions_unneeded"] for w in workers.values()),
                "timed_out": summary.get("timed_out"),
            }
        )
    return pd.DataFrame(rows)
