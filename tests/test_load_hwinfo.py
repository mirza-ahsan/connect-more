"""Tests for analysis/load_hwinfo.py on a small synthetic HWiNFO log, written like the real ones:
Latin-1, D.M.YYYY dates, a comma after every value, a repeated column name, and (once logging
stops) the header again plus a row of sensor group names at the end."""

from pathlib import Path

import pytest

from analysis import load_hwinfo as lh

HEADER = [
    "Date",
    "Time",
    "Core Clocks (avg) [MHz]",
    "Average Effective Clock [MHz]",
    "P-core 0 Clock [MHz]",
    "E-core 6 Clock [MHz]",
    "CPU Package [°C]",
    "CPU Package Power [W]",
    "Core Thermal Throttling (avg) [Yes/No]",
    "CPU Package [°C]",  # the same name in a second sensor group
]
ROWS = [
    ["5.11.2026", "14:00:00.250", "4200.5", "3100.0", "4600", "3400", "52", "28.5", "No", "53"],
    ["5.11.2026", "14:00:01.251", "4100.0", "3500.0", "4500", "3300", "71", "45.0", "No", "72"],
    ["5.11.2026", "14:00:02.250", "3200.0", "2900.0", "3800", "3000", "95", "40.0", "Yes", "96"],
]
GROUPS = ["", "", "CPU [#0]", "CPU [#0]", "CPU [#0]", "CPU [#0]", "CPU [#0]", "CPU [#0]", "", "DTS"]


def write(path: Path, rows: list[list[str]], stopped: bool = True, partial: str = "") -> Path:
    lines = [HEADER, *rows] + ([HEADER, GROUPS] if stopped else [])
    text = "".join(",".join(f'"{v}"' if " " in v else v for v in line) + ",\n" for line in lines)
    path.write_bytes((text + partial).encode("latin-1"))
    return path


def test_read_hwinfo(tmp_path: Path) -> None:
    frame = lh.read_hwinfo(write(tmp_path / "log.CSV", ROWS))
    assert len(frame) == 3  # the two rows HWiNFO adds at the end are gone
    assert frame["CPU Package [°C]"].tolist() == [52, 71, 95]
    assert frame["CPU Package [°C].1"].tolist() == [53, 72, 96]
    assert frame["Core Thermal Throttling (avg) [Yes/No]"].tolist() == [False, False, True]
    assert frame["wall"].diff().iloc[1] == pytest.approx(1.001)
    assert str(frame["time"].iloc[0]) == "2026-11-05 14:00:00.250000"
    assert not [c for c in frame.columns if str(c).startswith("Unnamed")]  # the empty last one

    assert lh.column(frame, lh.PACKAGE_TEMP) == "CPU Package [°C]"
    assert lh.columns(frame, r"[PE]-core \d+ Clock \[MHz\]") == [
        "P-core 0 Clock [MHz]",
        "E-core 6 Clock [MHz]",
    ]
    with pytest.raises(KeyError):
        lh.column(frame, r"GPU Temperature")


def test_a_log_still_being_written(tmp_path: Path) -> None:
    path = write(tmp_path / "live.CSV", ROWS, stopped=False, partial="5.11.2026,14:00:03.2")
    assert len(lh.read_hwinfo(path)) == 3  # the half-written row is dropped
    assert lh.latest(path)["Time"] == "14:00:02.250"
    assert lh.latest_value(path, lh.PACKAGE_TEMP)[0] == 95


def test_latest_of_a_stopped_log_skips_the_footer(tmp_path: Path) -> None:
    path = write(tmp_path / "stopped.CSV", ROWS)
    assert lh.latest_value(path, lh.PACKAGE_TEMP)[0] == 95


def test_not_a_hwinfo_log(tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    path.write_text("a,b\n1,2\n", encoding="latin-1")
    with pytest.raises(ValueError, match="not an HWiNFO log"):
        lh.read_hwinfo(path)
    empty = write(tmp_path / "empty.CSV", [], stopped=False)
    with pytest.raises(ValueError, match="no complete sensor row"):
        lh.latest(empty)
