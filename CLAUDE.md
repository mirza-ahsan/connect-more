# CLAUDE.md — connect-more

Context for AI coding assistants working in this repository. Read this fully before acting.

## 0. Current status (read first)

All code steps of `docs/workplan.md` are done and pushed (1–24 and 33–36: solver, worker, coordinator with V0–V3, experiment scripts, analysis). **On 7 Nov 2026 the project moved to Linux, a desktop PC replaced Laptop 2, and all experiments were run** (36 valid runs; `docs/handoff.md` sections 00 and 0, `docs/decision-log.md` sections A and C). Only the paper is left. **`docs/handoff.md` says what is done, the decisions to keep, what is left (setup of both machines, pilot, freeze, experiments, paper) and the pitfalls met so far;** `docs/decision-log.md` holds the pilot choices to fill in and the decisions made without the group; `docs/session-checklist.md` is the procedure for every experiment session. Phase 3 is due **Sun 8 Nov 2026**. After any code change: run the `simplify` skill, then an independent review agent, fix their findings, rerun every check.

## 1. What this project is

A university research project (Parallel & Distributed Computing, Fall 2026, domain: Multi-Core / Many-Core Architectures) by a group of 2. We build a **distributed Connect Four solver** that splits an uneven game-tree search across **two unequal Linux machines** (a laptop and a desktop PC), and study how to share the work fairly when:

- task sizes are unpredictable (some positions solve in milliseconds, others take hours),
- the machines differ (12th-gen hybrid i7 laptop vs Ryzen 7 7700 desktop),
- speed changes during a run (turbo, thermal throttling, fast/slow cores),
- splitting the work causes repeated computation.

The output is a conference paper (IEEE/ACM). Correct, reproducible measurements matter more than clever code.

Both group members commit to this repository, each from their own clone. Every rule in this file applies to both members in the same way, whether a change is made by hand or with an AI assistant.

## 2. Hard rules (always follow)

### Code is written by the group
- The group members write the project code themselves. **Do not write or edit code** (C++, Python, CMake, scripts, tests) unless the user explicitly asks for it in their current message. Permission for one piece of code does not carry over to the next.
- Without such a request, the assistant's role is to explain, answer questions, review code and point out problems, and edit documentation or configuration files when asked.
- Explain in plain words, without code examples. Show code in an explanation only when the user directly asks for it.

### Git
1. **Never commit unless the user explicitly asks for a commit in their current message.** Finishing a task is not permission to commit.
2. **Keep commits small and focused.** A commit contains one file, or a few closely related files that belong to the same change (for example `.vscode/settings.json` + `.vscode/extensions.json`, or `pyproject.toml` + `uv.lock`). Never mix unrelated changes in one commit.
3. **Stage files by explicit path only:** `git add path/to/file`. Never use `git add -A`, `git add .`, `git add -u` or `git commit -a`.
4. **Never add AI attribution** to commits: no `Co-Authored-By` trailer, no "Generated with Claude Code" line, no `Claude-Session` trailer, no mention of Claude or AI in commit messages. The author is the user.
5. **Push only with `git push origin main`, and only when the user asks for a push in their current message.** Never force-push or delete remote branches. Also never: pull, fetch-and-rebase, rebase, merge, `reset --hard`, `commit --amend`, `checkout`/`switch` to another branch, create or delete branches or tags, `stash drop`, `clean -fd`, or edit `git config`.
6. Before committing, show the user `git status --short` and the staged diff summary (`git diff --cached --stat`).
7. Commit message format: `<area>: <imperative summary>` (max ~72 chars), optional blank line + short body. Areas: `solver`, `coordinator`, `worker`, `scripts`, `analysis`, `tests`, `build`, `env`, `docs`.
   Example: `solver: add transposition table with 2^24 entries`
8. Never commit generated or bulky files: `build/`, `dist/`, `.venv/`, `logs/` (sensor logs included), binaries, `compile_commands.json`.
9. The remote is `origin` = `git@github.com:mirza-ahsan/connect-more.git` (SSH). Pulling is done by the group members themselves, never by the assistant. `.claude/settings.json` allows exactly `git push origin main`, blocks force pushes, and asks for approval for any other push.
10. Each group member enables the commit hook once per clone by running `git config core.hooksPath .githooks` themselves (the assistant must not edit git config).

