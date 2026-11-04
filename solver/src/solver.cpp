#include "c4/solver.hpp"

#include <array>

#include "c4/move_sorter.hpp"

namespace
{

constexpr int CELLS = Board::WIDTH * Board::HEIGHT;

// Centre columns first, then outwards (Pons part 5). On 7 columns: 3, 2, 4, 1, 5, 0, 6.
constexpr std::array<int, Board::WIDTH> COLUMN_ORDER = []
{
    std::array<int, Board::WIDTH> order{};
    for (int i = 0; i < Board::WIDTH; i++)
    {
        order[i] = Board::WIDTH / 2 + (1 - 2 * (i % 2)) * (i + 1) / 2;
    }
    return order;
}();

// Table values (Pons part 12). Upper bounds use 1..MAX_SCORE - MIN_SCORE + 1, lower bounds use the
// numbers above that, so the two kinds can be told apart. 0 means nothing stored.
constexpr int UPPER_OFFSET = 1 - Solver::MIN_SCORE;
constexpr int LOWER_OFFSET = Solver::MAX_SCORE - 2 * Solver::MIN_SCORE + 2;
constexpr int LAST_UPPER = Solver::MAX_SCORE - Solver::MIN_SCORE + 1;

}  // namespace

Solver::Solver(TranspositionTable& shared_table) : table(shared_table) {}

SolveResult Solver::solve(const Board& board, const std::atomic<bool>* stop)
{
    const std::uint64_t before = positionsSearched();
    stop_flag = stop;
    stopped = false;
    auto finish = [&](bool was_stopped, Outcome outcome) -> SolveResult
    { return {was_stopped, outcome, positionsSearched() - before}; };

    if (board.canWinNext())
    {
        countPosition();
        return finish(false, Outcome::Win);
    }

    // Weak solving with null windows (Pons part 8): first "is the score above 0?", then, if not,
    // "is it above -1?". A result at or below the window is an upper bound, above it a lower bound.
    int result = negamax(board, 0, 1);
    if (stopped)
    {
        return finish(true, Outcome::Draw);
    }
    if (result > 0)
    {
        return finish(false, Outcome::Win);
    }
    if (result < 0)
    {
        return finish(false, Outcome::Loss);
    }

    result = negamax(board, -1, 0);
    if (stopped)
    {
        return finish(true, Outcome::Draw);
    }
    return finish(false, result < 0 ? Outcome::Loss : Outcome::Draw);
}

int Solver::negamax(const Board& board, int alpha, int beta)
{
    countPosition();

    // Stopped from outside: return at once. Every caller checks `stopped` before storing anything.
    if (stop_flag != nullptr && stop_flag->load(std::memory_order_relaxed))
    {
        stopped = true;
        return 0;
    }

    const int moves = board.moveCount();

    // Moves that do not let the opponent win next turn (Pons part 9). None means we lose.
    const std::uint64_t safe = board.nonLosingMoves();
    if (safe == 0)
    {
        return -(CELLS - moves) / 2;
    }

    // Nobody can win in the last two moves, so the game is a draw.
    if (moves >= CELLS - 2)
    {
        return 0;
    }

    // The opponent cannot win next turn, so our score is at least this.
    const int min = -(CELLS - 2 - moves) / 2;
    if (alpha < min)
    {
        alpha = min;
        if (alpha >= beta)
        {
            return alpha;
        }
    }

    // We cannot win with this move, so our score is at most this.
    const int max = (CELLS - 1 - moves) / 2;
    if (beta > max)
    {
        beta = max;
        if (alpha >= beta)
        {
            return beta;
        }
    }

    // What an earlier search learned about this position (Pons parts 7 and 12).
    const std::uint64_t key = board.key();
    if (const int value = table.get(key); value != 0)
    {
        if (value > LAST_UPPER)
        {
            const int lower = value - LOWER_OFFSET;
            if (alpha < lower)
            {
                alpha = lower;
                if (alpha >= beta)
                {
                    return alpha;
                }
            }
        }
        else
        {
            const int upper = value - UPPER_OFFSET;
            if (beta > upper)
            {
                beta = upper;
                if (alpha >= beta)
                {
                    return beta;
                }
            }
        }
    }

    // Try the moves that create the most winning cells first; ties keep centre-first order
    // (Pons parts 5 and 10). Added in reverse, because the sorter returns the last of equals first.
    MoveSorter sorter;
    for (int i = Board::WIDTH - 1; i >= 0; i--)
    {
        const std::uint64_t move = safe & Board::columnMask(COLUMN_ORDER[i]);
        if (move != 0)
        {
            sorter.add(move, board.moveScore(move));
        }
    }

    while (const std::uint64_t move = sorter.next())
    {
        Board child = board;
        child.playMove(move);
        const int score = -negamax(child, -beta, -alpha);

        if (stopped)
        {
            return 0;
        }
        if (score >= beta)
        {
            table.put(key, static_cast<std::uint8_t>(score + LOWER_OFFSET));
            return score;
        }
        if (score > alpha)
        {
            alpha = score;
        }
    }

    table.put(key, static_cast<std::uint8_t>(alpha + UPPER_OFFSET));
    return alpha;
}
