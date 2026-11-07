"""Reads the sensor logs that scripts/sensor_log.py writes (one JSON line per second, with both
clocks of common/runlog.py): logs/sensors/ on Laptop 1, logs/pc/sensors/ for the PC's copies.

A log may still be being written: a last line without its newline is a line being written right
now, and is left out; so is any line that cannot be read. The raw log is only read, never changed.
"""

import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from common.machines import COORDINATOR, MACHINES, PEER, PEER_DIR, SENSORS_DIR

SAMPLE = "sample"
# The sample values with one number each (keep in step with scripts/sensor_log.py
# Sensors.sample); the per-CPU lists are left out.
VALUES = (
    "package_c",
    "power_w",
    "clock_mhz",
    "usage_pct",
    "pl1_w",
    "pl2_w",
    "pl1_mmio_w",
    "pl2_mmio_w",
    "throttle_core",
    "throttle_package",
)


def _samples(lines: Iterable[str]) -> Iterable[dict]:
    """The sample records among complete lines (ending in a newline) that can be read."""
    for line in lines:
        if not line.endswith("\n"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("event") != SAMPLE:
            continue
        if isinstance(record.get("wall"), int | float):  # the clock every use needs
            yield record


def read_sensor_log(path: Path, columns: Iterable[str] = ("wall", "mono", *VALUES)) -> pd.DataFrame:
    """A sensor log's samples as a table of `columns` (by default `wall` in seconds since 1970,
    `mono`, then VALUES), NaN where a value could not be read. Reads one line at a time, so a
    long session's log is never in memory whole."""
    columns = list(columns)
    with path.open(encoding="utf-8") as f:
        rows = [[record.get(c) for c in columns] for record in _samples(f)]
    return pd.DataFrame(rows, columns=columns).astype(float)


def latest_sample(path: Path, tail_bytes: int = 64 * 1024) -> dict:
    """The newest complete sample of a log that may still be being written (for the cool-down
    check); ValueError if there is none yet. Reads only the end of the file."""
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - tail_bytes))
        tail = f.read().decode("utf-8", errors="replace")
    # Newest first, so usually only the last line is parsed; a cut first line is skipped.
    for record in _samples(reversed(tail.splitlines(keepends=True))):
        return record
    raise ValueError(f"{path}: no complete sensor sample yet")


def newest_log(folder: Path) -> Path | None:
    """The sensor log in `folder` written to last, or None if there is none."""
    return max(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)


def read_sensors(logs: Path) -> dict[str, list[pd.DataFrame]]:
    """Each machine's sensor logs, read once: wall clock, package temperature, power, clock and
    both power limits, from logs/sensors/ and logs/pc/sensors/ (the PC's copies on Laptop 1).
    A log belongs to the machine its name ends with (scripts/sensor_log.py names it
    "<date-time>_<machine>.jsonl"), so either machine's own logs folder can be read; a log
    with another name belongs to the folder's usual machine."""
    folders = {COORDINATOR: logs / SENSORS_DIR, PEER: logs / PEER_DIR / SENSORS_DIR}
    wanted = ("wall", "package_c", "power_w", "clock_mhz", "pl1_w", "pl1_mmio_w")
    sensors: dict[str, list[pd.DataFrame]] = {}
    for usual, folder in folders.items():
        for path in sorted(folder.glob("*.jsonl")):
            if ".rewritten-" in path.name:  # an old copy set aside by collect_logs
                continue
            named = path.stem.rsplit("_", 1)[-1]
            machine = named if named in MACHINES else usual
            sensors.setdefault(machine, []).append(read_sensor_log(path, wanted))
    return sensors
