# Environment

This file records the exact hardware, software and settings of both laptops so experiments are reproducible and the paper can report the setup accurately. Update it whenever anything changes, and never change the environment during an experiment session.

Fields marked `TBD` are still to be filled in.

---

## 1. Hardware

| | Laptop 1 | Laptop 2 |
|---|---|---|
| Manufacturer / model | Dell Precision 5570 | TBD |
| BIOS version | 1.42.0 | TBD |
| CPU | Intel Core i7-12800H (12th gen, Alder Lake) | Intel Core i5 (8th gen) — exact model TBD |
| Core layout | 14 cores: 6 Performance (with Hyper-Threading) + 8 Efficient | TBD |
| Logical threads | 20 | TBD |
| Base frequency | 2400 MHz | TBD |
| Architecture | x86_64 | x86_64 |
| RAM | 32 GB DDR5 (2 × 16 GB) at 4800 MT/s | 16 GB DDR4 — speed TBD |
| GPU | NVIDIA RTX A1000 4 GB (not used in this project) | Intel UHD 630 (not used in this project) |
| Ethernet | None built in; USB-C gigabit Ethernet adapter (model TBD) | TBD |
| Wi-Fi | Intel Wi-Fi 6E AX211 160 MHz | TBD |
| CPU power limits (HWiNFO, idle) | PL1 static 115 W, **PL1 dynamic 40 W**, PL2 115 W | TBD |

---

## 2. Operating System

| | Laptop 1 | Laptop 2 |
|---|---|---|
| OS | Windows 11 Pro | TBD |
| Version | 26H2 | 25H2 (update to match if available) |
| OS build | 26300.9457 | TBD |

Note: Python's `platform` module reports "Windows 10" on Windows 11. This is a known naming quirk; the OS is Windows 11. Versions 24H2, 25H2 and 26H2 share the same servicing branch.

---

## 3. C++ Toolchain

| Tool | Version | Source | Laptop 1 path | Laptop 2 path |
|---|---|---|---|---|
| GCC / G++ | 16.1.0 (MinGW-W64 x86_64-ucrt-posix-seh, Brecht Sanders, r4) | WinLibs via winget (`BrechtSanders.WinLibs.POSIX.UCRT`) | `C:\Users\ahsan\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin` | `C:\Tools\mingw64\bin` (copied from Laptop 1) |
| GDB | 17.2 | Bundled with WinLibs | Same `mingw64\bin` | Same `mingw64\bin` |
| Ninja | 1.13.2 | Bundled with WinLibs | Same `mingw64\bin` | Same `mingw64\bin` |
| CMake | 4.4.3 | Kitware via winget (`Kitware.CMake`) | `C:\Program Files\CMake\bin` | `C:\Program Files\CMake\bin` |

- WinLibs also bundles its own `cmake.exe`, but it is **not used**. Kitware CMake is in the system PATH, which Windows searches before the user PATH, so Kitware's copy takes priority. Confirm with `where.exe cmake` (Kitware's path must be listed first).
- Laptop 2 receives a byte-identical compiler by copying the whole `mingw64` folder from Laptop 1.

### Build settings

| Setting | Value |
|---|---|
| C++ standard | C++20 |
| Compile flags | `-O3 -march=x86-64-v3 -Wall -Wextra` |
| Link flags | `-static` |
| CMake generator | Ninja |

- `-march=x86-64-v3` (AVX2 level) is supported by both CPUs, so both laptops run identically built code.
- `-static` produces executables with no MinGW DLL dependencies; only Windows system DLLs are loaded.
- Board representations use `uint64_t` (on Windows, `long` is 32-bit). Use the `ll` builtins (`__builtin_popcountll`, `__builtin_ctzll`) or `std::popcount` / `std::countr_zero` from `<bit>`.

---

## 4. Python Environment (uv)

| Item | Version |
|---|---|
| uv | 0.12.8 (`x86_64-pc-windows-msvc`), enforced via `[tool.uv] required-version` |
| Project Python | 3.12.13 (uv-managed, pinned exactly in `.python-version`) |
| System Python (Laptop 1, not used by the project) | 3.12.10 |

The project uses uv's own Python, not the system Python. `.python-version` must contain the exact version (`3.12.13`), not just `3.12`, so both laptops get the same patch release.

### Pinned packages

| Package | Version | Purpose |
|---|---|---|
| pyzmq | 26.4.0 | Messaging between laptops |
| psutil | 7.0.0 | CPU usage, frequency, process control |
| py-cpuinfo | 9.0.0 | CPU model details |
| numpy | 2.2.6 | Numerical processing |
| pandas | 2.2.3 | Results processing |
| matplotlib | 3.10.3 | Charts |
| scipy | 1.15.3 | Statistics across repeated runs |
| pytest (dev) | 8.3.5 | Automated tests |

Exact resolved versions are locked in `uv.lock`. On a new machine, run `uv sync`. Run everything through `uv run`.

---

## 5. Supporting Tools

