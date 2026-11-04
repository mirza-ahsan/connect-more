#include "c4/position.hpp"

Board::Board()
{
    mask = 0;
    current = 0;
    move_count = 0;
    game_over = false;
}

bool Board::updateBoard(const std::string& move_string, std::string* error)
{
    // Work on a copy, so a bad move string leaves this board unchanged.
    Board next = *this;

    for (std::size_t i = 0; i < move_string.size(); i++)
    {
        const char c = move_string[i];
        std::string problem;

        // Move strings use columns 1..WIDTH; inside the board they are 0..WIDTH-1.
        const int col = c - '1';

        if (next.game_over)
        {
            problem = "move after the game was already won";
        }
        else if (col < 0 || col >= WIDTH)
        {
            problem = std::string("'") + c + "' is not a column from 1 to " + std::to_string(WIDTH);
        }
        else if (!next.canPlay(col))
        {
            problem = "column " + std::to_string(col + 1) + " is full";
        }

        if (!problem.empty())
        {
            if (error != nullptr)
            {
                *error = problem + " (move " + std::to_string(i + 1) + " of \"" + move_string + "\")";
            }
            return false;
        }

        if (next.winsNow(col))
        {
            next.game_over = true;
        }
        next.play(col);
    }

    *this = next;
    if (error != nullptr)
    {
        error->clear();
    }
    return true;
}
