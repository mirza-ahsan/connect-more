"""Tests for coordinator/tree.py: building the task tree and combining answers."""

import random
import re
import subprocess
from collections.abc import Callable
from functools import cache

import pytest

from common.messages import TASK_ID
from coordinator.board import Position
from coordinator.tree import GameTree, Node, Outcome
from tests.helpers import SOLVERS, needs_solver

W, D, L = Outcome.WIN, Outcome.DRAW, Outcome.LOSS


# --- Building ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("depth", "orders", "positions"), [(2, 49, 49), (3, 343, 238), (4, 2401, 1120)]
)
def test_tasks_and_copies_from_the_empty_board(depth: int, orders: int, positions: int) -> None:
    """The numbers given in the Phase 2 methodology."""
    tree = GameTree("", depth)
    tasks = tree.tasks()
    assert len(tasks) == orders
    assert len({task.position.key() for task in tasks}) == positions
    copies = tree.copies()
    assert sum(len(group) for group in copies.values()) - len(copies) == orders - positions
    assert all(task.depth == depth for task in tasks)


def test_task_ids() -> None:
    tree = GameTree("44", 3)
    ids = [task.task_id for task in tree.tasks()]
    assert len(set(ids)) == len(ids)
    assert all(TASK_ID.fullmatch(task_id) for task_id in ids)
    assert ids[0] == "p44111"
    assert tree.node("p44111").moves == "44111"
    assert GameTree("", 0).root.task_id == "p"


def test_mirror_images_are_different_tasks() -> None:
    tree = GameTree("", 2)
    assert tree.node("p17").position.key() != tree.node("p71").position.key()


def test_bad_start_positions() -> None:
    with pytest.raises(ValueError):
        GameTree("1212121", 2)  # already won
    with pytest.raises(ValueError):
        GameTree("8", 2)
    with pytest.raises(ValueError):
        GameTree("", -1)


def test_decided_by_the_rules_while_building() -> None:
    # The player to move can win at once: nothing to solve.
    tree = GameTree("121212", 3)
    assert tree.result == W and tree.root.by_rules and tree.tasks() == []

    # The second player faces two threats: every move lets the first player win at once.
    tree = GameTree("44556", 1)
    assert tree.result == L and not tree.root.by_rules and tree.tasks() == []
    assert all(child.outcome == W and child.by_rules for child in tree.root.children)

    # One move left, and it fills the board without a win.
    tree = GameTree("24222224634154366371751115374165335576677", 1)
    assert tree.result == D and tree.tasks() == []


# --- Combining answers ------------------------------------------------------------------------


def test_one_losing_reply_decides_a_win() -> None:
    tree = GameTree("", 1)
    decided = tree.record("p4", L)
    assert [node.task_id for node in decided] == ["p4", "p"]
    assert tree.result == W
    assert tree.tasks() == []  # nothing else is needed
    assert len(tree.open_tasks(tree.root)) == 6  # ... and these can be cancelled
    assert tree.record("p3", W) == []  # a late answer changes nothing


def test_loss_and_draw_need_every_answer() -> None:
    tree = GameTree("", 1)
    for col in "123456":
        assert tree.record("p" + col, W) == [tree.node("p" + col)]
    assert tree.result is None
    assert [node.task_id for node in tree.record("p7", W)] == ["p7", "p"]
    assert tree.result == L

    tree = GameTree("", 1)
    for col in "12345":
        tree.record("p" + col, W)
    tree.record("p6", D)
    assert tree.result is None
    tree.record("p7", W)
    assert tree.result == D


def test_bad_records() -> None:
    tree = GameTree("", 1)
    tree.record("p1", W)
    assert tree.record("p1", W) == []  # the same answer twice is harmless
    with pytest.raises(ValueError, match="contradicts"):
        tree.record("p1", L)
    with pytest.raises(KeyError):
        tree.record("p8", W)
    tree.split("p2")
    with pytest.raises(ValueError, match="was split"):
        tree.record("p2", W)


def test_split() -> None:
    tree = GameTree("", 1)
    new_tasks, decided = tree.split("p4")
    assert [t.task_id for t in new_tasks] == [f"p4{c}" for c in "1234567"]
    assert decided == []
    assert "p4" not in [t.task_id for t in tree.tasks()]
    assert len(tree.tasks()) == 6 + 7

    # Answers for the children decide the split task, then its parent.
    tree.record("p41", W)
    decided = tree.record("p42", W)
    assert tree.node("p4").outcome is None
    for col in "34567":
        decided = tree.record("p4" + col, W)
    assert [n.task_id for n in decided] == ["p47", "p4", "p"]  # p4 is a loss, so p is a win
    assert tree.result == W

    with pytest.raises(ValueError, match="cannot be split"):
        tree.split("p3")  # no longer needed


def test_split_decided_at_once_by_the_rules() -> None:
    tree = GameTree("4455", 1)
    task = "p44556"  # the second player to move, facing two threats
    assert task in [t.task_id for t in tree.tasks()]
    new_tasks, decided = tree.split(task)
    assert new_tasks == []
    assert [n.task_id for n in decided] == [task, "p4455"]
    assert tree.result == W


# --- Whole trees against a separate solver ----------------------------------------------------


@cache
def solve(position: Position) -> Outcome:
    """An exact solver written only for these tests: plain negamax with memory, fine for 4x4.
    Positions are frozen dataclasses, so equal positions share one cache entry."""
    if position.can_win_now():
        return W
    if position.is_full():
        return D
    moves = range(position.width)
    replies = [solve(position.play(c)) for c in moves if position.can_play(c)]
    return W if L in replies else D if D in replies else L


def check_tree(
    tree: GameTree, solve: Callable[[Position], Outcome], rng: random.Random, split_share: float
) -> None:
    """Answers the tasks in a random order, splitting some first, and checks every decision."""
    pending = tree.tasks()
    rng.shuffle(pending)
    while pending:
        task = pending.pop()
        if not task.is_needed():
            continue
        if task.depth < 5 and rng.random() < split_share:
            new_tasks, _ = tree.split(task.task_id)
            pending.extend(new_tasks)
            rng.shuffle(pending)
            continue
        tree.record(task.task_id, solve(task.position))
    for node in tree.nodes():
        if node.outcome is not None:
            assert node.outcome == solve(node.position), node.task_id


@pytest.mark.parametrize("split_share", [0.0, 0.3])
@pytest.mark.parametrize("depth", [1, 3, 5])
def test_4x4_combines_to_tromps_result(depth: int, split_share: float) -> None:
    assert solve(Position(4, 4)) == D  # Tromp's table: 4x4 is a draw
    tree = GameTree("", depth, 4, 4)
    check_tree(tree, solve, random.Random(depth), split_share)
    assert tree.result == D


@needs_solver("6x4")
def test_6x4_with_the_real_solver() -> None:
    """Tasks answered by the C++ solver combine to Tromp's result: 6x4 is a second-player win."""
    tree = GameTree("", 2, 6, 4)

    def real_solve(task: Node) -> Outcome:
        out = subprocess.run(
            [str(SOLVERS["6x4"]), "--single", task.moves, "--table-mb", "16"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        match = re.search(r"^result \S+ (win|draw|loss),", out, re.MULTILINE)
        assert match, out
        return Outcome(match.group(1))

    for task in tree.tasks():
        if task.is_needed():
            tree.record(task.task_id, real_solve(task))
    assert tree.result == L
