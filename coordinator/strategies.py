"""How the coordinator chooses which task goes to which worker (CLAUDE.md, "Experiment versions").

A strategy only decides the order and the destination of tasks. Sending them, crediting workers
and combining answers is the same for every strategy (coordinator/server.py).

Implemented so far:
- V0, fixed split by measured speed: after calibration (coordinator/calibration.py), the tasks are
  dealt out once, in the tree's order, in proportion to the laptops' speeds. Each laptop then
  works only through its own share; no work moves between laptops.
- V1, "ask for more": one shared pile in a fixed order; whichever worker has room gets the next
  task. A fast laptop asks more often, so it gets more tasks, without its speed being measured.

Both skip tasks that are no longer needed before sending them. That is not "cancelling" (neither
cancels a task that was sent); it only avoids asking for answers nobody needs.
"""

from collections import deque
from typing import ClassVar, Protocol

from common.messages import Hello
from coordinator.tree import GameTree, Node


class Strategy(Protocol):
    needs_calibration: ClassVar[bool]  # True: the coordinator calibrates before the run starts

    def start(self, tree: GameTree, workers: list[Hello], speeds: dict[str, float]) -> None:
        """Called once, when the run begins. Each worker's `hello` gives its name and number of
        solver threads; `speeds` (by worker name) is filled only if the strategy needs
        calibration."""
        ...

    def next_task(self, worker: str) -> Node | None:
        """A task to send to this worker now, or None if there is nothing for it right now."""
        ...

    def add_tasks(self, tasks: list[Node]) -> None:
        """Tasks that came back unanswered, or new ones (for example from splitting a task)."""
        ...

    def shares(self) -> dict[str, int]:
        """How many tasks each worker was dealt at the start, if the strategy deals them out
        (written to the run log); empty if it does not."""
        ...


def weighted_split[T](items: list[T], weights: dict[str, float]) -> dict[str, list[T]]:
    """Deals the items out in order, in turn, in proportion to the weights ("smooth weighted
    round robin"). With weights 3 and 1 the order is a, a, b, a, a, a, b, a, ...: every prefix of
    the list is split as close to 3:1 as whole items allow, and the result does not depend on
    chance."""
    if not weights or any(w <= 0 for w in weights.values()):
        raise ValueError(f"weights must be positive: {weights}")
    total = sum(weights.values())
    credit = dict.fromkeys(weights, 0.0)
    shares: dict[str, list[T]] = {name: [] for name in weights}
    for item in items:
        for name, weight in weights.items():
            credit[name] += weight
        chosen = max(credit, key=lambda name: credit[name])  # ties go to the first listed
        credit[chosen] -= total
        shares[chosen].append(item)
    return shares


def _next_needed(queue: deque[Node]) -> Node | None:
    while queue:
        task = queue.popleft()
        if task.is_needed():  # skip tasks made unnecessary by an answer that came in
            return task
    return None


class FixedSplit:
    """V0: each worker works only through the share it was dealt at the start."""

    needs_calibration: ClassVar[bool] = True

    def __init__(self) -> None:
        self._queues: dict[str, deque[Node]] = {}
        self._owner: dict[str, str] = {}  # task id -> worker, so returned tasks go back to it
        self._dealt: dict[str, int] = {}

    def start(self, tree: GameTree, workers: list[Hello], speeds: dict[str, float]) -> None:
        shares = weighted_split(tree.tasks(), {w.worker: speeds[w.worker] for w in workers})
        self._queues = {name: deque(tasks) for name, tasks in shares.items()}
        self._owner = {task.task_id: name for name, tasks in shares.items() for task in tasks}
        self._dealt = {name: len(tasks) for name, tasks in shares.items()}

    def shares(self) -> dict[str, int]:
        return dict(self._dealt)

    def next_task(self, worker: str) -> Node | None:
        return _next_needed(self._queues[worker])

    def add_tasks(self, tasks: list[Node]) -> None:
        # A returned task goes back to the front of its owner's share: work never moves.
        for task in reversed(tasks):
            self._queues[self._owner[task.task_id]].appendleft(task)


class SharedPile:
    """V1: every worker takes the next task from one shared pile, oldest first."""

    needs_calibration: ClassVar[bool] = False

    def __init__(self) -> None:
        self._pile: deque[Node] = deque()

    def start(self, tree: GameTree, workers: list[Hello], speeds: dict[str, float]) -> None:
        self._pile = deque(tree.tasks())

    def next_task(self, worker: str) -> Node | None:
        return _next_needed(self._pile)

    def add_tasks(self, tasks: list[Node]) -> None:
        self._pile.extend(tasks)

    def shares(self) -> dict[str, int]:
        return {}  # nothing is dealt out: workers take from the shared pile as they go


STRATEGIES: dict[str, type[Strategy]] = {"V0": FixedSplit, "V1": SharedPile}


def make_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(
            f"strategy {name!r} is not implemented yet; have {sorted(STRATEGIES)}"
        ) from None
