# connect-more

A distributed Connect Four solver, built to study how to share very uneven work between a few
unequal multi-core machines whose speed changes while they work.

This repository holds the code, the experiment scripts and the analysis behind the paper:

> **Distributing Uneven Workloads Across Unequal Multi-Core Machines: A Measurement Study with a
> Distributed Connect Four Solver**
> Mirza Ahsan Baig, Sundas Habib, Danyal Farhat. FAST School of Computing, FAST-NUCES Lahore.

It was built for the Parallel and Distributed Computing course (Fall 2026, Multi-Core / Many-Core
Architectures).

---

## What it does

A coordinator grows the Connect Four game tree from a start position down to a split depth *d*.
Every position at that depth becomes a task. Workers on two machines take tasks, solve them with a
multi-threaded C++ solver, and send the results back. The coordinator combines the results up the
tree until the start position is decided.

```
            Laptop (coordinator)                               PC
 ┌──────────────────────────────────────┐        ┌──────────────────────────┐
 │ coordinator ── task tree, strategy   │  JSON  │                          │
 │      │                               │ ZeroMQ │                          │
 │   worker ── c4solve (20 threads)     │◄──────►│ worker ── c4solve (16)   │
 │ sensor logger                        │TCP 5555│ sensor logger            │
 └──────────────────────────────────────┘        └──────────────────────────┘
               cable (direct, 1 GbE) or home Wi-Fi
```

Four work-sharing strategies are compared, each adding one idea to the one before:

| Version | Behaviour |
|---|---|
| **V0** | Fixed split decided before the run, in proportion to each machine's measured speed |
| **V1** | Ask for more: a machine gets a new task whenever it has spare credit |
| **V2** | V1, plus a task that runs longer than a time limit is split into its children, and tasks made unneeded by a win are cancelled |
| **V3** | V2, plus solved results are shared between copies of the same position, related tasks go to the same machine, and an idle machine takes work from the back of the other's queue |

The solver follows Pascal Pons's Connect Four tutorial: bitboard negamax with alpha-beta pruning,
a transposition table and move ordering. It is a weak solver: it only decides win, draw or loss.

## Results at a glance

Measured on 7 Nov 2026 on an Intel Core i7-12800H laptop and an AMD Ryzen 7 7700 desktop: 36 runs,
all valid, all with the correct result. Solve time in seconds, mean of three runs (lowest–highest):

| Setting | Cable | Wi-Fi |
|---|---|---|
| Laptop alone | 149 (123–200) | |
| PC alone | 153 (153–155) | |
| Unsplit search, one thread | 75.3 | |
| V0 fixed split | 137 (124–161) | 118 (113–121) |
| V1 ask for more | 124 (108–150) | 131 (112–153) |
| V2 + split, cancel | 84.7 (79–88) | 76.7 (74–80) |
| V3 + share results | 73.9 (67–86) | 66.5 (66–68) |

- V3 was about 1.8 times faster than V0, and 2.0 to 2.3 times faster than either machine alone
  running the same split search.
- Splitting is costly: split runs searched 16 to 68 times the positions of one unsplit search,
  so the best two-machine time only matched that search on a single thread.
- The laptop's power limit moved between 76 W and 45 W, and every unusually slow run happened at
  the low limit. Wi-Fi and cable gave no clear difference.

The code that produced these runs is the frozen commit `1ebc529`.

## Repository layout

| Path | Contents |
|---|---|
| `solver/` | C++20 solver library, the `c4solve` program, unit tests and known-result test sets |
| `coordinator/` | Task tree, the four strategies, the ZeroMQ server (runs on the laptop) |
| `worker/` | Worker agent: connects to the coordinator and drives one `c4solve` process |
| `common/` | JSON messages, run logs with two clocks, machine names |
| `scripts/` | Experiment runner, network check, sensor logger, log copying, unsplit count |
| `analysis/` | Turns the raw logs into the paper's tables and figures |
| `tests/` | Python tests (pytest) |
| `docs/` | Message protocol, solver manual, session checklist, decision log, work plan |
| `ENVIRONMENT.md` | Exact hardware, OS, toolchain and system settings of both machines |

## Requirements

