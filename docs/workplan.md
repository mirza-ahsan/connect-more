# Work Plan: What to Build, in Order

Do the steps in order. Don't start a step until the one before it works.

---

## Before coding (by Sun 11 Oct)

1. **Submit Phase 2.** Fill in the 5 yellow Laptop 2 boxes in the PDF, then submit.
2. **Set up Laptop 2.** Run the `laptop2-setup` folder (part 1 as admin, part 2 normally) until every check says PASS. Then fill in Laptop 2's details in `ENVIRONMENT.md`.
3. **Connect the laptops.**
   - Plug in the cable and set the addresses: Laptop 1 192.168.2.1, Laptop 2 192.168.2.2.
   - Choose one Wi-Fi network (router or hotspot) and use it for every Wi-Fi run.
   - Check the laptops can reach each other on both.
4. **Download Pons's 6 test files** into `solver/tests/data/7x6/`. Don't edit them.
5. **Decide what `solver/main.py` is for.** That folder is for C++ only.

---

## Build the solver (C++, by Wed 14 Oct)

6. **The board:** `solver/src/position.cpp` (+ `.hpp`)
   - Stores the board as two 64-bit numbers, like Pons's tutorial part 6.
   - Can play a move, check if a move wins, and turn "4453" into a board.
   - Test it in `solver/tests/test_position.cpp`: wins in all directions, bad move strings rejected.

7. **The search:** `solver/src/solver.cpp` (+ `.hpp`)
   - Follow Pons's tutorial parts 3 to 12, in order: search, cutting off useless moves, best moves first, the memory of solved positions, and the rest.
   - It only answers **win, draw or loss** (not "win in 12 moves").
   - It counts how many positions it looked at.
   - It can be told to **stop** halfway, and then must not save half-finished answers.

8. **Check it's correct:** `solver/apps/c4solve.cpp`
   - First mode: run a Pons test file and count wrong answers.
   - Second mode: solve one position on one thread and print only the count of positions searched (needed later).
   - Must get **0 wrong** on all 6 files, on **both** laptops.

9. **Use all cores:** `solver/src/task_pool.cpp` (+ `.hpp`)
   - 20 threads on Laptop 1 and 8 on Laptop 2, all taking jobs from one shared list and sharing one memory.
   - Prints the total positions searched every second.
   - Check: answers with all threads are the same as with 1 thread.

10. **Write down the messages:** `docs/protocol.md`. Before any program talks to another, agree on two things:
    - the lines the worker sends to the solver (`solve`, `stop`, `cancel`, `quit`), and what comes back (`result`, `stopped`, `cancelled`, `progress`, `error`);
    - the JSON messages between the coordinator and the workers (`hello`, `give me work`, `task`, `stop`, `cancel`, `result`, `progress`, `clock check`, `run over`).

11. **Solver listens for commands:** `solver/src/protocol.cpp` (+ `.hpp`), plus a third mode in `c4solve.cpp`.
    - It reads one command per line from the worker and answers one line at a time.
    - Check: start it, type a `solve` line by hand, and get an answer back.

12. **Small boards:** solve the empty board of 3 or 4 small sizes and compare with Tromp's table. They must all match.

---

## Build the Python side (by Fri 16 Oct)

13. **Shared message code:** `common/messages.py` and `common/runlog.py`
    - `messages.py` defines the JSON messages from step 10, so the coordinator and the workers use exactly the same format.
    - `runlog.py` writes each log line with two clocks: one for measuring durations, and the normal time for matching against HWiNFO. A log file is never overwritten.

14. **Worker:** `worker/` (runs on both laptops)
    - Starts the solver once.
    - Connects to the coordinator over port 5555.
    - Passes tasks in and results out.
    - Never holds more than one waiting task per thread.
    - Logs everything.

15. **Game tree:** `coordinator/tree.py`
    - Expands the start position 3 or 4 moves deep. Each position at the bottom is a task.
    - Spots the same position reached by different move orders.
    - Works results back up the tree: a position is a win if any move leads to a loss for the other player.
    - Test: at 3 moves deep from the empty board, there are 343 move orders but 238 different positions.

16. **Coordinator, with V1 first:** `coordinator/server.py`, `coordinator/run.py`, `coordinator/strategies.py`
    - V1 = one shared list of tasks; a laptop asks when it needs more.
    - Check 1: with both workers on Laptop 1, small boards give the right answer.
    - Check 2: with Laptop 2 over the cable, it still gives the right answer.