| Tool | Laptop 1 version | Source | Purpose |
|---|---|---|---|
| Git | 2.55.0.windows.3 | winget `Git.Git` | Version control |
| HWiNFO64 | 8.54 | winget `REALiX.HWiNFO` | Temperature, per-core clock and power logging |
| iperf3 | 3.21 | winget `ar51an.iPerf3` | Network throughput tests |
| Process Explorer | 17.14 | winget `Microsoft.Sysinternals.ProcessExplorer` | Per-core activity |
| Coreinfo | 3.6 | Sysinternals download to `C:\Tools\Sysinternals` | Identifying core types |
| OpenSSH Server | Windows capability `OpenSSH.Server~~~~0.0.1.0` | Windows optional feature | Remote control between laptops |

### Pinned in winget (protected from `winget upgrade --all`)

- `BrechtSanders.WinLibs.POSIX.UCRT`
- `astral-sh.uv`
- `Kitware.CMake`

---

## 6. System Settings

| Setting | Laptop 1 | Laptop 2 |
|---|---|---|
| Power plan | High Performance (`8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`) | TBD |
| Power mode (Settings) | Best performance | TBD |
| Manufacturer performance profile | Dell Thermal Management: Ultra Performance (`UltraPerformance`, set in BIOS / Dell Command \| Configure) | TBD |
| Sleep on AC | Disabled | TBD |
| Defender exclusion | `C:\University\connect-more` | TBD |
| sshd service | Automatic, running | TBD |
| HWiNFO logging interval | 1000 ms (verified: rows ~1.0 s apart) | Same (copy `HWiNFO64.INI`) |

### Firewall rules (Private profile only)

| Rule | Port / protocol | Laptop 1 | Laptop 2 |
|---|---|---|---|
| ConnectFour Distributed Solver (TCP-5555) | TCP 5555 | ✅ | TBD |
| iperf3 (TCP-5201) | TCP 5201 | ✅ | TBD |
| Allow Ping (Private) | ICMPv4 echo request | ✅ | TBD |

---

### HWiNFO log format notes

- Encoding: Latin-1 (for the `°C` symbol). Read with `encoding="latin-1"`.
- Date format: `D.M.YYYY` (e.g. `3.10.2026` = 3 October 2026); time includes milliseconds.
- The last two rows are a repeated header and a sensor-group row, so drop them when parsing.
- Laptop 1 logs about 530 columns (P-core/E-core names). Laptop 2's column names will differ (no P/E cores), so analysis scripts should select columns by pattern, not by position.

Key columns for analysis: `Core Clocks (avg)`, `Average Effective Clock`, per-core `P-core n` / `E-core n` clocks and usage, `CPU Package [°C]`, `Core Max [°C]`, `CPU Package Power [W]`, `PL1 Power Limit (Dynamic) [W]`, `Core Thermal Throttling (avg)`, `Package/Ring Thermal Throttling`, `Package/Ring Power Limit Exceeded`, `IA: PROCHOT`, `IA: Package-Level RAPL/PBM PL1`, `IA: Package-Level RAPL/PBM PL2 PL3`.

---

## 7. Network

| | Laptop 1 | Laptop 2 |
|---|---|---|
| Wi-Fi subnet (home) | 192.168.18.0/24 | Same network |
| Direct cable address | 192.168.2.1/24 | 192.168.2.2/24 |
| Cable connection profile | Private (to be set when connected) | Private |

- The cable subnet (192.168.2.x) does not clash with the home Wi-Fi (192.168.18.x). If experiments run on another Wi-Fi network, check for a clash with `ipconfig` and switch the cable to 10.42.0.x if needed.
- Messaging port: TCP 5555.

---

## 8. Smoke Test Results

| Test | Laptop 1 | Laptop 2 |
|---|---|---|
| C++20 build (`uint64_t`, builtins, `<bit>`, 4 × `std::thread`), fully static | ✅ Passed | TBD |
| ZeroMQ REQ/REP, 1,000 messages on localhost | ✅ 80.44 µs average round trip | TBD |
| pytest | ✅ Passed (2.07 s) | TBD |
| Cross-laptop ZeroMQ over cable | — | TBD |
| Cross-laptop ZeroMQ over Wi-Fi | — | TBD |
| iperf3 over cable | — | TBD |
| iperf3 over Wi-Fi | — | TBD |

---

## 9. Experiment Session Checklist

Before every experiment session, on both laptops:

1. Plug in the charger.
2. Pause Windows Update.
3. Run `w32tm /resync` while online, so both clocks match.
4. Close all other apps.
5. Confirm the power mode and manufacturer profile are unchanged.
6. Note the room temperature.

---

## 10. Verification Commands

Run on each laptop; outputs must match the tables above.

```powershell
winver
gcc --version
cmake --version
where.exe cmake
ninja --version
gdb --version
uv --version
uv run python --version
uv pip list
git --version
```

---

## 11. Change Log

| Date | Laptop | Change |
|---|---|---|
| 2026-10 | Laptop 1 | Initial environment setup and smoke tests |
| 2026-10 | Laptop 1 | Added hardware details, Dell thermal profile; pinned project Python to exact 3.12.13 |
| 2026-10 | Laptop 1 | HWiNFO logging set to 1000 ms and verified; Ethernet adapter obtained; power limits recorded |
