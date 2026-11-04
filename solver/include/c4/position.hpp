#pragma once
#include <bit>
#include <cstdint>
#include <string>

// Connect Four board stored as two 64-bit numbers (the layout from Pascal Pons's tutorial).
// Each column uses HEIGHT + 1 bits: HEIGHT cells from the bottom up, then one spare bit that is
// always empty. Columns are 0..WIDTH-1 everywhere except in move strings, which use 1..WIDTH.
//
// The small functions are defined in this header so the search can inline them.
class Board
{
public:
    static constexpr int WIDTH = C4_WIDTH;
    static constexpr int HEIGHT = C4_HEIGHT;
    static_assert(WIDTH * (HEIGHT + 1) <= 64, "board does not fit in 64 bits");
    static_assert(3 * (HEIGHT + 2) < 64, "board too tall for the win checks");
    static_assert(WIDTH <= 9, "move strings use one digit per column");

    // Bit of the bottom cell of a column.
    static constexpr std::uint64_t bottomBit(int col)
    {
        return std::uint64_t{1} << (col * (HEIGHT + 1));
    }

    // All real cells of a column.
    static constexpr std::uint64_t columnMask(int col)
    {
        return ((std::uint64_t{1} << HEIGHT) - 1) << (col * (HEIGHT + 1));
    }

private:
    std::uint64_t mask;     // every stone
    std::uint64_t current;  // stones of the player to move
    int move_count;
    bool game_over;  // set by updateBoard when a move string contains a winning move

    // Bit of the top real cell of a column (the cell just under the spare bit).
    static constexpr std::uint64_t topBit(int col)
    {
        return std::uint64_t{1} << (col * (HEIGHT + 1) + HEIGHT - 1);
    }

    // The bottom cell of every column.
    static constexpr std::uint64_t bottomRow()
    {
        std::uint64_t row = 0;
        for (int col = 0; col < WIDTH; col++)
        {
            row |= bottomBit(col);
        }
        return row;
    }

    // Every real cell of the board (no spare bits).
    static constexpr std::uint64_t boardMask()
    {
        return bottomRow() * ((std::uint64_t{1} << HEIGHT) - 1);
    }

    static bool hasFour(std::uint64_t stones);

    // Empty cells where `stones` would complete four in a row, whether or not the cell can be
    // played yet (Pons part 9).
    static std::uint64_t winningCells(std::uint64_t stones, std::uint64_t filled);

public:
    Board();

    // Plays a move string such as "4453". Returns false if a character is not a column, a column
    // is full, or a move comes after the game was already won. On failure the board is left
    // unchanged and, if `error` is given, it gets the reason.
    bool updateBoard(const std::string& move_string, std::string* error = nullptr);

    bool canPlay(int col) const;

    // Drops a stone for the player to move. The column must be playable; nothing is checked here.
    void play(int col);

    // Plays a move given as the single bit of the cell where it lands (one bit of possible()).
    void playMove(std::uint64_t move);

    // True if the player to move gets four in a row by playing this (playable) column.
    bool winsNow(int col) const;

    // True if the player to move can win with their next stone, in any column.
    bool canWinNext() const;

    // The cell where a stone would land in each column that is not full.
    std::uint64_t possible() const;

    // The playable cells that do not let the opponent win straight after (Pons part 9).
    // 0 means every move loses. Only valid when the player to move cannot win at once.
    std::uint64_t nonLosingMoves() const;

    // How many empty cells would complete four for the player to move after playing `move`
    // (Pons part 10). Used to try the most promising moves first.
    int moveScore(std::uint64_t move) const;

    // A number that identifies the position: no two different positions share a key.
    std::uint64_t key() const;

    int moveCount() const;

    // True if updateBoard read a move string that ends the game with a win.
    bool gameOver() const;
};

// True if the stones contain four in a row in any direction.
inline bool Board::hasFour(std::uint64_t stones)
{
    // Distance between neighbouring cells: vertical, horizontal, and the two diagonals.
    const int steps[4] = {1, HEIGHT + 1, HEIGHT, HEIGHT + 2};

    for (int step : steps)
    {
        // A pair is a stone with another stone one step away; two pairs two steps apart make four.
        std::uint64_t pairs = stones & (stones >> step);
        if ((pairs & (pairs >> (2 * step))) != 0)
        {
            return true;
        }
    }
    return false;
}

inline std::uint64_t Board::winningCells(std::uint64_t stones, std::uint64_t filled)
{
    // Vertical: three stones directly below the cell.
    std::uint64_t cells = (stones << 1) & (stones << 2) & (stones << 3);

    // Other directions: for each step, the cell is the missing end or one of the two missing
    // middle cells of a line of four.
    const int steps[3] = {HEIGHT + 1, HEIGHT, HEIGHT + 2};
    for (int step : steps)
    {
        std::uint64_t pair = (stones << step) & (stones << (2 * step));
        cells |= pair & (stones << (3 * step));
        cells |= pair & (stones >> step);
        pair = (stones >> step) & (stones >> (2 * step));
        cells |= pair & (stones << step);
        cells |= pair & (stones >> (3 * step));
    }

    constexpr std::uint64_t board = boardMask();
    return cells & (board ^ filled);
}

inline bool Board::canPlay(int col) const
{
    return (mask & topBit(col)) == 0;
}

inline void Board::playMove(std::uint64_t move)
{
    // The other player moves next, so their stones become current. Done before adding the new stone.
    current ^= mask;
    mask |= move;
    move_count++;
}

inline void Board::play(int col)
{
    // Adding the bottom bit carries up through the column's stones and lands on its first empty cell.
    playMove((mask + bottomBit(col)) & columnMask(col));
}

inline bool Board::winsNow(int col) const
{
    // The cell where a stone dropped in this column would land.
    std::uint64_t landing = (mask + bottomBit(col)) & columnMask(col);
    return hasFour(current | landing);
}

inline std::uint64_t Board::possible() const
{
    constexpr std::uint64_t bottom = bottomRow();
    constexpr std::uint64_t board = boardMask();
    return (mask + bottom) & board;
}

inline bool Board::canWinNext() const
{
    return (winningCells(current, mask) & possible()) != 0;
}

inline std::uint64_t Board::nonLosingMoves() const
{
    std::uint64_t moves = possible();
    const std::uint64_t opponent_wins = winningCells(current ^ mask, mask);

    // If the opponent could win next turn somewhere, that cell must be blocked. Two such cells
    // cannot both be blocked, so every move loses.
    const std::uint64_t forced = moves & opponent_wins;
    if (forced != 0)
    {
        if ((forced & (forced - 1)) != 0)
        {
            return 0;
        }
        moves = forced;
    }

    // Never play directly under a cell where the opponent would win.
    return moves & ~(opponent_wins >> 1);
}

inline int Board::moveScore(std::uint64_t move) const
{
    return std::popcount(winningCells(current | move, mask));
}

inline std::uint64_t Board::key() const
{
    return current + mask;
}

inline int Board::moveCount() const
{
    return move_count;
}

inline bool Board::gameOver() const
{
    return game_over;
}
