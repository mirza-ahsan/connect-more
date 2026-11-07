"""The figures for the paper (work plan step 36). Every figure is made from the logs by this script.

    uv run python -m analysis.figures [--logs logs] [--out analysis/out] [--pilot]

Writes PNG (300 dpi, for the paper) into the output folder:
- solve_time.png: mean solve time per strategy, one bar group per link, with the lowest and
  highest repeat as a range (E2), from analysis/tables.py's runs;
- timeline_<run_id>.png: per run, every solver thread of each machine as a lane, with each task as
  a bar from its start to its end (how it ended shown by fill), so idle time is visible;
- sensors_<run_id>.png: per run, stacked panels on one time axis: positions per second of each
  machine (10 s windows), CPU package temperature, package power and average clock, from each
  machine's sensor log (scripts/sensor_log.py: logs/sensors/ for Laptop 1, logs/pc/sensors/ for
  the PC), lined up with the run through the clock checks. Never two y-scales on one panel.
Colours: one fixed colour per machine (Laptop 1 blue, the PC orange) in every figure.
"""

import argparse
import sys
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")  # files only, no window
import matplotlib.pyplot as plt
import pandas as pd

from analysis import metrics
from analysis.load_runs import Run, load_runs
from analysis.load_sensors import read_sensors
from analysis.tables import OUT, per_run, unsplit_positions
from common.machines import MACHINES

MACHINE_COLOURS = dict(zip(MACHINES, ("#2a78d6", "#eb6834"), strict=True))  # categorical slots 1, 2
LINK_COLOURS = {"cable": "#2a78d6", "wifi": "#eb6834"}
INK, MUTED = "#2b2b2b", "#8a8a8a"
END_HATCH = {"result": "", "stopped": "////", "cancelled": "...."}
STRATEGIES = ["V0", "V1", "V2", "V3"]


def _style(ax: plt.Axes) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(MUTED)
    ax.tick_params(colors=INK, labelsize=8)
    ax.grid(axis="y", color="#e6e6e6", linewidth=0.6)
    ax.set_axisbelow(True)


def solve_time_figure(runs_frame: pd.DataFrame, path: Path) -> bool:
    """Mean solve time per strategy and link (E2), with the min-max range of the repeats."""
    e2 = runs_frame[runs_frame["used"] & (runs_frame.get("experiment") == "E2")]
    if e2.empty:
        return False
    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    links = [link for link in ("cable", "wifi") if link in set(e2["link"])]
    width = 0.8 / max(1, len(links))
    for i, link in enumerate(links):
        for j, strategy in enumerate(STRATEGIES):
            times = e2[(e2["link"] == link) & (e2["strategy"] == strategy)]["solve_seconds"]
            if times.empty:
                continue
            x = j + (i - (len(links) - 1) / 2) * width
            middle = times.mean()
            ax.bar(x, middle, width * 0.9, color=LINK_COLOURS[link], label=link if j == 0 else None)
            ax.vlines(x, times.min(), times.max(), color=INK, linewidth=1.2)
    ax.set_xticks(range(len(STRATEGIES)), STRATEGIES)
    ax.set_ylabel("Solve time (s)", color=INK, fontsize=9)
    ax.set_title("Solve time: mean of repeats, line = lowest to highest", fontsize=9, color=INK)
    ax.legend(frameon=False, fontsize=8)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return True


