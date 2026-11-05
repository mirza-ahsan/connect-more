"""Tests for V0: coordinator/calibration.py and the FixedSplit strategy."""

import random
from pathlib import Path

import pytest

from common.messages import Outcome
from coordinator.calibration import (
    DEFAULT_COUNT,
    DEFAULT_FILE,
    Calibration,
    CalibrationError,
    Position,
    load_positions,
)
from coordinator.strategies import FixedSplit, weighted_split
from coordinator.tree import GameTree
from tests.helpers import fake_hello as hello

# --- Loading the calibration set --------------------------------------------------------------


def test_load_positions(tmp_path: Path) -> None:
    path = tmp_path / "set.txt"
    path.write_text("4453 -2\n44 3\n- 0\nnot a line\n1234 1\n")
    assert load_positions(path, 3) == (
        Position("4453", Outcome.LOSS),
        Position("44", Outcome.WIN),
        Position("", Outcome.DRAW),  # "-" is the empty board
    )
    with pytest.raises(ValueError, match="only 4 positions"):
        load_positions(path, 5)
    for count in (0, -1):  # not "the whole file"
        with pytest.raises(ValueError, match="at least 1"):
            load_positions(path, count)


def test_default_set_is_there() -> None:
    positions = load_positions(DEFAULT_FILE, DEFAULT_COUNT)
    assert len(positions) == DEFAULT_COUNT == 200
    assert len({p.moves for p in positions}) == DEFAULT_COUNT


# --- Dealing tasks out by speed ---------------------------------------------------------------


def test_weighted_split_order() -> None:
    shares = weighted_split(list("ABCDEFGH"), {"a": 3.0, "b": 1.0})
    assert shares == {"a": list("ABDEFH"), "b": list("CG")}  # a, a, b, a, a, a, b, a
    assert weighted_split([1, 2, 3, 4], {"x": 1.0, "y": 1.0}) == {"x": [1, 3], "y": [2, 4]}


@pytest.mark.parametrize("seed", range(5))
def test_weighted_split_is_proportional_at_every_point(seed: int) -> None:
    rng = random.Random(seed)
    weights = {"fast": rng.uniform(1, 10), "slow": rng.uniform(1, 10)}
    items = list(range(200))
    shares = weighted_split(items, weights)
    assert sorted(shares["fast"] + shares["slow"]) == items  # every item dealt exactly once
    fraction = weights["fast"] / sum(weights.values())
    for k in range(1, len(items) + 1):  # every prefix is as close to the ratio as whole items allow
        dealt = sum(1 for i in shares["fast"] if i < k)
        assert abs(dealt - k * fraction) < 1


def test_weighted_split_refuses_bad_weights() -> None:
    for weights in ({}, {"a": 0.0}, {"a": 1.0, "b": -1.0}):
        with pytest.raises(ValueError):
            weighted_split([1, 2], weights)


# --- One calibration round --------------------------------------------------------------------


def test_calibration_round() -> None:
    positions = (Position("44", Outcome.WIN), Position("4453", Outcome.LOSS))
    calibration = Calibration(positions, ["fast", "slow"])

    # Each worker gets the whole set, under ids of its own.
    fast = [calibration.next_task("fast", now=10.0), calibration.next_task("fast", now=10.1)]
    slow = [calibration.next_task("slow", now=10.0), calibration.next_task("slow", now=10.2)]
    assert [t[1] for t in fast if t] == [t[1] for t in slow if t] == ["44", "4453"]
    ids = [t[0] for t in fast + slow if t]
    assert len(set(ids)) == 4 and all(calibration.owns(i) for i in ids)
    assert not calibration.owns("p44")
    assert calibration.next_task("fast", now=11.0) is None

    with pytest.raises(CalibrationError):
        calibration.report()  # not finished yet

    calibration.record(ids[0], Outcome.WIN, 100, now=10.5)
    calibration.record(ids[1], Outcome.LOSS, 300, now=11.0)  # fast: 10.0 -> 11.0
    calibration.record(ids[2], Outcome.WIN, 100, now=12.0)
    assert not calibration.done()
    calibration.record(ids[3], Outcome.LOSS, 300, now=14.0)  # slow: 10.0 -> 14.0
    assert calibration.done()

    report = calibration.report()
    assert report["fast"] == {
        "seconds": pytest.approx(1.0),  # 10.0 -> 11.0
        "speed": pytest.approx(2.0),  # 2 positions in 1 s
        "positions": 400,
        "share": pytest.approx(0.8),
    }
    assert report["slow"]["seconds"] == pytest.approx(4.0)
    assert report["slow"]["speed"] == pytest.approx(0.5)
    assert report["slow"]["share"] == pytest.approx(0.2)


def test_wrong_calibration_answer_is_refused() -> None:
    calibration = Calibration((Position("44", Outcome.WIN),), ["w"])
    task = calibration.next_task("w", now=0.0)
    assert task is not None
    with pytest.raises(CalibrationError, match="expected win"):
        calibration.record(task[0], Outcome.LOSS, 10, now=1.0)


def test_empty_calibration_set() -> None:
    with pytest.raises(ValueError):
        Calibration((), ["w"])


# --- The V0 strategy --------------------------------------------------------------------------


def test_fixed_split_deals_by_speed_and_never_moves_work() -> None:
    tree = GameTree("", 1)  # 7 tasks: p1 .. p7
    split = FixedSplit()
    split.start(tree, [hello("fast"), hello("slow")], {"fast": 3.0, "slow": 1.0})
    assert split.shares() == {"fast": 5, "slow": 2}  # 7 tasks at 3:1, in tree order

    fast = [split.next_task("fast") for _ in range(6)]
    assert [t.task_id if t else None for t in fast] == ["p1", "p2", "p4", "p5", "p6", None]
    assert [split.next_task("slow"), split.next_task("slow")] == [tree.node("p3"), tree.node("p7")]
    assert split.next_task("slow") is None  # slow has nothing left, even though fast had more

    # A task that comes back goes to the front of its owner's share, not to whoever is free.
    split.add_tasks([tree.node("p3")])
    assert split.next_task("fast") is None
    assert split.next_task("slow") is tree.node("p3")


def test_fixed_split_skips_tasks_no_longer_needed() -> None:
    tree = GameTree("", 1)
    split = FixedSplit()
    split.start(tree, [hello("a"), hello("b")], {"a": 1.0, "b": 1.0})
    tree.record("p2", Outcome.LOSS)  # decides the start position: nothing else is needed
    assert split.next_task("a") is None and split.next_task("b") is None
