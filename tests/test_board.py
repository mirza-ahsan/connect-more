"""Tests for coordinator/board.py: the coordinator's small Connect Four board."""

import random

import pytest

from coordinator.board import Position


def board(moves: str, width: int = 7, height: int = 6) -> Position:
    return Position.from_moves(moves, width, height)


@pytest.mark.parametrize(
    "moves",
    ["0", "8", "a", "4 4", "1111111", "12121212"],
    ids=["column 0", "column 8", "letter", "space", "full column", "after a win"],
)
def test_bad_move_strings(moves: str) -> None:
    with pytest.raises(ValueError):
        board(moves)


def test_unsupported_board() -> None:
    with pytest.raises(ValueError):
        Position.from_moves("", 9, 7)  # 9 x 8 = 72 bits


def test_wins_in_every_direction() -> None:
    # The same positions as solver/tests/test_position.cpp (columns 0..6 here).
    assert board("121212").wins_now(0)  # vertical
    assert not board("121212").wins_now(1)
    assert board("445566").wins_now(6) and board("445566").wins_now(2)  # horizontal, both ends
    assert board("112244").wins_now(2)  # horizontal, filling a gap
    assert not board("4455").wins_now(5)
    assert board("1223343441").wins_now(3)  # diagonal rising to the right
    assert board("7665545447").wins_now(3)  # diagonal rising to the left
    assert not board("121314").wins_now(4)  # the opponent's three in a row does not count
    assert board("121314").can_win_now()


def test_game_over_and_full_board() -> None:
    assert board("1212121").game_over()
    assert not board("121212").game_over()
    draw = "242222246341543663717511153741653355766774"  # a full board with no four in a row
    assert board(draw).is_full() and not board(draw).game_over()
    assert not board(draw[:-1]).can_win_now()


def test_keys() -> None:
    assert board("1213").key() == board("1312").key()  # same stones, different order
    assert board("12").key() != board("21").key()  # stones swapped
    assert board("1").key() != board("7").key()  # mirror images are different positions
    assert board("").key() == 0


class Grid:
    """A plain grid board, written separately, to check Position against."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells = [[0] * height for _ in range(width)]
        self.heights = [0] * width
        self.moves = 0

    def wins_now(self, col: int) -> bool:
        row, player = self.heights[col], 1 + self.moves % 2
        for dc, dr in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            for sign in (1, -1):
                c, r = col + sign * dc, row + sign * dr
                while 0 <= c < self.width and 0 <= r < self.height and self.cells[c][r] == player:
                    count += 1
                    c, r = c + sign * dc, r + sign * dr
            if count >= 4:
                return True
        return False

    def play(self, col: int) -> None:
        self.cells[col][self.heights[col]] = 1 + self.moves % 2
        self.heights[col] += 1
        self.moves += 1

    def stones(self) -> tuple[tuple[int, ...], ...]:
        return tuple(tuple(column) for column in self.cells)


@pytest.mark.parametrize(("width", "height"), [(7, 6), (4, 4), (6, 4), (6, 6)])
def test_against_grid_in_random_games(width: int, height: int) -> None:
    rng = random.Random(5)
    key_of: dict[tuple[tuple[int, ...], ...], int] = {}
    stones_of: dict[int, tuple[tuple[int, ...], ...]] = {}
    for _ in range(1500):
        position, grid = Position(width, height), Grid(width, height)
        while True:
            stones = grid.stones()
            assert key_of.setdefault(stones, position.key()) == position.key()
            assert stones_of.setdefault(position.key(), stones) == stones
            playable = [c for c in range(width) if grid.heights[c] < height]
            for col in range(width):
                assert position.can_play(col) == (col in playable)
                if col in playable:
                    assert position.wins_now(col) == grid.wins_now(col)
            if not playable:
                assert position.is_full()
                break
            col = rng.choice(playable)
            wins = grid.wins_now(col)
            position, _ = position.play(col), grid.play(col)
            assert position.game_over() == wins
            if wins:
                break
