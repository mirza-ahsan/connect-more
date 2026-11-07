# Handoff: where the code stands, and what is left

Updated in session 3 on 7 Nov 2026 (the move to Linux, section 0). Sections 1–5 are from the end of build session 2 on 6 Nov 2026, at commit `84fd1ea`; read "Laptop 2" there as "the PC". Read this with `CLAUDE.md`, `docs/workplan.md` (the 40-step plan), `docs/protocol.md`, `docs/decision-log.md` and `docs/session-checklist.md`. **Phase 3 deadline: Sun 8 Nov 2026, 23:55.**

**All code steps are done** (1–24 and 33–36), and ported to Linux. What is left needs people and both machines: steps 25–32 (tests on both machines, pilot, freeze, experiments) and 37–39 (paper, Turnitin, submit).

---

## 0. Session 3 (7 Nov 2026): Linux, and a desktop PC in place of Laptop 2

Laptop 2 is no longer available. A desktop PC (AMD Ryzen 7 7700, 8 cores / 16 threads) takes its place, and both machines now run Omarchy Linux with the same configuration. Decisions: `docs/decision-log.md`, section C. Machine details: `ENVIRONMENT.md`.

What changed in the code (not yet committed):
- **Names:** `laptop2` is now `pc` everywhere (worker name, `logs/pc/`, settings, tests, docs); "Laptop 2" in prose is "the PC". The `alone` role takes `--machine` (was `--laptop`); the machine names and their log folders are in `common/machines.py` (`MACHINES`, `COORDINATOR`, `PEER`, `PEER_DIR`, `SENSORS_DIR`).
- **Build:** GCC 16.2.1 is the locked compiler (`CMakePresets.json`, `CMakeLists.txt`); the Windows parts of `CMakeLists.txt` and `c4solve.cpp` are gone; binaries have no `.exe`.
- **Python:** uv 0.13.4 is required (`pyproject.toml`; `uv.lock` unchanged). The worker's and the tests' solver paths have no `.exe`.
- **Sensors:** HWiNFO is replaced by `scripts/sensor_log.py` (writes `logs/sensors/*.jsonl`) and `analysis/load_sensors.py`; `analysis/load_hwinfo.py` and its test are deleted. The cool-down reads the newest sensor log (`sensor_max_age` in `scripts/experiment.toml`); the figures add a package-power panel.
- **Network check:** Linux `ping`.
- **Editors and settings:** VS Code points at `.venv/bin/python`; `.claude/settings.json` blocks pacman/yay/paru and `uv self update` instead of winget.

Checked on the PC (setup done, firewall rules in): `cmake --workflow --preset release` (11/11) and `small-boards` (18/18) with Ninja, no warnings; 229 Python tests, ruff and pyrefly; a real sensor log with temperature, clocks and power. Not checked yet: anything on Laptop 1 under Linux, and the two machines together.

**Next, on both machines:**
1. Install the missing tools: `sudo pacman -S ninja iperf3`, then record their versions in `ENVIRONMENT.md`.
2. `git config core.hooksPath .githooks` (once per clone).
3. `uv sync --locked`; `cmake --workflow --preset release`; `cmake --workflow --preset small-boards`; `uv run pytest`. All must pass.
4. Firewall, cable address, power profile and the RAPL `chmod`: `ENVIRONMENT.md` sections 5–7.
5. Fill in the **(fill in)** values of `ENVIRONMENT.md` on Laptop 1 (kernel, power limits from its first sensor log, repository path).
6. Then section 3 below from step 2 (pilot), with `docs/session-checklist.md`.

---

## 1. Done

