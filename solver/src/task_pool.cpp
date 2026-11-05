#include "c4/task_pool.hpp"

#include <algorithm>
#include <utility>

TaskPool::TaskPool(TranspositionTable& shared_table, int threads, EventFunction event_function,
                   std::chrono::milliseconds interval)
    : table(shared_table),
      on_event(std::move(event_function)),
      progress_interval(interval),
      start(Clock::now())
{
    if (threads <= 0)
    {
        threads = std::max(1, static_cast<int>(std::thread::hardware_concurrency()));
    }

    // Create every worker before starting any thread, so `workers` never changes while threads run.
    for (int i = 0; i < threads; i++)
    {
        workers.push_back(std::make_unique<Worker>(table));
    }
    for (int i = 0; i < threads; i++)
    {
        Worker& worker = *workers[static_cast<std::size_t>(i)];
        worker.thread = std::thread([this, &worker, i] { workerLoop(worker, i); });
    }
    if (progress_interval.count() > 0)
    {
        progress_thread = std::thread([this] { progressLoop(); });
    }
}

TaskPool::~TaskPool()
{
    shutdown();
}

double TaskPool::secondsSinceStart() const
{
    return std::chrono::duration<double>(Clock::now() - start).count();
}

int TaskPool::threadCount() const
{
    return static_cast<int>(workers.size());
}

std::uint64_t TaskPool::positionsSearched() const
{
    std::uint64_t total = 0;
    for (const auto& worker : workers)
    {
        total += worker->solver.positionsSearched();
    }
    return total;
}

std::vector<TaskPool::ThreadStats> TaskPool::threadStats() const
{
    const double elapsed = secondsSinceStart();
    const std::lock_guard<std::mutex> lock(mutex);
    std::vector<ThreadStats> stats;
    for (const auto& worker : workers)
    {
        stats.push_back({worker->tasks, worker->solver.positionsSearched(), worker->busy_seconds,
                         std::max(0.0, elapsed - worker->busy_seconds)});
    }
    return stats;
}

bool TaskPool::submit(const std::string& task_id, const Board& board)
{
    {
        const std::lock_guard<std::mutex> lock(mutex);
        if (shutting_down || !ids.insert(task_id).second)
        {
            return false;
        }
        waiting.push_back({task_id, board});
    }
    work_ready.notify_one();
    return true;
}

bool TaskPool::stop(const std::string& task_id)
{
    return endTask(task_id, Request::Stop);
}

bool TaskPool::cancel(const std::string& task_id)
{
    return endTask(task_id, Request::Cancel);
}

bool TaskPool::endTask(const std::string& task_id, Request request)
{
    {
        const std::lock_guard<std::mutex> lock(mutex);

        // Running: raise its stop flag. The worker reports the end when the search returns.
        for (const auto& worker : workers)
        {
            if (worker->task_id == task_id)
            {
                // Cancel wins over stop if both are asked for.
                if (worker->request != Request::Cancel)
                {
                    worker->request = request;
                }
                worker->stop_flag.store(true, std::memory_order_relaxed);
                return true;
            }
        }

        // Waiting: take it out of the list; it never starts.
        const auto it = std::find_if(waiting.begin(), waiting.end(),
                                     [&](const Task& task) { return task.id == task_id; });
        if (it == waiting.end())
        {
            // Finishing: its search has ended and its final answer is being written. There is
            // nothing left to stop, and that answer is on its way (docs/protocol.md, promise 3).
            return ids.count(task_id) != 0;
        }
        waiting.erase(it);
        ids.erase(task_id);
        unreported++;
    }

    TaskEvent event{request == Request::Cancel ? TaskEvent::Type::Cancelled : TaskEvent::Type::Stopped,
                    task_id};
    event.time = secondsSinceStart();
    on_event(event);
    finishedReporting();
    return true;
}

void TaskPool::waitUntilIdle()
{
    std::unique_lock<std::mutex> lock(mutex);
    all_idle.wait(lock, [this] { return waiting.empty() && unreported == 0; });
}

