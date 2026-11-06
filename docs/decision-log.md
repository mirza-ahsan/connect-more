# Decision log

Choices that affect the measurements, with the reason and the date. The pilot choices (work plan step 26) are written in `scripts/experiment.toml` as well, which the code freeze (step 27) freezes with the code.

---

## A. Pilot choices (to fill in after the pilot, step 26)

| Choice | Value | Why | Date |
|---|---|---|---|
| Start position (move string) | | Laptop 1 alone should need a few minutes | |
| Its known result (win / draw / loss) | | From Pons's solver; confirmed by every E1 run | |
| Split depth for E1 and E2 (3 or 4) | | | |
| The other depth, for E3 | | | |
| V2/V3 time limit (`split_after`, s) | | | |
| V0 calibration set and size | | Default now: `Test_L1_R2.txt`, 200 positions | |
| Cool-down: rest time (s) | | | |
| Cool-down: max CPU package temperature, Laptop 1 (°C) | | | |
| Cool-down: max CPU package temperature, Laptop 2 (°C) | | | |
| Wi-Fi network used | | | |
| Freeze commit (step 27) | | | |

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
