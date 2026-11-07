# Environment

This file records the exact hardware, software and settings of both machines so experiments are reproducible and the paper can report the setup accurately. Update it whenever anything changes, and never change the environment during an experiment session.

**Since 7 Nov 2026 the experiments run on Linux, on Laptop 1 and a desktop PC.** Laptop 2 (HP EliteBook 840 G5, i5-8350U) is no longer available; the PC takes its place, and both machines run Omarchy with the same configuration. The Windows 11 setup used until then is in this file's git history. Values marked **(fill in)** must be read on that machine and written here before the pilot.

---

## 1. Hardware

| | Laptop 1 (`laptop1`) | PC (`pc`) |
|---|---|---|
| Manufacturer / model | Dell Precision 5570 | Desktop; MSI PRO B650-S WIFI motherboard (MS-7E26) |
| BIOS version | 1.42.0 | 1.N0 |
| CPU | Intel Core i7-12800H (12th gen, Alder Lake) | AMD Ryzen 7 7700 (Zen 4, Raphael) |
| Core layout | 14 cores: 6 Performance (with Hyper-Threading) + 8 Efficient | 8 cores, all the same (SMT on) |
| Logical threads | 20 | 16 |
| Base / top frequency | 2400 MHz base | 3800 MHz base, 5300 MHz boost (AMD's figures; Linux reports a top of 5393 MHz) |
| Architecture | x86_64 | x86_64 (also has AVX-512; not used, the build targets x86-64-v3) |
| RAM | 32 GB DDR5 (2 × 16 GB) at 4800 MT/s | 1 × 16 GB DDR5, A-DATA XPG AX5U6000C3016G in slot DIMMB2 (from SMBIOS), single channel, at 4800 MT/s (rated 6000 MT/s with EXPO, which is off). Linux sees 15.2 GB (part is kept for the integrated GPU). Laptop 1 has two channels, so the PC has about half its memory bandwidth |
| GPU | NVIDIA RTX A1000 4 GB (not used in this project) | AMD Radeon integrated graphics (not used in this project) |
| Ethernet | None built in; USB-C gigabit Ethernet adapter: ASIX AX88179B (USB ID `0b95:1790`, `cdc_ncm` driver), `enp0s20f0u1u1c2` | Built in: Realtek RTL8125 2.5 GbE (`r8169` driver), `enp12s0` |
| Wi-Fi | Intel Wi-Fi 6E AX211 160 MHz | MediaTek MT7921 (`mt7921e`), `wlp13s0` |
| CPU power limits | Under Windows: PL1 static 115 W, PL1 dynamic 40 W, PL2 115 W. Under Linux (from the `sensor_log_start` record, on the charger): `intel-rapl:0` PL1 76 W, PL2 115 W; `intel-rapl-mmio:0` PL1 45 W, PL2 115 W. The lower PL1, 45 W, is the one that holds | Not shown by Linux (AMD's RAPL interface has no limits). AMD's default for a 65 W part is 88 W package power (maker figure, not measured) |
| Cooling | Laptop (throttles under long loads) | Desktop tower cooler |

---

## 2. Operating System

| | Laptop 1 | PC |
|---|---|---|
| Distribution | Omarchy (Arch Linux based), same configuration as the PC | Omarchy 4.5.0 (Arch Linux based) |
| Kernel | 7.2.15-1-omarchy | 7.2.15-1-omarchy |
| CPU frequency driver | `intel_pstate` (active mode) | `amd-pstate-epp` |
| Governor / energy preference | `performance` / `performance`. Under `intel_pstate` the power profile sets only the energy preference, so the governor must be set after every boot (command in section 9) | `performance` / `performance` (already set) |
| Power profile (`powerprofilesctl get`) | `performance` (ACPI platform profile `performance`) | `performance` |

Both machines are rolling-release Arch systems: **do not update packages (`pacman -Syu`, `omarchy-update`) from the code freeze until the last experiment**, so the compiler and libraries stay as recorded here.

---

## 3. C++ Toolchain

All tools come from the Omarchy / Arch Linux packages (pacman). Both machines must show the same versions.

| Tool | Version | Package |
|---|---|---|
| GCC / G++ | 16.2.1 (`16.2.1+r23+gd564253eb6c8-1`), POSIX threads | `gcc` |
| GDB | 18.1 | `gdb` |
| CMake | 4.4.4 | `cmake` |
| Ninja | 1.13.2 | `ninja` |
| clang-format / clangd | 23.1.3 (editor tooling only; never used to compile) | `clang` |

- The `release` preset refuses any GCC other than 16.2.1 (`CM_EXPECTED_GCC_VERSION`).
- clangd provides code navigation and clang-format formats C++ on save (rules in `.clang-format`). Both machines must have the same version, because different clang-format versions can format the same code slightly differently.

### Build settings

| Setting | Value |
|---|---|
| C++ standard | C++20 |
| Compile flags (`release` preset) | `-std=c++20 -O3 -DNDEBUG -march=x86-64-v3 -mtune=generic` |
| Warning flags | `-Wall -Wextra -Wpedantic -Wshadow -Wcast-align -Wnon-virtual-dtor -Woverloaded-virtual -Wold-style-cast -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wimplicit-fallthrough -Wmisleading-indentation -Wduplicated-cond -Wduplicated-branches -Wlogical-op` |
| Link flags | `-static` (glibc and libstdc++ linked in) |
| CMake generator | Ninja |

- `-march=x86-64-v3` (AVX2 level) is supported by both CPUs, so both machines run identically built code. The Ryzen's AVX-512 is deliberately not used.
- `-static` produces executables that need no shared libraries at run time.
- Board representations use `uint64_t`. Use `std::popcount` / `std::countr_zero` from `<bit>` or the `ll` builtins, never `long` (it is 64-bit on Linux but 32-bit on Windows; the code must not depend on it).

---

## 4. Python Environment (uv)

| Item | Version |
|---|---|
| uv | 0.13.4 (pacman package `uv 0.13.4-1`), enforced via `[tool.uv] required-version` |
| Project Python | 3.12.13 (uv-managed, pinned exactly in `.python-version`) |
| System Python (not used by the project) | 3.14.8 |

The project uses uv's own Python, not the system Python. `.python-version` must contain the exact version (`3.12.13`), not just `3.12`, so both machines get the same patch release. `uv.lock` is unchanged from the Windows setup (the same package versions).

### Pinned packages

| Package | Version | Purpose |
|---|---|---|
| pyzmq | 26.4.0 | Messaging between machines |
| psutil | 7.0.0 | CPU usage, battery check, process control |
| py-cpuinfo | 9.0.0 | CPU model details |
| numpy | 2.2.6 | Numerical processing |
| pandas | 2.2.3 | Results processing |
| matplotlib | 3.10.3 | Charts |
| scipy | 1.15.3 | Statistics across repeated runs |
| pytest (dev) | 8.3.5 | Automated tests |
| ruff (dev) | 0.16.5 | Lint and format (config in `pyproject.toml`) |
| pyrefly (dev) | 1.2.0 | Type checking (config in `pyproject.toml`) |

Exact resolved versions are locked in `uv.lock`. On a new machine, run `uv sync --locked`. Run everything through `uv run`.

---

## 5. Supporting Tools

| Tool | Version | Package | Purpose |
|---|---|---|---|
| Git | 2.56.0 | `git` | Version control |
| Sensor logger | this repository | `scripts/sensor_log.py` | Temperature, clock, power, CPU use and throttling, every 1000 ms (replaces HWiNFO) |
| lm_sensors | 3.6.2 | `lm_sensors` | Checking the sensors by hand (`sensors`) |
| iperf3 | 3.22 | `iperf3` | Network throughput tests |
| ufw | 0.36.2 | `ufw` | Firewall |
| NetworkManager | (Omarchy default) | `networkmanager` | Cable address (`nmcli`) |
| power-profiles-daemon | (Omarchy default) | `power-profiles-daemon` | Power profile (`powerprofilesctl`) |

### Where the sensor values come from

| Value | Laptop 1 (Intel) | PC (AMD) |
|---|---|---|
| CPU package temperature | coretemp `Package id 0` | k10temp `Tctl` (no offset on Ryzen 7000) |
| Package power | RAPL `intel-rapl:0` energy counter | Same interface (`intel-rapl:0`, package-0) |
| Clock | cpufreq `cpuinfo_avg_freq` per logical CPU | Same |
| Power limits | RAPL `constraint_0` (PL1) / `constraint_1` (PL2) of `intel-rapl:0` and of `intel-rapl-mmio:0` | None |
| Throttling | `thermal_throttle/*_throttle_count` (each physical core counted once) | None |

The RAPL energy counter is readable only by root. Once after every boot, on both machines: `sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj`. Without it the logs have no power values; the logger warns at start.

---

## 6. System Settings

| Setting | Laptop 1 | PC |
|---|---|---|
| Power profile | `powerprofilesctl set performance` | `powerprofilesctl set performance` |
| Manufacturer thermal profile | Dell Ultra Performance: under Linux this is `/sys/firmware/acpi/platform_profile` = `performance` (choices: cool, quiet, balanced, performance), set by `powerprofilesctl set performance`. Only if it is not `performance`, check Dell Thermal Management in the BIOS | None |
| Sleep / idle lock during runs | Off for the session: `omarchy-toggle-idle stay-awake` before it, `omarchy-toggle-idle allow-idle` after it | Same |
| Time sync | systemd-timesyncd (`timedatectl`: "System clock synchronized: yes") | Same (time zone Asia/Karachi) |
| Repository path | `/home/aztrek/projects/connect-more` (a fresh clone on the Linux disk) | `/home/aztrek/projects/connect-more` |
| Sensor logging interval | 1000 ms (`scripts/sensor_log.py`) | Same |

### Firewall (ufw, both machines)

Omarchy enables ufw and refuses incoming connections by default. Allow the two ports from the cable and Wi-Fi subnets only:

| Rule | Command |
|---|---|
| Messaging and log copying, TCP 5555 | `sudo ufw allow from 192.168.2.0/24 to any port 5555 proto tcp comment connect-more` and the same for the Wi-Fi subnet (`192.168.18.0/24` at home). Both machines: done 7 Nov |
| iperf3, TCP 5201 | `sudo ufw allow from 192.168.2.0/24 to any port 5201 proto tcp comment connect-more`, and the same for the Wi-Fi subnet. Both machines: done 7 Nov |
| Ping | allowed by ufw's default rules |

---

## 7. Network

| | Laptop 1 | PC |
|---|---|---|
| Wi-Fi subnet (home) | 192.168.18.0/24 | Same network (`wlp13s0`) |
| Direct cable address | 192.168.2.1/24 (USB-C adapter) | 192.168.2.2/24 (`enp12s0`) |

- The PC's only Ethernet port normally goes to the router. **For cable runs**, plug it into Laptop 1's USB-C adapter instead. **For Wi-Fi runs**, unplug the PC's cable and connect its Wi-Fi, so both machines really talk over Wi-Fi.
- Cable addresses with NetworkManager, once per machine: `nmcli connection add type ethernet ifname <interface> con-name c4-cable ipv4.method manual ipv4.addresses 192.168.2.x/24 ipv6.method disabled`, then `nmcli connection up c4-cable` when the cable is plugged in.
- The cable subnet (192.168.2.x) does not clash with the home Wi-Fi (192.168.18.x). On another Wi-Fi network, check for a clash with `ip -br addr` and switch the cable to 10.42.0.x if needed.
- Messaging port: TCP 5555.
- Laptop 1's cable link is the ASIX AX88179B USB adapter (`cdc_ncm` driver). `ping -c 20 -n 192.168.2.2` on 7 Nov: min/avg/max 1.358/1.480/1.770 ms. This is higher than the usual 0.2–0.5 ms on gigabit Ethernet, likely because `cdc_ncm` batches packets; the network check (`scripts/network_check.py`) measures it properly.

---

## 8. Smoke Test Results

| Test | Laptop 1 (Linux) | PC |
|---|---|---|
| `release` build (GCC 16.2.1, static, Ninja), no warnings | ✅ | ✅ |
| C++ unit tests (5) and Pons verify files (6) | ✅ 11/11 (`cmake --workflow --preset release`) | ✅ 11/11 (`cmake --workflow --preset release`) |
| Small boards against Tromp (4x4, 6x4, 6x6) | ✅ 18/18 | ✅ 18/18 |
| pytest | ✅ 229 passed | ✅ 229 passed |
| Sensor logger reads temperature, clocks and power | ✅ (about 5–10 W package power and 45–46 °C at idle) | ✅ (about 20 W package power at idle) |
| Cross-machine ZeroMQ over cable | — | — |
| Cross-machine ZeroMQ over Wi-Fi | — | — |
| iperf3 over cable | — | — |
| iperf3 over Wi-Fi | — | — |

---

## 9. Experiment Session Checklist

The full procedure is `docs/session-checklist.md`.

After every boot, on both machines:

1. `sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj` (RAPL counter readable).
2. `powerprofilesctl set performance`.
3. Laptop 1 only: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` (the PC's governor is already `performance`).
4. `nmcli connection up c4-cable`.
5. `omarchy-toggle-idle stay-awake` (and `omarchy-toggle-idle allow-idle` after the session).

Before every session, on both machines:

1. Laptop 1 on its charger.
2. No package updates since the freeze.
3. Clocks synchronised (`timedatectl`).
4. All other apps closed.
5. Power profile, governor and Laptop 1's Dell profile as in sections 2 and 6.
6. Note the room temperature.

---

## 10. Verification Commands

Run on each machine; outputs must match the tables above.

```sh
uname -r
gcc --version
cmake --version
ninja --version
gdb --version
clang-format --version
clangd --version
uv --version
uv run python --version
uv pip list
git --version
iperf3 --version
powerprofilesctl get
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
```

---

## 11. Change Log

| Date | Machine | Change |
|---|---|---|
| 2026-10 | Laptop 1 | Initial environment setup and smoke tests |
| 2026-10 | Laptop 1 | Added hardware details, Dell thermal profile; pinned project Python to exact 3.12.13 |
| 2026-10 | Laptop 1 | HWiNFO logging set to 1000 ms and verified; Ethernet adapter obtained; power limits recorded |
| 2026-10-03 | Both (via `uv.lock`) | Added dev dependencies ruff 0.16.5 and pyrefly 1.2.0; added Ruff/Pyrefly config to `pyproject.toml` |
| 2026-10-03 | Both | Build settings table corrected to match `CMakeLists.txt` (added `-mtune=generic` and the full warning list); recorded Laptop 2 OS as Windows 11 |
| 2026-10-03 | Laptop 1 | Recorded LLVM 22.1.8 (clangd, clang-format) and pinned it in winget; added `.clang-format` and `.editorconfig`; C++ now formats on save in Zed and VS Code |
| 2026-10-04 | Laptop 1 | Corrected Coreinfo version to 4.02 (file version of the installed copy); recorded PowerShell 7.6.6.0 and VS Code 1.140.0 |
| 2026-10-05 | Laptop 2 | Initial setup completed (admin script run clean; user script run clean). Hardware and toolchain recorded. Power-mode, manufacturer performance profile, and cable network steps still pending manual completion. |
| 2026-10-11 | Laptop 2 | Confirmed power mode "Best performance"; no manufacturer performance profile is available. Repo is at `D:\University\connect-more`, tools in `D:\Tools` |
| 2026-11-06 | Laptop 2 | Repo is now at `D:\Projects\connect-more` (tools still in `D:\Tools`); the Defender exclusion still names the old path |
| 2026-11-07 | Both | **Moved to Linux.** Laptop 2 is no longer available; a desktop PC (Ryzen 7 7700) replaces it as `pc`. Both machines run Omarchy. Toolchain now from pacman: GCC 16.2.1, CMake 4.4.4, LLVM 23.1.3, uv 0.13.4 (`CM_EXPECTED_GCC_VERSION` and `required-version` updated; `uv.lock` unchanged). HWiNFO replaced by `scripts/sensor_log.py`; firewall by ufw; cable addresses by NetworkManager |
| 2026-11-07 | PC | Installed Ninja 1.13.2 and iperf3 3.22; ufw rules for TCP 5555 and 5201 from 192.168.2.0/24 and 192.168.18.0/24; all smoke tests pass |
| 2026-11-07 | Laptop 1 | Linux setup: installed Ninja 1.13.2 and iperf3 3.22; ufw rules for TCP 5555 and 5201 from 192.168.2.0/24 and 192.168.18.0/24; `c4-cable` (192.168.2.1/24 on `enp0s20f0u1u1c2`, no autoconnect), PC answers ping; recorded kernel, `intel_pstate` settings, power limits (PL1 45 W holds) and the ASIX AX88179B adapter; all smoke tests pass |
| 2026-11-07 | Both | `performance` governor on both machines (Laptop 1: set after every boot). Recorded the PC's memory (1 × 16 GB, single channel, 4800 MT/s), the stay-awake steps, Dell's thermal profile under Linux, Laptop 1's cable ping; one per-boot list in section 9 |
