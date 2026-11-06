"""The tables for the paper (work plan step 36): every average with its lowest and highest value.

    uv run python -m analysis.tables [--logs logs] [--out analysis/out] [--pilot]

Writes, into the output folder (git-ignored; derived data only, the raw logs are never changed):
- runs.csv: one row per run with every metric (analysis/metrics.py);
- summary.csv and summary.md: per experiment, strategy, link and depth, the mean, minimum and
  maximum of the main metrics over the repeats, and how many runs there were.
Only runs that ended with the right result, and that scripts/run_experiment.py found valid, are
counted; the others stay in runs.csv with `used` = False, so nothing is dropped silently.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from analysis import metrics
from analysis.load_runs import Run, load_runs, read_log, runs_table

OUT = Path(__file__).resolve().parent / "out"
GROUP = ["experiment", "laptop", "strategy", "link", "depth", "start"]
MAIN = [
    "solve_seconds",
    "S1",
    "S2",
    "E",
    "extra_vs_laptop1",
    "extra_vs_unsplit",
    "idle_share_laptop1",
    "idle_share_laptop2",
    "tail_idle_laptop1",
    "tail_idle_laptop2",
    "positions",
    "positions_lost",
    "repeated_sent",
    "repeated_positions",
    "positions_unneeded",
    "answered_from_memory",
    "round_trip_median",
]


def unsplit_positions(logs: Path) -> dict[str, float]:
    """Positions searched by the unsplit single search (E1), by start position: from the newest
    unsplit log of each start position that gave the right result."""
    found: dict[str, float] = {}
    for path in sorted(logs.glob("*/unsplit.jsonl")):
        records = read_log(path)
        start, end = records[0], records[-1]
        if end.get("event") == "unsplit_end" and end.get("correct") is True:
            found[start["start"]] = float(end["result"]["positions"])
    return found


def used(run: Run) -> bool:
    """A result: it ended with the right answer and was not found invalid."""
    summary = run.summary or {}
    return summary.get("correct") is True and run.valid is not False


def per_run(runs: list[Run], unsplit: dict[str, float]) -> pd.DataFrame:
    refs = metrics.references([r for r in runs if used(r)])
    table = runs_table(runs)
    extra = []
    for run in runs:
        row: dict[str, object] = {"used": used(run), "valid": run.valid}
        t = metrics.solve_time(run)
        if t:
            row.update(metrics.speedup(t, refs))
            for _, idle in metrics.idle_time(run).iterrows():
                laptop = run.laptop(str(idle["worker"]))
                row[f"idle_share_{laptop}"] = idle["idle_share"]
                row[f"tail_idle_{laptop}"] = idle["tail_idle"]
                row[f"thread_tail_idle_{laptop}"] = idle["thread_tail_idle"]
        row.update(metrics.work(run, refs, unsplit.get(run.config["start"])))
        row.update(metrics.repeated_work(run))
        row.update(metrics.network_cost(run))
        if (error := metrics.calibration_error(run)) is not None:
            row.update({f"calibration_{k}": v for k, v in error.items()})
        extra.append(row)
    extra_frame = pd.DataFrame(extra)
    duplicated = [c for c in extra_frame.columns if c in table.columns]
    return pd.concat([table, extra_frame.drop(columns=duplicated)], axis=1)


def summary(runs_frame: pd.DataFrame) -> pd.DataFrame:
    """Mean, min and max of the main metrics per setting, over the runs that are used."""
    chosen = runs_frame[runs_frame["used"]]
    columns = [c for c in MAIN if c in chosen.columns]
    if chosen.empty or not columns:
        return pd.DataFrame()
    keys = [g for g in GROUP if g in chosen.columns]
    groups = chosen.groupby(keys, dropna=False)
    grouped = groups[columns].agg(["mean", "min", "max"])
    grouped.columns = [f"{name}_{stat}" for name, stat in grouped.columns]
    grouped.insert(0, "runs", groups.size())
    return grouped.reset_index()


def markdown(frame: pd.DataFrame) -> str:
    """A plain Markdown table (numbers to 3 significant digits)."""
    if frame.empty:
        return "_No runs to show._\n"
    head = "| " + " | ".join(map(str, frame.columns)) + " |"
    rule = "|" + "---|" * len(frame.columns)
    rows = [
        "| " + " | ".join(f"{v:.3g}" if isinstance(v, float) else str(v) for v in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([head, rule, *rows]) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.tables", description=__doc__)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--pilot", action="store_true", help="include pilot runs")
    args = parser.parse_args(argv)
    runs = load_runs(args.logs, pilot=args.pilot)
    if not runs:
        print(f"tables: no runs under {args.logs}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    frame = per_run(runs, unsplit_positions(args.logs))
    frame.to_csv(args.out / "runs.csv", index=False)
    table = summary(frame)
    table.to_csv(args.out / "summary.csv", index=False)
    (args.out / "summary.md").write_bytes(markdown(table).encode())
    print(f"tables: {len(runs)} runs ({int(frame['used'].sum())} used) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
