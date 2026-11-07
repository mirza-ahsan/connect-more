"""Run logs: one JSON object per line, each stamped with two clocks.

- `mono` is time.perf_counter(): a steady clock for measuring durations. It never jumps, and on
  Linux it is CLOCK_MONOTONIC, which every process on the same machine shares, so the
  coordinator's and the worker's logs on Laptop 1 can be compared directly.
- `wall` is the normal clock (seconds since 1970), for lining logs of the two machines up (with
  the clock checks) and for the sensor logs of scripts/sensor_log.py, and `local` the same moment
  as local date and time, for people reading the logs.

A log file is never overwritten: opening a path that already exists fails. Raw logs are kept
exactly as written (CLAUDE.md); anything derived from them is made by scripts in analysis/.
"""

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
RESERVED = ("mono", "wall", "local", "event")
NAME = re.compile(r"[A-Za-z0-9_.\-]+")


def stamp() -> str:
    """The local date and time for naming logs and runs, e.g. "2026-11-05_14-03-27"."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def log_path(run_id: str, name: str, root: Path = LOGS_DIR) -> Path:
    """Where a run's log goes: logs/<run_id>/<name>.jsonl."""
    for part in (run_id, name):
        if not NAME.fullmatch(part):
            raise ValueError(f"{part!r}: use only letters, digits, '-', '_' and '.'")
    return root / run_id / f"{name}.jsonl"


class RunLog:
    """Writes one log file. Safe to use from several threads; use one per process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # "x" creates the file and fails if it exists, so a log can never be overwritten.
        self._file = path.open("x", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, event: str, /, **fields: object) -> None:
        """Writes one line: both clocks, the event name, then the fields."""
        clash = [name for name in fields if name in RESERVED]
        if clash:
            raise ValueError(f"field names {clash} are reserved for the clocks and event")
        mono = time.perf_counter()
        wall = time.time()
        record = {
            "mono": mono,
            "wall": wall,
            "local": datetime.fromtimestamp(wall).astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()  # a crash loses at most the line being written

    def close(self) -> None:
        with self._lock:
            self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
