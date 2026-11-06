"""The metrics of the methodology (Phase 2, Table V), computed from the run logs.

Every function takes runs read by analysis/load_runs.py and returns numbers or tables; nothing
here changes a log. Times are seconds on the coordinator's clock from the first task sent (t0);
the solve time T is the summary's `solve_seconds`.

- Solve time T (analysis/tables.py gives each setting's mean with its lowest and highest).
- Speedup S1 = T1 / T and S2 = T2 / T, with T1, T2 the mean E1 solve times of each laptop alone.
- Combined efficiency E = T_ideal / T, with T_ideal = T1 * T2 / (T1 + T2).
- Idle thread-time per laptop: threads * T minus the busy time of its solver threads inside
  [0, T] (a task still running at T counts as busy until T). The tail, as the methodology
  defines it, is threads * (T - the end of the laptop's last task); `thread_tail_idle` adds up
  each thread's own idle time after its last task instead, which also shows threads that ran
  out of work before the others.
- Extra work: positions searched by both laptops (stopped and cancelled tasks included) divided
  by a reference: Laptop 1 alone (E1), or the unsplit single search (E1). The E1 references are V3
  runs with one worker, which lose the work of the tasks they stop and split, so the unsplit
  reference is the cleaner one; report the positions lost next to both.
- Repeated work: tasks sent whose position had already been solved (an earlier copy of it ended
  with a result) or was still being solved (an earlier copy was sent and not ended yet) when it
  was sent, and the positions searched in them. Copies sent only after an earlier copy was
  stopped or cancelled are not repeats (that lost work is in the positions lost). The summary's
  `copied_tasks` (copies among the tasks at the start) is a separate number.
- Speed over time: positions per second of each laptop in 10-second windows, from its solver's
  `progress` reports, placed at the middle of the time each rate covers.
- Calibration error (V0): Laptop 1's speed over Laptop 2's in positions per second, measured on
  the calibration set before the run, against the same ratio reached over the run (each laptop
  only up to the end of its last task, so time it sat idle after its share does not count).
- Network cost: messages and bytes per run, and the clock-check round trips.
"""

from itertools import pairwise
from statistics import mean

import pandas as pd

from analysis.load_runs import Run
from common import messages as m
from coordinator.board import Position

WINDOW = 10.0  # seconds per window for speed over time


def solve_time(run: Run) -> float | None:
    summary = run.summary or {}
    return summary.get("solve_seconds")


def references(runs: list[Run]) -> dict[str, float]:
    """The E1 references: mean solve time and positions of each laptop alone ("T1", "T2", "P1",
    "P2"), from the E1 runs given (the caller passes only valid runs with the right result)."""
    found: dict[str, dict[str, list[float]]] = {}
    for run in runs:
        info, summary = run.info, run.summary
        if info.get("experiment") != "E1" or not summary or not summary.get("correct"):
            continue
        laptop = info["laptop"]
        times = found.setdefault(laptop, {"T": [], "P": []})
        times["T"].append(summary["solve_seconds"])
        times["P"].append(sum(w["positions"] for w in summary["workers"].values()))
    out = {}
    for laptop, number in (("laptop1", "1"), ("laptop2", "2")):
        if laptop in found:
            out["T" + number] = mean(found[laptop]["T"])
            out["P" + number] = mean(found[laptop]["P"])
    return out


def speedup(t: float, refs: dict[str, float]) -> dict[str, float]:
    """S1, S2 and the combined efficiency E for a solve time `t` (the keys missing references
    are left out)."""
    out = {}
    if "T1" in refs:
        out["S1"] = refs["T1"] / t
    if "T2" in refs:
        out["S2"] = refs["T2"] / t
    if "T1" in refs and "T2" in refs:
        ideal = refs["T1"] * refs["T2"] / (refs["T1"] + refs["T2"])
        out["T_ideal"] = ideal
        out["E"] = ideal / t
    return out


def busy_tasks(run: Run) -> pd.DataFrame:
    """The tasks that started, with `ended` cut to [0, T]; a task that never answered (still
    running when the run was over) runs until T."""
    t = solve_time(run) or 0.0
    tasks = run.tasks().dropna(subset=["started"])
    ended = tasks["ended"].fillna(t).clip(lower=0, upper=t)
    return tasks.assign(started=tasks["started"].clip(lower=0, upper=t), ended=ended)


def idle_time(run: Run) -> pd.DataFrame:
    """Per worker: threads, busy thread-seconds inside [0, T], idle thread-seconds and share,
    the tail (methodology) and the per-thread tail (see the module notes)."""
    t = solve_time(run)
    if not t:
        return pd.DataFrame()
    tasks = busy_tasks(run)
    rows = []
    for worker, hello in run.hellos.items():
        threads = hello["threads"]
        mine = tasks[tasks["worker"] == worker]
        busy = float((mine["ended"] - mine["started"]).sum())
        last_end = float(mine["ended"].max()) if len(mine) else 0.0
        per_thread = mine.groupby("thread")["ended"].max()
        rows.append(
            {
                "worker": worker,
                "threads": threads,
                "busy": busy,
                "idle": threads * t - busy,
                "idle_share": 1 - busy / (threads * t),
                "last_task_end": last_end,
                "tail_idle": threads * (t - last_end),
                "thread_tail_idle": float((t - per_thread).sum()) + t * (threads - len(per_thread)),
            }
        )
    return pd.DataFrame(rows)


