# Handoff: where the code stands, and what is left

Written at the end of the build session on 5 Nov 2026, at commit `7b59c63` (pushed to `origin/main`). Read this with `CLAUDE.md`, `docs/workplan.md` (the 40-step plan) and `docs/protocol.md`. **Phase 3 deadline: Sun 8 Nov 2026, 23:55.**

---

## 1. Done (steps 1–18)

| Step | What | Where |
|---|---|---|
| 6–12 | C++ solver: bitboard board, weak negamax (Pons parts 4–12), thread-safe transposition table (56 key bits per 64-bit slot, prime size), task pool (stop/cancel/progress), `c4solve` with `--verify`, `--single` and service mode, small boards 4x4/6x4/6x6 checked against Tromp | `solver/` |
| 13 | JSON messages (`common/messages.py`, incl. `Outcome`, `parse_board`, `as_dict`), run logs with two clocks, never overwritten (`common/runlog.py`) | `common/` |
| 14 | Worker: drives one `c4solve`, credits (2 x threads), answers clock checks first, logs to `logs/workers/` | `worker/` |
| 15 | Board for the coordinator (same bit layout and keys as C++), task tree: expand, copies, combine answers, split | `coordinator/board.py`, `coordinator/tree.py` |
| 16 | Coordinator: phases joining -> (calibrating) -> running -> draining, in-flight table, clock checks, silence/timeout/error handling, summary; V1 shared pile | `coordinator/server.py`, `strategies.py`, `run.py` |
| 17 | V0: calibration (200 Begin-Medium positions by default, speed = set size / time to finish it), fixed split by smooth weighted round robin | `coordinator/calibration.py`, `strategies.py` |
| 18 | V2: tasks past `--split-after` (default 60 s, counted from `started`) are stopped and split, children to the front of the pile; sent tasks under a newly decided position are cancelled | `server.py`, `strategies.py` |

**Checks at `7b59c63`:** 156 Python tests pass (`uv run pytest`), ruff and pyrefly clean; C++: 5 unit tests + 6 Pons verify files (0 wrong of 6000), small-boards preset, debug-ubsan on 8 sizes. All on Laptop 1 only.

**Not done on Laptop 2 yet:** pull, `cmake --workflow --preset release` (the solver changed in `865ca15`), `cmake --workflow --preset small-boards`, `uv sync`, `uv run pytest`. Needs a person at Laptop 2.

---

## 2. Decisions to keep (already agreed with the user)

- **Protocol** (`docs/protocol.md`, version 1, agreed): `give_me_work.free` is an additive credit; `hello`'s clocks are read when the solver said `ready`, solver `time` counts from then; `moves` is `""` for the empty board; a task stopped or cancelled as it finishes may still answer `result`; every task gets exactly one final answer.
- **Task ids:** tree tasks `p<moves>` (`p` = empty board); calibration tasks `c<worker>.<n>`.
- **Tree:** every move order is its own task (copies are measured); mirror images are different positions; positions decided by the rules (can win at once, full board, every move loses at once) are never sent.
- **Counts:** calibration is never part of solve time, tasks sent or positions; `positions_lost` = stopped/cancelled work; `positions_unneeded` = results that changed nothing; solve time = first run task sent -> result known.
- **V0:** time-based speed; both laptops calibrate at once; the transposition table is not cleared after calibration (say so in the paper); tail effect may lower the 20-thread laptop's measured speed by a few percent (estimate, for the pilot to check).
- **Transposition table is kept for the whole run in every strategy** (methodology); V3 does not change this.
- **Board sizes:** 7x6 for all experiments; 4x4, 6x4, 6x6 only for correctness (`small-boards` preset).

---

## 3. What is left, in order

### Step 19: V3 (do not skip; the user asked for it)
Methodology: *"V3: V2 plus sharing solved results. The coordinator remembers every result it receives, merges tasks that are the same position into one, and answers repeated positions without sending them out again. Where possible, it also sends tasks that share a parent position to the same laptop, so that the transposition table on that laptop, which already holds related positions, can be reused."* No protocol change is needed: all of this is coordinator-side.

Suggested design (from the step 16–18 altitude reviews):
1. **Position memory in the tree:** keep an index `key -> [open tasks]`, updated in `GameTree._add_children` (so split children are covered too), and a `key -> Outcome` memory of every result. Recording one task's answer also records it for every open copy and works each up the tree; `record` returns all decided nodes, so `_decided` cancels as today. (Copies are only same-depth: equal keys imply equal move counts.)
2. **Merge copies before sending:** when the strategy picks a task whose position is already in flight elsewhere, do not send it; it waits for that answer. When the position's result is already known, answer it from memory without sending. Count both in the summary (`answered_from_memory`, `merged_copies`).
3. **Related positions to the same laptop:** a split's children go to the laptop that held the parent first (a per-worker "related" queue checked before the shared pile; others may still take them if that laptop has nothing queued). Needs a hook that knows the worker, e.g. `add_split(parent, children, worker)`, keeping `add_tasks` for returns.
4. **Strategy flags:** add a small base class with every flag `False` by default, so each version sets only what it turns on (V3 = V2 flags + `shares_results` + `keeps_related_together`).
5. Tests: unit tests for the tree memory and merging; fake-worker runs proving a copy is never sent twice and a known position is answered from memory; a real two-worker V3 run on a Pons position with the right result; summary counts.

