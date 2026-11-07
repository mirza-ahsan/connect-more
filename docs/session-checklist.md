# Session checklist

Follow this for every experiment session (work plan steps 24 and 28–32). Pilot runs use the same steps with `--pilot`. Both machines run Linux (Omarchy); commands run from the repository folder in a terminal. Laptop 1 (`laptop1`) runs the coordinator; the PC (`pc`) is the second worker.

---

## 1. Before the session, on both machines

- [ ] **Laptop 1 on its charger** (the scripts refuse to start a run on battery; the PC has no battery and passes).
- [ ] **No package updates** since the freeze (no `pacman -Syu` or `omarchy-update`).
- [ ] **Clocks synced:** `timedatectl` shows "System clock synchronized: yes" on both.
- [ ] **Other apps closed**: browsers, chat, sync tools, editors. Only terminals stay open.
- [ ] **Power settings** unchanged (ENVIRONMENT.md sections 2 and 6): `powerprofilesctl get` says `performance`; Laptop 1's Dell *Ultra Performance* profile still set.
- [ ] **Governor** `performance` on both: `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | sort -u` shows only `performance`. On Laptop 1, set it after every boot: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` (the PC's is already `performance`).
- [ ] **Stay awake:** `omarchy-toggle-idle stay-awake` on both, so there is no sleep or screen lock during the session (undone in section 6).
- [ ] **Power counter readable** (once after every boot): `sudo chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj`.
- [ ] **Room temperature** noted in the session notes (below).
- [ ] **Code frozen**: `git status` shows nothing to commit, and `git log -1` shows the freeze commit written in `docs/decision-log.md`. After any pull, rebuild: `cmake --workflow --preset release`.
- [ ] **Sensor logging** started in its own terminal, and left running all session:
  - Laptop 1: `uv run python -m scripts.sensor_log --machine laptop1`
  - PC: `uv run python -m scripts.sensor_log --machine pc`

  It writes `logs/sensors/<date-time>_<machine>.jsonl`, one sample a second. The run scripts read the newest log there for the cool-down and refuse to start if it has no sample from the last 10 s.
- [ ] **Network** for this block of runs:
  - Cable: the PC's Ethernet port goes to Laptop 1's USB-C adapter (not the router); `nmcli connection up c4-cable` on both (again after every reboot); `ip -br addr` shows 192.168.2.1 and 192.168.2.2.
  - Wi-Fi: the PC's cable unplugged, both machines on the chosen Wi-Fi network; write down both Wi-Fi addresses from `ip -br addr`.
- [ ] **Firewall**: ufw allows TCP 5555 and 5201 from the cable and Wi-Fi subnets on both machines (ENVIRONMENT.md section 6).

## 2. Start the PC (once per session, and again when the link changes)

```
uv run python -m scripts.run_experiment worker --link cable
uv run python -m scripts.run_experiment worker --link wifi --coordinator <Laptop 1 Wi-Fi address>
```

It checks itself and cools down before each run, then joins the next run, and loops until Ctrl+C. All along it serves its logs, the network echo and `iperf3 -s` to Laptop 1. Leave it alone while it runs.

## 3. Network check (Laptop 1, start of every session, both links)

```
uv run python -m scripts.network_check --link cable
uv run python -m scripts.network_check --link wifi --peer <PC Wi-Fi address>
```

The PC's worker role must be running (on either link: its server answers on every address). Results go to `logs/network/`.

## 4. The runs (Laptop 1)

Each command runs one run: checks, cool-down, the run, then a copy of the PC's new logs into `logs/pc/`. A run that is not valid says so and exits with a non-zero code.

**E2 (main, 24 runs):** every strategy over each link, 3 rounds, the order reversed in every other round (round 1: V0 V1 V2 V3; round 2: V3 V2 V1 V0; round 3: V0 V1 V2 V3). Alternate cable and Wi-Fi blocks where possible.

```
uv run python -m scripts.run_experiment coordinator --strategy V0 --link cable --repeat 1
uv run python -m scripts.run_experiment coordinator --strategy V0 --link wifi --repeat 1 --peer <PC Wi-Fi address>
```

**E3 (depth, 6 runs):** V1 and V3 over the cable at the other split depth:

```
uv run python -m scripts.run_experiment coordinator --strategy V1 --link cable --repeat 1 --depth <other depth>
```

**E1 (each machine alone, 3 + 3 runs):**

- Laptop 1: `uv run python -m scripts.run_experiment alone --machine laptop1 --repeat 1`. The PC's worker role may keep running (an alone run only accepts its own machine's worker).
- PC: first stop its worker role (Ctrl+C), then `uv run python -m scripts.run_experiment alone --machine pc --repeat 1`. Afterwards, start `uv run python -m scripts.collect_logs serve` on the PC (or the worker role again) and copy its logs from Laptop 1: `uv run python -m scripts.collect_logs pull --peer 192.168.2.2`.
- Unsplit count, once, on Laptop 1, after a cool-down and not between measured runs: `uv run python -m scripts.unsplit_count --start <start> --expect <result>` (only its positions searched are used; it can take long).

**Do not touch either machine while a run is going on.**

## 5. If a run goes wrong (step 32)

- Keep every log; never delete or edit one.
- Wrong result: stop the session and find the bug.
- Crash, timeout, invalid run or interruption: write it in the session notes and redo that run at the end of the session.

## 6. After the session

- [ ] Stop the sensor logs on both machines (Ctrl+C), then stop the PC's worker role (Ctrl+C) and start `uv run python -m scripts.collect_logs serve` there.
- [ ] Laptop 1: `uv run python -m scripts.collect_logs pull --peer 192.168.2.2` (copies the final logs, including the PC's complete sensor log).
- [ ] Laptop 1: `uv run python -m scripts.collect_logs backup --to <second place>` (a USB drive or another disk). The PC too, for its own folder.
- [ ] Session notes written.
- [ ] `omarchy-toggle-idle allow-idle` on both machines.

## Session notes

Write one file per session, `logs/sessions/<date>_notes.md` on Laptop 1, by hand:

- date, start and end time, who ran it;
- room temperature at the start and the end;
- Wi-Fi network used and both Wi-Fi addresses;
- commit (`git log -1 --format=%h`);
- runs done, in order, with their run ids;
- anything unusual (a run redone, a warning, a machine touched, a window opened).
