#pragma once
#include <cstdint>

#include "c4/position.hpp"

// Holds up to one move per column, each with a score, and hands them back highest score first
// (Pons part 10). Among equal scores the move added last comes out first, so the solver adds
// moves in the reverse of the order it wants for ties.
class MoveSorter
{
private:
    struct Entry
    {
        std::uint64_t move;
        int score;
    };

    Entry entries[Board::WIDTH];
    int size = 0;

public:
    void add(std::uint64_t move, int score)
    {
        // Insertion sort: shift every entry with a higher score one place up.
        int pos = size++;
        while (pos > 0 && entries[pos - 1].score > score)
        {
            entries[pos] = entries[pos - 1];
            pos--;
        }
        entries[pos] = {move, score};
    }

    // The best move left, or 0 when there are none.
    std::uint64_t next()
    {
        if (size == 0)
        {
            return 0;
        }
        return entries[--size].move;
    }
};
