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
(copies are part of what the experiments measure); `copies()` groups them. Mirror images are
different positions.

For V2, `split()` replaces a task by its children one move deeper. Tasks under a decided position
are no longer needed (`is_needed()`), so they can be cancelled.

For V3 (`shares_results`), every decision, from a task's answer or by combining answers, is passed
on at once to every other node that is the same position (its copies, wherever they are in the
tree), and each copy is worked up the tree in turn; a task split later gets the answers of
children whose position is already decided elsewhere. A copy answered this way is never solved.
Copies always have the same depth, since equal positions have the same number of stones.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from common.messages import Outcome
from coordinator.board import Position

__all__ = ["GameTree", "Node", "Outcome"]


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

    def is_open_task(self) -> bool:
        """A task still to be solved: a leaf (not split) whose answer is still needed."""
        return self.is_leaf() and self.is_needed()


class GameTree:
    def __init__(
        self, start: str, depth: int, width: int = 7, height: int = 6, shares_results: bool = False
    ) -> None:
        """Expands `start` (a move string) `depth` moves deep. With `shares_results` (V3), every
        decision is passed on to the copies of that position. Raises ValueError for an invalid
        start position, or one where the game is already over."""
        if depth < 0:
            raise ValueError("the split depth cannot be negative")
        position = Position.from_moves(start, width, height)
        if position.game_over():
            raise ValueError(f"the game is already over in the start position {start!r}")
        self.width = width
        self.shares_results = shares_results
        self.root = Node(start, position, None)
        self._by_task_id: dict[str, Node] = {self.root.task_id: self.root}
        self._by_key: dict[int, list[Node]] = {position.key(): [self.root]}  # copies
        self.answered_from_memory: list[Node] = []  # V3: needed tasks given a known answer
        if not self._decide_by_rules(self.root):
            self._expand(self.root, depth)

    # --- Building -------------------------------------------------------------------------

    def _decide_by_rules(self, node: Node) -> bool:
        position = node.position
        if position.can_win_now():
            node.outcome, node.by_rules = Outcome.WIN, True
        elif position.is_full():
            node.outcome, node.by_rules = Outcome.DRAW, True
        return node.outcome is not None

    def _add_children(self, node: Node) -> list[Node]:
        """Gives `node` one child per playable column; children the rules decide are marked."""
        for col in range(self.width):
            if node.position.can_play(col):
                child = Node(node.moves + str(col + 1), node.position.play(col), node)
                node.children.append(child)
                self._by_task_id[child.task_id] = child
                self._by_key.setdefault(child.position.key(), []).append(child)
                self._decide_by_rules(child)
        return node.children

    def _expand(self, node: Node, levels: int) -> None:
        """Expands an undecided node `levels` moves deep."""
        if levels == 0:
            return
        for child in self._add_children(node):
            if child.outcome is None:
                self._expand(child, levels - 1)
        # Children are built (and decided where possible) before their parent is looked at, so
        # the parent is only ever judged on its full list of moves. For example, if every move
        # lets the opponent win at once, the position is a loss without solving anything.
        self._decide_from_children(node)

    # --- Looking things up ----------------------------------------------------------------

    def node(self, task_id: str) -> Node:
        return self._by_task_id[task_id]

    def nodes(self, start: Node | None = None) -> Iterator[Node]:
        """Every node from `start` (the root by default) down, in move-string order."""
        stack = [start or self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def tasks(self) -> list[Node]:
        """Leaves still to be solved and still needed, in a fixed order (by move string)."""
        return [n for n in self.nodes() if n.is_open_task()]

    def same_position(self, node: Node) -> list[Node]:
        """Every node that is the same position as `node`, `node` included."""
        return self._by_key[node.position.key()]

    def copies(self) -> dict[int, list[Node]]:
        """Tasks that are the same position, grouped by key; only groups of two or more."""
        groups = {
            key: [n for n in nodes if n.is_open_task()] for key, nodes in self._by_key.items()
        }
        return {key: tasks for key, tasks in groups.items() if len(tasks) > 1}

    @property
    def result(self) -> Outcome | None:
        """The start position's result, once known."""
        return self.root.outcome

    # --- Answers coming in ----------------------------------------------------------------

    def record(self, task_id: str, outcome: Outcome) -> list[Node]:
        """Stores a task's answer and works it up the tree. Returns every needed node decided by
        it: the task and the nodes above it, then (V3) its copies and the nodes above them. Empty
        if the answer was no longer needed or already known."""
        node = self.node(task_id)
        if not node.is_leaf():
            raise ValueError(f"{task_id} was split; record answers for its children instead")
        if node.outcome is not None:
            if node.outcome != outcome:
                raise ValueError(f"{task_id}: answer {outcome} contradicts {node.outcome}")
            return []
        return self._settle(node, outcome)

    def _settle(self, node: Node, outcome: Outcome, from_memory: bool = False) -> list[Node]:
        """Gives an undecided node its outcome, works it up the tree and (V3) passes every
        decision on to the copies. Returns the needed nodes decided."""
        needed = node.is_needed()
        node.outcome = outcome
        if not needed:
            if not self.shares_results:
                return []
            # It changes nothing here, but a copy of it, or of a node above it that it decides
            # (below the decided node that made it unneeded), may still be needed elsewhere.
            return self._pass_on([node, *self._work_up(node.parent)])
        if from_memory and node.is_leaf():
            self.answered_from_memory.append(node)
        decided = [node, *self._work_up(node.parent)]
        return decided + self._pass_on(decided)

    def _pass_on(self, decided: list[Node]) -> list[Node]:
        """V3: decides the undecided copies of each decided node the same way. Returns the needed
        nodes that this decides in turn."""
        if not self.shares_results:
            return []
        more: list[Node] = []
        for node in decided:
            assert node.outcome is not None
            for copy in self.same_position(node):
                if copy.outcome is None:
                    more += self._settle(copy, node.outcome, from_memory=True)
                elif copy.outcome != node.outcome:
                    raise ValueError(
                        f"{node.task_id} is {node.outcome}, but the same position "
                        f"{copy.task_id} is {copy.outcome}"
                    )
        return more

    @staticmethod
    def _decide_from_children(node: Node) -> bool:
        """Decides an undecided node from its children if they allow it. True if it is now
        decided."""
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
        new tasks, and the nodes decided straight away: children decided by the rules (or, in V3,
        known from memory) can decide the split node itself (for example when every move loses
        at once), and that decision is worked up the tree like an answer."""
        node = self.node(task_id)
        if not node.is_open_task():
            raise ValueError(f"{task_id} cannot be split: it is not a needed, unanswered task")
        self._add_children(node)
        decided: list[Node] = []
        if self.shares_results:  # children whose position is already decided elsewhere
            for child in node.children:
                known = next((c.outcome for c in self.same_position(child) if c.outcome), None)
                if child.outcome is None and known is not None:
                    decided += self._settle(child, known, from_memory=True)
        by_rules = self._work_up(node)  # children decided by the rules
        decided += by_rules + self._pass_on(by_rules)
        new_tasks = [child for child in node.children if child.is_needed()]
        return new_tasks, decided
