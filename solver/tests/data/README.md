# Known results for `c4solve --verify`

Each folder holds the positions with known results for one board size (width x height). The build turns every `.txt` file in a folder into a verify test for that size. Don't edit these files by hand; if a result is wrong, find out why first.

**Format:** one position per line, `moves score`.
- `moves` is a move string with columns from 1, or `-` for the empty board.
- `score` is from the point of view of the player to move. Only its sign is checked: positive means a win, 0 a draw, negative a loss.

| Folder | Files | Source |
|---|---|---|
| `7x6/` | `Test_L1_R1` … `Test_L3_R1`, 1000 positions each | Pascal Pons, "Solving Connect 4: how to build a perfect AI", test sets (blog.gamesolver.org). Scores are Pons's exact scores |
| `4x4/` | `Tromp_empty.txt`: the empty board, a draw (`0`) | John Tromp, "John's Connect Four Playground", table of game values for each width and height (tromp.github.io/c4/c4.html), read on 4 Nov 2026 |
| `6x4/` | `Tromp_empty.txt`: the empty board, a second-player win (`-1`, a loss for the player to move) | Same table |
| `6x6/` | `Tromp_empty.txt`: the empty board, a second-player win (`-1`) | Same table |

The three small sizes were chosen so that the checks cover all three outcomes: draw (4x4), loss for the first player (6x4 and 6x6), and, with 7x6, a win for the first player. Tromp's table marks "+" for a first-player win, "=" for a tie and "-" for a second-player win.

**Running them:**
- 7x6: `ctest --preset release-verify` (part of `cmake --workflow --preset release`).
- Small boards: `cmake --workflow --preset small-boards`, which builds and tests only 4x4, 6x4 and 6x6 in `build/small-boards/`.
