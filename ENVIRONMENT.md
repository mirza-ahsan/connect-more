# Environment

This file records the exact hardware, software and settings of both laptops so experiments are reproducible and the paper can report the setup accurately. Update it whenever anything changes, and never change the environment during an experiment session.

---

## 1. Hardware

| | Laptop 1 | Laptop 2 |
|---|---|---|
| Manufacturer / model | Dell Precision 5570 | HP EliteBook 840 G5 |
| BIOS version | 1.42.0 | Q78 Ver. 01.31.00 |
| CPU | Intel Core i7-12800H (12th gen, Alder Lake) | Intel Core i5-8350U (8th gen, Kaby Lake R) |
| Core layout | 14 cores: 6 Performance (with Hyper-Threading) + 8 Efficient | 4 cores, no P/E split (Hyper-Threading enabled) |
| Logical threads | 20 | 8 |
| Base frequency | 2400 MHz | 1896 MHz |
| Architecture | x86_64 | x86_64 |
| RAM | 32 GB DDR5 (2 × 16 GB) at 4800 MT/s | 16 GB DDR4 at 2400 MT/s |
| GPU | NVIDIA RTX A1000 4 GB (not used in this project) | Intel UHD Graphics 620 (not used in this project) |
| Ethernet | None built in; USB-C gigabit Ethernet adapter (model TBD) | Built-in: Intel Ethernet Connection (4) I219-LM (no USB adapter needed) |
| Wi-Fi | Intel Wi-Fi 6E AX211 160 MHz | Intel Dual Band Wireless-AC 8265 |
| CPU power limits (HWiNFO, idle) | PL1 static 115 W, **PL1 dynamic 40 W**, PL2 115 W | PL1 static 18 W, PL2 44 W (dynamic same) |

---

## 2. Operating System

| | Laptop 1 | Laptop 2 |
|---|---|---|
| OS | Windows 11 Pro | Windows 11 Pro |
| Version | 26H2 | 25H2 |
| OS build | 26300.9457 | 26200.9457 |

Note: Python's `platform` module reports "Windows 10" on Windows 11. This is a known naming quirk; the OS is Windows 11. Versions 24H2, 25H2 and 26H2 share the same servicing branch.

---

## 3. C++ Toolchain

| Tool | Version | Source | Laptop 1 path | Laptop 2 path |
|---|---|---|---|---|
| GCC / G++ | 16.1.0 (MinGW-W64 x86_64-ucrt-posix-seh, Brecht Sanders, r4) | WinLibs via winget (`BrechtSanders.WinLibs.POSIX.UCRT`) | `C:\Users\ahsan\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin` | `D:\Tools\mingw64\bin` |
| GDB | 17.2 | Bundled with WinLibs | Same `mingw64\bin` | Same `mingw64\bin` |
| Ninja | 1.13.2 | Bundled with WinLibs | Same `mingw64\bin` | Same `mingw64\bin` |
| CMake | 4.4.3 | Kitware via winget (`Kitware.CMake`) | `C:\Program Files\CMake\bin` | `C:\Program Files\CMake\bin` |
| clang-format / clangd | 22.1.8 (editor tooling only; never used to compile) | LLVM via winget (`LLVM.LLVM`) | `C:\Program Files\LLVM\bin` | `C:\Program Files\LLVM\bin` |

- WinLibs also bundles its own `cmake.exe`, but it is **not used**. Kitware CMake is in the system PATH, which Windows searches before the user PATH, so Kitware's copy takes priority. Confirm with `where.exe cmake` (Kitware's path must be listed first).
- Laptop 2 receives a byte-identical compiler by copying the whole `mingw64` folder from Laptop 1.
- LLVM provides clangd (code navigation) and clang-format (C++ formatting on save, rules in `.clang-format`). Both laptops must have the same version, because different clang-format versions can format the same code slightly differently. Both editors are set to use the `clangd` on PATH, not a copy they download. On Laptop 2: `winget install --id LLVM.LLVM --exact --version 22.1.8`, then `winget pin add --id LLVM.LLVM --exact`.

### Build settings

