"""Reads HWiNFO64 sensor logs (CSV, one row per second; ENVIRONMENT.md, "HWiNFO log format notes").

- The file is Latin-1 text (for the degree sign).
- The first row names the columns. `Date` is D.M.YYYY and `Time` is HH:MM:SS.mmm, in the laptop's
  local time. Once logging stops, HWiNFO adds two more rows (the header again and a row of sensor
  group names); rows whose date cannot be read are dropped, which removes them whether they are
  there yet or not (a log still being written has neither).
- Columns are found by name, never by position: the two laptops log different sensors (Laptop 1
  has P-core and E-core columns, Laptop 2 does not). The same name can appear twice; pandas then
  calls the second one "name.1".
- "Yes"/"No" columns (throttling flags) become True/False; other values become numbers.

The raw CSV is only read, never changed.
"""

import io
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

ENCODING = "latin-1"
TIME_FORMAT = "%d.%m.%Y %H:%M:%S.%f"

# The columns the analysis uses, as patterns over the column names (ENVIRONMENT.md).
PACKAGE_TEMP = r"CPU Package \[°C\]"
PACKAGE_POWER = r"CPU Package Power \[W\]"
EFFECTIVE_CLOCK = r"Average Effective Clock \[MHz\]"
CORE_CLOCKS = r"Core Clocks \(avg\) \[MHz\]"
THROTTLE_FLAGS = (
    r"Core Thermal Throttling.*",
    r"Package/Ring Thermal Throttling.*",
    r"Package/Ring Power Limit Exceeded.*",
    r"IA: PROCHOT.*",
    r"IA: Package-Level RAPL/PBM PL1.*",
    r"IA: Package-Level RAPL/PBM PL2.*",
)


def columns(frame: pd.DataFrame, pattern: str) -> list[str]:
    """The names of every column that matches `pattern` in full."""
    rx = re.compile(pattern)
    return [c for c in frame.columns if rx.fullmatch(str(c))]


def column(frame: pd.DataFrame, pattern: str) -> str:
    """The first column that matches `pattern`; KeyError if none does."""
    found = columns(frame, pattern)
    if not found:
        raise KeyError(f"no HWiNFO column matches {pattern!r}")
    return found[0]


def _local_to_wall(times: pd.Series) -> pd.Series:
    """Local wall-clock times to seconds since 1970, with this computer's present offset from UTC
    (both laptops and the analysis use the same zone, which has no daylight saving time)."""
    offset = datetime.now().astimezone().utcoffset()
    assert offset is not None
    since_1970 = (times - pd.Timestamp("1970-01-01")) / pd.Timedelta(seconds=1)
    return since_1970 - offset.total_seconds()


def _convert(values: pd.Series) -> pd.Series:
    text = values.astype(str).str.strip()
    if text.isin(["Yes", "No"]).all():
        return text == "Yes"
    return pd.to_numeric(text, errors="coerce")


def read_hwinfo(path: Path) -> pd.DataFrame:
    """A sensor log as a table: `time` (local), `wall` (seconds since 1970), then every sensor
    column by its HWiNFO name, converted to numbers or True/False. A last line without its
    newline is a row still being written, and is left out."""
    text = path.read_bytes().decode(ENCODING)
    text = text[: text.rfind("\n") + 1]
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    if "Date" not in raw.columns or "Time" not in raw.columns:
        raise ValueError("not an HWiNFO log: no Date and Time columns")
    stamps = raw["Date"].str.cat(raw["Time"], sep=" ")
    times = pd.to_datetime(stamps, format=TIME_FORMAT, errors="coerce")
    keep = times.notna()
    raw, times = raw[keep].reset_index(drop=True), times[keep].reset_index(drop=True)
    sensors = raw.drop(columns=["Date", "Time"])
    data = {name: _convert(sensors[name]) for name in sensors.columns}
    frame = pd.DataFrame({"time": times, "wall": _local_to_wall(times), **data})
    # Columns HWiNFO leaves empty would come out as all-NaN; they carry nothing.
    return frame.dropna(axis="columns", how="all")


def latest(path: Path, tail_bytes: int = 256 * 1024) -> dict[str, str]:
    """The last complete row of a log that may still be being written (for the cool-down check):
    column name -> raw text. Reads only the first line and the end of the file."""
    with path.open("rb") as f:
        header = f.readline().decode(ENCODING).rstrip("\r\n")
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        tail = f.read().decode(ENCODING)
    # Named as read_hwinfo names them (a repeated name gets ".1").
    names = [str(name) for name in pd.read_csv(io.StringIO(header + "\n"), dtype=str).columns]
    lines = tail.splitlines()
    if not tail.endswith("\n"):
        lines = lines[:-1]  # the row being written right now
    for line in reversed(lines):
        row = pd.read_csv(io.StringIO(line + "\n"), header=None, dtype=str, keep_default_na=False)
        values = list(row.iloc[0])
        if len(values) == len(names) and re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", values[0]):
            return dict(zip(names, values, strict=True))
    raise ValueError(f"{path}: no complete sensor row found yet")


def latest_value(path: Path, pattern: str) -> tuple[float, float]:
    """The newest reading of the first column matching `pattern`, as a number, and its age in
    seconds (now minus the row's local time). ValueError if there is no complete row yet or the
    value is not a number; KeyError if no column matches."""
    row = latest(path)
    taken = datetime.strptime(f"{row['Date']} {row['Time']}", TIME_FORMAT)
    age = (datetime.now() - taken).total_seconds()
    rx = re.compile(pattern)
    for name, value in row.items():
        if rx.fullmatch(name):
            return float(value), age
    raise KeyError(f"no HWiNFO column matches {pattern!r}")
