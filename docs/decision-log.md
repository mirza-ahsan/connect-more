# Decision log

Choices that affect the measurements, with the reason and the date. The pilot choices (work plan step 26) are written in `scripts/experiment.toml` as well, which the code freeze (step 27) freezes with the code.

---

## A. Pilot choices (to fill in after the pilot, step 26)

| Choice | Value | Why | Date |
|---|---|---|---|
| Start position (move string) | `""` (the empty board) | Laptop 1 alone (V3, depth 3) took 116.8 s; every shorter candidate (`444444` to `4`) took 0.4–27 s, so none reached the 180–480 s aim, and the group chose the empty board | 2026-11-07 |
| Its known result (win / draw / loss) | win | The known first-player win; all 5 pilot runs from it (Laptop 1 alone, and V1 depth 3 and 4, V0, V3 together) gave win | 2026-11-07 |
| Split depth for E1 and E2 (3 or 4) | 3 | 343 tasks; pilot V1 took 114.5 s at depth 3 and 244.7 s at depth 4, so depth 3 keeps the 30 two-machine runs short | 2026-11-07 |
| The other depth, for E3 | 4 | Pilot V1 at depth 4 (2,401 tasks) was correct and valid, 244.7 s | 2026-11-07 |
| V2/V3 time limit (`split_after`, s) | 40 | About the 95th percentile (41.6 s) of the 265 answered task times in pilot V1 depth 3; pilot V3 with it: 84.5 s, 3 tasks split, 19.2 G positions = 0.57 × V1's 33.7 G | 2026-11-07 |
| V0 calibration set and size | `Test_L1_R2.txt`, 200 positions | Calibration took 0.73 s (Laptop 1) and 0.86 s (PC), under 1% of pilot V0's 155.2 s solve | 2026-11-07 |
| Cool-down: rest time (s) | 60 | 60 s after a pilot run, Laptop 1 was at 42–46 °C (peaks 100 °C, idle 37–40 °C) and the PC at 45.1–46.6 °C (idle about 45 °C) | 2026-11-07 |
| Cool-down: max CPU package temperature, Laptop 1 (°C) | 51 | Its highest reading 60 s after a pilot run (46 °C) + 5 | 2026-11-07 |
| Cool-down: max CPU package temperature, PC (°C; k10temp Tctl) | 52 | Its highest reading 60 s after a pilot run (46.6 °C) + 5, rounded | 2026-11-07 |
| Wi-Fi network used | StormFiber-07B6-5G (home, 192.168.18.0/24) | The home network both machines use; network check on 7 Nov: ping median 4.96 ms, iperf3 264 / 243 Mbit/s | 2026-11-07 |
| Freeze commit (step 27) | The commit that adds these values (titled "scripts: pilot choices for the measured runs") | Code and settings frozen after the pilot | 2026-11-07 |

---

## B. Decisions made while building steps 19–24 (6 Nov 2026)

Made without the group, as the safest reasonable choice; change them if you disagree.

1. **V3 needs no protocol change.** Sharing results is done by the coordinator alone: it passes every decided position on to the copies of that position in its tree, holds back a task whose position is already being solved, and never sends a position it already knows. Nothing is sent into a solver's table. Protocol stays at version 1 (`docs/protocol.md`).
2. **V3 keeps a running task that a copy still needs.** When a position stops mattering in one place but a held copy of it is still needed elsewhere, the running task is not cancelled; its answer settles the copy. Without this, V3 would solve such positions twice.
3. **"Related tasks to the same laptop" (V3)** means siblings (tasks with the same parent position): each laptop takes whole groups of siblings, and the children of a task it held that was split go to the front of its own queue. A laptop with nothing left takes tasks from the back of the other laptop's queue, so it never idles while work waits.
4. **V3 counts:** `answered_from_memory` = needed tasks never sent because a copy was decided; `merged_copies` = tasks held back (and never sent) because a copy was being solved. They overlap, so they are reported side by side, never added.
5. **E1 (laptop alone) is V3 with one worker**, as the methodology says ("every feature of V3 that also helps a single machine"). It still stops and splits long tasks, which loses some work; say so in the paper.
6. **Unsplit count:** `c4solve --single <start>` with the solver's default table size (the same as in every run), its output kept unchanged in `logs/<run_id>/unsplit.txt`.
7. **Log copy method (step 23):** Laptop 2 serves its `logs` folder read-only over ZeroMQ on TCP 5555 (already allowed by the firewall), from its worker role all session; Laptop 1 copies new or grown files into `logs/laptop2/` after each run, with SHA-256 checks. No admin setup, no new tools, and not part of the run protocol. Backups go to a second place with `collect_logs backup`.
8. **Network check:** the ZeroMQ round trip is measured against an echo from Laptop 2's worker-role server (REQ/REP). It is a lower bound for a message's cost (no protocol decoding or handling). iperf3 runs only if installed; `iperf3 -s` is started by the worker role.
9. **Settings in `scripts/experiment.toml`, committed:** a measured run refuses to start if anything is uncommitted, so the pilot values are frozen with the code. Wi-Fi addresses change from day to day, so they are given on the command line, never written in the file.
10. **Worker names:** `laptop1` and `laptop2`. A pilot-mode Laptop 2 joins as `laptop2-pilot`, which a measured run does not accept, so a pilot laptop can never take part in a result.
11. **A measured run is invalid** (logged as `valid: false`, non-zero exit) if it timed out, Laptop 1's worker failed, other workers took part than expected, or the coordinator warned about a worker's solver (other commit, uncommitted changes, other table size).
12. **Cool-down needs a fresh reading:** the HWiNFO row must be at most 10 s old; otherwise the laptop waits. Laptop 2's checks and cool-down are in its session log (`logs/laptop2/sessions/`), not in the run's own log.
13. **A worker gives up after 90 s without any message from the coordinator** (the coordinator sends a clock check every 10 s in every phase), so a crashed Laptop 1 never leaves Laptop 2 solving for nobody.
14. **E3 runs only V1 and V3** at the other depth, as planned; a measured run of another strategy at that depth is refused.

