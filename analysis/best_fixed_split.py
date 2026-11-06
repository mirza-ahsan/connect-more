"""The best fixed split that would have been possible if every task's size had been known in
advance (work plan step 35; methodology, RQ2).

V0 deals the tasks out once, by the laptops' measured speeds, without knowing task sizes. This
works out, after a run, how long a fixed split could have taken had the sizes been known:
- a task's size is the number of positions searched to solve it in that run;
- each laptop's speed is its positions per second while it had work (metrics.run_speeds);
- a laptop's time for its share is its share's positions over its speed, but never less than its
  largest task alone on one thread (a task runs on one thread: speed / threads);
- the tasks are dealt largest first, each to the laptop that would then finish sooner (LPT for
  two machines of different speeds; close to the best possible split).
The gap between V0's solve time and this time is the cost of not knowing task sizes; the rest of
V0's loss against V1 to V3 is the cost of speed changing during the run. This is an estimate: it
assumes a task searches the same positions on either laptop, and a constant speed.

    uv run python -m analysis.best_fixed_split logs/<run_id>
"""

import argparse
import sys
from pathlib import Path

from analysis import metrics
from analysis.load_runs import Run, load_run


def best_split(sizes: list[int], speeds: dict[str, float], threads: dict[str, int]) -> dict:
    """Deals the tasks largest first, each to the laptop that would finish it sooner. Returns
    the estimated time and each laptop's share (tasks, positions, time)."""
    load = dict.fromkeys(speeds, 0)
    largest = dict.fromkeys(speeds, 0)
    count = dict.fromkeys(speeds, 0)

    def finish(laptop: str, extra: int = 0, task: int = 0) -> float:
        whole = (load[laptop] + extra) / speeds[laptop]
        one_thread = max(largest[laptop], task) / (speeds[laptop] / threads[laptop])
        return max(whole, one_thread)

    for size in sorted(sizes, reverse=True):
        laptop = min(speeds, key=lambda name: finish(name, size, size))
        load[laptop] += size
        largest[laptop] = max(largest[laptop], size)
        count[laptop] += 1
    shares = {
        name: {"tasks": count[name], "positions": load[name], "seconds": finish(name)}
        for name in speeds
    }
    return {"seconds": max(s["seconds"] for s in shares.values()), "shares": shares}


def analyse(run: Run) -> dict[str, object]:
    """The best fixed split for one run, next to its own solve time."""
    if run.config["strategy"] not in ("V0", "V1"):
        raise ValueError(f"{run.run_id}: only V0 and V1 runs keep every task whole (no splits)")
    tasks = run.tasks()
    solved = tasks[tasks["end"] == "result"]
    speeds = metrics.run_speeds(run)
    if len(speeds) != len(run.hellos) or solved.empty:
        raise ValueError(f"{run.run_id}: not enough progress reports or results")
    threads = {name: hello["threads"] for name, hello in run.hellos.items()}
    best = best_split([int(p) for p in solved["positions"]], speeds, threads)
    actual = metrics.solve_time(run)
    return {
        "run_id": run.run_id,
        "strategy": run.config["strategy"],
        "solve_seconds": actual,
        "best_fixed_seconds": best["seconds"],
        "gap": None if actual is None else actual / best["seconds"],
        "speeds": speeds,
        "shares": best["shares"],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.best_fixed_split")
    parser.add_argument("runs", nargs="+", type=Path, help="run folders, logs/<run_id>")
    args = parser.parse_args(argv)
    for folder in args.runs:
        result = analyse(load_run(folder))
        print(
            f"{result['run_id']} ({result['strategy']}): solve {result['solve_seconds']:.2f} s, "
            f"best fixed split {result['best_fixed_seconds']:.2f} s, ratio {result['gap']:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
