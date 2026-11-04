# Solver Manual

This is everything you need to build the C++ solver on your own: what it does, how Pascal Pons's method works, which files to write, how the parts talk to each other, and how to prove it is correct.

Read `CLAUDE.md` first for the project rules: code style, git rules and build commands. This manual does not repeat them.

---

## 1. What the solver is

The solver is one C++ program. You give it a Connect Four position, and it tells you whether the player to move will **win, draw or lose** if both players play perfectly. It also tells you **how many positions it looked at** to find out.

In the full system it sits at the bottom of the chain:

| Part | Runs where | Job |
|---|---|---|
| Coordinator (Python) | Laptop 1 | Cuts the game into many positions (tasks) and hands them out |
| Worker (Python) | Each laptop | Receives tasks and passes them to its solver |
| **Solver (C++, this manual)** | Each laptop | Solves the tasks using every core of its laptop, and reports the results |

The solver never talks over the network. It only reads lines from the worker (its standard input) and writes lines back (its standard output).

**Positions are written as move strings.** Each character is the column played, counting from 1 on the left. "4453" means: first player in column 4, second player in column 4, first player in column 5, second player in column 3. The empty string is the empty board. This is Pons's notation, and it is used everywhere in the project.

---

## 2. Pons's method, explained simply

Pascal Pons wrote a 12-part online tutorial, "Solving Connect 4: How to build a perfect AI" (blog.gamesolver.org; code at github.com/PascalPons/connect4). Each part adds one idea and measures how much faster the solver becomes. We follow his method, and we must credit him in the paper.

You do not need to read his whole tutorial, but read parts 6, 8, 9 and 10 at least once. Below is what each part does and what we take from it.

### Part 1: Introduction
Background and history. Connect Four on the standard 7×6 board was first solved in 1988 and is a first-player win. Nothing to build.

### Part 2: Notation, scores and the test sets
**Position notation:** the move strings described above.

**Score of a position**, always seen from the player to move:
- **Positive:** the player to move can win. The sooner the win, the bigger the score. Pons's formula is 22 minus the number of stones the winner has played when the game ends (on 7×6).
- **Zero:** the game ends in a draw.
- **Negative:** the player to move loses whatever they do. A later loss is closer to zero.

A **strong solver** finds the exact score. A **weak solver** finds only win, draw or loss. **We build a weak solver.** It is enough for our study and much faster.

**Test sets:** six files of 1,000 positions each, with the correct score of every position.

| File | Name | Positions taken from |
|---|---|---|
| Test_L3_R1 | End-Easy | Late game, ending soon |
| Test_L2_R1 | Middle-Easy | Mid game, ending soon |
| Test_L2_R2 | Middle-Medium | Mid game, ending later |
| Test_L1_R1 | Begin-Easy | Early game, ending soon |
| Test_L1_R2 | Begin-Medium | Early game, ending later |
| Test_L1_R3 | Begin-Hard | Early game, ending much later (hardest) |

Each line holds a position and its correct score, separated by a space. Our weak solver is correct for a line when its answer has the **same sign** as the score: positive means win, zero means draw, negative means loss.

### Part 3: Minimax
The basic idea: to score a position, try every move, score each resulting position, and keep the best. When it's the opponent's turn, they pick what's worst for you. This works but is far too slow, because the number of positions grows explosively. Nothing to keep except the idea.

### Part 4: Alpha-beta pruning (and negamax)
**Negamax** is minimax written once instead of twice. A position's score for me is the **negative** of the score of the position after my move, seen from my opponent's side. So one function handles both players.

**Alpha-beta pruning** lets the search skip moves that cannot change the answer. The search carries a window of interest, from alpha (the least I'm already sure of) to beta (the most that's still useful):
- once a move scores at least beta, the rest of the moves don't matter; stop and return;
- once a move scores more than alpha, raise alpha, so later moves are checked against a stricter bar.

The result is exact when it falls inside the window. Otherwise it is only a bound: "at most alpha" or "at least beta". That is fine, because the caller only needed to know which side of the window it fell on.

**Weak solving comes from this part:** search with the window from −1 to +1 and you only learn whether the score is negative, zero or positive, which is exactly win, draw or loss.

