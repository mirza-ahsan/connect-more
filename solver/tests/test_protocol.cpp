// Unit tests for the worker <-> solver protocol (c4/protocol.hpp, docs/protocol.md part A).
// Returns 0 when every check passes.
//
// 1. Every command is read correctly; bad lines give an error, with the task id when there is one.
// 2. Every answer is written in the agreed format.
// 3. A whole session through serve(): solve, stop, cancel, errors, progress and quit, with the
//    promises from the document checked on the answers.

#include "c4/protocol.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace
{

using namespace std::chrono_literals;
using protocol::Command;

int failures = 0;

void check(bool ok, const std::string& what)
{
    if (!ok)
    {
        std::cerr << "FAIL: " << what << '\n';
        failures++;
    }
}

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

void testParsing()
{
    auto parse = protocol::parseCommand;

    auto solve = parse("solve t1 4453");
    check(solve.command && solve.command->type == Command::Type::Solve && solve.command->task_id == "t1" &&
              solve.command->moves == "4453",
          "solve not read");
    auto empty_board = parse("solve t2 -");
    check(empty_board.command && empty_board.command->moves.empty(), "\"-\" not read as the empty board");
    auto stop = parse("stop t1");
    check(stop.command && stop.command->type == Command::Type::Stop && stop.command->task_id == "t1",
          "stop not read");
    auto cancel = parse("cancel run1.t-9_x");
    check(cancel.command && cancel.command->type == Command::Type::Cancel &&
              cancel.command->task_id == "run1.t-9_x",
          "cancel not read");
    auto quit = parse("quit");
    check(quit.command && quit.command->type == Command::Type::Quit, "quit not read");

    // Extra spaces and a Windows line end are fine; an empty line is neither command nor error.
    auto spaced = parse("  solve   t3   44 \r");
    check(spaced.command && spaced.command->task_id == "t3" && spaced.command->moves == "44",
          "extra spaces or \\r not allowed");
    auto blank = parse("   ");
    check(!blank.command && blank.error.empty(), "empty line gave a command or an error");

    // Bad lines: an error, with the task id when the line has a valid one.
    struct Bad
    {
        const char* line;
        const char* id;
    };
    const Bad bad[] = {
        {"hello", ""},        {"SOLVE t1 44", ""},
        {"solve t1", "t1"},   {"solve t1 44 extra", "t1"},
        {"solve", ""},        {"stop", ""},
        {"stop t1 t2", "t1"}, {"cancel", ""},
        {"quit now", ""},     {"solve bad/id 44", ""},
        {"stop t*", ""},
    };
    for (const Bad& b : bad)
    {
        auto parsed = parse(b.line);
        check(!parsed.command && !parsed.error.empty(), std::string("bad line accepted: ") + b.line);
        check(parsed.task_id == b.id, std::string("wrong task id for the error on: ") + b.line);
    }

    check(protocol::validTaskId(std::string(64, 'a')), "64-character id refused");
    check(!protocol::validTaskId(std::string(65, 'a')), "65-character id accepted");
    check(!protocol::validTaskId(""), "empty id accepted");
}

void testFormatting()
{
    check(protocol::readyLine(20, 1024) == std::string("ready 1 ") + C4_VARIANT + " 20 1024", "ready line");

    TaskEvent started{TaskEvent::Type::Started, "t1", 3};
    started.time = 1.5;
    check(protocol::eventLine(started) == "started t1 3 1.500000",
          "started line: " + protocol::eventLine(started));

    TaskEvent result{TaskEvent::Type::Result, "t1", 3, Outcome::Loss, 31799719, 2.541, 4.0};
    check(protocol::eventLine(result) == "result t1 loss 31799719 2.541000 4.000000",
          "result line: " + protocol::eventLine(result));
    result.outcome = Outcome::Win;
    check(wordsOf(protocol::eventLine(result))[2] == "win", "win word");
    result.outcome = Outcome::Draw;
    check(wordsOf(protocol::eventLine(result))[2] == "draw", "draw word");

    TaskEvent stopped{TaskEvent::Type::Stopped, "t2", 1, Outcome::Draw, 400, 0.25, 5.0};
    check(protocol::eventLine(stopped) == "stopped t2 400 0.250000 5.000000", "stopped line");
    TaskEvent cancelled{TaskEvent::Type::Cancelled, "t3"};
    check(protocol::eventLine(cancelled) == "cancelled t3 0 0.000000 0.000000", "cancelled line");
    TaskEvent progress{TaskEvent::Type::Progress, ""};
    progress.positions = 8104577;
    progress.time = 1.0;
    check(protocol::eventLine(progress) == "progress 8104577 1.000000", "progress line");

    check(protocol::errorLine("t9", "no task with this id") == "error t9 no task with this id", "error line");
    check(protocol::errorLine("", "bad\nline") == "error - bad line",
          "error line without id or with a line break");
    check(protocol::byeLine(3.5) == "bye 3.500000", "bye line");
}

// Plays the worker's side of a session: sends lines, and can wait until the solver has answered
// something before sending the next one.
class Script
{
private:
    struct Step
    {
        std::string send;      // line to send, or empty
        std::string wait_for;  // wait until an answer starts with this, or empty
    };

    std::vector<Step> steps;
    std::size_t next = 0;
    std::mutex mutex;
    std::condition_variable answered;
    std::vector<std::string> answers;

public:
    Script& send(const std::string& line)
    {
        steps.push_back({line, ""});
        return *this;
    }

    Script& waitFor(const std::string& start)
    {
        steps.push_back({"", start});
        return *this;
    }

    // serve() calls this for each command line.
    bool readLine(std::string& line)
    {
        while (next < steps.size())
        {
            const Step& step = steps[next++];
            if (!step.wait_for.empty())
            {
                std::unique_lock<std::mutex> lock(mutex);
                const bool seen = answered.wait_for(
                    lock, 30s,
                    [&]
                    {
                        return std::any_of(answers.begin(), answers.end(), [&](const std::string& a)
                                           { return a.rfind(step.wait_for, 0) == 0; });
                    });
                check(seen, "no answer starting with \"" + step.wait_for + "\" within 30 s");
                continue;
            }
            line = step.send;
            return true;
        }
        return false;  // end of input, same as the worker closing the pipe
    }

    // serve() calls this for each answer line.
    void writeLine(const std::string& line)
    {
        {
            const std::lock_guard<std::mutex> lock(mutex);
            answers.push_back(line);
        }
        answered.notify_all();
    }

    std::vector<std::string> allAnswers()
    {
        const std::lock_guard<std::mutex> lock(mutex);
        return answers;
    }
};

void testSession()
{
    TranspositionTable table(std::max<std::size_t>(64, TranspositionTable::minimumMegabytes()));
    Script script;
    script.send("solve quick 4453")
        .waitFor("result quick")
        .send("solve quick 4453")  // an id can be used again after its final answer
        .waitFor("result quick")
        .send("")  // ignored
        .send("hello")
        .send("solve bad 448")
        .send("solve over 1212121")
        .send("stop nobody")
        .send("solve long -")  // the empty board takes minutes: it runs until stopped
        .send("solve long -")
        .send("solve later 4")  // only one thread, so this one waits
        .waitFor("started long")
        .waitFor("progress")
        .send("cancel later")
        .send("cancel later")
        .send("stop long")
        .waitFor("stopped long")
        .send("solve atquit 4")
        .waitFor("started atquit")
        .send("quit")
        .send("solve ignored 4");  // nothing after quit is read

    const int code = protocol::serve(
        table, 1, 64, [&](std::string& line) { return script.readLine(line); },
        [&](const std::string& line) { script.writeLine(line); }, 50ms);
    check(code == 0, "serve did not return 0");

    const std::vector<std::string> answers = script.allAnswers();
    check(!answers.empty() && answers.front() == std::string("ready 1 ") + C4_VARIANT + " 1 64",
          "first answer is not the ready line");
    check(!answers.empty() && wordsOf(answers.back()).size() == 2 && wordsOf(answers.back())[0] == "bye",
          "last answer is not bye");

    // Every line is one of the agreed answers, with the right number of words.
    const std::map<std::string, std::size_t> words_per_answer = {
        {"ready", 5},     {"started", 4},  {"result", 6}, {"stopped", 5},
        {"cancelled", 5}, {"progress", 3}, {"bye", 2},
    };
    std::map<std::string, std::vector<std::string>> by_task;  // answers about each task id
    std::map<std::string, int> errors;                        // error answers per id
    for (const std::string& answer : answers)
    {
        const std::vector<std::string> words = wordsOf(answer);
        check(!words.empty(), "empty answer line");
        if (words.empty())
        {
            continue;
        }
        if (words[0] == "error")
        {
            check(words.size() >= 3, "error line without a message: " + answer);
            errors[words[1]]++;
            continue;
        }
        const auto expected = words_per_answer.find(words[0]);
        check(expected != words_per_answer.end(), "unknown answer: " + answer);
        if (expected != words_per_answer.end())
        {
            check(words.size() == expected->second, "wrong number of words: " + answer);
        }
        if (words[0] != "ready" && words[0] != "progress" && words[0] != "bye" && words.size() > 1)
        {
            by_task[words[1]].push_back(words[0]);
        }
    }

    // The promises from docs/protocol.md.
    const std::vector<std::string> quick = {"started", "result", "started", "result"};
    check(by_task["quick"] == quick, "task used twice did not answer started/result twice");
    check(errors["-"] == 1, "unknown command did not give one error without an id");
    check(errors["bad"] == 1 && by_task["bad"].empty(), "invalid position did not give exactly an error");
    check(errors["over"] == 1 && by_task["over"].empty(), "finished game did not give exactly an error");
    check(errors["nobody"] == 1, "stop of an unknown task did not give an error");
    check(errors["long"] == 1, "a task id used twice at once did not give an error");
    check(by_task["long"] == std::vector<std::string>({"started", "stopped"}), "stopped task: wrong answers");
    check(by_task["later"] == std::vector<std::string>({"cancelled"}),
          "cancelled waiting task: wrong answers");
    check(errors["later"] == 1, "second cancel of the same task did not give an error");
    check(by_task["atquit"] == std::vector<std::string>({"started", "stopped"}),
          "quit did not stop the running task");
    check(by_task.count("ignored") == 0 && errors.count("ignored") == 0, "a line after quit was read");
    check(std::count_if(answers.begin(), answers.end(),
                        [](const std::string& a) { return a.rfind("progress", 0) == 0; }) >= 1,
          "no progress answers");
}

}  // namespace

int main()
{
    std::cout << "Protocol, board " << C4_VARIANT << '\n';

    testParsing();
    testFormatting();
    // The session uses positions and timings of the standard board.
    if (Board::WIDTH == 7 && Board::HEIGHT == 6)
    {
        testSession();
    }

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}