| Setting | Value |
|---|---|
| C++ standard | C++20 |
| Compile flags (`release` preset) | `-std=c++20 -O3 -DNDEBUG -march=x86-64-v3 -mtune=generic` |
| Warning flags | `-Wall -Wextra -Wpedantic -Wshadow -Wcast-align -Wnon-virtual-dtor -Woverloaded-virtual -Wold-style-cast -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wimplicit-fallthrough -Wmisleading-indentation -Wduplicated-cond -Wduplicated-branches -Wlogical-op` |
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
| ruff (dev) | 0.16.5 | Lint and format (config in `pyproject.toml`) |
| pyrefly (dev) | 1.2.0 | Type checking (config in `pyproject.toml`) |

Exact resolved versions are locked in `uv.lock`. On a new machine, run `uv sync`. Run everything through `uv run`.

Laptop 1's system Python also has copies of ruff 0.16.5 and pyrefly 1.2.0. The project does not use them; `uv run` and the editors use the copies in `.venv`.

---

## 5. Supporting Tools

| Tool | Laptop 1 version | Source | Purpose |
|---|---|---|---|
| Git | 2.55.0.windows.3 | winget `Git.Git` | Version control |
| HWiNFO64 | 8.54 | winget `REALiX.HWiNFO` | Temperature, per-core clock and power logging |
| iperf3 | 3.21 | winget `ar51an.iPerf3` | Network throughput tests |
| Process Explorer | 17.14 | winget `Microsoft.Sysinternals.ProcessExplorer` | Per-core activity |
| Coreinfo | 4.02 | Sysinternals download to `C:\Tools\Sysinternals` | Identifying core types |
| PowerShell 7 | 7.6.6.0 | winget `Microsoft.PowerShell` | Default shell in Windows Terminal; setup scripts |
| Visual Studio Code | 1.140.0 (updates itself) | winget `Microsoft.VisualStudioCode` | Editor (Laptop 2 uses VS Code only) |
| OpenSSH Server | Windows capability `OpenSSH.Server~~~~0.0.1.0` | Windows optional feature | Remote control between laptops |

### Pinned in winget (protected from `winget upgrade --all`)

- `BrechtSanders.WinLibs.POSIX.UCRT`
- `astral-sh.uv`
- `Kitware.CMake`
- `LLVM.LLVM`

---

## 6. System Settings

| Setting | Laptop 1 | Laptop 2 |
|---|---|---|
| Power plan | High Performance (`8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`) | High Performance (`8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`) |
| Power mode (Settings) | Best performance | Best performance |
| Manufacturer performance profile | Dell Thermal Management: Ultra Performance (`UltraPerformance`, set in BIOS / Dell Command \| Configure) | TBD |
| Sleep on AC | Disabled | Disabled |
| Defender exclusion | `C:\University\connect-more` | `D:\University\connect-more` |
| sshd service | Automatic, running | Automatic, running |
| HWiNFO logging interval | 1000 ms (verified: rows ~1.0 s apart) | Same (copy `HWiNFO64.INI`) |

### Firewall rules (Private profile only)

| Rule | Port / protocol | Laptop 1 | Laptop 2 |
|---|---|---|---|
| ConnectFour Distributed Solver (TCP-5555) | TCP 5555 | ✅ | ✅ |
| iperf3 (TCP-5201) | TCP 5201 | ✅ | ✅ |
| Allow Ping (Private) | ICMPv4 echo request | ✅ | ✅ |

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
| Cross-laptop ZeroMQ over cable | — | — |
| Cross-laptop ZeroMQ over Wi-Fi | — | — |
| iperf3 over cable | — | — |
| iperf3 over Wi-Fi | — | — |

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
clang-format --version
clangd --version
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
| 2026-10-03 | Both (via `uv.lock`) | Added dev dependencies ruff 0.16.5 and pyrefly 1.2.0; added Ruff/Pyrefly config to `pyproject.toml` |
| 2026-10-03 | Both | Build settings table corrected to match `CMakeLists.txt` (added `-mtune=generic` and the full warning list); recorded Laptop 2 OS as Windows 11 |
| 2026-10-03 | Laptop 1 | Recorded LLVM 22.1.8 (clangd, clang-format) and pinned it in winget; added `.clang-format` and `.editorconfig`; C++ now formats on save in Zed and VS Code |
| 2026-10-04 | Laptop 1 | Corrected Coreinfo version to 4.02 (file version of the installed copy); recorded PowerShell 7.6.6.0 and VS Code 1.140.0 |
| 2026-10-05 | Laptop 2 | Initial setup completed (admin script run clean; user script run clean). Hardware and toolchain recorded. Power-mode, manufacturer performance profile, and cable network steps still pending manual completion. |
