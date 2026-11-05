"""The task tree: the game expanded from a start position to the split depth (Phase 2 methodology,
"Turning the game into tasks").

Every position at the split depth is a task, sent to a worker and solved as win, draw or loss for
the player to move. The coordinator records each answer, and the tree combines them back up:
- a position is a **win** as soon as one move leads to a position that is a loss for the opponent;
- it is a **loss** once every move leads to a win for the opponent;
- otherwise, once every move is known, it is a **draw**.

A position is decided by the rules alone, and never becomes a task, when the player to move can
win at once (a win) or the board is full (a draw).

Different move orders can reach the same position. Each order is its own node and its own task
(copies are part of what the experiments measure); `copies()` groups them for V3, which merges
them. Mirror images are different positions.

For V2, `split()` replaces a task by its children one move deeper, and `open_tasks()` lists the
tasks under a decided position, which are then no longer needed and can be cancelled.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from coordinator.board import Position


class Outcome(StrEnum):
    """The result of a position for the player to move, with perfect play."""

    WIN = "win"
    DRAW = "draw"
    LOSS = "loss"

    def flipped(self) -> "Outcome":
        """The same result seen by the other player."""
        return {Outcome.WIN: Outcome.LOSS, Outcome.LOSS: Outcome.WIN}.get(self, Outcome.DRAW)


@dataclass(eq=False)
class Node:
    moves: str  # from the empty board: the start position's moves, then the path to here
    position: Position
    parent: "Node | None"
    children: list["Node"] = field(default_factory=list)
    outcome: Outcome | None = None
    by_rules: bool = False  # decided without solving: an immediate win, or a full board

    @property
    def task_id(self) -> str:
        """The id used in messages: "p" and the move string (just "p" for the empty board)."""
        return "p" + self.moves

    @property
    def depth(self) -> int:
        """Moves below the tree's start position."""
        return 0 if self.parent is None else self.parent.depth + 1

    def is_leaf(self) -> bool:
        return not self.children

    def ancestors(self) -> Iterator["Node"]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def is_needed(self) -> bool:
        """False once this node or any node above it is decided: its answer changes nothing."""
        return self.outcome is None and all(a.outcome is None for a in self.ancestors())


class GameTree:
    def __init__(self, start: str, depth: int, width: int = 7, height: int = 6) -> None:
        """Expands `start` (a move string) `depth` moves deep. Raises ValueError for an invalid
        start position, or one where the game is already over."""
        if depth < 0:
            raise ValueError("the split depth cannot be negative")
        position = Position.from_moves(start, width, height)
        if position.game_over():
            raise ValueError(f"the game is already over in the start position {start!r}")
        self.width = width
        self.height = height
        self.depth = depth
        self.root = Node(start, position, None)
        self._by_task_id: dict[str, Node] = {}
        self._expand(self.root, depth)

    # --- Building -------------------------------------------------------------------------

    def _decide_by_rules(self, node: Node) -> bool:
        position = node.position
        if position.can_win_now():
            node.outcome, node.by_rules = Outcome.WIN, True
        elif position.is_full():
            node.outcome, node.by_rules = Outcome.DRAW, True
        return node.outcome is not None

    def _expand(self, node: Node, levels: int) -> None:
        self._by_task_id[node.task_id] = node
        if self._decide_by_rules(node) or levels == 0:
            return
        for col in range(self.width):
            if node.position.can_play(col):
                child = Node(node.moves + str(col + 1), node.position.play(col), node)
                node.children.append(child)
                self._expand(child, levels - 1)
        # Children are built (and decided where possible) before their parent is looked at, so
        # the parent is only ever judged on its full list of moves. For example, if every move
        # lets the opponent win at once, the position is a loss without solving anything.
        self._decide_from_children(node)

    # --- Looking things up ----------------------------------------------------------------

    def node(self, task_id: str) -> Node:
        return self._by_task_id[task_id]

    def nodes(self) -> Iterator[Node]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def tasks(self) -> list[Node]:
        """Leaves still to be solved and still needed, in a fixed order (by move string)."""
        return [n for n in self.nodes() if n.is_leaf() and n.is_needed()]

    def open_tasks(self, node: Node) -> list[Node]:
        """Leaves below `node` (or `node` itself) that have no answer yet. Once `node` is decided,
        these are the tasks that can be cancelled."""
        found: list[Node] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.is_leaf():
                if current.outcome is None:
                    found.append(current)
            else:
                stack.extend(current.children)
        return found

    def copies(self) -> dict[int, list[Node]]:
        """Tasks that are the same position, grouped by key; only groups of two or more."""
        groups: dict[int, list[Node]] = {}
        for task in self.tasks():
            groups.setdefault(task.position.key(), []).append(task)
        return {key: nodes for key, nodes in groups.items() if len(nodes) > 1}

    @property
    def result(self) -> Outcome | None:
        """The start position's result, once known."""
        return self.root.outcome

    # --- Answers coming in ----------------------------------------------------------------

    def record(self, task_id: str, outcome: Outcome) -> list[Node]:
        """Stores a task's answer and works it up the tree. Returns every node decided by it,
        from the task upwards (empty if the answer was no longer needed or already known)."""
        node = self.node(task_id)
        if not node.is_leaf():
            raise ValueError(f"{task_id} was split; record answers for its children instead")
        if node.outcome is not None:
            if node.outcome != outcome:
                raise ValueError(f"{task_id}: answer {outcome} contradicts {node.outcome}")
            return []
        needed = node.is_needed()
        node.outcome = outcome
        if not needed:
            return []
        return [node, *self._work_up(node.parent)]

    @staticmethod
    def _decide_from_children(node: Node) -> bool:
        """Decides `node` from its children if they allow it. True if it is now decided."""
        if node.outcome is not None or not node.children:
            return node.outcome is not None
        outcomes = [child.outcome for child in node.children]
        if Outcome.LOSS in outcomes:
            node.outcome = Outcome.WIN
        elif None in outcomes:
            return False
        elif Outcome.DRAW in outcomes:
            node.outcome = Outcome.DRAW
        else:
            node.outcome = Outcome.LOSS
        return True

    def _work_up(self, node: Node | None) -> list[Node]:
        decided: list[Node] = []
        while node is not None and node.outcome is None and self._decide_from_children(node):
            decided.append(node)
            node = node.parent
        return decided

    def split(self, task_id: str) -> tuple[list[Node], list[Node]]:
        """Replaces a needed, unanswered task by its children one move deeper (V2). Returns the
        new tasks, and the nodes decided straight away: children decided by the rules can
        decide the split node itself (for example when every move loses at once), and that
        decision is worked up the tree like an answer."""
        node = self.node(task_id)
        if not node.is_leaf() or not node.is_needed():
            raise ValueError(f"{task_id} cannot be split: it is not a needed, unanswered task")
        self._expand_children(node)
        decided = self._work_up(node)
        new_tasks = [child for child in node.children if child.is_needed()]
        return new_tasks, decided

    def _expand_children(self, node: Node) -> None:
        for col in range(self.width):
            if node.position.can_play(col):
                child = Node(node.moves + str(col + 1), node.position.play(col), node)
                node.children.append(child)
                self._by_task_id[child.task_id] = child
                self._decide_by_rules(child)
