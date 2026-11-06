# Session checklist

Follow this for every experiment session (work plan steps 24 and 28–32). Pilot runs use the same steps with `--pilot`. Commands run from the repository folder (`C:\University\connect-more` on Laptop 1, `D:\Projects\connect-more` on Laptop 2) in a normal terminal.

---

## 1. Before the session, on both laptops

- [ ] **Charger** plugged in (the scripts refuse to start a run on battery).
- [ ] **Windows Update paused** (Settings → Windows Update → Pause updates).
- [ ] **Clocks synced** while online: `w32tm /resync` (as administrator if Windows asks).
- [ ] **Other apps closed**: browsers, chat, OneDrive and other sync tools, editors. Only a terminal and HWiNFO stay open.
- [ ] **Power settings** unchanged (ENVIRONMENT.md section 6): power mode *Best performance*; Laptop 1 also Dell *Ultra Performance*; sleep on AC off.
- [ ] **Room temperature** noted in the session notes (below).
- [ ] **Code frozen**: `git status` shows nothing to commit, and `git log -1` shows the freeze commit written in `docs/decision-log.md`. After any pull, rebuild: `cmake --workflow --preset release`.
- [ ] **HWiNFO logging** to `logs\hwinfo\<date>_laptop1.CSV` (Laptop 1) or `logs\hwinfo\<date>_laptop2.CSV` (Laptop 2), every 1000 ms (sensors window → *Logging start*). The scripts read the newest CSV there for the cool-down and refuse to start if it has no row from the last 10 s.
- [ ] **Network** for the link of this block of runs: cable plugged in with 192.168.2.1 / 192.168.2.2, or both laptops on the chosen Wi-Fi network (write down both Wi-Fi addresses from `ipconfig`).

## 2. Start Laptop 2 (once per session, and again when the link changes)

```
uv run python -m scripts.run_experiment worker --link cable
uv run python -m scripts.run_experiment worker --link wifi --coordinator <Laptop 1 Wi-Fi address>
```

It checks itself and cools down before each run, then joins the next run, and loops until Ctrl+C. All along it serves its logs, the network echo and `iperf3 -s` to Laptop 1. Leave it alone while it runs.

## 3. Network check (Laptop 1, start of every session, both links)

```
uv run python -m scripts.network_check --link cable
uv run python -m scripts.network_check --link wifi --peer <Laptop 2 Wi-Fi address>
```

Laptop 2's worker role must be running (on either link: its server answers on every address). Results go to `logs\network\`.

## 4. The runs (Laptop 1)

Each command runs one run: checks, cool-down, the run, then a copy of Laptop 2's new logs into `logs\laptop2\`. A run that is not valid says so and exits with a non-zero code.

**E2 (main, 24 runs):** every strategy over each link, 3 rounds, the order reversed in every other round (round 1: V0 V1 V2 V3; round 2: V3 V2 V1 V0; round 3: V0 V1 V2 V3). Alternate cable and Wi-Fi blocks where possible.

```
uv run python -m scripts.run_experiment coordinator --strategy V0 --link cable --repeat 1
uv run python -m scripts.run_experiment coordinator --strategy V0 --link wifi --repeat 1 --peer <Laptop 2 Wi-Fi address>
```

**E3 (depth, 6 runs):** V1 and V3 over the cable at the other split depth:

```
uv run python -m scripts.run_experiment coordinator --strategy V1 --link cable --repeat 1 --depth <other depth>
```

**E1 (each laptop alone, 3 + 3 runs):**

- Laptop 1: `uv run python -m scripts.run_experiment alone --laptop laptop1 --repeat 1`. Laptop 2's worker role may keep running (an alone run only accepts its own laptop's worker).
- Laptop 2: first stop its worker role (Ctrl+C), then `uv run python -m scripts.run_experiment alone --laptop laptop2 --repeat 1`. Afterwards, start `uv run python -m scripts.collect_logs serve` on Laptop 2 (or the worker role again) and copy its logs from Laptop 1: `uv run python -m scripts.collect_logs pull --peer 192.168.2.2`.
- Unsplit count, once, on Laptop 1, after a cool-down and not between measured runs: `uv run python -m scripts.unsplit_count --start <start> --expect <result>` (only its positions searched are used; it can take long).

**Do not touch either laptop while a run is going on.**

## 5. If a run goes wrong (step 32)

- Keep every log; never delete or edit one.
- Wrong result: stop the session and find the bug.
- Crash, timeout, invalid run or interruption: write it in the session notes and redo that run at the end of the session.

## 6. After the session

- [ ] Stop HWiNFO logging on both laptops, then stop Laptop 2's worker role (Ctrl+C) and start `uv run python -m scripts.collect_logs serve` there.
- [ ] Laptop 1: `uv run python -m scripts.collect_logs pull --peer 192.168.2.2` (copies the final logs, including Laptop 2's complete HWiNFO file).
- [ ] Laptop 1: `uv run python -m scripts.collect_logs backup --to <second place>` (a USB drive or another disk). Laptop 2 too, for its own folder.
- [ ] Session notes written.

## Session notes

Write one file per session, `logs\sessions\<date>_notes.md` on Laptop 1, by hand:

- date, start and end time, who ran it;
- room temperature at the start and the end;
- Wi-Fi network used and both Wi-Fi addresses;
- commit (`git log -1 --format=%h`);
- runs done, in order, with their run ids;
- anything unusual (a run redone, a warning, a laptop touched, a window opened).
