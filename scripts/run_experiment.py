"""Runs the measured experiment runs (work plan steps 22 and 28-31; docs/session-checklist.md).

The PC, once per session (it then loops until Ctrl+C: checks and cools down, joins the next run
with its worker, and so on; all along it offers its logs, the network echo and `iperf3 -s` to
Laptop 1, see scripts/collect_logs.py):
    uv run python -m scripts.run_experiment worker --link cable
    (over Wi-Fi: --link wifi --coordinator <Laptop 1's Wi-Fi address>)

Laptop 1, once per run of E2 or E3 (both machines; it starts its own worker too, and copies the
PC's logs into logs/pc/ afterwards):
    uv run python -m scripts.run_experiment coordinator --strategy V1 --link cable --repeat 1
    (E3: add --depth with the other split depth; over Wi-Fi add --peer <the PC's Wi-Fi address>)

Either machine, for E1 (that machine alone: V3, one worker, its own logs folder):
    uv run python -m scripts.run_experiment alone --machine laptop1 --repeat 1

Before a run, each machine checks on itself:
1. the solver is the release build with no uncommitted changes, from the commit the repository
   is at, and the repository has no uncommitted changes (scripts/experiment.toml included);
2. the charger is plugged in (a machine without a battery, like the PC, passes);
3. its sensor log is being written (scripts/sensor_log.py: the newest log in logs/sensors/ has a
   sample from the last few seconds);
4. cool-down: it rests for the set time, then waits until a fresh reading of its CPU package
   temperature is at most the level for that machine (both values from the pilot).
After the run, Laptop 1 also checks that exactly the expected workers took part and that the
coordinator gave no warning about their solvers (other commit, uncommitted changes, other table
size); otherwise the run is marked invalid.

With --pilot, failed checks are only warnings, the run id starts with "pilot", and the PC's
worker joins as "pc-pilot", which a measured run on Laptop 1 does not accept: pilot runs are
never results. Each run's own log, logs/<run_id>/experiment.jsonl, records the settings, the
checks, the temperatures, the outcome and whether the run is valid; the PC's checks and
cool-down readings are in its session log, logs/pc/sessions/.
"""

import argparse
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from analysis.load_sensors import latest_sample, newest_log
from common import messages as m
from common.build import ROOT, parse_commit
from common.machines import COORDINATOR, MACHINES, PEER, SENSORS_DIR
from common.runlog import LOGS_DIR, RunLog, log_path, stamp
from coordinator import calibration
from coordinator.server import Config, Coordinator, CoordinatorError, Summary
from coordinator.strategies import STRATEGIES
from scripts import collect_logs
from scripts.helpers import PORT, SETTINGS, SOLVER, build_info, iperf_server, load_settings

WORKER_EXIT_WAIT = 120.0  # seconds for this machine's worker to exit after a run that ended well
WORKER_KILL_WAIT = 15.0  # ... after a run that failed (it may never have joined)
RETRY_SECONDS = 30.0  # The PC's loop: wait before checking again after a failed check
DEPTH_RUNS = ("V1", "V3")  # E3: the strategies run again at the other split depth


class CheckFailed(RuntimeError):
    """A run must not start (a check before it failed)."""


# --- Checks before a run ----------------------------------------------------------------------


@dataclass
class Checks:
    """What was found before a run; written to its log."""

    build: dict = field(default_factory=dict)  # scripts/helpers.build_info()
    on_charger: bool | None = None
    sensors: str | None = None  # the sensor log read
    problems: list[str] = field(default_factory=list)


def check_build(checks: Checks, solver: Path) -> None:
    checks.build = build_info(solver)
    repository, header = checks.build["repository"], checks.build["solver_header"]
    if not isinstance(header, list):
        checks.problems.append(f"cannot start the solver {solver}: {header}")
        return
    if not any(line.startswith("build Release,") for line in header):
        checks.problems.append("the solver is not a release build")
    commit = next((c for line in header if (c := parse_commit(line))), None)
    if commit is None or commit[1]:
        checks.problems.append("the solver was built with uncommitted changes (rebuild release)")
    elif commit[0] != repository["commit"]:
        checks.problems.append(
            f"the solver is from commit {commit[0]}, the repository is at "
            f"{repository['commit']} (rebuild release)"
        )
    if repository["dirty"] is not False:
        checks.problems.append("the repository has uncommitted changes (or git cannot read it)")


def check_charger(checks: Checks) -> None:
    battery = psutil.sensors_battery()
    checks.on_charger = None if battery is None else bool(battery.power_plugged)
    if checks.on_charger is False:
        checks.problems.append("the charger is not plugged in")