void TaskPool::shutdown()
{
    // Only one shutdown at a time: two threads must never join the same thread.
    const std::lock_guard<std::mutex> one_at_a_time(shutdown_mutex);

    std::vector<std::string> dropped;
    {
        const std::lock_guard<std::mutex> lock(mutex);
        if (!shutting_down)
        {
            shutting_down = true;
            for (const Task& task : waiting)
            {
                dropped.push_back(task.id);
                ids.erase(task.id);
                unreported++;
            }
            waiting.clear();
            for (const auto& worker : workers)
            {
                if (!worker->task_id.empty())
                {
                    if (worker->request == Request::None)
                    {
                        worker->request = Request::Stop;
                    }
                    worker->stop_flag.store(true, std::memory_order_relaxed);
                }
            }
        }
    }

    for (const std::string& task_id : dropped)
    {
        TaskEvent event{TaskEvent::Type::Cancelled, task_id};
        event.time = secondsSinceStart();
        on_event(event);
        finishedReporting();
    }

    work_ready.notify_all();
    progress_now.notify_all();
    for (const auto& worker : workers)
    {
        if (worker->thread.joinable())
        {
            worker->thread.join();
        }
    }
    if (progress_thread.joinable())
    {
        progress_thread.join();
    }
}

void TaskPool::workerLoop(Worker& worker, int index)
{
    while (true)
    {
        Task task;
        {
            std::unique_lock<std::mutex> lock(mutex);
            work_ready.wait(lock, [this] { return shutting_down || !waiting.empty(); });
            if (waiting.empty())
            {
                return;  // shutting down
            }
            task = std::move(waiting.front());
            waiting.pop_front();
            worker.task_id = task.id;
            worker.request = Request::None;
            worker.stop_flag.store(false, std::memory_order_relaxed);
            unreported++;
        }

        TaskEvent started{TaskEvent::Type::Started, task.id, index};
        started.time = secondsSinceStart();
        on_event(started);

        const Clock::time_point solve_start = Clock::now();
        const SolveResult result = worker.solver.solve(task.board, &worker.stop_flag);
        const double seconds = std::chrono::duration<double>(Clock::now() - solve_start).count();

        Request request = Request::None;
        {
            const std::lock_guard<std::mutex> lock(mutex);
            request = worker.request;
            worker.task_id.clear();
            worker.tasks++;
            worker.busy_seconds += seconds;
            // The id stays in `ids` until the final event is out, so a stop or cancel that
            // arrives meanwhile is answered by that event, not by "no task with this id".
        }

        TaskEvent ended{TaskEvent::Type::Result, task.id, index};
        if (result.stopped)
        {
            ended.type = request == Request::Cancel ? TaskEvent::Type::Cancelled : TaskEvent::Type::Stopped;
        }
        ended.outcome = result.outcome;
        ended.positions = result.positions;
        ended.seconds = seconds;
        ended.time = secondsSinceStart();
        on_event(ended);

        {
            const std::lock_guard<std::mutex> lock(mutex);
            ids.erase(task.id);  // the id can be used again now that its final event is out
        }
        // Counted until its final event is out, so waitUntilIdle() sees every event.
        finishedReporting();
    }
}

void TaskPool::progressLoop()
{
    std::unique_lock<std::mutex> lock(mutex);
    Clock::time_point next = start + progress_interval;
    while (!progress_now.wait_until(lock, next, [this] { return shutting_down; }))
    {
        lock.unlock();
        TaskEvent event{TaskEvent::Type::Progress, ""};
        event.positions = positionsSearched();
        event.time = secondsSinceStart();
        on_event(event);
        lock.lock();

        // Fixed ticks from the start, so the reports don't drift.
        next += progress_interval;
        const Clock::time_point now = Clock::now();
        while (next <= now)
        {
            next += progress_interval;
        }
    }
}

void TaskPool::finishedReporting()
{
    const std::lock_guard<std::mutex> lock(mutex);
    unreported--;
    if (unreported == 0 && waiting.empty())
    {
        all_idle.notify_all();
    }
}
