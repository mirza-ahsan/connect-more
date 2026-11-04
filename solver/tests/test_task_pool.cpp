// Unit tests for the task pool (c4/task_pool.hpp). Returns 0 when every check passes.
//
// 1. Pons's positions solved with 1 thread and with all threads give the same, right answers.
// 2. Every task ends with exactly one Result, Stopped or Cancelled event, after its Started event.
// 3. Tasks stopped or cancelled part-way, then solved again with the same table, give the right
//    answers (stopped searches must not poison the shared table).
// 4. A cancelled waiting task never starts; stop and cancel of unknown tasks are refused; ids
//    can't be used twice at once.
// 5. Progress reports arrive and never go down; shutdown ends running and waiting tasks quickly.

#include "c4/task_pool.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <random>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace
{

using Type = TaskEvent::Type;
using namespace std::chrono_literals;

int failures = 0;

void check(bool ok, const std::string& what)
{
    if (!ok)
    {
        std::cerr << "FAIL: " << what << '\n';
        failures++;
    }
}

// Collects events from many threads.
class Recorder
{
private:
    mutable std::mutex mutex;
    std::vector<TaskEvent> events;

public:
    void add(const TaskEvent& event)
    {
        const std::lock_guard<std::mutex> lock(mutex);
        events.push_back(event);
    }

    std::vector<TaskEvent> all() const
    {
        const std::lock_guard<std::mutex> lock(mutex);
        return events;
    }

    std::vector<TaskEvent> forTask(const std::string& id) const
    {
        std::vector<TaskEvent> found;
        for (const TaskEvent& event : all())
        {
            if (event.task_id == id && event.type != Type::Progress)
            {
                found.push_back(event);
            }
        }
        return found;
    }

    TaskPool::EventFunction function()
    {
        return [this](const TaskEvent& event) { add(event); };
    }
};

bool isFinal(Type type)
{
    return type == Type::Result || type == Type::Stopped || type == Type::Cancelled;
}

struct TestPosition
{
    std::string moves;
    Outcome expected;
};

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
        std::string moves;
        int score = 0;
        if (fields >> moves >> score)
        {
            positions.push_back(
                {moves, score > 0 ? Outcome::Win : (score < 0 ? Outcome::Loss : Outcome::Draw)});
        }
    }
    check(!positions.empty(), "no positions read from " + file.string());
    return positions;
}

Board boardOf(const std::string& moves)
{
    Board board;
    check(board.updateBoard(moves), "test position rejected: " + moves);
    return board;
}

std::size_t tableMb()
{
    return std::max<std::size_t>(64, TranspositionTable::minimumMegabytes());
}

// Every task has exactly one final event, and a Started event (from the same thread) before it
// if it ever ran.
void checkEventOrder(const Recorder& recorder, const std::vector<std::string>& ids)
{
    for (const std::string& id : ids)
    {
        const std::vector<TaskEvent> events = recorder.forTask(id);
        const auto finals =
            std::count_if(events.begin(), events.end(), [](const TaskEvent& e) { return isFinal(e.type); });
        const auto starts = std::count_if(events.begin(), events.end(),
                                          [](const TaskEvent& e) { return e.type == Type::Started; });
        check(finals == 1, "task " + id + " has " + std::to_string(finals) + " final events");
        check(starts <= 1, "task " + id + " started more than once");
        if (starts == 1 && finals == 1)
        {
            check(events.front().type == Type::Started && isFinal(events.back().type),
                  "task " + id + ": final event before Started");
            check(events.front().thread == events.back().thread, "task " + id + ": ended on another thread");
        }
        if (starts == 0 && finals == 1)
        {
            check(events.back().thread == -1 && events.back().positions == 0,
                  "task " + id + " never started but reports a thread or positions");
        }
    }
}

// Solves positions with the given number of threads and returns the outcome of each.
std::vector<Outcome> solveAll(const std::vector<TestPosition>& positions, int threads)
{
    TranspositionTable table(tableMb());
    Recorder recorder;
    std::vector<std::string> ids;
    {
        TaskPool pool(table, threads, recorder.function(), 0ms);
        for (std::size_t i = 0; i < positions.size(); i++)
        {
            ids.push_back(std::to_string(i));
            check(pool.submit(ids.back(), boardOf(positions[i].moves)), "submit refused");
        }
        pool.waitUntilIdle();

        // Statistics add up.
        std::uint64_t tasks = 0;
        std::uint64_t positions_searched = 0;
        for (const TaskPool::ThreadStats& stats : pool.threadStats())
        {
            tasks += stats.tasks;
            positions_searched += stats.positions;
        }
        check(tasks == positions.size(), "thread statistics miss tasks");
        check(positions_searched == pool.positionsSearched(), "thread statistics miss positions");
    }
    checkEventOrder(recorder, ids);

    std::vector<Outcome> outcomes(positions.size(), Outcome::Draw);
    for (const TaskEvent& event : recorder.all())
    {
        if (event.type == Type::Result)
        {
            outcomes[std::stoul(event.task_id)] = event.outcome;
        }
        check(event.type != Type::Stopped && event.type != Type::Cancelled, "task ended without a result");
    }
    return outcomes;
}

