"""How the coordinator chooses which task goes to which worker (CLAUDE.md, "Experiment versions").

A strategy only decides the order and the destination of tasks. Sending them, crediting workers
and combining answers is the same for every strategy (coordinator/server.py).

Implemented so far:
- V1, "ask for more": one shared pile in a fixed order; whichever worker has room gets the next
  task. A fast laptop asks more often, so it gets more tasks, without its speed being measured.
"""

from collections import deque
from typing import Protocol

from common.messages import Hello
from coordinator.tree import GameTree, Node


class Strategy(Protocol):
    def start(self, tree: GameTree, workers: list[Hello]) -> None:
        """Called once, when every worker has joined and the run begins. Each worker's `hello`
        gives its name and number of solver threads."""
        ...

    def next_task(self, worker: str) -> Node | None:
        """A task to send to this worker now, or None if there is nothing for it right now."""
        ...

    def add_tasks(self, tasks: list[Node]) -> None:
        """New tasks that appeared during the run (for example from splitting a task)."""
        ...


class SharedPile:
    """V1: every worker takes the next task from one shared pile, oldest first."""

    def __init__(self) -> None:
        self._pile: deque[Node] = deque()

    def start(self, tree: GameTree, workers: list[Hello]) -> None:
        self._pile = deque(tree.tasks())

    def next_task(self, worker: str) -> Node | None:
        while self._pile:
            task = self._pile.popleft()
            if task.is_needed():  # skip tasks made unnecessary by an answer that came in
                return task
        return None

    def add_tasks(self, tasks: list[Node]) -> None:
        self._pile.extend(tasks)


STRATEGIES: dict[str, type[Strategy]] = {"V1": SharedPile}


def make_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(
            f"strategy {name!r} is not implemented yet; have {sorted(STRATEGIES)}"
        ) from None
