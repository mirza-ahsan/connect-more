#pragma once
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>

#include "c4/position.hpp"
#include "c4/solver.hpp"
#include "c4/transposition_table.hpp"

// Something that happened in the pool, reported through the pool's event function.
struct TaskEvent
{
    enum class Type
    {
        Started,    // a thread took the task
        Result,     // the task was solved: outcome, positions, seconds
        Stopped,    // stopped by stop() (or by shutdown) before it finished: positions, seconds
        Cancelled,  // cancelled by cancel() (or by shutdown): positions, seconds (0 if it never started)
        Progress    // every progress interval: positions searched by all threads so far
    };

    Type type;
    std::string task_id;              // empty for Progress
    int thread = -1;                  // thread number 0..threads-1, or -1 if the task never started
    Outcome outcome = Outcome::Draw;  // Result only
    std::uint64_t positions = 0;      // for this task, or for all tasks so far (Progress)
    double seconds = 0;               // time spent solving this task
    double time = 0;                  // when it happened, in seconds since the pool started
};

// Runs many solver tasks at once on one laptop: one thread per logical processor, all taking
// tasks from one shared list (oldest first) and sharing one transposition table.
//
// Every submitted task ends with exactly one Result, Stopped or Cancelled event. A task that is
// stopped or cancelled just as it finishes may still report Result. Events are reported from the
// pool's threads, several at once, so the event function must be safe to call from many threads.
class TaskPool
{
public:
    using Clock = std::chrono::steady_clock;
    using EventFunction = std::function<void(const TaskEvent&)>;

    // threads <= 0 means one per logical processor. A progress interval of zero turns progress
    // reports off.
    TaskPool(TranspositionTable& table, int threads, EventFunction on_event,
             std::chrono::milliseconds progress_interval = std::chrono::seconds(1));

    // Same as shutdown().
    ~TaskPool();

    TaskPool(const TaskPool&) = delete;
    TaskPool& operator=(const TaskPool&) = delete;

    // Adds a task to the end of the list. False if the id is already waiting or running, or the
    // pool is shutting down. The game in `board` must not be over.
    bool submit(const std::string& task_id, const Board& board);

    // Ends a task early: a running one reports Stopped when its search returns; a waiting one is
    // removed and reports Stopped with 0 positions. False if no such task is waiting or running.
    bool stop(const std::string& task_id);

    // Like stop(), but the task reports Cancelled. A waiting task is removed and never starts.
    bool cancel(const std::string& task_id);

    // Waits until no task is waiting or running, and every final event has been reported.
    void waitUntilIdle();

    // Takes no new tasks, cancels waiting ones, stops running ones, and waits for every thread to
    // finish. Safe to call more than once, and from several threads.
    void shutdown();

    int threadCount() const;

    // Positions searched by all threads since the pool started.
    std::uint64_t positionsSearched() const;

    struct ThreadStats
    {
        std::uint64_t tasks;      // tasks this thread finished, stopped or cancelled
        std::uint64_t positions;  // positions this thread searched
        double busy_seconds;      // time spent solving
        double idle_seconds;      // time since the pool started, minus busy time
    };
    std::vector<ThreadStats> threadStats() const;

    // Seconds since the pool started: the clock used for every event's `time`.
    double secondsSinceStart() const;

private:
    enum class Request
    {
        None,
        Stop,
        Cancel
    };

    struct Task
    {
        std::string id;
        Board board;
    };

    // One per thread. Each worker is a separate allocation, so their busy fields never share a
    // cache line; the solver's counter has a cache line of its own too.
    struct Worker
    {
        explicit Worker(TranspositionTable& table) : solver(table) {}

        Solver solver;
        std::atomic<bool> stop_flag{false};
        std::thread thread;

        // Guarded by the pool's mutex.
        std::string task_id;  // the running task, empty when idle
        Request request = Request::None;
        std::uint64_t tasks = 0;
        double busy_seconds = 0;
    };

    TranspositionTable& table;
    const EventFunction on_event;
    const std::chrono::milliseconds progress_interval;
    const Clock::time_point start;

    mutable std::mutex mutex;
    std::condition_variable work_ready;    // a task was added, or shutdown began
    std::condition_variable all_idle;      // the last unreported task reported its end
    std::condition_variable progress_now;  // shutdown began
    std::deque<Task> waiting;
    std::unordered_set<std::string> ids;  // ids of every waiting or running task
    int unreported = 0;                   // tasks taken off the list whose final event is not reported yet
    bool shutting_down = false;

    std::vector<std::unique_ptr<Worker>> workers;
    std::thread progress_thread;
    std::mutex shutdown_mutex;

    bool endTask(const std::string& task_id, Request request);

    // Called after a task's final event is reported; wakes waitUntilIdle() when nothing is left.
    void finishedReporting();
    void workerLoop(Worker& worker, int index);
    void progressLoop();
};