void testOneThreadVersusAll()
{
    std::vector<TestPosition> positions = loadPons("Test_L2_R2", 300);    // Middle-Medium
    const std::vector<TestPosition> begin = loadPons("Test_L1_R1", 300);  // Begin-Easy
    positions.insert(positions.end(), begin.begin(), begin.end());

    const std::vector<Outcome> one = solveAll(positions, 1);
    const std::vector<Outcome> all = solveAll(positions, 0);
    int wrong = 0;
    for (std::size_t i = 0; i < positions.size(); i++)
    {
        check(one[i] == all[i], "1 thread and all threads disagree on " + positions[i].moves);
        if (all[i] != positions[i].expected)
        {
            wrong++;
            check(false, "wrong answer for " + positions[i].moves);
        }
    }
    std::cout << "1 thread vs all: " << positions.size() << " positions, " << wrong << " wrong\n";
}

void testStopAndCancelPartWay()
{
    const std::vector<TestPosition> positions = loadPons("Test_L1_R2", 200);  // Begin-Medium
    TranspositionTable table(tableMb());
    Recorder recorder;
    std::vector<std::string> ids;

    {
        TaskPool pool(table, 0, recorder.function(), 0ms);
        std::mt19937 rng(11);
        for (std::size_t i = 0; i < positions.size(); i++)
        {
            ids.push_back(std::to_string(i));
            pool.submit(ids.back(), boardOf(positions[i].moves));
        }
        // Stop or cancel every third task, mostly while it runs: wait until it has started (or
        // finished), then a random short moment more. Tasks that are still waiting at that point
        // get stopped or cancelled while waiting instead.
        for (std::size_t i = 0; i < positions.size(); i += 3)
        {
            const auto give_up = std::chrono::steady_clock::now() + 10s;
            while (recorder.forTask(ids[i]).empty() && std::chrono::steady_clock::now() < give_up)
            {
                std::this_thread::sleep_for(100us);
            }
            std::this_thread::sleep_for(
                std::chrono::microseconds(std::uniform_int_distribution<int>(0, 300)(rng)));
            if (i % 2 == 0)
            {
                pool.stop(ids[i]);
            }
            else
            {
                pool.cancel(ids[i]);
            }
        }
        pool.waitUntilIdle();
    }
    checkEventOrder(recorder, ids);

    // Results that did finish must be right; everything else is solved again with the same table.
    int ended_early = 0;
    int stopped_running = 0;
    std::vector<std::size_t> again;
    for (const TaskEvent& event : recorder.all())
    {
        if (!isFinal(event.type))
        {
            continue;
        }
        const std::size_t i = std::stoul(event.task_id);
        if (event.type == Type::Result)
        {
            check(event.outcome == positions[i].expected,
                  "wrong result before re-solving: " + positions[i].moves);
        }
        else
        {
            ended_early++;
            stopped_running += event.thread >= 0 ? 1 : 0;
            check((i % 3 == 0), "task ended early without being stopped or cancelled: " + event.task_id);
            check((i % 2 == 0) == (event.type == Type::Stopped),
                  "stop reported as cancel or the other way round");
            again.push_back(i);
        }
    }

    Recorder second;
    {
        TaskPool pool(table, 0, second.function(), 0ms);
        for (std::size_t i : again)
        {
            pool.submit(std::to_string(i), boardOf(positions[i].moves));
        }
        pool.waitUntilIdle();
    }
    int wrong = 0;
    for (const TaskEvent& event : second.all())
    {
        if (event.type == Type::Result && event.outcome != positions[std::stoul(event.task_id)].expected)
        {
            wrong++;
        }
        check(event.type != Type::Stopped && event.type != Type::Cancelled,
              "task ended early without being asked");
    }
    check(wrong == 0, std::to_string(wrong) + " wrong answers after stopped and cancelled searches");
    std::cout << "stop and cancel: " << ended_early << " ended early (" << stopped_running
              << " while running), " << wrong << " wrong when solved again\n";
    check(stopped_running >= 20, "too few running tasks were stopped for the test to mean anything");
}

