#pragma once
#include <chrono>
#include <cstddef>
#include <functional>
#include <optional>
#include <string>

#include "c4/task_pool.hpp"
#include "c4/transposition_table.hpp"

// The text lines between the worker and the solver: Part A of docs/protocol.md (version 1).
// Keep this file and the document in step.

namespace protocol
{

constexpr int VERSION = 1;

struct Command
{
    enum class Type
    {
        Solve,
        Stop,
        Cancel,
        Quit
    };

    Type type;
    std::string task_id;  // empty for Quit
    std::string moves;    // Solve only; "" for the empty board ("-" on the line)
};

// A command, or why the line is not one. `task_id` is filled in when the line names one, so the
// error can be reported for that task.
struct ParsedLine
{
    std::optional<Command> command;
    std::string error;
    std::string task_id;
};

// True for 1 to 64 characters, each a letter, digit, '-', '_' or '.'.
bool validTaskId(const std::string& id);

// Reads one command line. Extra spaces and a trailing carriage return are allowed. An empty line
// gives neither a command nor an error, and is ignored.
ParsedLine parseCommand(const std::string& line);

// Answer lines, without the newline.
std::string readyLine(int threads, std::size_t table_mb);
std::string eventLine(const TaskEvent& event);
std::string errorLine(const std::string& task_id, const std::string& message);
std::string byeLine(double time);

// Runs a whole session: sends `ready`, then reads commands with `read_line` until `quit` or the
// end of the input, then stops all tasks and sends `bye`. `write_line` is only ever called by one
// thread at a time. Returns 0.
int serve(TranspositionTable& table, int threads, std::size_t table_mb,
          const std::function<bool(std::string&)>& read_line,
          const std::function<void(const std::string&)>& write_line,
          std::chrono::milliseconds progress_interval = std::chrono::seconds(1));

}  // namespace protocol