**Count positions here.** Pons adds one to a counter every time the search function is entered. That count is our "positions searched".

### Part 5: Move order
Alpha-beta skips the most when the best move is tried first. Pons tries **centre columns first**, then moves outwards (on 7 columns: 4, 3, 5, 2, 6, 1, 7). This alone cut the work more than tenfold in his tests.

### Part 6: Bitboards (how the board is stored)
The board is stored as bits in a 64-bit number, which makes every board operation a few very fast arithmetic steps.
- **Bit layout:** each column gets height + 1 bits: one per cell from bottom to top, plus one spare bit on top. A 7×6 board therefore uses 7 × 7 = 49 bits. Column 1 uses bits 0 to 6 (bit 0 is the bottom cell, bit 6 the spare), column 2 uses bits 7 to 13, and so on.
- **Two numbers per position:**
  - "current", the stones of the player to move;
  - "mask", all stones of both players.

  You also keep the number of moves played.
- **Playing a move:** first swap whose turn it is, by combining current and mask so that current becomes the other player's stones. Then add the new stone to the mask in the lowest empty cell of that column. Pons does both with two short bit operations.
- **Can a column be played?** Only if its top cell is empty in the mask.
- **Four in a row:** shift the player's stones by one step in a direction, keep where both overlap, then repeat with a step of two. If anything remains, there are four in a row. The step sizes are: 1 for vertical, height + 1 for horizontal, height for one diagonal and height + 2 for the other.
- **Unique key:** current + mask, plus a constant bottom-row value, gives one number that identifies the position exactly, however it was reached. This is the key used by the memory table (part 7). Adding the constant does not change uniqueness, so current + mask alone also works.

The spare top bit per column is what makes the key unique, and it is also why the board must satisfy **width × (height + 1) ≤ 64**.

### Part 7: Transposition table (memory of solved positions)
The same position is reached through many different move orders. The transposition table is a big fixed-size table that remembers, for each position it has seen, what the search learned about it, so the work is not repeated.
- The slot for a position is chosen from its key.
- A new entry simply overwrites whatever was in that slot. There is no clever replacement, and that works well because recent positions are the most useful.
- At the start of the search function, look the position up. A stored value can narrow the window at once, or end the search immediately.
- At the end of the search function, store what was found.

### Part 8: Iterative deepening and null-window search
A **null window** is the narrowest possible window: from x to x + 1. It does not find the score; it only answers "is the score above x or not?" Because the window is so narrow, alpha-beta skips a great deal and the answer comes fast.

Pons finds the score by asking several such yes/no questions, narrowing the range each time, like guessing a number by halving. For the weak solver, the range is only −1 to +1, so at most two questions are needed. In practice, the solve function:
1. checks for an immediate winning move first, returning a win at once;
2. then runs the null-window questions on the range −1 to +1.

### Part 9: Avoid losing moves
Before searching, remove moves that would let the opponent win on their very next move.
- If the opponent threatens to win in a column right now, you **must** play there.
- If they have two such threats at once, you have already lost; return a loss straight away.
- Never play directly underneath a cell where the opponent would win, because that hands them that cell.

Pons computes all of this at once with bit operations, using a helper that finds every empty cell where a player would complete four. If no safe move is left, the position is lost. This also means the search never needs to check for an immediate win of its own, except once at the very top.

### Part 10: Better move order
Order moves by **how many winning chances they create.** For each safe move, count the empty cells where the player would complete four after making it, and try the highest count first. When two moves tie, keep the centre-first order from part 5.

Pons uses a tiny sorting helper that holds at most one entry per column. This was the biggest single speed-up: up to 100 times fewer positions searched on the hardest tests.

### Part 11: Compact transposition table
Pons stores only part of the key (32 bits) and puts the entry in slot "key modulo table size", where the table size is an odd number, ideally a prime. A mathematical rule (the Chinese remainder theorem) guarantees that two different positions can never share both the slot and the stored 32 bits, **as long as the table has at least 2^17 slots for a 49-bit key**. So the table saves memory and still never mixes up positions.

**For us:** the table must be big (our default is 1024 MB) and its size must be a prime number of slots. Then this guarantee holds for 7×6 and every smaller board.