def package_reading(logs: Path, max_age: float) -> tuple[float | None, str]:
    """The CPU package temperature from the newest sensor log, if it has a sample from the last
    `max_age` seconds, and the log's name; otherwise None and what is wrong."""
    path = newest_log(logs / SENSORS_DIR)
    if path is None:
        return None, f"no sensor log in {logs / SENSORS_DIR} (start scripts.sensor_log)"
    try:
        sample = latest_sample(path)
    except (OSError, ValueError) as e:
        return None, f"cannot read {path.name}: {e}"
    if time.time() - sample["wall"] > max_age:
        return None, f"{path.name} has no sample from the last {max_age:.0f} s (is it running?)"
    if sample.get("package_c") is None:
        return None, f"{path.name} has no CPU package temperature"
    return float(sample["package_c"]), path.name


def run_checks(settings: dict, logs: Path, solver: Path, pilot: bool) -> Checks:
    checks = Checks()
    check_build(checks, solver)
    check_charger(checks)
    package, about = package_reading(logs, settings["cooldown"]["sensor_max_age"])
    if package is None:
        checks.problems.append(about)
    else:
        checks.sensors = about
    for problem in checks.problems:
        print(f"{'warning' if pilot else 'check failed'}: {problem}", file=sys.stderr)
    if checks.problems and not pilot:
        raise CheckFailed("; ".join(checks.problems))
    return checks


def cool_down(machine: str, settings: dict, logs: Path, pilot: bool, log: RunLog) -> None:
    """Rests for the set time, then waits until a fresh sensor reading of the CPU package is at
    most the machine's level. Every reading is logged. In pilot mode, a machine without a working
    sensor log only rests."""
    rule = settings["cooldown"]
    rest, limit = rule["rest_seconds"], rule["max_package_c"][machine]
    log.write("cooldown_start", machine=machine, rest_seconds=rest, max_package_c=limit)
    print(f"run_experiment: resting {rest:.0f} s, then until the package is at most {limit} C")
    time.sleep(rest)
    while True:
        package, about = package_reading(logs, rule["sensor_max_age"])
        log.write("cooldown_reading", package_c=package, source=about)
        if package is None and pilot:
            break
        if package is not None and package <= limit:
            break
        state = about if package is None else f"package {package:.0f} C"
        print(f"run_experiment: {state}; waiting")
        time.sleep(rule["poll_seconds"])
    log.write("cooldown_end")


# --- The coordinator side (Laptop 1, or a machine alone) ---------------------------------------


def start_worker(
    name: str, coordinator: str, port: int, logs: Path, solver: Path, console: Path | None
) -> subprocess.Popen[bytes]:
    """This machine's worker (python -m worker, with its default threads and table). Its console
    output goes to `console` if given."""
    command = [sys.executable, "-m", "worker", "--coordinator", coordinator, "--port", str(port)]
    command += ["--name", name, "--log-dir", str(logs), "--solver", str(solver)]
    out = None if console is None else console.open("xb")
    try:
        stderr = None if out is None else subprocess.STDOUT
        return subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=stderr)
    finally:
        if out is not None:
            out.close()  # the worker has its own handle


def wait_for_worker(worker: subprocess.Popen[bytes], seconds: float) -> int:
    try:
        return worker.wait(seconds)
    except subprocess.TimeoutExpired:
        worker.kill()
        return worker.wait()


@dataclass
class Plan:
    """One run, as decided from the command line and the settings."""

    experiment: str  # E1, E2 or E3
    machine: str  # where the coordinator runs
    link: str
    workers: tuple[str, ...]  # the worker names that must take part
    config: Config


