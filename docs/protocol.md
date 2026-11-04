# Message Protocol (version 1)

**Status: agreed by the group.** The solver (step 11) and the Python side (steps 13–16) are built to match this file exactly. Any later change needs the version number raised.

There are two connections:

| Connection | Where | Format |
|---|---|---|
| Worker ↔ solver | Same laptop. The worker starts `c4solve` and talks to it through its standard input and output | One line of plain words per message |
| Coordinator ↔ worker | Between laptops, over ZeroMQ on TCP port 5555 | One JSON object per message |

---

## Part A: Worker ↔ solver (text lines)

### General rules
- One message per line, ending with a newline. Words are separated by single spaces. The first word says what the message is.
- The solver writes each line in one piece and flushes it at once. Lines from different threads never mix.
- **Task ids** are chosen by the coordinator: 1 to 64 characters, using only letters, digits, `-`, `_` and `.`. No spaces.
- **Positions** are move strings with columns from 1 (for example `4453`). `-` means the empty board.
- **Times** are in seconds, with decimals:
  - `seconds` is how long a task took to solve;
  - `time` is when something happened, counted from the moment the solver sent `ready` (a steady clock that never jumps).
- **Positions searched** are whole numbers, counted per task, or for all tasks together in `progress`.
- Lines starting with `#` are information for the log, such as the build header. The worker writes them to its log and otherwise ignores them.

### Commands (worker → solver)

| Command | Meaning |
|---|---|
| `solve <id> <moves>` | Add a task to the solver's list |
| `stop <id>` | End the task early, because the coordinator is splitting it (V2 and V3) |
| `cancel <id>` | End the task, or take it off the list, because its answer is no longer needed |
| `quit` | Stop everything and exit |

### Answers (solver → worker)

| Answer | Meaning |
|---|---|
| `ready <protocol> <board> <threads> <table_mb>` | Sent once, after start-up, when the table is allocated and the solver can take tasks. Example: `ready 1 7x6 20 1024`. Before it come the `#` build lines (commit, dirty flag, compiler and flags) |
| `started <id> <thread> <time>` | A thread took the task |
| `result <id> <win\|draw\|loss> <positions> <seconds> <time>` | The task is solved. The outcome is for the player to move in that position |
| `stopped <id> <positions> <seconds> <time>` | The task ended because of `stop` (or `quit`) |
| `cancelled <id> <positions> <seconds> <time>` | The task ended because of `cancel` (or `quit`). A task that never started has 0 positions and 0 seconds |
| `progress <positions> <time>` | Every second: positions searched by all threads since the solver started |
| `error <id> <message>` | Something was wrong. `<id>` is the task's id, or `-` if the problem has none. The message is the rest of the line |
| `bye <time>` | Last line after `quit`, once every thread has finished |

### Promises the solver keeps
1. Every `solve` gets exactly one final answer: `result`, `stopped`, `cancelled` or `error`.
2. `started` always comes before that task's `result`, `stopped` or `cancelled`. A task that is stopped or cancelled while still on the list never gets `started`.
3. A task that is stopped or cancelled just as it finishes may still answer `result`. The worker must accept that.
4. A task id can be used again once its final answer has been sent.
5. Bad input never crashes the solver. It answers `error` instead, for:
   - an unknown command or wrong number of words;
   - an id that is already in use, or an unknown id in `stop` or `cancel`;
   - an invalid position (bad column, full column), or a game that is already won.
6. After `quit`, waiting tasks answer `cancelled`, running ones `stopped`, and then `bye`.

### Example
```
# c4solve 0.1.0
# commit 76208ebc0e2a, dirty false
# ...
ready 1 7x6 20 1024
solve t1 4453
solve t2 4
started t1 0 0.412
started t2 1 0.412
progress 8104577 1.000
result t1 loss 31799719 2.541 2.953
stop t2
stopped t2 40122631 3.020 3.432
cancel t9
error t9 no task with this id
quit
bye 3.501
```

---

## Part B: Coordinator ↔ worker (JSON)