17. **Add V0:** `coordinator/calibration.py`
    - Both laptops solve a few test positions to measure their speed.
    - The tasks are then split once, by that speed, and never moved between laptops.

18. **Add V2:** V1, plus:
    - a task running longer than the time limit is stopped and broken into smaller tasks;
    - tasks that are no longer needed (because a winning move was found) are cancelled.

19. **Add V3:** V2, plus repeated positions are solved only once and answered from saved results. **If this isn't working by Sun 18 Oct, skip it.**

20. **One-laptop mode:** the same program with only one worker, used for the "laptop alone" runs.

---

## Scripts for running experiments (by Fri 16 Oct)

21. **`scripts/network_check.py`:** measures ping, iperf3 and the time for one message on port 5555, for both Wi-Fi and the cable.

22. **`scripts/run_experiment.py`:** starts one run. Before starting, it checks:
    - the build is `release` and has no uncommitted changes;
    - the charger is plugged in;
    - both laptops have cooled down.

    Then it starts the workers and the coordinator, and says whether the answer was right.

23. **`scripts/collect_logs.py`:** after each run, copies Laptop 2's logs to Laptop 1. After each session, backs everything up to a second place.

24. **`docs/session-checklist.md`:**
    - charger plugged in;
    - Windows Update paused;
    - clocks synced;
    - other apps closed;
    - power settings checked;
    - room temperature noted;
    - network check run.

---

## Test, try out, then freeze (Sat 17 – Sun 18 Oct)

25. **Run every test on both laptops:**
    - the C++ tests;
    - the 6 Pons files;
    - small boards with splitting on and off;
    - the Python tests;
    - Ruff and Pyrefly;
    - one full run over the cable and one over Wi-Fi.

26. **Pilot (trial runs).** These aren't results. Pick, and write in `docs/decision-log.md`:
    - the start position: Laptop 1 alone should take a few minutes;
    - which depth (3 or 4) to use for the main runs;
    - the time limit for V2 and V3;
    - the speed-test positions for V0;
    - the cool-down rule: how long to wait, and how cool each laptop must be.

27. **Freeze the code:**
    - Commit everything and write the commit in the decision log.
    - Rebuild `release` on both laptops.
    - No code changes from here. If a bug forces a change, redo every run made before it.

---

## Run the experiments (Mon 19 – Wed 21 Oct)

28. **Before each session:** follow the checklist and run the network check.

29. **Laptop alone, first:** Laptop 1 ×3, Laptop 2 ×3, plus one single-thread count on Laptop 1.

30. **Main runs:** V0, V1, V2 and V3, each over Wi-Fi and over the cable, each 3 times. That's 24 runs.
    - Reverse the order every other round (V0→V3, then V3→V0).
    - Wait for cool-down before every run, and don't touch the laptops while it runs.

31. **Depth runs:** V1 and V3 at the other depth, over the cable, 3 times each. That's 6 runs.

32. **If a run goes wrong:**
    - Keep its logs.
    - If the answer was wrong, stop and fix the bug.
    - If it crashed or was interrupted, redo it at the end of the session.
    - Write it in the session notes.

---

## Analyse (Wed 21 – Thu 22 Oct)

33. **Read the logs:** `analysis/load_runs.py` and `analysis/load_hwinfo.py`
    - HWiNFO files are Latin-1 text; drop the last 2 rows.
    - Find columns by name, not position.

34. **Compute the numbers:** `analysis/metrics.py`
    - solve time;
    - speedup against each laptop alone;
    - efficiency compared with a perfect split;
    - idle time per laptop;
    - extra work;
    - speed over time against temperature and clock;
    - network time.

35. **Best possible fixed split:** `analysis/best_fixed_split.py`. It works out, after the run, how well V0 could have done if it had known the task sizes.

36. **Make the charts and tables:** `analysis/figures.py` and `analysis/tables.py`. Show every average with its lowest and highest value.

---

## Write and submit (by Sun 8 Nov; the deadline was extended from 25 Oct)

37. **Write the paper** in the IEEE template:
    - Introduction and Methodology from Phase 2;
    - Related Work from Phase 1, **reworded**;
    - plus Implementation, Results and Conclusion.

38. **Run Turnitin by Fri 6 Nov.** It must be under 10%. Never paste from sources or from our own earlier PDFs.

39. **Submit by Sun 8 Nov, 23:55.** Put a copy in `deliverables/`.

40. **Later:** fix what the instructor says and submit to an IEEE or ACM conference.

---

**If you're running late, cut in this order:** first the depth runs (step 31), then V3 (step 19), then Wi-Fi runs for everything except V1.
