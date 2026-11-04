#pragma once
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
    static_assert(2 * (HEIGHT + 2) < 64, "board too tall for the win check");
    static_assert(WIDTH <= 9, "move strings use one digit per column");

private:
    std::uint64_t mask;     // every stone
    std::uint64_t current;  // stones of the player to move
    int move_count;
    bool game_over;  // set by updateBoard when a move string contains a winning move

    // Bit of the bottom cell of a column.
    static constexpr std::uint64_t bottomBit(int col)
    {
        return std::uint64_t{1} << (col * (HEIGHT + 1));
    }

    // Bit of the top real cell of a column (the cell just under the spare bit).
    static constexpr std::uint64_t topBit(int col)
    {
        return std::uint64_t{1} << (col * (HEIGHT + 1) + HEIGHT - 1);
    }

    // All real cells of a column.
    static constexpr std::uint64_t columnMask(int col)
    {
        return ((std::uint64_t{1} << HEIGHT) - 1) << (col * (HEIGHT + 1));
    }

    static bool hasFour(std::uint64_t stones);

public:
    Board();

    // Plays a move string such as "4453". Returns false if a character is not a column, a column
    // is full, or a move comes after the game was already won. On failure the board is left
    // unchanged and, if `error` is given, it gets the reason.
    bool updateBoard(const std::string& move_string, std::string* error = nullptr);

    bool canPlay(int col) const;

    // Drops a stone for the player to move. The column must be playable; nothing is checked here.
    void play(int col);

    // True if the player to move gets four in a row by playing this (playable) column.
    bool winsNow(int col) const;

    // A number that identifies the position: no two different positions share a key.
    std::uint64_t key() const;

    int moveCount() const;
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

inline bool Board::canPlay(int col) const
{
    return (mask & topBit(col)) == 0;
}

inline void Board::play(int col)
{
    // The other player moves next, so their stones become current. Done before adding the new stone.
    current ^= mask;
    // Adding the bottom bit carries up through the column's stones and lands on its first empty cell.
    mask |= mask + bottomBit(col);
    move_count++;
}

inline bool Board::winsNow(int col) const
{
    // The cell where a stone dropped in this column would land.
    std::uint64_t landing = (mask + bottomBit(col)) & columnMask(col);
    return hasFour(current | landing);
}

inline std::uint64_t Board::key() const
{
    return current + mask;
}

inline int Board::moveCount() const
{
    return move_count;
}
