"""Calibration for V0 (Phase 2 methodology, "V0: Fixed split based on measured speed").

Just before a V0 run, every machine solves the same short calibration set at the same time. A
machine's speed is the size of the set divided by the time it took to solve all of it, with all its
threads (from the first calibration task sent to the last answer received, on the coordinator's
steady clock). Positions searched per second is not used: it depends on how full each machine's
transposition table already is.

The tasks are then dealt out once, in proportion to those speeds (the V0 strategy,
coordinator/strategies.py).

Calibration tasks are ordinary tasks to the workers, with ids "c<worker>.<n>"; they are not part
of the game tree, and their time is reported separately from the solve time. The transposition
tables are not cleared afterwards (the methodology keeps them for the whole run); the paper says so.

A caveat for the pilot study and the paper: a machine's time includes the tail of the set, when its
last few positions are still running and its other threads have nothing left to do. A machine with
more threads works through the set in fewer rounds (about 10 for 200 positions on 20 threads, 13
on 16), so the tail is a larger part of its time and its speed may come out a little low; with 20
and 16 threads the two machines differ only slightly here. This is an estimate, not a
measurement; the pilot study can check it with a larger set.
"""

from dataclasses import dataclass, field
from pathlib import Path

from common.messages import Outcome

ROOT = Path(__file__).resolve().parent.parent
# Pons's Begin-Medium set: about 0.5 million positions searched each, so 200 of them take a few
# seconds on either machine. The pilot study may choose another set.
DEFAULT_FILE = ROOT / "solver/tests/data/7x6/Test_L1_R2.txt"
DEFAULT_COUNT = 200


@dataclass(frozen=True)
class Position:
    moves: str
    expected: Outcome  # for the player to move; a wrong answer means a broken solver


def load_positions(path: Path, count: int) -> tuple[Position, ...]:
    """The first `count` positions of a "moves score" file (the format of solver/tests/data)."""
    if count < 1:
        raise ValueError(f"the calibration set needs at least 1 position, not {count}")
    positions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        moves, score = fields[0], int(fields[1])
        expected = Outcome.WIN if score > 0 else Outcome.LOSS if score < 0 else Outcome.DRAW
        positions.append(Position("" if moves == "-" else moves, expected))
        if len(positions) == count:
            break
    if len(positions) < count:
        raise ValueError(f"{path} has only {len(positions)} positions, {count} were asked for")
    return tuple(positions)


class CalibrationError(RuntimeError):
    """A calibration task got a wrong answer, or calibration is not finished."""


@dataclass
class _Progress:
    """One worker's way through the set."""

    prefix: str  # task ids are prefix + position number
    sent: int = 0  # positions sent so far (the set is sent in order)
    unanswered: dict[str, Position] = field(default_factory=dict)
    first_sent: float | None = None
    last_answer: float | None = None
    positions_searched: int = 0


class Calibration:
    """One calibration round: every worker solves the whole set."""

    def __init__(self, positions: tuple[Position, ...], workers: list[str]) -> None:
        if not positions:
            raise ValueError("the calibration set is empty")
        self._positions = positions
        self._progress = {name: _Progress(f"c{i}.") for i, name in enumerate(workers, start=1)}
        self._owner: dict[str, str] = {}  # task id -> worker, for tasks sent

    def owns(self, task_id: str) -> bool:
        return task_id in self._owner

    def next_task(self, worker: str, now: float) -> tuple[str, str] | None:
        """The next (task id, moves) to send to this worker, or None once it has them all."""
        progress = self._progress[worker]
        if progress.sent == len(self._positions):
            return None
        position = self._positions[progress.sent]
        progress.sent += 1
        task_id = f"{progress.prefix}{progress.sent}"
        progress.unanswered[task_id] = position
        self._owner[task_id] = worker
        if progress.first_sent is None:
            progress.first_sent = now
        return task_id, position.moves

    def record(self, task_id: str, outcome: Outcome, positions: int, now: float) -> None:
        """A calibration answer. Raises CalibrationError if it is wrong."""
        progress = self._progress[self._owner[task_id]]
        position = progress.unanswered.pop(task_id)
        if outcome != position.expected:
            raise CalibrationError(
                f"calibration position {position.moves} solved as {outcome}, "
                f"expected {position.expected}"
            )
        progress.positions_searched += positions
        progress.last_answer = now

    def done(self) -> bool:
        return all(
            p.sent == len(self._positions) and not p.unanswered for p in self._progress.values()
        )

    def report(self) -> dict[str, dict[str, float]]:
        """Each worker's time for the whole set, positions searched, and share of the total
        speed (speed = positions in the set / time). Only once calibration is done."""
        if not self.done():
            raise CalibrationError("calibration has not finished")
        seconds = {}
        for name, p in self._progress.items():
            # done() means every position was sent and answered, so both times are set.
            assert p.first_sent is not None and p.last_answer is not None
            seconds[name] = p.last_answer - p.first_sent
        speeds = {name: len(self._positions) / s for name, s in seconds.items()}
        total = sum(speeds.values())
        return {
            name: {
                "seconds": seconds[name],
                "speed": speeds[name],
                "positions": self._progress[name].positions_searched,
                "share": speeds[name] / total,
            }
            for name in self._progress
        }