### Step 20: one-laptop mode
`python -m coordinator --workers 1` already runs with one worker; add what the "laptop alone" references (experiment E1) need: a test and script support for running coordinator + one local worker, for each laptop, and the single-thread unsplit count (`c4solve --single <start>`). Check the methodology's E1 wording in `deliverables/Phase-2_Introduction_and_Research_Methodology.pdf` (read with `pdftotext -layout`; never edit `deliverables/`).

### Steps 21–24: experiment scripts (`scripts/`)
- **21 `network_check.py`:** ping and the time for one message on TCP 5555 (a small ZeroMQ echo), for Wi-Fi and cable; iperf3 only if installed (do not install tools without asking; report "not installed").
- **22 `run_experiment.py`:** one run. Checks first: release build with no uncommitted changes (read the `#` header lines of `c4solve`, or `build/release/generated/cm/build_info.hpp`); charger plugged in (`psutil.sensors_battery()`); cooled down (rule chosen in the pilot: wait time and/or temperature from the live HWiNFO CSV). Then starts the local worker(s) and the coordinator with the chosen strategy, depth, start position and link, and prints whether the answer was right. Laptop 2's worker cannot be started remotely without extra setup: give the script a worker role for Laptop 2 that starts its worker and waits for runs (e.g. loops: one worker per run).
- **23 `collect_logs.py`:** gets Laptop 2's logs to Laptop 1 after each run, and backs everything up to a second place after a session. The copy mechanism is not decided: prefer one that needs no admin setup and no new tools; if it needs a protocol change, raise the protocol version and update `docs/protocol.md`. Write the choice in `docs/decision-log.md`.
- **24 `docs/session-checklist.md`:** charger, Windows Update paused, clocks synced, other apps closed, power settings, room temperature, network check.

### Steps 33–36: analysis (`analysis/`), can be written before the experiments
`load_runs.py` (coordinator and worker JSONL logs; `common/runlog.py` format), `load_hwinfo.py` (HWiNFO CSV: Latin-1, drop the last 2 rows, find columns by name), `metrics.py` (solve time, speedup vs each laptop alone, efficiency vs a perfect split `T_ideal = T1*T2/(T1+T2)`, idle time per laptop, extra work, speed vs temperature/clock over time, network time), `best_fixed_split.py`, `figures.py`, `tables.py` (every average with min and max). Test them on logs from real local runs and on a small synthetic HWiNFO file. Raw logs are never edited; derived data only comes from these scripts.

### Also prepare
- `docs/decision-log.md` with empty entries for the pilot choices (step 26): start position, depth, V2/V3 time limit, V0 calibration set, cool-down rule, log-copy method, freeze commit.

### Steps that need people and Laptop 2 (not code)
25 (tests on both laptops), 26 (pilot), 27 (freeze), 28–32 (experiments), 37–39 (paper, Turnitin, submit).

---

## 4. How the work was done (keep doing it)

After every step: run the `simplify` skill (4 reviewers: reuse, simplification, efficiency, altitude), apply its fixes, then an independent read-only review agent hunting bugs and bad measurements, fix what it finds, rerun everything. Real bugs were found this way in every step.

**Pitfalls met in this session:**
- **Line endings:** Python's `Path.write_text` on Windows writes `\r\n`. Edit files with `read_bytes()`/`write_bytes()`, or the Edit tool; check changed files for `\r\n` before committing.
- `python -I` ignores `PYTHONPATH`; scratch scripts that import the project need `PYTHONPATH=.` without `-I`.
- Perl one-liners treat `@name` in a pattern as an array; long inline heredocs can be cut off: write edit scripts to a scratch file and run them.
- Inserting a function right above a test can steal its `@pytest.mark.parametrize` decorator.
- Test helper threads must be daemon threads and fake ZeroMQ sockets closed with `context.destroy(linger=0)`, or a stuck test can keep pytest from exiting (one unexplained hang was seen once).
- Timing tests must check behaviour, not exact counts (answers for different tasks arrive in any order).
- The release `c4solve` prints `dirty true` until changes are committed and rebuilt; rebuild before real runs.