### General rules
- ZeroMQ over TCP port 5555. The coordinator (Laptop 1) uses a **ROUTER** socket and each worker a **DEALER** socket, so either side can send at any time. That's needed, because the coordinator must be able to send `stop` and `cancel` while a worker is busy.
- One JSON object per message, in UTF-8.
- Every message has `"type"` and `"v"` (the protocol version, `1`). A message with another version is answered with `error` and otherwise ignored.
- Names use lower case and underscores. Times are in seconds.
- Each side keeps its own clocks. Messages carry the sender's times, and `clock_check` (below) lets the coordinator line the two laptops' logs up afterwards.

### Worker → coordinator

| `type` | Other fields | When |
|---|---|---|
| `hello` | `worker` (name, for example `"laptop2"`), `threads`, `board`, `table_mb`, `commit`, `dirty`, `wall_time` (seconds since 1970), `mono_time` (the worker's steady clock) | Once, after the worker's solver has said `ready`. Both clocks are read at the moment `ready` arrived; the solver's `time` values count from that same moment, so `mono_time + time` puts any task event on the worker's steady clock |
| `give_me_work` | `free`: how many **more** tasks the coordinator may send, on top of any it has sent already (a credit) | First right after `welcome`, with `free` = 2 × threads (one running and one waiting task per thread). Then `free` = 1 each time a task ends (result, stopped, cancelled, or an error for that task). The coordinator adds the credits up and never sends more tasks than the credit allows, so a worker never holds more than one waiting task per thread |
| `started` | `task_id`, `thread`, `time` | Passed on from the solver |
| `result` | `task_id`, `outcome` (`"win"`, `"draw"`, `"loss"`), `positions`, `seconds`, `time` | Passed on from the solver |
| `stopped` | `task_id`, `positions`, `seconds`, `time` | Passed on from the solver |
| `cancelled` | `task_id`, `positions`, `seconds`, `time` | Passed on from the solver |
| `progress` | `positions`, `time` | Passed on from the solver, every second |
| `clock_reply` | `check_id`, `wall_time`, `mono_time` | Straight away, in answer to `clock_check` |
| `error` | `task_id` (or `null`), `message` | Passed on from the solver, or a problem in the worker itself |

### Coordinator → worker

| `type` | Other fields | When |
|---|---|---|
| `welcome` | `worker`, `run_id`, `strategy` (`"V0"` … `"V3"`) | Reply to `hello` |
| `task` | `task_id`, `moves` (`""` for the empty board) | One message per task, only while the worker has credit. A task beyond the credit is refused with `error` |
| `no_work` | — | For information only: the coordinator has the worker's credit but no task right now. It keeps the credit and sends tasks as soon as some are available, so the worker never needs to ask again |
| `stop` | `task_id` | Split a running task (V2 and V3) |
| `cancel` | `task_id` | The task's answer is no longer needed |
| `clock_check` | `check_id`, `sent_time` | A few times per run. The coordinator measures the round trip, which is also our network delay measurement, and the gap between the two laptops' clocks |
| `run_over` | — | The run has finished. The worker sends `quit` to its solver, waits for `bye`, closes its log and exits |

### Example
```json
{"type": "hello", "v": 1, "worker": "laptop2", "threads": 8, "board": "7x6", "table_mb": 1024, "commit": "76208ebc0e2a", "dirty": false, "wall_time": 1793600000.12, "mono_time": 5123.4}
{"type": "welcome", "v": 1, "worker": "laptop2", "run_id": "2026-11-04_V1_cable_r1", "strategy": "V1"}
{"type": "give_me_work", "v": 1, "free": 16}
{"type": "task", "v": 1, "task_id": "t17", "moves": "4453"}
{"type": "started", "v": 1, "task_id": "t17", "thread": 3, "time": 12.004}
{"type": "result", "v": 1, "task_id": "t17", "outcome": "loss", "positions": 31799719, "seconds": 2.541, "time": 14.545}
```

---

## Open questions for the group
1. **V3, sharing solved results:** the coordinator will need a way to send a known answer to the other laptop's solver, and the solver a command to accept it. That's left for when V3 is built, and will raise the version to 2.
2. **Who gives task ids:** this draft says the coordinator does (for example `t1`, `t2`, …), so ids are unique across both laptops.
3. **`free` in `give_me_work`:** settled as a credit (see the table): a worker holds at most one running and one waiting task per thread, so a thread never sits idle while a new task travels over the network.
