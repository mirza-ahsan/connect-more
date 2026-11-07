"""Tests for scripts/sensor_log.py on a fake /sys tree laid out like the real one (an AMD desktop
and an Intel laptop), and for analysis/load_sensors.py on the logs it writes."""

import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest

from analysis import load_sensors as ls
from scripts import sensor_log as sl
from tests.helpers import read_log, write_sensor_log


def put(path: Path, text: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{text}\n".encode())


def fake_sys(root: Path, intel: bool = False) -> Path:
    """Package sensor at 45.25 °C, CPUs 0, 1, 2 and 10 at 3.5 to 5 GHz, a RAPL energy counter at
    1 J; an Intel laptop also has power limits (115 W in the CPU, 40 W through the memory-mapped
    interface) and throttle counts, with CPUs 0-1 and 2,10 sharing a core."""
    hwmon = root / "class/hwmon"
    put(hwmon / "hwmon0/name", "nvme")  # another sensor with a temperature: not the CPU
    put(hwmon / "hwmon0/temp1_label", "Composite")
    put(hwmon / "hwmon0/temp1_input", 38000)
    chip, label = ("coretemp", "Package id 0") if intel else ("k10temp", "Tctl")
    put(hwmon / "hwmon3/name", chip)
    put(hwmon / "hwmon3/temp1_label", "Core 0" if intel else "Tccd1")
    put(hwmon / "hwmon3/temp1_input", 40000)
    put(hwmon / "hwmon3/temp2_label", label)
    put(hwmon / "hwmon3/temp2_input", 45250)
    for number, khz in ((0, 3_500_000), (1, 4_000_000), (2, 4_500_000), (10, 5_000_000)):
        cpu = root / f"devices/system/cpu/cpu{number}"
        put(cpu / "cpufreq/cpuinfo_avg_freq", khz)
        put(cpu / "cpufreq/scaling_driver", "intel_pstate" if intel else "amd-pstate-epp")
        if intel:
            put(cpu / "thermal_throttle/core_throttle_count", number)
            put(cpu / "thermal_throttle/package_throttle_count", 7)
            put(cpu / "topology/thread_siblings_list", "0-1" if number < 2 else "2,10")
    put(root / "devices/system/cpu/cpufreq/boost", 1)
    rapl = root / sl.RAPL
    put(rapl / "energy_uj", 1_000_000)
    put(rapl / "max_energy_range_uj", 262_143_328_850)
    if intel:
        for domain, pl1 in ((rapl, 115_000_000), (root / sl.RAPL_MMIO, 40_000_000)):
            put(domain / "constraint_0_name", "long_term")
            put(domain / "constraint_0_power_limit_uw", pl1)
            put(domain / "constraint_1_name", "short_term")
            put(domain / "constraint_1_power_limit_uw", 115_000_000)
    return root


def ticks(*times: float) -> Iterator[float]:
    return itertools.cycle(times)


def test_an_amd_desktop(tmp_path: Path) -> None:
    root = fake_sys(tmp_path)
    clock = ticks(0.0, 2.0)
    sensors = sl.Sensors(root, clock=lambda: next(clock))
    put(root / sl.RAPL / "energy_uj", 21_000_000)  # 20 J in 2 s
    sample = sensors.sample()
    assert sample["package_c"] == 45.25
    assert sample["power_w"] == pytest.approx(10.0)
    assert sample["cpu_mhz"] == [3500.0, 4000.0, 4500.0, 5000.0]  # cpu10 last
    assert sample["clock_mhz"] == 4250.0
    assert sample["pl1_w"] is None and sample["throttle_core"] is None
    about = sensors.describe()
    assert about["package_temp_file"] == str(root / "class/hwmon/hwmon3/temp2_input")
    assert about["frequency_file"] == "cpuinfo_avg_freq" and about["boost"] == "1"
    assert about["rapl_energy_readable"] is True and about["cpufreq_driver"] == "amd-pstate-epp"


def test_an_intel_laptop(tmp_path: Path) -> None:
    sensors = sl.Sensors(fake_sys(tmp_path, intel=True))
    sample = sensors.sample()
    assert sample["package_c"] == 45.25
    assert (sample["pl1_w"], sample["pl2_w"]) == (115.0, 115.0)
    assert (sample["pl1_mmio_w"], sample["pl2_mmio_w"]) == (40.0, 115.0)
    # Each core once (CPUs 0 and 2), not each hyper-thread: 0 + 2, not 0 + 1 + 2 + 10.
    assert (sample["throttle_core"], sample["throttle_package"]) == (2, 7)
    assert sensors.describe()["pl1_mmio_w"] == 40.0


def test_the_energy_counter_wraps(tmp_path: Path) -> None:
    root = fake_sys(tmp_path)
    put(root / sl.RAPL / "max_energy_range_uj", 9_999_999)
    put(root / sl.RAPL / "energy_uj", 9_000_000)
    clock = ticks(0.0, 1.0)
    sensors = sl.Sensors(root, clock=lambda: next(clock))
    put(root / sl.RAPL / "energy_uj", 1_000_000)  # 1 J to the top, then 1 J more
    assert sensors.sample()["power_w"] == pytest.approx(2.0)


def test_unreadable_values_are_none(tmp_path: Path) -> None:
    root = fake_sys(tmp_path)
    put(root / "devices/system/cpu/cpu1/cpufreq/cpuinfo_avg_freq", "")  # an idle CPU's answer
    (root / sl.RAPL / "energy_uj").unlink()  # as when only root may read it
    sensors = sl.Sensors(root)
    sample = sensors.sample()
    assert sample["power_w"] is None and sensors.describe()["rapl_energy_readable"] is False
    assert sample["cpu_mhz"] == [3500.0, None, 4500.0, 5000.0]
    assert sample["clock_mhz"] == pytest.approx(13000 / 3)

    nothing = sl.Sensors(tmp_path / "empty")
    assert nothing.temp_file is None and nothing.sample()["package_c"] is None
    assert nothing.describe()["logical_cpus"] == 0


def test_scaling_cur_freq_when_there_is_no_average(tmp_path: Path) -> None:
    root = fake_sys(tmp_path)
    for cpu in (root / "devices/system/cpu").glob("cpu*/cpufreq"):
        (cpu / "cpuinfo_avg_freq").rename(cpu / "scaling_cur_freq")
    assert sl.Sensors(root).sample()["clock_mhz"] == 4250.0


def test_run_writes_a_log_the_analysis_reads(tmp_path: Path) -> None:
    root = fake_sys(tmp_path / "sys")
    path = sl.run("pc", tmp_path / "logs", interval=0.01, samples=3, sys_root=root)
    assert path.parent == tmp_path / "logs/sensors" and path.name.endswith("_pc.jsonl")
    records = read_log(path)
    assert [r["event"] for r in records] == ["sensor_log_start", *["sample"] * 3, "sensor_log_end"]
    assert records[0]["machine"] == "pc" and "repository" in records[0]["build"]
    frame = ls.read_sensor_log(path)
    assert len(frame) == 3 and frame["package_c"].tolist() == [45.25] * 3
    assert frame["wall"].is_monotonic_increasing
    assert ls.latest_sample(path)["wall"] == records[3]["wall"]
    assert ls.newest_log(tmp_path / "logs/sensors") == path


def test_a_log_still_being_written(tmp_path: Path) -> None:
    path = write_sensor_log(tmp_path / "live.jsonl", [1.0, 2.0], [50.0, None], partial='{"mono":')
    frame = ls.read_sensor_log(path)
    assert frame["wall"].tolist() == [1.0, 2.0] and frame["package_c"].isna().tolist() == [
        False,
        True,
    ]
    assert ls.latest_sample(path)["wall"] == 2.0
    assert ls.latest_sample(path, tail_bytes=150)["wall"] == 2.0  # the tail starts mid-line
    with pytest.raises(ValueError, match="no complete sensor sample"):
        ls.latest_sample(write_sensor_log(tmp_path / "empty.jsonl", [], []))
    assert ls.newest_log(tmp_path / "nothing") is None

    damaged = tmp_path / "damaged.jsonl"
    damaged.write_text(
        '{"event":"sample","package_c":1}\n'  # no clock: of no use
        '{"event":"sample","wall":"x"}\n'
        "[1, 2]\n"
        "not json\n"
        '{"event":"sample","wall":5.0,"package_c":40}\n',
        encoding="utf-8",
    )
    assert ls.read_sensor_log(damaged)["wall"].tolist() == [5.0]


def test_this_machine() -> None:
    """The real /sys of the machine the tests run on (skipped without a known CPU sensor)."""
    if sl.package_temp_file(sl.SYS) is None:
        pytest.skip("no coretemp or k10temp package sensor here")
    sample = sl.Sensors().sample()
    assert 0 < sample["package_c"] < 110 and sample["clock_mhz"] > 0