| Step | What | Where |
|---|---|---|
| 6–12 | C++ solver: bitboard board, weak negamax (Pons parts 4–12), thread-safe transposition table, task pool (stop/cancel/progress), `c4solve` with `--verify`, `--single` and service mode; small boards checked against Tromp | `solver/` |
| 13 | JSON messages, run logs with two clocks, never overwritten | `common/messages.py`, `common/runlog.py` |
| 14 | Worker: drives one `c4solve`, credits, clock checks first; gives up after 90 s without any coordinator message (`--silence-limit`) | `worker/` |
| 15 | Coordinator board and task tree: expand, copies, combine answers, split | `coordinator/board.py`, `tree.py` |
| 16–18 | Coordinator with V1, V0 (calibration + fixed split), V2 (split long tasks, cancel unneeded); clock checks every 10 s in every phase; `allowed_workers` refuses any other worker at `hello` | `coordinator/` |
| 19 | **V3**: the tree passes every decision to the copies of that position; a task whose position is being solved is held back; a running task is kept while a copy still needs it; sibling groups per laptop, split children to the laptop that held the task, others take from the back. Counts `answered_from_memory`, `merged_copies` (tasks never sent) | `tree.py`, `server.py`, `strategies.py` (`SharingPile`) |
| 20 | **One-laptop mode** (E1 = V3 with one worker) and the unsplit count (`c4solve --single`, raw output kept) | `scripts/unsplit_count.py` |
| 21 | Network check: ping (any Windows language), ZeroMQ round trips and transfer against Laptop 2's echo, iperf3 if installed | `scripts/network_check.py` |
| 22 | Experiment runner: roles `coordinator` (Laptop 1, E2/E3), `alone` (E1, either laptop), `worker` (Laptop 2 loop); checks build/charger/HWiNFO, cool-down from the live HWiNFO CSV, marks invalid runs; settings in `scripts/experiment.toml` | `scripts/run_experiment.py` |
| 23 | Log copying: Laptop 2 serves its `logs/` read-only over ZeroMQ (with the network echo and `iperf3 -s`); Laptop 1 pulls into `logs/laptop2/` with SHA-256 checks; `backup` to a second place | `scripts/collect_logs.py` |
| 24 | Session checklist | `docs/session-checklist.md` |
| 33–36 | Analysis: HWiNFO reader, run loader (all clocks on the coordinator's, from t0), metrics of Table V, best fixed split (V0/V1), tables (mean/min/max per setting), figures (solve time, thread timelines, speed/temperature/clock panels) | `analysis/` (outputs to `analysis/out/`, git-ignored) |

**Checks at `84fd1ea`:** 223 Python tests pass (`uv run pytest`), ruff and pyrefly clean, no CRLF. C++ unchanged since `865ca15`; on Laptop 1 the `release` workflow passed 11/11 this session (5 unit + 6 Pons files).

Every step had the `simplify` skill (four reviewers) and an independent bug review; their findings and what was fixed are summarised in section 5.

---

## 2. Decisions to keep

Agreed with the user (session 1):
- **Protocol** (`docs/protocol.md`, version 1): additive credit; `hello`'s clocks read at the solver's `ready`; `moves` is `""` for the empty board; a stopped or cancelled task may still answer `result`; exactly one final answer per task.
- **Task ids:** `p<moves>` for tree tasks, `c<worker>.<n>` for calibration tasks.
- **Tree:** every move order is its own task; mirror images differ; positions decided by the rules are never sent.
- **Counts:** calibration is never part of solve time, tasks sent or positions; solve time = first run task sent -> result known.
- **V0:** time-based speed; both laptops calibrate at once; the transposition table is not cleared after calibration.
- **The transposition table is kept for the whole run in every strategy.**
- **Board sizes:** 7x6 for the experiments; 4x4, 6x4, 6x6 for correctness only.

Made in session 2, without the user, as the safest reasonable choice: all are listed with reasons in `docs/decision-log.md`, section B (V3 needs no protocol change; what "related" means; E1 = V3 with one worker; log copy method; Wi-Fi addresses only on the command line; worker names and pilot names; when a run is invalid; cool-down needs a fresh reading; the 90 s worker silence limit; E3 only V1/V3).

---

## 3. What is left (people and both laptops)

1. **Machine setup (step 25):** see section 0, "Next, on both machines"; then start the sensor log (`uv run python -m scripts.sensor_log --machine <name>`).
2. **Both laptops together:** one pilot run over the cable and one over Wi-Fi (see the pilot commands in the checklist; use `--pilot`).
3. **Pilot (step 26):** choose the start position (Laptop 1 alone a few minutes), the depth, `split_after`, the V0 calibration set, and the cool-down rule; write them in `scripts/experiment.toml` and `docs/decision-log.md`. A local trial in session 2 showed that a very short `split_after` (3 s) on the empty board makes V3 lose most of its work in stopped tasks (about 50 times the positions of the unsplit search): choose it with care. That trial ran next to the test suite and is not a result.
4. **Freeze (step 27):** commit, write the freeze commit in the decision log, then rebuild `release` on both laptops **after** that commit (the runner refuses a solver built from another commit or with uncommitted changes).
5. **Experiments (steps 28–32):** follow `docs/session-checklist.md`.
6. **Analysis:** `uv run python -m analysis.tables` and `uv run python -m analysis.figures` (add `--pilot` to include pilot runs); `uv run python -m analysis.best_fixed_split logs/<V0 run>`.
7. **Paper (37–39).** Points to state: the TT is kept after calibration; E1 references are V3 runs, which lose stopped work (`extra_vs_unsplit` is the cleaner extra-work measure); the ZeroMQ round trip is a lower bound for a message's cost; tail idle is the methodology's definition (`thread_tail_idle` is also reported); calibration error compares positions per second.

---

## 4. How the work was done (keep doing it)

After every step: the `simplify` skill (reuse, simplification, efficiency, altitude), then an independent read-only bug review, fix, rerun every check, commit, push.

**Pitfalls:**
- **Line endings:** still check for `\r` before committing (files written on Windows earlier, or by a Windows editor).
- **Arch is rolling:** a `pacman -Syu` changes GCC and every check against `ENVIRONMENT.md`; do not update between the freeze and the last run.
- **zsh does not split words** in an unquoted `$files`; pass file lists with `xargs`.
- `python -I` ignores `PYTHONPATH`; scratch scripts need `PYTHONPATH=.`.
- Long inline heredocs and backslashes in heredocs get mangled in Git Bash: write edit scripts to a scratch file, or use the Edit tool.
- Test helper threads must be daemon threads; close fake ZeroMQ sockets with `context.destroy(linger=0)`.
- Timing tests must check behaviour, not exact counts; they fail when the CPU is busy (do not run long solver jobs next to `pytest`).
- Default arguments are bound at import: pass module constants explicitly where tests monkeypatch them.
- The release `c4solve` prints `dirty true` until changes are committed and rebuilt.
- Background shell jobs can outlive their task; stop the process tree of the shell itself.

---

## 5. What the reviews found and fixed in session 2

- **V3:** a running task was cancelled although a held copy still needed its answer (the position was then solved twice); two counts included tasks that had also been sent; late answers were not worked up for copies.
- **Step 20:** the dirty flag missed new files; the logged solver header described the probe, not the run; failed unsplit runs were not recorded.
- **Network / log copying:** a path with backslashes could read any file on Laptop 2; growing HWiNFO files failed their checksum; a rewritten file could replace the only copy; `.partial` files were left behind; ping parsing worked only on English Windows; the echo server would have clashed with the log server on Laptop 2's port.
- **Runner:** an E1 run on Laptop 1 could be served by Laptop 2's waiting worker; a pilot-mode Laptop 2 could take part in a measured run; stale temperatures were accepted; Laptop 2's loop stopped for good on one failed check; a Wi-Fi address in the settings file would have blocked every measured run; a dead coordinator left Laptop 2 solving for ever.
- **Analysis:** E1 runs of both laptops were averaged together; run speeds included idle time; calibration compared tasks/s with positions/s; "copied" work counted copies of stopped tasks; tasks still running at T were not counted as busy.