### Part 12: Store lower bounds too
A search can end with an upper bound ("at most this") or a lower bound ("at least this", after a cut-off). Pons first stored only upper bounds, then added lower bounds in the same table, marked so the two kinds can be told apart. On a look-up, a lower bound can raise alpha and an upper bound can lower beta; either may end the search at once. This cut the work by another 10–15%.

### Pons's final weak-solver numbers (for comparison only)
These are **his** numbers from his single-threaded solver on his own computer, with a much smaller table. Ours will differ. Use them only to check that your solver is in the right range.

| Test set | Mean time per position | Mean positions searched |
|---|---|---|
| End-Easy | 3.2 µs | 29 |
| Middle-Easy | 48 µs | 533 |
| Middle-Medium | 1.7 ms | 20,210 |
| Begin-Easy | 1.9 ms | 22,230 |
| Begin-Medium | 45 ms | 538,700 |
| Begin-Hard | 3.1 s | 38,120,000 |

If your positions-searched counts are many times higher than his, something in parts 5, 9, 10 or 12 is missing or wrong.

---

## 3. What we add that Pons does not have

Pons's solver solves one position on one thread. Ours must also:

1. **Use every core:** 20 threads on Laptop 1 and 8 on Laptop 2, each solving a different task at the same time.
2. **Share one memory table** between all threads on the laptop, kept for the whole run.
3. **Stop or cancel a task** in the middle, when the coordinator asks (strategies V2 and V3).
4. **Report progress every second:** the total positions searched so far.
5. **Talk to the worker** through text lines.
6. **Print its build information** at start-up, so every result can be traced to the exact code.

Sections 5 and 6 explain how to do each of these safely.

---

## 4. Files to write

All paths are inside `solver/`. The build (`solver/CMakeLists.txt`) picks files up automatically:
- every `.cpp` in `src/` goes into the solver library;
- every `.cpp` in `apps/` becomes a program;
- every `tests/test_*.cpp` becomes a unit test;
- every `.txt` in `tests/data/<size>/` becomes a check run with `c4solve --verify`.

After adding a new file, re-run the configure step.

| File | What it holds |
|---|---|
| `include/c4/position.hpp`, `src/position.cpp` | The board: storing, playing, win checks, key, safe moves, move scores (Pons parts 6, 9, 10) |
| `include/c4/move_sorter.hpp` | The small helper that orders moves by score (part 10) |
| `include/c4/transposition_table.hpp`, `src/transposition_table.cpp` | The shared memory table (parts 7, 11, 12), safe for many threads |
| `include/c4/solver.hpp`, `src/solver.cpp` | The search (parts 4, 5, 8, 9, 12): weak solve, position counting, stopping |
| `include/c4/task_pool.hpp`, `src/task_pool.cpp` | The threads, the shared task list, stop and cancel, and progress reports |
| `include/c4/protocol.hpp`, `src/protocol.cpp` | Reading command lines and writing answer lines |
| `apps/c4solve.cpp` | The program, with three modes: verify, single and service |
| `apps/c4bench.cpp` | Timing the test sets (optional but useful) |
| `tests/test_position.cpp`, `tests/test_transposition_table.cpp`, `tests/test_solver.cpp`, `tests/test_task_pool.cpp`, `tests/test_protocol.cpp` | Unit tests; each returns 0 if everything passes |
| `tests/data/7x6/` | Pons's six test files, unchanged |
| `tests/data/<small size>/` | One file per small board, with the empty board's known result from Tromp's table |

---

## 5. File by file

### 5.1 `position.hpp` / `position.cpp`: the board
**Purpose:** everything about one board position, stored as in Pons part 6.

**It must provide:**
- an empty board;
- building a board from a move string, refusing anything invalid: a character that is not a valid column, a full column, or a move after someone has already won;
- whether a column can be played;
- playing a column;
- whether playing a column wins at once;
- whether the player to move can win at once (with any column);
- the number of moves played;
- the key (part 6);
- the set of safe moves that do not lose at once (part 9);
- the score of a move by the number of winning cells it creates (part 10).