---

## C. Moving to Linux, with a desktop PC in place of Laptop 2 (7 Nov 2026)

Agreed with the group on 7 Nov 2026.

1. **Laptop 2 is no longer available; a desktop PC takes its place.** AMD Ryzen 7 7700 (8 cores, 16 threads, desktop cooling). It is called `pc` everywhere: worker name, log folder (`logs/pc/` on Laptop 1), settings and docs (`pc-pilot` in pilot mode). Laptop 1 keeps the name `laptop1` and still runs the coordinator. Sections A and B above still hold, with "Laptop 2" read as "the PC". The paper must say that the second machine changed after the Phase 2 methodology was submitted.
2. **Both machines run Linux (Omarchy, the same configuration).** Removes the operating system as a difference between the two machines. The Windows setup is in git history.
3. **Toolchain from pacman, on both machines:** GCC 16.2.1, CMake 4.4.4, LLVM 23.1.3, uv 0.13.4. `CM_EXPECTED_GCC_VERSION` (release preset) and `required-version` (`pyproject.toml`) were changed to match; `uv.lock` is unchanged (`uv sync --locked` passes). No package updates from the freeze until the last experiment.
4. **HWiNFO is replaced by `scripts/sensor_log.py`.** It reads Linux's own sensor files once a second: package temperature (coretemp `Package id 0` / k10temp `Tctl`), package power (RAPL energy counter), the clock of each logical CPU (`cpuinfo_avg_freq`), CPU use, and on Intel the RAPL power limits (from both the CPU's own interface and the memory-mapped one that laptop firmware often uses; the lower one holds) and thermal-throttle counts (each physical core once). Its logs are run logs (JSON lines with both clocks), so they line up with the runs by the same `wall` clock and clock checks as everything else. Differences from HWiNFO to state in the paper: the clock is the average while running (halted time does not lower it, unlike HWiNFO's "effective clock"); there are no PROCHOT or power-limit flags; the AMD machine has no power limits or throttle counts at all.
5. **Package power needs one command per boot** (`sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj`), since Linux lets only root read the energy counter. The logger runs as the user and logs `null` power otherwise; it is not run as root, so no log or `.venv` file is ever owned by root.
6. **The cool-down setting `hwinfo_max_age` is now `sensor_max_age`** (same 10 s).
7. **The network check uses Linux `ping`** (`-c`, numeric, `LC_ALL=C`), which reports fractions of a millisecond; the Windows rule "<1ms = 0.5 ms" is gone.
8. **The `alone` role takes `--machine`** (`laptop1` or `pc`) instead of `--laptop`.
9. **Both machines use the `performance` governor and energy preference** (7 Nov 2026). The same frequency policy on both, and the closest Linux match to Windows' "Best performance". Cost: higher idle temperature and power on Laptop 1, which only lengthens its cool-down.
10. **The PC's memory runs in a single channel at 4800 MT/s** (1 × 16 GB; rated 6000 MT/s with EXPO, which is off). It is left as found, because changing BIOS memory settings the day before the experiments is a risk. The paper reports it, because the large transposition table depends on memory speed.
11. **Laptop 1's dynamic power limit is left as the platform sets it** (7 Nov 2026). Its `intel-rapl-mmio:0` PL1 moves with temperature (45 W read right after long builds, 76 W at 40 °C idle), as Windows' dynamic PL1 did (40 W). It is not pinned, because that would change how the laptop really behaves, and changing speed during a run is one of the effects the project studies. The sensor log records both PL1 values every second, so the paper can show how the limit moved in each run.
12. **Wi-Fi runs take the cable link down on both machines** (`nmcli connection down c4-cable`). The PC's cable now stays plugged into the laptop, so this, not unplugging it, guarantees that nothing in a Wi-Fi run goes over the cable.
