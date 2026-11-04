#include "c4/protocol.hpp"

#include <cstdio>
#include <mutex>
#include <sstream>
#include <vector>

namespace protocol
{

namespace
{

std::vector<std::string> wordsOf(const std::string& line)
{
    std::istringstream in(line);
    std::vector<std::string> words;
    std::string word;
    while (in >> word)
    {
        words.push_back(word);
    }
    return words;
}

std::string outcomeWord(Outcome outcome)
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

// Seconds with 6 decimals (microseconds), whatever the stream's locale.
std::string secondsText(double seconds)
{
    char text[32];
    std::snprintf(text, sizeof text, "%.6f", seconds);
    return text;
}

}  // namespace

bool validTaskId(const std::string& id)
{
    if (id.empty() || id.size() > 64)
    {
        return false;
    }
    for (char c : id)
    {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                        c == '-' || c == '_' || c == '.';
        if (!ok)
        {
            return false;
        }
    }
    return true;
}

ParsedLine parseCommand(const std::string& line)
{
    const std::vector<std::string> words = wordsOf(line);
    ParsedLine parsed;
    if (words.empty())
    {
        return parsed;
    }

    const std::string& name = words[0];
    Command command{};
    std::size_t expected_words = 0;
    if (name == "solve")
    {
        command.type = Command::Type::Solve;
        expected_words = 3;
    }
    else if (name == "stop")
    {
        command.type = Command::Type::Stop;
        expected_words = 2;
    }
    else if (name == "cancel")
    {
        command.type = Command::Type::Cancel;
        expected_words = 2;
    }
    else if (name == "quit")
    {
        command.type = Command::Type::Quit;
        expected_words = 1;
    }
    else
    {
        parsed.error = "unknown command \"" + name + "\"";
        return parsed;
    }

    if (expected_words > 1 && words.size() > 1 && validTaskId(words[1]))
    {
        parsed.task_id = words[1];
    }
    if (words.size() != expected_words)
    {
        const char* usage[] = {"solve <id> <moves>", "stop <id>", "cancel <id>", "quit"};
        parsed.error = std::string("expected: ") + usage[static_cast<int>(command.type)];
        return parsed;
    }
    if (expected_words > 1 && parsed.task_id.empty())
    {
        parsed.error = "invalid task id \"" + words[1] + "\" (1 to 64 letters, digits, '-', '_' or '.')";
        return parsed;
    }

    command.task_id = parsed.task_id;
    if (command.type == Command::Type::Solve)
    {
        command.moves = words[2] == "-" ? "" : words[2];
    }
    parsed.command = command;
    return parsed;
}

std::string readyLine(int threads, std::size_t table_mb)
{
    return "ready " + std::to_string(VERSION) + " " + C4_VARIANT + " " + std::to_string(threads) + " " +
           std::to_string(table_mb);
}

std::string eventLine(const TaskEvent& event)
{
    const std::string id = event.task_id;
    const std::string positions = std::to_string(event.positions);
    const std::string seconds = secondsText(event.seconds);
    const std::string time = secondsText(event.time);
    switch (event.type)
    {
        case TaskEvent::Type::Started:
            return "started " + id + " " + std::to_string(event.thread) + " " + time;
        case TaskEvent::Type::Result:
            return "result " + id + " " + outcomeWord(event.outcome) + " " + positions + " " + seconds + " " +
                   time;
        case TaskEvent::Type::Stopped:
            return "stopped " + id + " " + positions + " " + seconds + " " + time;
        case TaskEvent::Type::Cancelled:
            return "cancelled " + id + " " + positions + " " + seconds + " " + time;
        case TaskEvent::Type::Progress:
            return "progress " + positions + " " + time;
    }
    return errorLine(id, "unknown event");
}

std::string errorLine(const std::string& task_id, const std::string& message)
{
    // A message never contains a line break, so it can't be mistaken for another answer.
    std::string text = message;
    for (char& c : text)
    {
        if (c == '\n' || c == '\r')
        {
            c = ' ';
        }
    }
    return "error " + (task_id.empty() ? std::string("-") : task_id) + " " + text;
}

std::string byeLine(double time)
{
    return "bye " + secondsText(time);
}

int serve(TranspositionTable& table, int threads, std::size_t table_mb,
          const std::function<bool(std::string&)>& read_line,
          const std::function<void(const std::string&)>& write_line,
          std::chrono::milliseconds progress_interval)
{
    // Every answer goes through here, so lines from different threads never mix.
    std::mutex output;
    auto send = [&](const std::string& line)
    {
        const std::lock_guard<std::mutex> lock(output);
        write_line(line);
    };

    TaskPool pool(table, threads, [&](const TaskEvent& event) { send(eventLine(event)); }, progress_interval);
    send(readyLine(pool.threadCount(), table_mb));

    std::string line;
    while (read_line(line))
    {
        const ParsedLine parsed = parseCommand(line);
        if (!parsed.command)
        {
            if (!parsed.error.empty())
            {
                send(errorLine(parsed.task_id, parsed.error));
            }
            continue;
        }

        const Command& command = *parsed.command;
        if (command.type == Command::Type::Quit)
        {
            break;
        }
        if (command.type == Command::Type::Solve)
        {
            Board board;
            std::string error;
            if (!board.updateBoard(command.moves, &error))
            {
                send(errorLine(command.task_id, "invalid position: " + error));
            }
            else if (board.gameOver())
            {
                send(errorLine(command.task_id, "the game is already over"));
            }
            else if (!pool.submit(command.task_id, board))
            {
                send(errorLine(command.task_id, "task id already in use"));
            }
        }
        else
        {
            const bool known = command.type == Command::Type::Stop ? pool.stop(command.task_id)
                                                                   : pool.cancel(command.task_id);
            if (!known)
            {
                send(errorLine(command.task_id, "no task with this id"));
            }
        }
    }

    // `quit`, or the worker closed our input: end every task, then say goodbye.
    pool.shutdown();
    send(byeLine(pool.secondsSinceStart()));
    return 0;
}

}  // namespace protocol