**Rules:**
- Width and height come **only** from `C4_WIDTH` and `C4_HEIGHT`, which the build sets for each board size. Never type 7 or 6.
- All board numbers are **unsigned 64-bit**. On Windows `long` is only 32 bits, so never use `long` or `unsigned long` for a board.
- To count set bits, use the standard library bit-counting function (see `CLAUDE.md`).
- The helper masks (the bottom cell of every column, and the whole board) are worked out from width and height once, when the program is built.
- Check at build time that width × (height + 1) ≤ 64, and stop the build with a clear message if not.

### 5.2 `move_sorter.hpp`: ordering moves
**Purpose:** collect up to one move per column with its score, then hand them back highest score first. Ties keep the order they were added in, so the centre-first order survives (part 10).

**Rules:** fixed-size storage (no memory allocation during search); a simple insertion sort is enough.

### 5.3 `transposition_table.hpp` / `.cpp`: the shared memory table
**Purpose:** remember what was learned about positions, shared by **all** threads on the laptop for the whole run.

**Size:**
- The size comes from `CM_DEFAULT_TT_MB` (1024 MB), which must be the same on both laptops.
- Each slot is 8 bytes, so 1024 MB gives about 134 million slots. Use the **largest prime number of slots** that fits in the budget; part 11's guarantee needs an odd size, ideally prime.
- Allocate the table once, when the program starts. Allocating 1 GB takes a moment, so do it before measuring anything.

**What one slot holds:** part of the key (the lowest 32 bits) and the stored value: the bound, plus whether it is an upper or lower bound (part 12).

**Making it safe for many threads (our addition):**
- Several threads read and write the table at the same time. If a slot's key part and value part were stored separately, one thread could read the key from one position and the value from another, and get a wrong answer.
- **Pack the key part and the value into one 64-bit word** and read or write that whole word in one step, using the standard atomic type with the cheapest ("relaxed") ordering. A reader then sees either the old entry or the new one, never a mix.
- An empty slot is all zeros. Make sure a real stored value can never be all zeros; Pons uses 0 to mean "nothing stored".
- If two threads write the same slot at once, one simply wins. That is fine.
- Provide: clear everything (once per run, at start-up), store, and look up.

### 5.4 `solver.hpp` / `solver.cpp`: the search
**Purpose:** decide win, draw or loss for one position, counting positions searched, and stop early if asked.