### Environment (locked; see `ENVIRONMENT.md`)
- Do **not** run `uv add`, `uv remove`, `uv lock --upgrade`, `uv pip install`, `uv self update`, `pacman`, `yay`/`paru`, `omarchy-update`, or anything that changes installed tools or `uv.lock`, unless the user asks.
- Do **not** change `CMakePresets.json` values used for experiments (`CM_ARCH`, `CM_STATIC`, `CM_DEFAULT_TT_MB`, `CM_EXPECTED_GCC_VERSION`, build type of the `release` preset) without asking.
- Do **not** edit `ENVIRONMENT.md` unless asked, or unless the user approved an environment change in this session (then update it, including the Change Log table).
- Do **not** touch `deliverables/` (submitted course documents).

### Experiments and data integrity
- Experiment builds use **only** the `release` preset, on both machines.
- Never fabricate, edit, round, or "clean up" raw results. Raw logs in `logs/` are immutable; derived data is produced by scripts in `analysis/`.
- Every program that produces results must print the values from `cm/build_info.hpp` (commit, dirty flag, compiler, flags) and the board variant into its log header.
- Never claim a speedup, result, or citation that was not actually measured or read. If unsure, say so.

### General
- Ask before deleting files, renaming folders, or making changes outside the task.
- Prefer small, reviewable changes. Explain what changed and why in plain English.

## 3. Where to look