def plan_run(args: argparse.Namespace, settings: dict) -> Plan:
    run = settings["run"]
    alone = args.role == "alone"
    depth = args.depth if args.depth is not None else run["depth"]
    experiment = "E1" if alone else ("E2" if depth == run["depth"] else "E3")
    if not args.pilot:
        if args.settings.resolve() != SETTINGS.resolve():
            raise CheckFailed("measured runs use scripts/experiment.toml, which is committed")
        if not run["expect"]:
            raise CheckFailed("scripts/experiment.toml has no start position and result yet")
        if experiment == "E3" and args.strategy not in DEPTH_RUNS:
            raise CheckFailed(f"E3 (the other depth) runs only {', '.join(DEPTH_RUNS)}")
    workers = (args.machine,) if alone else MACHINES
    allowed = workers + (tuple(f"{w}-pilot" for w in workers) if args.pilot else ())
    tag = "pilot_" if args.pilot else ""
    where = args.machine if alone else args.link
    run_id = f"{tag}{stamp()}_{experiment}_{args.strategy}_{where}_d{depth}_r{args.repeat}"
    calibration_set = ()
    if STRATEGIES[args.strategy].needs_calibration:
        calibration_set = calibration.load_positions(
            ROOT / run["calibration_file"], run["calibration_count"]
        )
    config = Config(
        run_id=run_id,
        start=run["start"],
        depth=depth,
        strategy=args.strategy,
        workers=len(workers),
        expect=m.Outcome(run["expect"]) if run["expect"] else None,
        # Alone, only this machine's own worker may join: the PC's worker role must not.
        bind=f"tcp://{'127.0.0.1' if alone else '*'}:{args.port}",
        timeout=run["timeout"],
        split_after=run["split_after"],
        calibration=calibration_set,
        allowed_workers=allowed,
        log_dir=args.logs,
    )
    return Plan(experiment, args.machine, args.link, workers, config)


def run_coordinator(args: argparse.Namespace, settings: dict) -> int:
    """One run with the coordinator on this machine. 0 if it is valid and its result right."""
    plan = plan_run(args, settings)
    checks = run_checks(settings, args.logs, args.solver, args.pilot)
    with RunLog(log_path(plan.config.run_id, "experiment", args.logs)) as log:
        log.write(
            "experiment_start",
            experiment=plan.experiment,
            machine=plan.machine,
            link=plan.link,
            repeat=args.repeat,
            pilot=args.pilot,
            settings=settings,
            checks=asdict(checks),
        )
        try:
            summary = run_once(args, settings, plan, log)
        except CoordinatorError as e:  # a worker crashed, went silent or could not be reached
            log.write("experiment_failed", error=str(e))
            print(f"run_experiment: the run failed: {e}", file=sys.stderr)
            summary = None
        except BaseException as e:  # Ctrl+C: the log still says how the run ended
            log.write("experiment_failed", error=f"{type(e).__name__}: {e}")
            raise
        if args.peer:
            pull_logs(args.peer, args.logs, args.log_port, log)
    if summary is None:
        return 1
    print(
        f"run_experiment: {plan.config.run_id}: result {summary.result} "
        f"(expected {summary.expected}), correct: {summary.correct}, "
        f"solve time {summary.solve_seconds} s"
    )
    return 0


def run_once(args: argparse.Namespace, settings: dict, plan: Plan, log: RunLog) -> Summary | None:
    """Cools down, runs the coordinator with this machine's worker, and checks the run. The
    summary if the run ended and is valid; None (and the reason in the log) if not."""
    cool_down(plan.machine, settings, args.logs, args.pilot, log)
    coordinator = Coordinator(plan.config)
    console = log.path.with_name(f"{plan.machine}_worker_console.txt")
    worker = start_worker(
        plan.machine, "127.0.0.1", coordinator.port, args.logs, args.solver, console
    )
    summary = None
    try:
        summary = coordinator.run()
    finally:
        code = wait_for_worker(worker, WORKER_EXIT_WAIT if summary else WORKER_KILL_WAIT)
        log.write("local_worker_exit", code=code)
    problems = run_problems(summary, plan, code, args.pilot)
    log.write("experiment_end", summary=asdict(summary), valid=not problems, problems=problems)
    for problem in problems:
        print(f"run_experiment: invalid run: {problem}", file=sys.stderr)
    return None if problems else summary


def run_problems(summary: Summary, plan: Plan, worker_code: int, pilot: bool) -> list[str]:
    """Why a run that ended cannot be used as a result (empty if it can)."""
    problems = []
    if summary.timed_out or summary.result is None:
        problems.append("no result (timed out)")
    elif summary.correct is False:  # None only in a pilot run without a known result
        problems.append(f"wrong result {summary.result}, expected {summary.expected}")
    if worker_code != 0:
        problems.append(f"this machine's worker exited with code {worker_code}")
    joined = set(summary.workers)
    allowed = set(plan.workers) | ({f"{w}-pilot" for w in plan.workers} if pilot else set())
    if not joined <= allowed or len(joined) != len(plan.workers):
        problems.append(f"workers {sorted(joined)} took part, not {list(plan.workers)}")
    coordinator_log = plan.config.log_dir / plan.config.run_id / "coordinator.jsonl"
    if not pilot and '"event":"settings_warning"' in coordinator_log.read_text("utf-8"):
        problems.append("the coordinator warned about a worker's solver (see its log)")
    return problems


