"""Logs this machine's CPU sensors once a second for a whole session (on Linux, in place of HWiNFO).

Start it in its own terminal before the first run of a session, on both machines, and stop it
with Ctrl+C after the last one (docs/session-checklist.md):
    uv run python -m scripts.sensor_log --machine laptop1        (on the PC: --machine pc)

It writes logs/sensors/<date-time>_<machine>.jsonl (common/runlog.py: never overwritten, both clocks
on every line). The first record, `sensor_log_start`, holds the build (CLAUDE.md), the CPU model,
the file each value is read from, the frequency driver, governor, energy preference and turbo
setting, the platform profile, and the RAPL power limits. Then one `sample` record per second:
- package_c: CPU package temperature in °C: coretemp "Package id 0" (Intel) or k10temp "Tctl"
  (AMD; on the Ryzen 7000 series Tctl is the real die temperature, without an offset);
- power_w: CPU package power in W, from the RAPL energy counter (powercap "intel-rapl:0", which
  AMD processors use too) over the last interval. Linux lets only root read that counter: run
  `sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj` once after every boot, otherwise
  power_w is null (the start record says whether it could be read);
- clock_mhz: the average clock of all logical CPUs, and cpu_mhz for each one, from cpufreq's
  cpuinfo_avg_freq (the kernel's APERF/MPERF average while the CPU was running; scaling_cur_freq
  where that is missing). Unlike HWiNFO's "effective clock", time a CPU spends halted does not
  lower it; under a full solver load the two are about the same;
- usage_pct: CPU use over the last interval, of the whole machine and (cpu_usage_pct) of each
  logical CPU;
- pl1_w and pl2_w: the RAPL long- and short-term power limits at that moment, as set in the CPU
  (powercap "intel-rapl:0"), and pl1_mmio_w and pl2_mmio_w: the same limits as set through the
  memory-mapped interface ("intel-rapl-mmio:0"), which laptop firmware often uses for the limit
  that really applies; the lower of the two holds. Intel only, and they can change during a run;
- throttle_core and throttle_package: Intel's counts of thermal throttling events since boot
  (summed over the physical cores, each counted once, and of the package).
AMD has no power limits or throttle counts here, so those values are null there.
A value that cannot be read is null. The raw log is never changed; analysis/load_sensors.py
reads it.
"""

import argparse
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from common.machines import MACHINES, SENSORS_DIR
from common.runlog import LOGS_DIR, RunLog, log_path, stamp
from scripts.helpers import build_info

SYS = Path("/sys")
INTERVAL = 1.0  # seconds between samples
CPU = "devices/system/cpu"
PACKAGE_LABELS = {"coretemp": "Package id 0", "k10temp": "Tctl"}  # hwmon driver -> label
RAPL = "class/powercap/intel-rapl:0"  # the package domain (Intel and AMD)
RAPL_MMIO = "class/powercap/intel-rapl-mmio:0"  # the same limits set another way (Intel)


def read(path: Path) -> str | None:
    """The stripped text of a sysfs file, or None if it is missing or cannot be read."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def read_number(path: Path, scale: float = 1.0) -> float | None:
    """A sysfs number divided by `scale`, or None."""
    text = read(path)
    try:
        return None if text is None else int(text) / scale
    except ValueError:
        return None


def package_temp_file(sys_root: Path) -> Path | None:
    """The temp*_input file of the CPU package sensor (PACKAGE_LABELS), or None if none is found."""
    for hwmon in sorted((sys_root / "class/hwmon").glob("hwmon*")):
        wanted = PACKAGE_LABELS.get(read(hwmon / "name") or "")
        if wanted is None:
            continue
        for label in sorted(hwmon.glob("temp*_label")):
            if read(label) == wanted:
                return label.with_name(label.name.replace("_label", "_input"))
    return None


def cpu_dirs(sys_root: Path) -> list[Path]:
    """The logical CPUs' sysfs folders, in CPU order (cpu2 before cpu10)."""
    found = (sys_root / CPU).glob("cpu[0-9]*")
    return sorted(found, key=lambda p: int(p.name.removeprefix("cpu")))


def first_thread(cpu: Path) -> bool:
    """Whether `cpu` is the first logical CPU of its physical core (True if the topology is not
    known), so that a per-core counter is counted once, not once per hyper-thread."""
    siblings = read(cpu / "topology/thread_siblings_list")  # e.g. "0-1" or "0,10"
    if not siblings:
        return True
    return re.split(r"[,-]", siblings)[0] == cpu.name.removeprefix("cpu")


def power_limit_files(rapl: Path) -> dict[str, Path]:
    """The RAPL limit files by name ("long_term" = PL1, "short_term" = PL2); none on AMD."""
    files = {}
    for constraint in sorted(rapl.glob("constraint_*_name")):
        if name := read(constraint):
            files[name] = constraint.with_name(constraint.name.replace("_name", "_power_limit_uw"))
    return files