**The search function, step by step** (Pons's final version, parts 4 to 12). Each call is given a position and a window from alpha to beta:
1. Add one to this thread's position counter.
2. **Check the stop flag**, at least every few thousand calls. If it is set, return at once and mark the result as invalid (see "Stopping" below).
3. Work out the safe moves (part 9). If there are none, the position is lost: return the losing score.
4. If the board is two moves or fewer from full, it is a draw: return 0.
5. Raise alpha to the lowest possible score for this position, given the moves left. If alpha has reached beta, return.
6. **Look up the table:**
   - a stored lower bound may raise alpha;
   - a stored upper bound may lower beta;
   - if alpha has reached beta, return.
7. Lower beta to the highest possible score for this position. If alpha has reached beta, return.
8. Put the safe moves into the move sorter in centre-first order, each with its move score.
9. Take moves from the sorter, best first. For each one, play it on a copy of the board and search the result with the window reversed and negated: minus beta to minus alpha. Negate the answer.
   - If it is at least beta: **store it as a lower bound** and return it.
   - If it is more than alpha: raise alpha.
10. **Store alpha as an upper bound**, and return alpha.

**The top-level solve, for one task:**
1. If the player to move can win at once, the answer is **win**, with 1 position searched.
2. Otherwise, run the null-window questions of part 8 on the range −1 to +1.
3. The final number is −1, 0 or +1: **loss, draw or win**.

**Counting:**
- Each thread has its own counter, so threads never slow each other down.
- The counter for a task is the number of search calls made while solving it, including when the task was stopped or cancelled.
- Use a 64-bit counter.

**Stopping (our addition, important):**
- Each task has a stop flag that another thread can set.
- When the search sees it, every level must return straight away **without storing anything in the table**, because the half-finished values are wrong. A wrong value stored in a shared table would poison other tasks, and they would give wrong answers.
- The simplest way: once the flag has been seen, every level checks it after each child search returns, and returns at once without storing.
- The top-level solve then reports "stopped", with the positions searched so far, instead of a result.

**Rules:** no global variables for search state, because several threads run searches at once. Each thread has its own board copies and counter; only the table is shared.

### 5.5 `task_pool.hpp` / `task_pool.cpp`: using every core
**Purpose:** run many tasks at once, one per thread, all taking work from one shared list on this laptop.

**What it holds:**
- the list of waiting tasks: task id and position;
- for each running task, its stop flag;
- one counter per thread;
- each thread's busy and idle times.

**How it works:**
1. At start-up, create one solver thread per logical processor (20 on Laptop 1, 8 on Laptop 2). Allow a different number for tests.
2. Each thread repeatedly:
   - waits for a task;
   - takes the oldest one;
   - reports **started** (task id, thread number, time);
   - solves it;
   - reports **result** (task id, win/draw/loss, positions searched, time taken) or **stopped**.
3. **Stop** a task: set its flag; it ends and reports **stopped** with its positions searched so far.
4. **Cancel** a task:
   - if it is waiting, remove it from the list and report **cancelled** with 0 positions searched;
   - if it is running, set its flag and report **cancelled**, with the positions searched so far, when it ends.
5. **Progress:** a separate small thread wakes once per second, adds up all thread counters, and reports **progress** (total positions searched so far, time).
6. **Shutdown:** stop taking new tasks, stop running ones, wait for every thread to finish, then exit.

**Rules:**
- Use the standard C++ thread, mutex, condition-variable and atomic tools.
- Do **not** pin threads to particular cores; the methodology leaves that to Windows on purpose.
- Measure time with the steady clock, which never jumps.
- Keep each thread's counter apart from the others in memory, so threads don't slow each other down by sharing a cache line.

### 5.6 `protocol.hpp` / `protocol.cpp`: talking to the worker
**Purpose:** read one command per line from standard input, and write one answer per line to standard output.

**Commands the solver accepts:**

| Command | Contents | What happens |
|---|---|---|
| solve | task id, position | Task added to the list |
| stop | task id | Task stopped (used when the coordinator splits it) |
| cancel | task id | Task removed or stopped (no longer needed) |
| quit | — | Clean shutdown |

**Answers the solver writes:**

| Answer | Contents |
|---|---|
| ready | Build information, board size, table size, thread count (once, at start-up) |
| started | task id, thread number, time |
| result | task id, win/draw/loss, positions searched, time taken |
| stopped | task id, positions searched so far, time taken |
| cancelled | task id, positions searched so far |
| progress | total positions searched so far, time (every second) |
| error | short message (bad command, invalid position, unknown task id) |

**Rules:**
- The exact field order and separators must match `docs/protocol.md`. Agree it with whoever writes the worker **before** coding.
- Write each answer as one whole line, then flush the output at once, so the worker sees it immediately.
- Only one thread may write to the output at a time (use one lock), so lines from different threads never mix.
- A bad command gets an **error** line. The program must never crash or stop because of bad input.
- An invalid position, such as a full column, is answered with an **error** for that task id.

### 5.7 `apps/c4solve.cpp`: the program
**At start-up, always:** print the build information from the generated header `cm/build_info.hpp`:
- commit and whether there were uncommitted changes (dirty flag);
- build type, compiler and its version, thread model;
- target instruction set (arch), flags, static linking;

plus the board size (`C4_VARIANT`), the table size and the thread count. Every program that produces results must do this (`CLAUDE.md`).

**Three modes:**

| Mode | Started with | What it does | Used for |
|---|---|---|---|
| Verify | `--verify` and a test file | Solves every line, compares signs, prints the number of wrong answers, mean time and mean positions searched. Exits with failure if anything is wrong | Correctness checks; the build runs this on every file in `tests/data/<size>/` |
| Single | `--single` and a position | Solves one position on **one thread**, with no splitting, and prints the positions searched | The "unsplit reference" count for the experiments |
| Service | no options | Prints the ready line, then serves commands until **quit** | Every experiment run, controlled by the worker |

Use the exact option name `--verify` followed by the file path, because the build already calls it that way.

### 5.8 `apps/c4bench.cpp` (optional)
Runs each test set with a chosen number of threads and prints the mean time and mean positions searched. Use it to compare the two laptops and to pick the speed-test positions for V0 in the pilot.

---

## 6. The tests

Each test file is its own small program that returns 0 if everything passes. Run them in the `release` build and in the `debug-ubsan` build, which stops on undefined behaviour.

| Test file | Checks |
|---|---|
| `test_position.cpp` | Valid strings accepted and invalid ones refused; wins found in all four directions; a full board with no four in a row is a draw; two move orders reaching the same stones give the same key; mirror-image positions give different keys; safe moves exclude moves that let the opponent win at once |
| `test_transposition_table.cpp` | A stored value is found again; a different position in the same slot is not mistaken for it; with many threads writing at once, a look-up never returns a value that was never stored |
| `test_solver.cpp` | A handful of positions from the easiest test set give the right answer; a search that is stopped and then run again with the same table still gives the right answer |
| `test_task_pool.cpp` | A test set solved with 1 thread and with all threads gives the same answers; stopping random tasks part-way and then re-solving them gives the right answers; a cancelled waiting task never starts |
| `test_protocol.cpp` | Every command is read correctly; bad lines produce an error; every answer is written in the agreed format |

**Data checks (run automatically by the build):**
- `tests/data/7x6/`: Pons's six files. Must give **0 wrong answers** on **both** laptops.
- `tests/data/<small size>/`: the empty board for 3 or 4 small sizes, with results from Tromp's table (tromp.github.io/c4/c4.html). The empty position is written as a dash (`-`). The sizes are 4x4, 6x4 and 6x6; build and check them with `cmake --workflow --preset small-boards`, which does not change the default size. Sources are listed in `tests/data/README.md`.

---

## 7. Build order (do it in this order, testing each step)

1. **Board** (`position`), with its tests. Check: `test_position` passes.
2. **Search without the table,** using centre-first order, as in Pons part 5. Check: Pons's End-Easy file gives 0 wrong answers. It will be slow on the harder files; that is expected.
3. **Add the table** (part 7), then **null-window weak solving** (part 8), then **safe moves** (part 9), then **move scores** (part 10), then the **compact table** (part 11), then **lower bounds** (part 12). Run the test files after **each** addition: answers must stay correct, and positions searched should fall. Write down the counts after each step; they are good material for the paper.
4. **Verify and single modes** in `c4solve`. Check: all six files give 0 wrong answers on Laptop 1 and on Laptop 2.
5. **Thread-safe table and stop flag.** Check: the table and solver tests pass, including the stop test.
6. **Task pool** (many threads). Check: `test_task_pool` passes, and all six files still give 0 wrong answers with all threads.
7. **Protocol and service mode.** Check: start `c4solve` with no options, type a solve line by hand, and get started, progress and result lines back.
8. **Small boards.** Check: every small size matches Tromp's table.
9. **`debug-ubsan` build:** all tests pass, with no undefined-behaviour stops.

---

## 8. Mistakes to avoid

- **Using `long` for boards.** It is 32 bits on Windows, and the board will silently break.
- **Writing 7 or 6 instead of `C4_WIDTH` and `C4_HEIGHT`.** The small boards will then give wrong answers.
- **Storing values after a stop.** It poisons the shared table and causes wrong answers in other tasks.
- **Storing the key and value as two separate writes.** With many threads, a reader can mix two entries.
- **Several threads writing to the output at once.** Lines get mixed and the worker cannot read them.
- **Forgetting to flush output.** The worker then waits for answers that are sitting in a buffer.
- **Comparing exact scores in verify mode.** Our solver is weak; compare **signs** only.
- **Allocating the 1 GB table inside a measured run.** Allocate it once at start-up, before the ready line.
- **Ignoring warnings.** The build has strict warnings on; fix every one (`CLAUDE.md`).

---

## 9. When the solver is done

- [ ] All unit tests pass in the release and debug-ubsan builds.
- [ ] Pons's six files give 0 wrong answers on both laptops, with 1 thread and with all threads.
- [ ] Small boards match Tromp's table.
- [ ] Service mode works by hand: solve, stop, cancel and quit all behave as described.
- [ ] The start-up header shows the right commit and no uncommitted changes in the release build.
- [ ] Positions-searched counts per test set are written down, for comparison with Pons's numbers and for the paper.
