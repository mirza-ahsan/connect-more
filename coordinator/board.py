"""A small Connect Four board for the coordinator: enough to expand the task tree, spot wins and
recognise the same position reached by different move orders. The search itself is done by the
C++ solver.

The layout is the same as the C++ board (solver/include/c4/position.hpp), so keys agree: each
column uses height + 1 bits (the cells from the bottom up, then one spare bit that is always
empty), `mask` holds every stone and `current` the stones of the player to move. Columns are
0..width-1 here and 1..width in move strings.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    width: int
    height: int
    mask: int = 0
    current: int = 0
    moves: int = 0

    @classmethod
    def from_moves(cls, moves: str, width: int = 7, height: int = 6) -> "Position":
        """The position after a move string such as "4453". Raises ValueError for a character
        that is not a column, a full column, or a move after the game was already won."""
        if not (1 <= width <= 9 and height >= 1 and width * (height + 1) <= 64):
            raise ValueError(f"unsupported board {width}x{height}")
        position = cls(width, height)
        for i, char in enumerate(moves):
            col = ord(char) - ord("1")
            if not 0 <= col < width:
                raise ValueError(f"{char!r} is not a column from 1 to {width} (move {i + 1})")
            if not position.can_play(col):
                raise ValueError(f"column {col + 1} is full (move {i + 1} of {moves!r})")
            if position.game_over():
                raise ValueError(f"move {i + 1} of {moves!r} comes after the game was won")
            position = position.play(col)
        return position

    def _bottom(self, col: int) -> int:
        return 1 << (col * (self.height + 1))

    def _column(self, col: int) -> int:
        return ((1 << self.height) - 1) << (col * (self.height + 1))

    def can_play(self, col: int) -> bool:
        top = 1 << (col * (self.height + 1) + self.height - 1)
        return self.mask & top == 0

    def play(self, col: int) -> "Position":
        """The position after the player to move drops a stone in a playable column."""
        landing = (self.mask + self._bottom(col)) & self._column(col)
        return Position(
            self.width, self.height, self.mask | landing, self.current ^ self.mask, self.moves + 1
        )

    def _has_four(self, stones: int) -> bool:
        for step in (1, self.height + 1, self.height, self.height + 2):
            pairs = stones & (stones >> step)
            if pairs & (pairs >> (2 * step)):
                return True
        return False

    def wins_now(self, col: int) -> bool:
        """True if the player to move gets four in a row by playing this (playable) column."""
        landing = (self.mask + self._bottom(col)) & self._column(col)
        return self._has_four(self.current | landing)

    def can_win_now(self) -> bool:
        return any(self.can_play(c) and self.wins_now(c) for c in range(self.width))

    def game_over(self) -> bool:
        """True if the player who just moved has four in a row."""
        return self._has_four(self.current ^ self.mask)

    def is_full(self) -> bool:
        return self.moves == self.width * self.height

    def key(self) -> int:
        """Identifies the position: equal for the same stones, whatever the move order."""
        return self.current + self.mask