def cpu_model() -> str | None:
    """The CPU's name from /proc/cpuinfo, or None."""
    for line in (read(Path("/proc/cpuinfo")) or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


class Sensors:
    """Reads one sample at a time. Package power and CPU use are averages since the previous
    sample (for the first one, since the logger started). The files to read are found once."""

    def __init__(self, sys_root: Path = SYS, clock: Callable[[], float] = time.monotonic) -> None:
        self.sys_root = sys_root
        self.clock = clock
        self.temp_file = package_temp_file(sys_root)
        self.cpus = cpu_dirs(sys_root)
        frequency = "cpuinfo_avg_freq"
        if self.cpus and not (self.cpus[0] / "cpufreq" / frequency).exists():
            frequency = "scaling_cur_freq"
        self.frequency_files = [cpu / "cpufreq" / frequency for cpu in self.cpus]
        # Intel only: thermal throttle counts and RAPL power limits (AMD has neither).
        cores = [cpu / "thermal_throttle" for cpu in self.cpus if first_thread(cpu)]
        self.core_throttle_files = [d / "core_throttle_count" for d in cores if d.is_dir()]
        package = cores[0] / "package_throttle_count" if cores else None
        self.package_throttle_file = package if package and package.exists() else None
        self.rapl = sys_root / RAPL
        self.limit_files = power_limit_files(self.rapl)
        self.mmio_limit_files = power_limit_files(sys_root / RAPL_MMIO)
        self.energy_range = read_number(self.rapl / "max_energy_range_uj")
        self._energy: tuple[float, float] | None = self._read_energy()
        psutil.cpu_percent(percpu=True)  # starts psutil's interval for the first sample

    def _read_energy(self) -> tuple[float, float] | None:
        """(clock, µJ) now, or None if the counter cannot be read."""
        energy = read_number(self.rapl / "energy_uj")
        return None if energy is None else (self.clock(), energy)

    def _power(self) -> float | None:
        """Package power in W since the last call; the counter wraps at max_energy_range_uj."""
        before, self._energy = self._energy, self._read_energy()
        if before is None or self._energy is None or self._energy[0] <= before[0]:
            return None
        used = self._energy[1] - before[1]
        if used < 0 and self.energy_range:
            used += self.energy_range + 1
        return used / 1e6 / (self._energy[0] - before[0]) if used >= 0 else None

    def _limits(self) -> dict[str, float | None]:
        """PL1 and PL2 in W from both RAPL interfaces (None where there is no such limit)."""
        limits = {}
        for suffix, files in (("", self.limit_files), ("_mmio", self.mmio_limit_files)):
            for key, name in (("pl1", "long_term"), ("pl2", "short_term")):
                path = files.get(name)
                limits[f"{key}{suffix}_w"] = None if path is None else read_number(path, 1e6)
        return limits

    def describe(self) -> dict[str, object]:
        """Where each value comes from and the machine's power settings (the start record)."""
        cpus = self.sys_root / CPU
        return {
            "cpu_model": cpu_model(),
            "logical_cpus": len(self.cpus),
            "package_temp_file": str(self.temp_file) if self.temp_file else None,
            "frequency_file": self.frequency_files[0].name if self.frequency_files else None,
            "rapl_energy_readable": self._energy is not None,
            "cpufreq_driver": read(cpus / "cpu0/cpufreq/scaling_driver"),
            "governor": read(cpus / "cpu0/cpufreq/scaling_governor"),
            "energy_performance_preference": read(
                cpus / "cpu0/cpufreq/energy_performance_preference"
            ),
            "boost": read(cpus / "cpufreq/boost"),  # AMD and acpi-cpufreq: 1 = turbo on
            "intel_no_turbo": read(cpus / "intel_pstate/no_turbo"),  # Intel: 0 = turbo on
            "platform_profile": read(self.sys_root / "firmware/acpi/platform_profile"),
            **self._limits(),
        }

    def sample(self) -> dict[str, Any]:
        """One reading of every value (None where it cannot be read)."""
        mhz = [read_number(path, 1000.0) for path in self.frequency_files]
        known = [v for v in mhz if v is not None]
        usage = psutil.cpu_percent(percpu=True)
        counts = [c for path in self.core_throttle_files if (c := read_number(path)) is not None]
        package = self.package_throttle_file
        return {
            "package_c": read_number(self.temp_file, 1000.0) if self.temp_file else None,
            "power_w": self._power(),
            "clock_mhz": sum(known) / len(known) if known else None,
            "cpu_mhz": mhz,
            "usage_pct": sum(usage) / len(usage) if usage else None,
            "cpu_usage_pct": usage,
            **self._limits(),
            "throttle_core": sum(counts) if counts else None,
            "throttle_package": read_number(package) if package else None,
        }


def run(
    machine: str,
    logs: Path,
    interval: float = INTERVAL,
    samples: int | None = None,
    sys_root: Path = SYS,
) -> Path:
    """Logs a sample every `interval` seconds, on a fixed beat, until Ctrl+C (or `samples`
    samples). Gives the log's path."""
    sensors = Sensors(sys_root)
    with RunLog(log_path(SENSORS_DIR, f"{stamp()}_{machine}", logs)) as log:
        about = sensors.describe()
        log.write("sensor_log_start", machine=machine, interval=interval, build=build_info(),
                  **about)  # fmt: skip
        print(f"sensor_log: {machine} -> {log.path} every {interval:g} s (Ctrl+C stops it)")
        if sensors.temp_file is None:
            print("sensor_log: warning: no CPU package temperature found", file=sys.stderr)
        if not about["rapl_energy_readable"]:
            print(
                "sensor_log: warning: package power cannot be read; run once after boot:\n"
                f"    sudo chmod a+r {SYS / RAPL / 'energy_uj'}",
                file=sys.stderr,
            )
        start, taken = time.monotonic(), 0
        try:
            while samples is None or taken < samples:
                time.sleep(max(0.0, start + (taken + 1) * interval - time.monotonic()))
                log.write("sample", **sensors.sample())
                taken += 1
        except KeyboardInterrupt:
            pass
        log.write("sensor_log_end", samples=taken)
    return log.path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.sensor_log",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--machine", required=True, choices=MACHINES)
    parser.add_argument("--logs", type=Path, default=LOGS_DIR)
    parser.add_argument("--interval", type=float, default=INTERVAL, help="seconds per sample")
    parser.add_argument("--samples", type=int, help="stop after this many (default: Ctrl+C)")
    args = parser.parse_args(argv)
    run(args.machine, args.logs, args.interval, args.samples)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