void testWaitingTasksAndIds()
{
    TranspositionTable table(tableMb());
    Recorder recorder;
    TaskPool pool(table, 1, recorder.function(), 0ms);

    // One thread, busy with the empty board, which takes minutes; everything else has to wait.
    check(pool.submit("long", Board()), "submit refused");
    check(!pool.submit("long", Board()), "the same id was accepted twice");
    check(pool.submit("waiting-1", boardOf("4453")), "submit refused");
    check(pool.submit("waiting-2", boardOf("4453")), "submit refused");
    std::this_thread::sleep_for(50ms);

    check(pool.cancel("waiting-1"), "cancel of a waiting task refused");
    check(pool.stop("waiting-2"), "stop of a waiting task refused");
    check(!pool.cancel("waiting-1"), "a task was cancelled twice");
    check(!pool.stop("no-such-task"), "stop of an unknown task accepted");
    check(!pool.cancel("no-such-task"), "cancel of an unknown task accepted");

    // Ids of finished tasks can be used again.
    check(pool.submit("waiting-1", boardOf("4453")), "id of a cancelled task cannot be used again");
    check(pool.cancel("waiting-1"), "cancel of a waiting task refused");

    check(pool.stop("long"), "stop of a running task refused");
    pool.waitUntilIdle();

    const std::vector<TaskEvent> cancelled = recorder.forTask("waiting-1");
    check(
        cancelled.size() == 2 && cancelled[0].type == Type::Cancelled && cancelled[1].type == Type::Cancelled,
        "cancelled waiting task did not report Cancelled twice (once per submit)");
    const std::vector<TaskEvent> stopped = recorder.forTask("waiting-2");
    check(stopped.size() == 1 && stopped[0].type == Type::Stopped && stopped[0].positions == 0,
          "stopped waiting task did not report Stopped with 0 positions");
    for (const TaskEvent& event : recorder.all())
    {
        check(!(event.type == Type::Started && event.task_id != "long"),
              "a cancelled or stopped waiting task started");
    }
    const std::vector<TaskEvent> long_task = recorder.forTask("long");
    check(long_task.size() == 2 && long_task[1].type == Type::Stopped && long_task[1].positions > 0,
          "running task did not report Stopped with its positions");
}

void testProgressAndShutdown()
{
    TranspositionTable table(tableMb());
    Recorder recorder;
    TaskPool pool(table, 2, recorder.function(), 20ms);
    check(pool.threadCount() == 2, "wrong thread count");

    // Two long tasks running and two waiting.
    for (const char* id : {"a", "b", "c", "d"})
    {
        pool.submit(id, Board());
    }
    std::this_thread::sleep_for(300ms);

    const auto shutdown_start = std::chrono::steady_clock::now();
    pool.shutdown();
    const double shutdown_seconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - shutdown_start).count();
    check(shutdown_seconds < 2.0, "shutdown took " + std::to_string(shutdown_seconds) + " s");
    check(!pool.submit("e", Board()), "a task was accepted after shutdown");
    pool.shutdown();  // a second call does nothing

    int progress = 0;
    std::uint64_t last = 0;
    for (const TaskEvent& event : recorder.all())
    {
        if (event.type == Type::Progress)
        {
            progress++;
            check(event.positions >= last, "progress went down");
            last = event.positions;
        }
    }
    check(progress >= 5, "only " + std::to_string(progress) + " progress reports in 300 ms at 20 ms each");
    check(last > 0, "progress reported no positions searched");
    check(pool.positionsSearched() >= last, "total positions below the last progress report");

    checkEventOrder(recorder, {"a", "b", "c", "d"});
    int stopped = 0;
    int cancelled = 0;
    for (const TaskEvent& event : recorder.all())
    {
        stopped += event.type == Type::Stopped ? 1 : 0;
        cancelled += event.type == Type::Cancelled ? 1 : 0;
    }
    check(stopped == 2 && cancelled == 2,
          "shutdown should stop the 2 running tasks and cancel the 2 waiting");
}

}  // namespace

int main()
{
    std::cout << "Task pool, board " << Board::WIDTH << "x" << Board::HEIGHT << ", "
              << std::thread::hardware_concurrency() << " logical processors\n";

    // These use Pons's files, or rely on the empty standard board taking minutes to solve.
    if (Board::WIDTH == 7 && Board::HEIGHT == 6)
    {
        testOneThreadVersusAll();
        testStopAndCancelPartWay();
        testWaitingTasksAndIds();
        testProgressAndShutdown();
    }
    else
    {
        // Other sizes: every task still ends with exactly one final event, even at shutdown.
        TranspositionTable table(tableMb());
        Recorder recorder;
        {
            TaskPool pool(table, 2, recorder.function(), 0ms);
            for (const char* id : {"a", "b", "c", "d"})
            {
                pool.submit(id, Board());
            }
        }
        checkEventOrder(recorder, {"a", "b", "c", "d"});
    }

    if (failures != 0)
    {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "all checks passed\n";
    return 0;
}
