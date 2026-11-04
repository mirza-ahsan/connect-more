#pragma once
#include <atomic>
#include <cstdint>

#include "c4/position.hpp"
#include "c4/transposition_table.hpp"

enum class Outcome
{
    Loss = -1,
    Draw = 0,
    Win = 1
};

struct SolveResult
{
    bool stopped;             // the stop flag ended the search early; `outcome` is then meaningless
    Outcome outcome;          // for the player to move, with perfect play by both sides
    std::uint64_t positions;  // positions searched (search calls), also when stopped
};

// Weak Connect Four solver: win, draw or loss, following Pascal Pons's tutorial parts 4 to 12.
// Use one Solver per thread. The transposition table may be shared by many solvers at once.
class Solver
{
public:
    // Lowest and highest possible scores in Pons's scoring (a win needs at least 4 own stones).
    static constexpr int MIN_SCORE = -(Board::WIDTH * Board::HEIGHT) / 2 + 3;
    static constexpr int MAX_SCORE = (Board::WIDTH * Board::HEIGHT + 1) / 2 - 3;
    static_assert(MIN_SCORE <= -2 && MAX_SCORE >= 2, "board too small for the weak search window");
    static_assert(2 * MAX_SCORE - 2 * MIN_SCORE + 2 <= 255, "scores do not fit in a table entry");

    explicit Solver(TranspositionTable& table);

    // Finds whether the player to move wins, draws or loses. The game must not be over already.
    // If `stop` is given and becomes true, the search ends early and returns stopped = true. A
    // stopped search stores nothing in the table, because its half-finished values are wrong.
    SolveResult solve(const Board& board, const std::atomic<bool>* stop = nullptr);

private:
    TranspositionTable& table;
    std::uint64_t positions = 0;
    const std::atomic<bool>* stop_flag = nullptr;
    bool stopped = false;

    // Pons's negamax with alpha-beta: the result is exact inside (alpha, beta); otherwise it is
    // an upper bound (at most alpha) or a lower bound (at least beta).
    // The player to move must not be able to win at once.
    int negamax(const Board& board, int alpha, int beta);
};