def timeline_figure(run: Run, path: Path) -> bool:
    """One lane per solver thread, each task a bar from start to end."""
    tasks = run.tasks().dropna(subset=["started", "ended", "thread"])
    t = metrics.solve_time(run)
    if tasks.empty or t is None:
        return False
    lanes = sorted({(w, int(th)) for w, th in zip(tasks["worker"], tasks["thread"], strict=True)})
    lane_of = {lane: i for i, lane in enumerate(lanes)}
    fig, ax = plt.subplots(figsize=(7.0, max(2.0, 0.12 * len(lanes) + 1.2)))
    # One collection per lane and way of ending: thousands of tasks draw quickly.
    bars: dict[tuple[tuple[str, int], str], list[tuple[float, float]]] = {}
    columns = ["worker", "thread", "started", "ended", "end"]
    for worker, thread, started, ended, end in tasks[columns].itertuples(index=False, name=None):
        lane = (worker, int(thread))
        bars.setdefault((lane, end), []).append((float(started), float(ended) - float(started)))
    for ((worker, thread), end), spans in bars.items():
        ax.broken_barh(
            spans,
            (lane_of[(worker, thread)] - 0.4, 0.8),
            facecolors=MACHINE_COLOURS.get(Run.machine(worker), MUTED),
            hatch=END_HATCH.get(end, ""),
            edgecolor="white",
            linewidth=0.3,
        )
    ax.axvline(t, color=INK, linewidth=0.8, linestyle="--")
    ax.text(t, len(lanes) - 0.5, " result known", fontsize=7, color=INK, va="top")
    ax.set_yticks([])
    ax.set_xlabel("Time from the first task sent (s)", color=INK, fontsize=9)
    names = sorted({Run.machine(w) for w, _ in lanes})
    ax.set_title(
        f"{run.run_id}: one lane per solver thread ("
        + ", ".join(f"{n}: {sum(Run.machine(w) == n for w, _ in lanes)}" for n in names)
        + "); hatched = stopped or cancelled",
        fontsize=7,
        color=INK,
    )
    for name in names:
        ax.barh([], [], color=MACHINE_COLOURS.get(name, MUTED), label=name)
    ax.legend(frameon=False, fontsize=7, loc="upper right", bbox_to_anchor=(1.0, -0.12), ncol=2)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return True


def sensors_figure(run: Run, readings: dict[str, list[pd.DataFrame]], path: Path) -> bool:
    speed = metrics.speed_over_time(run)
    sensors = metrics.sensor_series(run, readings, margin=5.0)
    if speed.empty and not sensors:
        return False
    panels = [("Positions per second (millions)", None)]
    if sensors:
        panels += [
            ("CPU package (°C)", "package_c"),
            ("Package power (W)", "power_w"),
            ("Average clock (MHz)", "clock_mhz"),
        ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(6.5, 1.9 * len(panels)), sharex=True)
    axes = [axes] if len(panels) == 1 else list(axes)
    for worker in sorted(speed["worker"].unique()):
        mine = speed[speed["worker"] == worker]
        colour = MACHINE_COLOURS.get(Run.machine(worker), MUTED)
        axes[0].plot(mine["time"], mine["positions_per_s"] / 1e6, color=colour, linewidth=2,
                     label=Run.machine(worker))  # fmt: skip
    for ax, (label, key) in zip(axes, panels, strict=True):
        if key is not None:
            for machine, frame in sensors.items():
                ax.plot(frame["time"], frame[key], color=MACHINE_COLOURS[machine], linewidth=1.5,
                        label=machine)  # fmt: skip
        ax.set_ylabel(label, color=INK, fontsize=8)
        _style(ax)
    if axes[0].get_legend_handles_labels()[0]:
        axes[0].legend(frameon=False, fontsize=7)
    axes[0].set_title(run.run_id, fontsize=8, color=INK)
    axes[-1].set_xlabel("Time from the first task sent (s)", color=INK, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m analysis.figures", description=__doc__)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--pilot", action="store_true", help="include pilot runs")
    args = parser.parse_args(argv)
    runs = load_runs(args.logs, pilot=args.pilot)
    if not runs:
        print(f"figures: no runs under {args.logs}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    made = []
    sensors = read_sensors(args.logs)
    runs_frame = per_run(runs, unsplit_positions(args.logs), sensors)
    if solve_time_figure(runs_frame, args.out / "solve_time.png"):
        made.append("solve_time.png")
    for run in runs:
        timeline = args.out / f"timeline_{run.run_id}.png"
        if timeline_figure(run, timeline):
            made.append(timeline.name)
        panels = args.out / f"sensors_{run.run_id}.png"
        if sensors_figure(run, sensors, panels):
            made.append(panels.name)
    print(f"figures: {len(made)} figures -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