def work(run: Run, refs: dict[str, float], unsplit: float | None = None) -> dict[str, float]:
    """Positions searched (total, lost in stopped and cancelled tasks, unneeded) and the extra
    work against Laptop 1 alone and against the unsplit search."""
    summary = run.summary or {}
    workers = summary.get("workers", {}).values()
    total = sum(w["positions"] for w in workers)
    out: dict[str, float] = {
        "positions": total,
        "positions_lost": sum(w["positions_lost"] for w in workers),
        "positions_unneeded": sum(w["positions_unneeded"] for w in workers),
    }
    if refs.get("P1"):
        out["extra_vs_laptop1"] = total / refs["P1"]
    if unsplit:
        out["extra_vs_unsplit"] = total / unsplit
    return out


def repeated_work(run: Run) -> dict[str, int]:
    """Tasks sent whose position had already been solved, or was still being solved, by an
    earlier copy, and the positions searched in them (see the module notes)."""
    board = m.parse_board(run.config.get("board", "7x6"))
    tasks = run.tasks().sort_values("sent")
    earlier: dict[int, list[tuple[float | None, str | None]]] = {}  # key -> (ended, how)
    count = positions = 0
    for task_id, sent, ended, end, searched in tasks[
        ["task_id", "sent", "ended", "end", "positions"]
    ].itertuples(index=False, name=None):
        key = Position.from_moves(task_id[1:], *board).key()  # task ids are "p" + moves
        copies = earlier.setdefault(key, [])
        if any(how == "result" or done is None or done > sent for done, how in copies):
            count += 1
            positions += int(searched)
        copies.append((None if pd.isna(ended) else float(ended), end))
    return {"repeated_sent": count, "repeated_positions": positions}


def speed_over_time(run: Run, window: float = WINDOW) -> pd.DataFrame:
    """Positions per second of each worker in windows of `window` seconds, from the cumulative
    counts of its solver's progress reports: between the last report of one window and the last
    of the next, placed at the middle of that span."""
    progress = run.progress()
    rows = []
    for worker in sorted(progress["worker"].unique()):
        mine = progress[progress["worker"] == worker]
        last: dict[int, tuple[float, float]] = {}  # window -> its last (time, positions)
        for time, positions in sorted(zip(mine["time"], mine["positions"], strict=True)):
            last[int(time // window)] = (time, positions)
        for before, after in pairwise(sorted(last)):
            (t0, p0), (t1, p1) = last[before], last[after]
            if t1 > t0:
                rate = (p1 - p0) / (t1 - t0)
                rows.append({"worker": worker, "time": (t0 + t1) / 2, "positions_per_s": rate})
    return pd.DataFrame(rows, columns=["worker", "time", "positions_per_s"])


def run_speeds(run: Run) -> dict[str, float]:
    """Positions per second of each worker while it had work: from its first progress report
    after t0 to its last one before its last task ended (or T). Workers with fewer than two
    reports there are left out."""
    t = solve_time(run) or 0.0
    progress = run.progress()
    tasks = busy_tasks(run)
    speeds = {}
    for worker in run.hellos:
        mine_tasks = tasks[tasks["worker"] == worker]
        until = float(mine_tasks["ended"].max()) if len(mine_tasks) else t
        mine = progress[(progress["worker"] == worker) & progress["time"].between(0, until)]
        if len(mine) >= 2:
            positions = mine["positions"].iloc[-1] - mine["positions"].iloc[0]
            seconds = mine["time"].iloc[-1] - mine["time"].iloc[0]
            if seconds > 0:
                speeds[worker] = float(positions / seconds)
    return speeds


def calibration_error(run: Run) -> dict[str, float] | None:
    """V0: Laptop 1's speed over Laptop 2's in positions per second, on the calibration set and
    over the run; None for runs without calibration or without both laptops."""
    summary = run.summary or {}
    calibration = {run.laptop(w): v for w, v in (summary.get("calibration") or {}).items()}
    rates = {run.laptop(w): v for w, v in run_speeds(run).items()}
    if not {"laptop1", "laptop2"} <= set(calibration) & set(rates):
        return None
    before = {
        k: calibration[k]["positions"] / calibration[k]["seconds"] for k in ("laptop1", "laptop2")
    }
    measured = before["laptop1"] / before["laptop2"]
    reached = rates["laptop1"] / rates["laptop2"]
    return {"calibrated_ratio": measured, "run_ratio": reached, "error": measured / reached - 1}


def network_cost(run: Run) -> dict[str, float]:
    """Messages and bytes in each direction, and the median clock-check round trip (s)."""
    messages = run.messages()
    trips = run.round_trips()["round_trip"]
    out: dict[str, float] = {
        "round_trip_median": float(trips.median()) if len(trips) else float("nan")
    }
    for direction in ("sent", "received"):
        mine = messages[messages["direction"] == direction]
        out[f"messages_{direction}"] = len(mine)
        out[f"bytes_{direction}"] = int(mine["bytes"].sum())
    return out