def pull_logs(peer: str, logs: Path, port: int, log: RunLog) -> None:
    """Copies the PC's new logs here; a failure is only reported (pull again later)."""
    try:
        report = collect_logs.pull(peer, logs, port)
    except OSError as e:
        log.write("logs_not_pulled", error=str(e))
        print(f"run_experiment: could not copy the PC's logs: {e}", file=sys.stderr)
        return
    log.write("logs_pulled", copied=report.copied, problems=report.problems)
    collect_logs.print_report(f"from {peer}", report)


# --- The worker side (the PC) ---------------------------------------------------------------


def run_worker_loop(args: argparse.Namespace, settings: dict) -> int:
    """The PC: before each run, checks and cools down, then starts its worker, which joins the
    next run and exits at its end. A failed check is retried until it passes. All along, it
    offers its logs and the network echo to Laptop 1 on the log port, and runs `iperf3 -s`; the
    worker only connects out, so both can use the same port. Ends with Ctrl+C."""
    address = args.coordinator or settings["network"][COORDINATOR].get(args.link)
    if not address:
        raise CheckFailed(f"give Laptop 1's {args.link} address with --coordinator")
    name = f"{args.name}-pilot" if args.pilot else args.name
    stop = threading.Event()
    server = threading.Thread(
        target=collect_logs.serve, args=(args.logs, args.log_port, stop), daemon=True
    )
    server.start()
    path = log_path("sessions", f"{stamp()}_{args.name}_{args.link}", args.logs)
    with iperf_server() as iperf, RunLog(path) as log:
        log.write(
            "worker_role_start",
            name=name,
            link=args.link,
            coordinator=address,
            pilot=args.pilot,
            iperf3=iperf,
            settings=settings,
        )
        try:
            while True:
                try:
                    checks = run_checks(settings, args.logs, args.solver, args.pilot)
                except CheckFailed as e:
                    log.write("checks_failed", problems=str(e))
                    print(f"run_experiment: checking again in {RETRY_SECONDS:.0f} s")
                    time.sleep(RETRY_SECONDS)
                    continue
                log.write("checks", checks=asdict(checks))
                cool_down(args.name, settings, args.logs, args.pilot, log)
                print(f"run_experiment: {name} ready; joining the next run on {address}")
                worker = start_worker(name, address, args.port, args.logs, args.solver, None)
                log.write("worker_exit", code=worker.wait())
        except KeyboardInterrupt:
            log.write("worker_role_end")
        finally:
            stop.set()
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_experiment",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--settings", type=Path, default=SETTINGS)
    parser.add_argument("--logs", type=Path, default=LOGS_DIR)
    parser.add_argument("--solver", type=Path, default=SOLVER)
    parser.add_argument("--port", type=int, default=PORT, help="the messaging port")
    parser.add_argument("--log-port", type=int, default=PORT, help="where the PC serves logs")
    parser.add_argument("--pilot", action="store_true", help="checks only warn; not a result")
    roles = parser.add_subparsers(dest="role", required=True)
    both = roles.add_parser("coordinator", help="Laptop 1: one run with both machines (E2, E3)")
    both.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    both.add_argument("--link", required=True, choices=["cable", "wifi"])
    both.add_argument("--repeat", type=int, required=True)
    both.add_argument("--depth", type=int, help="E3: the other split depth")
    both.add_argument("--peer", help="The PC's address, to copy its logs (default: cable)")
    both.set_defaults(machine=COORDINATOR)
    alone = roles.add_parser("alone", help="either machine: one E1 run, that machine alone")
    alone.add_argument("--machine", required=True, choices=MACHINES)
    alone.add_argument("--repeat", type=int, required=True)
    alone.set_defaults(strategy="V3", link="local", depth=None, peer=None)
    worker = roles.add_parser("worker", help="The PC: join runs until Ctrl+C")
    worker.add_argument("--link", required=True, choices=["cable", "wifi"])
    worker.add_argument("--coordinator", help="Laptop 1's address (default: its cable address)")
    worker.add_argument("--name", default=PEER, choices=MACHINES)
    args = parser.parse_args(argv)
    settings = load_settings(args.settings)
    try:
        if args.role == "worker":
            return run_worker_loop(args, settings)
        if args.role == "coordinator" and args.peer is None:
            args.peer = settings["network"][PEER].get(args.link)  # Wi-Fi: give --peer
        return run_coordinator(args, settings)
    except CheckFailed as e:
        print(f"run_experiment: not started: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