- Linux on x86-64. Both machines ran Omarchy (Arch Linux), kernel 7.2.15.
- GCC 16.2.1, CMake 4.4 or newer, and Ninja.
- [uv](https://docs.astral.sh/uv/) 0.13.4. It installs Python 3.12 and the locked packages from
  `uv.lock`.
- `iperf3`, optional, for the network check.
- For two-machine runs: both machines on the same network, with TCP 5555 (messages) and 5201
  (iperf3) allowed between them. A direct cable is optional.

`ENVIRONMENT.md` lists every version and setting used in the measured runs.

## Build and test

```bash
uv sync --locked                          # Python environment, exactly as locked
cmake --workflow --preset release         # build the solver, run its unit and verify tests
cmake --workflow --preset small-boards    # check 4x4, 6x4 and 6x6 against Tromp's table
uv run pytest                             # Python tests
```

The build is locked to GCC 16.2.1 so that both machines run identical binaries. With another GCC
version, configure with `-DCM_STRICT_TOOLCHAIN=OFF` for development; such builds are not comparable
with the measured runs.

The solver can also be used on its own:

```bash
build/release/bin/c4solve_7x6 --single 4453                                   # solve one position
build/release/bin/c4solve_7x6 --verify solver/tests/data/7x6/Test_L1_R1.txt   # check 1,000 positions
```

A move string lists the columns played, from 1 to 7. `-` is the empty board.

## Reproducing the experiments

The full procedure, with every check, is in `docs/session-checklist.md`. In short:

**1. Prepare both machines** (`ENVIRONMENT.md`, sections 5 to 7). Set the power profile and the
`performance` governor, make the power counter readable, open the firewall ports, and set the
cable addresses (laptop `192.168.2.1`, PC `192.168.2.2`). The laptop must be on its charger.

**2. Check the settings** in `scripts/experiment.toml`. The frozen values are: the empty board
(expected result: win), split depth 3 (4 in E3), a 40 s time limit for V2 and V3, 200 calibration
positions for V0, and a cool-down to 51 °C (laptop) and 52 °C (PC). A run refuses to start if the
repository has uncommitted changes, so commit any change first.

**3. Start the sensor logger** on each machine, in its own terminal, for the whole session:

```bash
uv run python -m scripts.sensor_log --machine laptop1     # on the PC: --machine pc
```

**4. Start the PC's worker.** It loops until Ctrl+C: it cools down, joins the next run, and serves
its logs to the laptop.

```bash
uv run python -m scripts.run_experiment worker --link cable
uv run python -m scripts.run_experiment worker --link wifi --coordinator <laptop Wi-Fi address>
```

**5. Check the network** from the laptop, once per link:

```bash
uv run python -m scripts.network_check --link cable
uv run python -m scripts.network_check --link wifi --peer <PC Wi-Fi address>
```

**6. Run the experiments** from the laptop, one command per run. Each run checks the build, the
charger and the sensor log, waits for the cool-down, runs, and copies the PC's logs.

```bash
# E2: every strategy over each link, three rounds (order V0-V3, then V3-V0, then V0-V3)
uv run python -m scripts.run_experiment coordinator --strategy V3 --link cable --repeat 1
uv run python -m scripts.run_experiment coordinator --strategy V3 --link wifi --repeat 1 --peer <PC Wi-Fi address>

# E3: V1 and V3 at split depth 4, cable only
uv run python -m scripts.run_experiment coordinator --strategy V1 --link cable --repeat 1 --depth 4

# E1: each machine alone (stop the PC's worker before the PC's own runs)
uv run python -m scripts.run_experiment alone --machine laptop1 --repeat 1
uv run python -m scripts.run_experiment alone --machine pc --repeat 1

# The unsplit reference: one search on one thread, once, at the end
uv run python -m scripts.unsplit_count --start "" --expect win
```

Do not use either machine while a run is going on.

**7. Collect the logs** at the end of the session. Stop the sensor loggers, start
`uv run python -m scripts.collect_logs serve` on the PC, then on the laptop:

```bash
uv run python -m scripts.collect_logs pull --peer 192.168.2.2
uv run python -m scripts.collect_logs backup --to <second location>
```

**8. Make the tables and figures:**

```bash
uv run python -m analysis.tables      # runs.csv, summary.csv, summary.md
uv run python -m analysis.figures     # solve times, thread timelines, sensor panels
```

Both write to `analysis/out/`. Only valid runs with the correct result are counted. The others stay
in `runs.csv`, marked as not used.

The machine names `laptop1` (coordinator) and `pc` are set in `common/machines.py`. To run on other
machines, keep the two roles and set your own cool-down temperatures in `scripts/experiment.toml`.

## Logs and data integrity

- Every program that produces results writes its build (commit, uncommitted-changes flag,
  compiler, flags, board size) at the top of its log.
- Logs are JSON lines with two clocks on every line. They are never overwritten or edited. All
  tables and figures are made from them by the scripts in `analysis/`.
- `logs/` is not tracked in git. The raw logs of the measured session are available from the
  authors on request.

## Documentation

| File | Contents |
|---|---|
| `docs/protocol.md` | Messages between coordinator, workers and solver |
| `docs/solver-manual.md` | How the solver works, step by step |
| `docs/session-checklist.md` | The checklist for every experiment session |
| `docs/decision-log.md` | The pilot choices and the decisions made during the experiments |
| `docs/workplan.md` | The build plan, step by step |
| `docs/handoff.md` | State of the project after the experiments |

## Acknowledgements

- The solver follows Pascal Pons's tutorial
  [*Solving Connect 4: How to build a perfect AI*](http://blog.gamesolver.org/), and the 7x6 test
  sets in `solver/tests/data/7x6/` are his.
- The small-board results come from John Tromp's
  [Connect Four Playground](https://tromp.github.io/c4/c4.html).
