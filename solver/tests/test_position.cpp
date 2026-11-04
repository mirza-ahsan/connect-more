// Unit tests for the board (c4/position.hpp). Returns 0 when every check passes.
//
// 1. Hand-made positions: wins in every direction, a full-board draw, bad move strings, keys.
// 2. Random games compared move by move with a simple grid board written separately below.
// 3. Pascal Pons's test positions: every line must load, and "can win right now" must match
//    the scores in the file. None of his positions has a winning move, so this part only catches
//    false wins; real wins are checked by parts 1 and 2.

#include "c4/position.hpp"

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace
{

constexpr int W = Board::WIDTH;
constexpr int H = Board::HEIGHT;

int failures = 0;

void check(bool ok, const std::string& what)
{
    if (!ok)
    {
        std::cerr << "FAIL: " << what << '\n';
        failures++;
    }
}

Board fromMoves(const std::string& moves)
{
    Board board;
    check(board.updateBoard(moves), "valid move string rejected: \"" + moves + "\"");
    return board;
}

// The move string character for a column 0..W-1.
char columnChar(int col)
{
    return static_cast<char>('1' + col);
}

// A simple board kept as a grid, used to check Board. Shares no code with it.
class GridBoard
{
private:
    std::array<std::array<int, H>, W> cells{};  // 0 empty, 1 first player, 2 second player
    std::array<int, W> heights{};
    int moves = 0;

    int toMove() const
    {
        return moves % 2 == 0 ? 1 : 2;
    }

    // Stones of `player` in a line from (col, row) in direction (dc, dr), not counting (col, row).
    int countFrom(int col, int row, int dc, int dr, int player) const
    {
        int count = 0;
        col += dc;
        row += dr;
        while (col >= 0 && col < W && row >= 0 && row < H && cells[col][row] == player)
        {
            count++;
            col += dc;
            row += dr;
        }
        return count;
    }

public:
    bool canPlay(int col) const
    {
        return heights[col] < H;
    }

    // True if a stone of `player` in the empty cell (col, row) would make four in a row,
    // whether or not the cell can be played yet.
    bool completesFour(int col, int row, int player) const
    {
        const int directions[4][2] = {{0, 1}, {1, 0}, {1, 1}, {1, -1}};
        for (const auto& d : directions)
        {
            int line =
                1 + countFrom(col, row, d[0], d[1], player) + countFrom(col, row, -d[0], -d[1], player);
            if (line >= 4)
            {
                return true;
            }
        }
        return false;
    }

    bool winsNow(int col) const
    {
        return completesFour(col, heights[col], toMove());
    }

    bool canWinNext() const
    {
        for (int col = 0; col < W; col++)
        {
            if (canPlay(col) && winsNow(col))
            {
                return true;
            }
        }
        return false;
    }

    // True if playing this column does not let the opponent win straight after.
    bool isSafe(int col) const
    {
        GridBoard after = *this;
        after.play(col);
        return !after.canWinNext();
    }

    // Empty cells (at any height) where the player to move would complete four after playing col.
    int moveScore(int col) const
    {
        const int player = toMove();
        GridBoard after = *this;
        after.cells[col][after.heights[col]] = player;
        after.heights[col]++;
        int count = 0;
        for (int c = 0; c < W; c++)
        {
            for (int r = 0; r < H; r++)
            {
                if (after.cells[c][r] == 0 && after.completesFour(c, r, player))
                {
                    count++;
                }
            }
        }
        return count;
    }

    int height(int col) const
    {
        return heights[col];
    }

    void play(int col)
    {
        cells[col][heights[col]] = toMove();
        heights[col]++;
        moves++;
    }

    int moveCount() const
    {
        return moves;
    }

    // The whole position as two numbers (first player's cells, second player's cells),
    // numbered col * H + row. Used to compare positions, not to play.
    std::pair<std::uint64_t, std::uint64_t> id() const
    {
        std::uint64_t first = 0;
        std::uint64_t second = 0;
        for (int col = 0; col < W; col++)
        {
            for (int row = 0; row < H; row++)
            {
                std::uint64_t bit = std::uint64_t{1} << (col * H + row);
                if (cells[col][row] == 1)
                {
                    first |= bit;
                }
                else if (cells[col][row] == 2)
                {
                    second |= bit;
                }
            }
        }
        return {first, second};
    }
};

void testEmptyBoard()
{
    Board board;
    check(board.moveCount() == 0, "empty board has 0 moves");
    for (int col = 0; col < W; col++)
    {
        check(board.canPlay(col), "empty board: every column playable");
        check(!board.winsNow(col), "empty board: no move wins");
    }
    check(fromMoves("").moveCount() == 0, "empty move string gives 0 moves");
    check(fromMoves("").key() == board.key(), "empty move string gives the empty board");
}

// Positions written for the standard board; they need at least 7 columns and 6 rows.
void testHandMadeWins()
{
    if (W < 7 || H < 6)
    {
        return;
    }

    // Vertical: first player has three in column 1.
    check(fromMoves("121212").winsNow(0), "vertical win");
    check(!fromMoves("121212").winsNow(1), "vertical: other column does not win");

    // Horizontal, completed at either end and in a gap.
    check(fromMoves("445566").winsNow(6), "horizontal win, right end");
    check(fromMoves("445566").winsNow(2), "horizontal win, left end");
    check(fromMoves("112244").winsNow(2), "horizontal win, filling a gap");
    check(!fromMoves("4455").winsNow(5), "horizontal: only three in a row");

    // Diagonals, one in each direction (the second is the mirror image of the first).
    check(fromMoves("1223343441").winsNow(3), "diagonal win, rising to the right");
    check(fromMoves("7665545447").winsNow(3), "diagonal win, rising to the left");
    check(!fromMoves("1223343441").winsNow(4), "diagonal: wrong column does not win");

    // Only the stones of the player to move count: second player has three on the bottom row,
    // first player is to move.
    check(!fromMoves("121314").winsNow(4), "opponent's three in a row does not count");
    check(fromMoves("121314").winsNow(0), "player to move still wins vertically");

    // The winning move itself is accepted, for either player.
    check(fromMoves("1212121").moveCount() == 7, "first player's winning move is accepted");
    check(fromMoves("1212121").gameOver(), "a winning move ends the game");

    // Safe moves: one threat must be blocked; two threats cannot both be blocked.
    check(fromMoves("11223").nonLosingMoves() == Board::bottomBit(3),
          "single threat: only the block is safe");
    check(fromMoves("44556").nonLosingMoves() == 0, "two threats: every move loses");
    check(fromMoves("44556").canWinNext() == false && fromMoves("4455").canWinNext() == false,
          "canWinNext false without three in a row");
    check(fromMoves("445566").canWinNext(), "canWinNext finds a winning move");
    check(!fromMoves("121212").gameOver(), "game not over before the winning move");
    check(fromMoves("31212121").moveCount() == 8, "second player's winning move is accepted");
}

// A whole game that fills the standard board without anyone getting four in a row.
void testFullBoardDraw()
{
    if (W != 7 || H != 6)
    {
        return;
    }

    const std::string draw = "242222246341543663717511153741653355766774";
    Board board = fromMoves(draw);
    check(board.moveCount() == W * H, "draw: every cell filled");
    for (int col = 0; col < W; col++)
    {
        check(!board.canPlay(col), "draw: no column playable on a full board");
    }

    // Before the last stone, the only move left must not win.
    Board before = fromMoves(draw.substr(0, draw.size() - 1));
    const int last = draw.back() - '1';
    check(before.canPlay(last) && !before.winsNow(last), "draw: the last stone does not win");
}

void testBadMoveStrings()
{
    const std::string full_column(static_cast<std::size_t>(H), '1');

    Board full = fromMoves(full_column);
    check(!full.canPlay(0), "full column cannot be played");
    for (int col = 1; col < W; col++)
    {
        check(full.canPlay(col), "other columns stay playable next to a full column");
    }

    const std::vector<std::string> bad = {
        full_column + "1",              // one stone too many in a column
        "0",                            // columns start at 1
        std::string(1, columnChar(W)),  // one past the last column
        "a",                            // not a digit
        "4 4",                          // space
        "4-4",                          // other characters
        "12121212",                     // a move after the first player has already won
        "312121212",                    // a move after the second player has already won
    };
    for (const std::string& moves : bad)
    {
        // A failed move string must leave the board exactly as it was, and say why.
        Board board;
        const std::uint64_t key_before = board.key();
        std::string error;
        check(!board.updateBoard(moves, &error), "bad move string accepted: \"" + moves + "\"");
        check(!error.empty(), "no reason given for bad move string \"" + moves + "\"");
        check(board.key() == key_before && board.moveCount() == 0,
              "bad move string \"" + moves + "\" changed the board");
    }

    // A good move string clears an old error.
    Board board;
    std::string error = "old error";
    check(board.updateBoard("1", &error) && error.empty(), "good move string leaves an error message");
}

void testKeys()
{
    if (W < 3 || H < 3)
    {
        return;
    }

    check(fromMoves("1213").key() == fromMoves("1312").key(), "same position in a different order, same key");
    check(fromMoves("12").key() != fromMoves("21").key(), "stones swapped, different key");
    check(fromMoves("1").key() != Board().key(), "one stone changes the key");
    check(fromMoves("11").key() != fromMoves("1").key(), "second stone changes the key");

    // Mirror images are different positions, so they get different keys.
    const std::string last_column(1, columnChar(W - 1));
    check(fromMoves("1").key() != fromMoves(last_column).key(), "mirror image, different key");

    // Playing a move string onto an existing board continues from it.
    Board joined = fromMoves("12");
    check(joined.updateBoard("33"), "move string onto an existing board rejected");
    check(joined.key() == fromMoves("1233").key() && joined.moveCount() == 4,
          "two move strings in a row differ from one");

    // Copies are independent.
    Board a = fromMoves("11");
    Board b = a;
    b.play(0);
    check(a.moveCount() == 2 && b.moveCount() == 3, "a copy is a separate board");
    check(a.key() != b.key(), "playing on a copy does not change the original");
}

// Plays random games on Board and GridBoard side by side and compares them at every step.
// Half the games avoid winning moves, so they run long and fill whole columns.
void testAgainstGridBoard()
{
    std::mt19937 rng(20261104);
    std::map<std::uint64_t, std::pair<std::uint64_t, std::uint64_t>> key_to_position;
    std::map<std::pair<std::uint64_t, std::uint64_t>, std::uint64_t> position_to_key;
    int mismatches = 0;
    int lost_positions = 0;        // every move lets the opponent win
    int restricted_positions = 0;  // some moves, but not all, let the opponent win
    const int games = 20000;

    for (int game = 0; game < games && mismatches < 20; game++)
    {
        const bool avoid_wins = game % 2 == 1;
        Board board;
        GridBoard grid;
        std::string moves;

        while (true)
        {
            auto report = [&](const std::string& what)
            {
                check(false, what + " after \"" + moves + "\"");
                mismatches++;
            };

            if (board.moveCount() != grid.moveCount())
            {
                report("move count differs");
            }

            // The key must identify the position exactly: one key per position, one position per key.
            const auto position = grid.id();
            const std::uint64_t key = board.key();
            auto [k, new_key] = key_to_position.emplace(key, position);
            if (!new_key && k->second != position)
            {
                report("two different positions share a key");
            }
            auto [p, new_position] = position_to_key.emplace(position, key);
            if (!new_position && p->second != key)
            {
                report("one position has two different keys");
            }

            std::vector<int> playable;
            std::vector<int> safe;
            for (int col = 0; col < W; col++)
            {
                if (board.canPlay(col) != grid.canPlay(col))
                {
                    report("canPlay differs in column " + std::to_string(col));
                }
                if (!grid.canPlay(col))
                {
                    continue;
                }
                if (board.winsNow(col) != grid.winsNow(col))
                {
                    report("winsNow differs in column " + std::to_string(col));
                }
                playable.push_back(col);
                if (!grid.winsNow(col))
                {
                    safe.push_back(col);
                }
            }

            // Playable cells, winning chances and moves that don't lose at once (Pons parts 9, 10).
            const std::uint64_t possible = board.possible();
            for (int col = 0; col < W; col++)
            {
                const std::uint64_t expected =
                    grid.canPlay(col) ? Board::bottomBit(col) << grid.height(col) : 0;
                if ((possible & Board::columnMask(col)) != expected)
                {
                    report("possible() wrong in column " + std::to_string(col));
                }
            }
            if (board.canWinNext() != grid.canWinNext())
            {
                report("canWinNext differs");
            }
            if (!grid.canWinNext())
            {
                const std::uint64_t non_losing = board.nonLosingMoves();
                if ((non_losing & ~possible) != 0)
                {
                    report("nonLosingMoves gives a cell that cannot be played");
                }
                if (non_losing == 0)
                {
                    lost_positions++;
                }
                else if (non_losing != possible)
                {
                    restricted_positions++;
                }

                for (int col = 0; col < W; col++)
                {
                    if (!grid.canPlay(col))
                    {
                        continue;
                    }
                    if (((non_losing & Board::columnMask(col)) != 0) != grid.isSafe(col))
                    {
                        report("nonLosingMoves differs in column " + std::to_string(col));
                    }
                    if (board.moveScore(possible & Board::columnMask(col)) != grid.moveScore(col))
                    {
                        report("moveScore differs in column " + std::to_string(col));
                    }
                }
            }

            if (playable.empty())
            {
                check(board.moveCount() == W * H, "no playable column before the board is full");
                break;
            }

            const std::vector<int>& choices = avoid_wins && !safe.empty() ? safe : playable;
            const int col = choices[std::uniform_int_distribution<std::size_t>(0, choices.size() - 1)(rng)];
            const bool wins = grid.winsNow(col);

            board.play(col);
            grid.play(col);
            moves += columnChar(col);

            if (wins)
            {
                // The winning move is accepted, anything after it is not.
                Board replay;
                check(replay.updateBoard(moves), "game ending in a win rejected: \"" + moves + "\"");
                check(replay.key() == board.key(), "move string and play() give different boards");
                for (int next = 0; next < W; next++)
                {
                    if (grid.canPlay(next))
                    {
                        Board after;
                        const std::string extra = moves + columnChar(next);
                        check(!after.updateBoard(extra), "move after a win accepted: \"" + extra + "\"");
                        break;
                    }
                }
                break;
            }
        }

        if (avoid_wins)
        {
            Board replay;
            check(replay.updateBoard(moves), "long game rejected: \"" + moves + "\"");
            check(replay.key() == board.key(), "move string and play() give different boards");
        }
    }

    check(key_to_position.size() > 1000, "random games reached enough different positions");
    if (W >= 4 && H >= 4)
    {
        // Make sure both special cases of nonLosingMoves were actually compared.
        check(lost_positions > 100 && restricted_positions > 100,
              "random games reached too few positions with losing moves");
    }
}

// Pons's files: "moves score" per line. The player to move can win with their next stone exactly
// when the score is (W * H + 1 - moves) / 2.
void testPonsFiles()
{
    const std::filesystem::path folder = std::filesystem::path(__FILE__).parent_path() / "data" / C4_VARIANT;
    if (!std::filesystem::is_directory(folder))
    {
        check(W != 7 || H != 6, "Pons test files not found in " + folder.string());
        return;
    }

    int files = 0;
    int positions = 0;
    for (const auto& entry : std::filesystem::directory_iterator(folder))
    {
        if (entry.path().extension() != ".txt")
        {
            continue;
        }
        files++;
        const std::string name = entry.path().filename().string();
        std::ifstream in(entry.path());
        std::string line;
        int line_number = 0;
        int file_positions = 0;
        while (std::getline(in, line))
        {
            line_number++;
            if (line.empty())
            {
                continue;
            }

            // Exactly two fields: the moves and the score.
            std::istringstream fields(line);
            std::string moves;
            int score = 0;
            std::string extra;
            if (!(fields >> moves >> score) || (fields >> extra))
            {
                check(false, name + " line " + std::to_string(line_number) + " is not \"moves score\"");
                continue;
            }
            file_positions++;

            Board board;
            std::string error;
            if (!board.updateBoard(moves == "-" ? "" : moves, &error))  // "-" is the empty board
            {
                check(false, name + ": position rejected: " + error);
                continue;
            }
            check(board.moveCount() == (moves == "-" ? 0 : static_cast<int>(moves.size())),
                  "move count wrong for " + moves);

            bool can_win = false;
            for (int col = 0; col < W; col++)
            {
                if (board.canPlay(col) && board.winsNow(col))
                {
                    can_win = true;
                }
            }
            const bool file_says_win_now = score == (W * H + 1 - board.moveCount()) / 2;
            check(can_win == file_says_win_now,
                  "winning move check disagrees with Pons's score for " + moves);
        }
        if (W == 7 && H == 6)
        {
            check(file_positions == 1000, name + " should have 1000 positions");
        }
        positions += file_positions;
    }

    if (W == 7 && H == 6)
    {
        check(files == 6, "expected Pons's 6 test files in " + folder.string());
    }
    check(files > 0 && positions > 0, "no Pons test positions read from " + folder.string());
    std::cout << "Pons files: " << files << " files, " << positions << " positions\n";
}

}  // namespace

int main()
{
    std::cout << "Board " << W << "x" << H << '\n';

    testEmptyBoard();
    testHandMadeWins();
    testFullBoardDraw();
    testBadMoveStrings();
    testKeys();
    testAgainstGridBoard();
    testPonsFiles();

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}
