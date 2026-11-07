// c4solve: the Connect Four solver program.
//
//   c4solve --verify <file> [--threads N] [--table-mb N]
//       Solves every position in a test file ("moves score" per line) and compares win/draw/loss
//       with the sign of the score, using the task pool: all threads share one table, as in a real
//       run. Exits with 1 if any answer is wrong or any line is invalid.
//
//   c4solve --single <moves> [--table-mb N]
//       Solves one position on one thread with an empty table, and prints the positions searched.
//       This is the "unsplit" reference count for the experiments.
//
//   c4solve [--threads N] [--table-mb N]
//       Service mode, used by the worker in every experiment run: reads commands from standard
//       input and answers on standard output, as described in docs/protocol.md.
//
// A move string of "-" stands for the empty board. Every run first prints the build information,
// board size, table size and thread count, so each result can be traced to the exact code.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "c4/position.hpp"
#include "c4/protocol.hpp"
#include "c4/solver.hpp"
#include "c4/task_pool.hpp"
#include "c4/transposition_table.hpp"
#include "cm/build_info.hpp"

namespace
{

using Clock = std::chrono::steady_clock;

struct Options
{
    std::string mode = "service";  // "service", "verify" or "single"
    std::string argument;          // the test file or the move string; empty in service mode
    int threads = 0;               // 0 means one per logical processor
    std::size_t table_mb = CM_DEFAULT_TT_MB;
};

void printUsage()
{
    std::cerr << "usage:\n"
                 "  c4solve --verify <file> [--threads N] [--table-mb N]\n"
                 "  c4solve --single <moves> [--table-mb N]\n"
                 "  c4solve [--threads N] [--table-mb N]     (service mode, see docs/protocol.md)\n"
                 "A move string of \"-\" is the empty board.\n";
}

std::optional<Options> parseArgs(int argc, char** argv)
{
    Options options;
    for (int i = 1; i < argc; i++)
    {
        const std::string arg = argv[i];
        if (i + 1 >= argc)
        {
            std::cerr << "missing value after " << arg << '\n';
            return std::nullopt;
        }
        const std::string value = argv[++i];

        if (arg == "--verify" || arg == "--single")
        {
            if (options.mode != "service")
            {
                std::cerr << "give only one of --verify and --single\n";
                return std::nullopt;
            }
            options.mode = arg.substr(2);
            options.argument = value;
        }
        else if (arg == "--threads" || arg == "--table-mb")
        {
            int number = 0;
            try
            {
                std::size_t used = 0;
                number = std::stoi(value, &used);
                if (used != value.size() || number < 1)
                {
                    throw std::invalid_argument(value);
                }
            }
            catch (const std::exception&)
            {
                std::cerr << arg << " needs a whole number of at least 1, not \"" << value << "\"\n";
                return std::nullopt;
            }
            if (arg == "--threads")
            {
                options.threads = number;
            }
            else
            {
                options.table_mb = static_cast<std::size_t>(number);
            }
        }
        else
        {
            std::cerr << "unknown option " << arg << '\n';
            return std::nullopt;
        }
    }

    if (options.mode == "single" && options.threads > 1)
    {
        std::cerr << "--single always uses one thread\n";
        return std::nullopt;
    }
    return options;
}

std::string movesOf(const std::string& text)
{
    return text == "-" ? "" : text;
}

const char* outcomeName(Outcome outcome)
{
    switch (outcome)
    {
        case Outcome::Win:
            return "win";
        case Outcome::Draw:
            return "draw";
        case Outcome::Loss:
            return "loss";
    }
    return "?";
}

Outcome outcomeOfScore(int score)
{
    return score > 0 ? Outcome::Win : (score < 0 ? Outcome::Loss : Outcome::Draw);
}

double secondsSince(Clock::time_point start)
{
    return std::chrono::duration<double>(Clock::now() - start).count();
}

void printHeader(const Options& options, const TranspositionTable& table, int threads)
{
    namespace info = cm::build_info;
    std::cout << "# c4solve " << info::project_version << '\n'
              << "# commit " << info::git_commit << ", dirty " << info::git_dirty << '\n'
              << "# build " << info::build_type << ", " << info::compiler_id << ' ' << info::compiler_version
              << ", thread model " << info::thread_model << ", arch " << info::arch << ", flags \""
              << info::config_flags << "\", static " << info::static_link << ", lto " << info::lto
              << ", configured " << info::configured_at << '\n'
              << "# board " << C4_VARIANT << '\n'
              << "# table " << options.table_mb << " MB, " << table.size() << " slots\n"
              << "# threads " << threads << '\n'
              << "# mode " << options.mode << (options.argument.empty() ? "" : " ") << options.argument
              << '\n'
              << std::flush;
}

int runService(const Options& options, TranspositionTable& table, int threads)
{
    auto read_line = [](std::string& line) { return static_cast<bool>(std::getline(std::cin, line)); };
    auto write_line = [](const std::string& line) { std::cout << line << '\n' << std::flush; };
    return protocol::serve(table, threads, options.table_mb, read_line, write_line);
}

struct TestLine
{
    int line_number;
    std::string moves;
    Board board;
    Outcome expected;
};

int runVerify(const Options& options, TranspositionTable& table, int threads)
{
    std::ifstream in(options.argument);
    if (!in)
    {
        std::cout << "error cannot open " << options.argument << '\n';
        return 2;
    }

    // Read and check every line before solving anything.
    std::vector<TestLine> lines;
    int bad_lines = 0;
    std::string text;
    int line_number = 0;
    while (std::getline(in, text))
    {
        line_number++;
        if (!text.empty() && text.back() == '\r')
        {
            text.pop_back();
        }
        if (text.empty())
        {
            continue;
        }

        std::istringstream fields(text);
        std::string moves;
        int score = 0;
        std::string extra;
        if (!(fields >> moves >> score) || (fields >> extra))
        {
            std::cout << "error line " << line_number << ": expected \"moves score\", got \"" << text
                      << "\"\n";
            bad_lines++;
            continue;
        }

        TestLine line{line_number, moves, Board(), outcomeOfScore(score)};
        std::string error;
        if (!line.board.updateBoard(movesOf(moves), &error))
        {
            std::cout << "error line " << line_number << ": " << error << '\n';
            bad_lines++;
            continue;
        }
        if (line.board.gameOver())
        {
            std::cout << "error line " << line_number << ": the game is already over in " << moves << '\n';
            bad_lines++;
            continue;
        }
        lines.push_back(line);
    }

    // Every line is a task in the task pool, as in a real run. The task id is the line's index.
    std::vector<double> seconds(lines.size());
    std::vector<std::uint64_t> searched(lines.size());
    std::size_t done = 0;
    int wrong = 0;
    std::mutex output;  // guards the counters above and both output streams

    auto on_event = [&](const TaskEvent& event)
    {
        const std::lock_guard<std::mutex> lock(output);
        if (event.type == TaskEvent::Type::Progress)
        {
            // Progress goes to the error stream, so the log stays clean.
            std::cerr << "  " << done << " / " << lines.size() << " solved, " << event.positions
                      << " positions searched, " << std::fixed << std::setprecision(1) << event.time
                      << " s\n";
            return;
        }
        if (event.type == TaskEvent::Type::Started)
        {
            return;
        }

        const std::size_t i = std::stoul(event.task_id);
        seconds[i] = event.seconds;
        searched[i] = event.positions;
        done++;
        if (event.type != TaskEvent::Type::Result || event.outcome != lines[i].expected)
        {
            wrong++;
            std::cout << "wrong line " << lines[i].line_number << ": " << lines[i].moves << " expected "
                      << outcomeName(lines[i].expected) << ", got "
                      << (event.type == TaskEvent::Type::Result ? outcomeName(event.outcome) : "no result")
                      << '\n';
        }
    };

    const Clock::time_point start = Clock::now();
    {
        TaskPool pool(table, threads, on_event, std::chrono::seconds(10));
        for (std::size_t i = 0; i < lines.size(); i++)
        {
            pool.submit(std::to_string(i), lines[i].board);
        }
        pool.waitUntilIdle();
    }
    const double total_seconds = secondsSince(start);

    double sum_seconds = 0;
    double sum_searched = 0;
    for (std::size_t i = 0; i < lines.size(); i++)
    {
        sum_seconds += seconds[i];
        sum_searched += static_cast<double>(searched[i]);
    }
    const double count = lines.empty() ? 1 : static_cast<double>(lines.size());

    std::cout << "verify " << options.argument << ": " << lines.size() << " positions, " << wrong
              << " wrong, " << bad_lines << " bad lines, mean " << std::fixed << std::setprecision(0)
              << sum_searched / count << " positions searched, mean " << std::setprecision(6)
              << sum_seconds / count << " s per position, total " << std::setprecision(3) << total_seconds
              << " s\n";

    return (wrong == 0 && bad_lines == 0 && !lines.empty()) ? 0 : 1;
}

int runSingle(const Options& options, TranspositionTable& table)
{
    Board board;
    std::string error;
    if (!board.updateBoard(movesOf(options.argument), &error))
    {
        std::cout << "error " << error << '\n';
        return 2;
    }
    if (board.gameOver())
    {
        std::cout << "error the game is already over\n";
        return 2;
    }

    Solver solver(table);
    const Clock::time_point start = Clock::now();
    const SolveResult result = solver.solve(board);
    const double seconds = secondsSince(start);

    std::cout << "result " << options.argument << ' ' << outcomeName(result.outcome) << ", "
              << result.positions << " positions searched, " << std::fixed << std::setprecision(6) << seconds
              << " s\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv)
{
    const std::optional<Options> parsed = parseArgs(argc, argv);
    if (!parsed)
    {
        printUsage();
        return 2;
    }
    const Options& options = *parsed;

    int threads = 1;
    if (options.mode != "single")
    {
        threads =
            options.threads > 0 ? options.threads : static_cast<int>(std::thread::hardware_concurrency());
        threads = std::max(threads, 1);
    }

    try
    {
        // Allocated before anything is timed: a 1 GB table takes a moment.
        TranspositionTable table(options.table_mb);
        printHeader(options, table, threads);
        if (options.mode == "verify")
        {
            return runVerify(options, table, threads);
        }
        if (options.mode == "single")
        {
            return runSingle(options, table);
        }
        return runService(options, table, threads);
    }
    catch (const std::exception& e)
    {
        std::cout << protocol::errorLine("", e.what()) << '\n';
        return 2;
    }
}
