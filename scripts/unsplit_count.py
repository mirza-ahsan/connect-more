"""The unsplit reference for experiment E1: Laptop 1 solves the start position once, as a single
search on one thread, without any splitting. Only its number of positions searched is used (RQ5,
"Extra work"); its time is not a result.

    uv run python -m scripts.unsplit_count --start 4453 --expect win

The solver's own output (its build lines, then the result line) is kept unchanged in
logs/<run_id>/unsplit.txt, and logs/<run_id>/unsplit.jsonl records the build, the clocks at the
start and end, the parsed result and whether it was right. The table has the solver's default
size (CM_DEFAULT_TT_MB), the same as in every other run.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common.messages import Outcome
from common.runlog import LOGS_DIR, RunLog, log_path, stamp
from scripts.helpers import SOLVER, build_info

RESULT = re.compile(
    r"^result (?P<moves>\S+) (?P<outcome>win|draw|loss), "
    r"(?P<positions>\d+) positions searched, (?P<seconds>[0-9.]+) s$",
    re.MULTILINE,
)


def parse_result(output: str) -> dict[str, object] | None:
    """The result line of `c4solve --single`: outcome, positions searched and seconds."""
    if (match := RESULT.search(output)) is None:
        return None
    found = match.groupdict()
    return {**found, "positions": int(found["positions"]), "seconds": float(found["seconds"])}


def run(start: str, expect: Outcome | None, solver: Path, log_dir: Path, run_id: str) -> int:
    """0: solved (and right, if the result is known); 1: wrong or no result."""
    path = log_path(run_id, "unsplit", log_dir)
    with RunLog(path) as log:
        log.write("unsplit_start", start=start, expect=expect, build=build_info(solver))
        print(f"unsplit_count: solving {start or 'the empty board'} on one thread ...")
        # The solver writes straight into the raw file ("x": never overwrite a raw log), so an
        # interrupted run still keeps what it printed.
        raw = path.with_suffix(".txt")
        with raw.open("xb") as out:
            command = [str(solver), "--single", start or "-"]
            done = subprocess.run(command, stdout=out, stderr=subprocess.STDOUT)
        result = parse_result(raw.read_text(encoding="utf-8"))
        correct = None if expect is None or result is None else result["outcome"] == expect
        log.write("unsplit_end", returncode=done.returncode, result=result, correct=correct)
    if result is None:
        print(
            f"unsplit_count: no result (exit code {done.returncode}); see {path}", file=sys.stderr
        )
        return 1
    print(
        f"unsplit_count: {result['outcome']}, {result['positions']} positions searched, "
        f"{result['seconds']:.1f} s" + ("" if correct is None else f"; correct: {correct}")
    )
    return 0 if correct is not False else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.unsplit_count",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--start", required=True, help="start position ('' = empty board)")
    parser.add_argument("--expect", choices=[o.value for o in Outcome], help="known result")
    parser.add_argument("--solver", type=Path, default=SOLVER)
    parser.add_argument("--log-dir", type=Path, default=LOGS_DIR)
    parser.add_argument("--run-id", help="default: date, time and 'E1_unsplit'")
    args = parser.parse_args(argv)
    expect = None if args.expect is None else Outcome(args.expect)
    run_id = args.run_id or f"{stamp()}_E1_unsplit"
    return run(args.start, expect, args.solver, args.log_dir, run_id)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