| Path | What it is |
|---|---|
| `CLAUDE.md` | This file |
| `ENVIRONMENT.md` | Exact hardware, OS, toolchain and settings of both machines (source of truth) |
| `CMakeLists.txt` | Top-level C++ build: toolchain lock checks, shared flags (`cm::options`), generates `cm/build_info.hpp` |
| `solver/CMakeLists.txt` | Builds one library + executables per board size (`c4core_7x6`, `c4solve_7x6`, ...) and C++ tests (labels `unit`, `verify`). Finds sources by glob; empty folders are skipped |
| `CMakePresets.json` | Build presets: `release` (experiments), `small-boards` (4x4/6x4/6x6 checks), `debug`, `debug-ubsan`, `relwithdebinfo`; test presets `release-unit`, `release-verify`; workflows `release`, `small-boards` |
| `cmake/build_info.cmake` | Refreshes the git commit and dirty flag in `cm/build_info.hpp` before every build |
| `solver/include/c4/` | C++ public headers (board, solver, transposition table) |
| `solver/src/` | C++ core library sources |
| `solver/apps/` | One executable per `.cpp` (e.g. `c4solve.cpp`, `c4bench.cpp`) |
| `solver/tests/test_*.cpp` | C++ unit tests (return 0 = pass) |
| `solver/tests/data/<WxH>/*.txt` | Positions with known results for `--verify` tests, one folder per board size (Pascal Pons's test sets go in `7x6/`). Each file becomes a test running `c4solve_<WxH> --verify <file>` |
| `coordinator/` | Python coordinator (runs on Laptop 1): task list, hand-out, re-split, cancel, combine results |
| `worker/` | Python worker agent (runs on both machines): receives tasks, drives the C++ solver, reports back |
| `common/` | Shared Python code: JSON messages (`messages.py`) and run logs with two clocks (`runlog.py`) |
| `docs/protocol.md`, `docs/handoff.md` | Agreed message protocol; current state and remaining work |
| `scripts/` | Experiment runners, network check, log copying, sensor logger (`sensor_log.py`, in place of HWiNFO) |
| `analysis/` | Turns logs into tables and charts for the paper |
| `tests/` | Python tests (pytest) |
| `logs/` | Raw experiment output (git-ignored) |
| `docs/` | Paper drafts, methodology notes, figures |
| `deliverables/` | Submitted course documents — do not modify |
| `pyproject.toml`, `uv.lock`, `.python-version` | Locked Python environment (uv) |
| `.githooks/commit-msg` | Strips AI attribution trailers from commit messages |
| `.claude/settings.json` | Claude Code project settings (attribution off, git safety permissions) |
| `.zed/settings.json`, `.vscode/settings.json`, `.vscode/extensions.json` | Shared editor settings |
| `.clangd` | clangd settings for both editors (reads `build/release/compile_commands.json`) |
| `.clang-format` | C++ formatting rules (Google base, Allman braces, 4 spaces, 110 columns, C++20) |
| `.editorconfig` | Line endings (LF), charset, indentation and final newline for every file type |
| `.gitattributes` | Forces LF line endings in git for all text files (whatever `core.autocrlf` is), marks binaries |

Some folders may not exist yet; create them only when a task needs them.

## 4. Environment summary

| | Laptop 1 (`laptop1`, coordinator) | PC (`pc`) |
|---|---|---|
| Machine | Dell Precision 5570 | Desktop, MSI PRO B650-S WIFI |
| CPU | i7-12800H: 6 P-cores (HT) + 8 E-cores, 20 threads | Ryzen 7 7700 (Zen 4): 8 cores (SMT), 16 threads |
| RAM | 32 GB DDR5-4800 | DDR5, 15.2 GB visible |
| OS | Omarchy (Arch Linux), same configuration as the PC | Omarchy 4.5.0, kernel 7.2.15 |
| Power | `powerprofilesctl` performance, Dell "Ultra Performance" (BIOS) | `powerprofilesctl` performance |
| Repo path | see `ENVIRONMENT.md` | `/home/aztrek/projects/connect-more` |
| Cable IP | 192.168.2.1/24 (USB-C adapter) | 192.168.2.2/24 (`enp12s0`) |

Toolchain (identical on both, from pacman): **GCC 16.2.1** (POSIX threads), **CMake 4.4.4**, **Ninja**, GDB 18.1, **LLVM 23.1.3** (clangd + clang-format, editor tooling only), **uv 0.13.4**, **Python 3.12.13** (uv-managed). Messaging port: **TCP 5555** (ufw: allowed from the cable and Wi-Fi subnets only). `scripts/sensor_log.py` logs sensors every 1000 ms. No package updates between the code freeze and the last experiment.

**Shell note:** the shell is zsh. It does not split an unquoted variable into words; pass file lists with `xargs`. Paths use forward slashes.

## 5. Commands

### C++
```bash
cmake --workflow --preset release      # configure + build + test (use for experiments)
cmake --preset debug && cmake --build --preset debug && ctest --preset debug
ctest --preset release-unit            # fast unit tests only
ctest --preset release-verify          # solve known positions and compare
cmake --workflow --preset small-boards  # build and check 4x4, 6x4, 6x6 against Tromp's table
```
Binaries: `build/<preset>/bin/` (e.g. `build/release/bin/c4solve_7x6`). Board sizes come from `CM_BOARD_VARIANTS`. **The project uses 7x6 for all experiments**, plus 4x4, 6x4 and 6x6 for correctness checks against Tromp's table (see `solver/tests/data/README.md`). `CMakeLists.txt` defaults to `7x6`; the `small-boards` preset builds the three small sizes in `build/small-boards/` (`cmake --workflow --preset small-boards`). Do not change either without asking. For a one-off build of other sizes: `cmake --preset debug "-DCM_BOARD_VARIANTS=7x6;6x7;6x5;5x4;4x4"`. The first configure prints a summary; it must show `GNU 16.2.1` and `Thread model: posix`.

### Python (always through uv)
```bash
uv sync --locked                        # install exactly what uv.lock says
uv run python -m coordinator ...        # run modules
uv run pytest                           # tests
uv run ruff check .                     # lint
uv run ruff format .                    # format
uv run pyrefly check                    # type check
```
Never call the system `python` directly.

## 6. Architecture and design decisions

- **Coordinator** (Python, Laptop 1) keeps the task tree. Tasks are Connect Four positions encoded as move strings (columns 1..W, e.g. `"4453"`).
- **Workers** (Python, one per machine) connect to the coordinator over ZeroMQ (TCP 5555), ask for work when free, and run the C++ solver.
- **C++ solver** runs as a long-lived subprocess per worker, talking over stdin/stdout, one line per request/response. It uses all cores of its machine with threads that pull from one shared pile and share one transposition table. (pybind11 is not used.)
- **Solver algorithm:** bitboard negamax with alpha-beta, transposition table, move ordering and iterative deepening (following Pascal Pons's Connect Four solver tutorial; credit it in the paper).
- Initial split: positions after 2–3 moves, so both machines have enough tasks from the start.

### Experiment versions (add one improvement at a time)
| Version | Behaviour |
|---|---|
| V0 | Fixed even split decided upfront (baseline) |
| V1 | Ask-for-more: workers request tasks when free; threads inside each machine pull from a shared pile |
| V2 | V1 + time-limit re-split (a task running longer than a limit, e.g. 60 s, is broken into child positions and returned to the pile) + cancel tasks made unnecessary by a found winning move |
| V3 | V2 + keep the transposition table between tasks + send related positions to the same machine + share solved results via the coordinator |

Each version runs over **Wi-Fi and the direct cable**, 3 repeats, with the sensor logger running on both machines.

### What to measure
Total solve time; idle time per machine; speedup vs the i7 laptop alone and the Ryzen PC alone; repeated work (positions searched by two machines vs one); temperature, clock speed and package power over time; network round-trip time and throughput. Correctness is checked against Tromp's published board-size results and Pascal Pons's test sets.

## 7. Coding conventions

### C++ (C++20, GCC on Linux)
- Boards are `uint64_t`. Never use `long`/`unsigned long` for bitboards (its size differs between systems).
- Prefer `std::popcount` / `std::countr_zero` from `<bit>`; if using builtins, use the `ll` forms (`__builtin_popcountll`, `__builtin_ctzll`).
- Board size comes from `C4_WIDTH` / `C4_HEIGHT` macros (set per variant by CMake); never hard-code 7 or 6.
- Default TT size: `CM_DEFAULT_TT_MB`. Keep it identical on both machines.
- Include `"cm/build_info.hpp"` and print its fields at startup.
- Use `std::thread`, `std::atomic`, `std::chrono::steady_clock` for timing. Avoid OS-specific headers; standard C++ only.
- Fix all warnings. No undefined behaviour; test with the `debug-ubsan` preset.
- Formatting is defined by `.clang-format` (Google base, Allman braces, 4 spaces, 110 columns, `int* p`) and applied on save by clangd in both editors. Do not edit `.clang-format` without asking. To check a file by hand: `clang-format --dry-run --Werror <file>`.
- CMake files use 2-space indentation (see `.editorconfig`).

### Python (3.12)
- Entry scripts must use `if __name__ == "__main__":` (`multiprocessing` with spawn or forkserver re-imports the module).
- Start worker processes once and reuse them; anything sent between processes must be picklable.
- Cancel work with an explicit "cancel" message over the existing connection, not OS signals.
- Use `pathlib.Path`, type hints on all functions, and `time.perf_counter()` / `time.monotonic()` for timing.
- Messages between coordinator and workers are JSON with an explicit `"type"` field and a protocol version.
- Ruff (lint + format) and Pyrefly (types) must pass with no errors.

## 8. Editors

- **Zed** and **VS Code** are both used; shared settings live in `.zed/settings.json` and `.vscode/`.
- Python: **Ruff** (lint/format, organise imports on save) and **Pyrefly** (types). Config is in `pyproject.toml`; editors read it.
- C++: **clangd** (LLVM 23.1.3 from pacman) reads `build/release/compile_commands.json` (via `.clangd`), queries the system `g++` for its headers, and formats on save using `.clang-format`. Both editors are set to use the `clangd` on PATH, not one they download.
- Both editors read `.editorconfig` (VS Code needs the EditorConfig extension from `.vscode/extensions.json`). Run the `release` configure once so the database exists (Ninja writes it only once `solver/` contains at least one `.cpp` file).

## 9. Paper and documents

- Paper sections: Title, Abstract, Keywords, Introduction, Related Work, Methodology, Implementation, Results, Conclusion & Future Work, References (IEEE style).
- Plain, simple English; every figure and table must be our own. Turnitin must report under 10% similarity, so never paste text from sources.
- Deadlines: Phase 2 (Introduction + Methodology) 11 Oct 2026; Phase 3 (full paper + Turnitin report) 8 Nov 2026 (extended from 25 Oct).

## 10. When unsure

Ask a short question instead of guessing — especially before anything involving git history, the locked environment, experiment settings, or deleting files.
