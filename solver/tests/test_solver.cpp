// Unit tests for the weak solver (c4/solver.hpp). Returns 0 when every check passes.
//
// 1. Positions from Pascal Pons's easier test files give the right win/draw/loss (same sign as
//    his score), with one table shared by all of them, as in a real run.
// 2. Searches stopped part-way report "stopped", and solving the same positions again with the
//    same table still gives the right answers (a stopped search must not poison the table).
// The full test files are checked by c4solve --verify; this test only takes a quick sample.

#include "c4/solver.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace
{

int failures = 0;

void check(bool ok, const std::string& what)
{
    if (!ok)
    {
        std::cerr << "FAIL: " << what << '\n';
        failures++;
    }
}

struct TestPosition
{
    std::string moves;
    int score;
};

Outcome expectedOutcome(int score)
{
    return score > 0 ? Outcome::Win : (score < 0 ? Outcome::Loss : Outcome::Draw);
}

// Up to `limit` positions from one of Pons's files, or none if the file is not there.
std::vector<TestPosition> loadPons(const std::string& name, std::size_t limit)
{
    const std::filesystem::path file =
        std::filesystem::path(__FILE__).parent_path() / "data" / C4_VARIANT / (name + ".txt");
    std::vector<TestPosition> positions;
    std::ifstream in(file);
    std::string line;
    while (positions.size() < limit && std::getline(in, line))
    {
        std::istringstream fields(line);
        TestPosition p;
        if (fields >> p.moves >> p.score)
        {
            positions.push_back(p);
        }
    }
    check(!positions.empty(), "no positions read from " + file.string());
    return positions;
}

Board boardOf(const TestPosition& p)
{
    Board board;
    std::string error;
    check(board.updateBoard(p.moves, &error), "test position rejected: " + error);
    return board;
}

std::size_t tableMb()
{
    return std::max<std::size_t>(64, TranspositionTable::minimumMegabytes());
}

void testPonsSample(TranspositionTable& table)
{
    // max_mean is about twice the mean positions searched that we measured. Far more than that
    // means part of the search (for example the table) is broken, even if the answers are right.
    struct Sample
    {
        const char* file;
        std::size_t count;
        std::uint64_t max_mean;
    };
    const Sample samples[] = {
        {"Test_L3_R1", 1000, 60},    // End-Easy
        {"Test_L2_R1", 1000, 1100},  // Middle-Easy
        {"Test_L2_R2", 100, 60000},  // Middle-Medium
        {"Test_L1_R1", 100, 60000},  // Begin-Easy
    };

    Solver solver(table);
    for (const Sample& sample : samples)
    {
        int wrong = 0;
        std::uint64_t searched = 0;
        const std::vector<TestPosition> positions = loadPons(sample.file, sample.count);
        for (const TestPosition& p : positions)
        {
            const SolveResult result = solver.solve(boardOf(p));
            searched += result.positions;
            check(!result.stopped, "search stopped without a stop flag: " + p.moves);
            check(result.positions > 0, "no positions counted: " + p.moves);
            if (result.outcome != expectedOutcome(p.score))
            {
                wrong++;
                check(false, std::string(sample.file) + ": wrong answer for " + p.moves);
            }
        }
        const std::uint64_t mean = positions.empty() ? 0 : searched / positions.size();
        std::cout << sample.file << ": " << positions.size() << " positions, " << wrong << " wrong, mean "
                  << mean << " positions searched\n";
        check(mean <= sample.max_mean,
              std::string(sample.file) + ": far more positions searched than expected");
    }
}

void testStop()
{
    TranspositionTable table(tableMb());
    Solver solver(table);

    // A stop flag that is already set ends the search at once.
    const std::atomic<bool> stop_now{true};
    const SolveResult early = solver.solve(Board(), &stop_now);
    check(early.stopped && early.positions == 1,
          "a search with the stop flag already set did not stop at once");

    // Stop searches after a random short time, then solve the same positions again with the same
    // table and no stop flag. The answers must all be right.
    const std::vector<TestPosition> positions = loadPons("Test_L2_R2", 200);  // Middle-Medium
    std::mt19937 rng(7);
    int stopped = 0;
    for (const TestPosition& p : positions)
    {
        std::atomic<bool> stop{false};
        const auto delay = std::chrono::microseconds(std::uniform_int_distribution<int>(0, 1000)(rng));
        std::thread timer(
            [&stop, delay]
            {
                std::this_thread::sleep_for(delay);
                stop = true;
            });
        const SolveResult result = solver.solve(boardOf(p), &stop);
        timer.join();

        if (result.stopped)
        {
            stopped++;
            check(result.positions > 0, "stopped search counted no positions: " + p.moves);
        }
        else
        {
            check(result.outcome == expectedOutcome(p.score),
                  "wrong answer from a search that finished: " + p.moves);
        }
    }

    int wrong = 0;
    for (const TestPosition& p : positions)
    {
        const SolveResult result = solver.solve(boardOf(p));
        check(!result.stopped, "search stopped without a stop flag: " + p.moves);
        if (result.outcome != expectedOutcome(p.score))
        {
            wrong++;
            check(false, "wrong answer after stopped searches: " + p.moves);
        }
    }

    std::cout << "stop test: " << stopped << " of " << positions.size() << " searches stopped, " << wrong
              << " wrong when solved again\n";
    check(stopped >= 20, "too few searches were stopped for the test to mean anything");
}

}  // namespace

int main()
{
    std::cout << "Solver, board " << Board::WIDTH << "x" << Board::HEIGHT << ", table " << tableMb()
              << " MB\n";

    // Pons's files only exist for the standard board.
    if (Board::WIDTH == 7 && Board::HEIGHT == 6)
    {
        TranspositionTable table(tableMb());
        testPonsSample(table);
        testStop();
    }
    else
    {
        TranspositionTable table(tableMb());
        Solver solver(table);
        const std::atomic<bool> stop_now{true};
        check(solver.solve(Board(), &stop_now).stopped,
              "a search with the stop flag already set did not stop");
    }

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}
