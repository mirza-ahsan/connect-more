"""Starts one experiment run: uv run python -m coordinator --start 4453 --depth 3 --workers 2"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from common.messages import Outcome
from common.runlog import stamp
from coordinator import calibration
from coordinator.server import Config, Coordinator, CoordinatorError
from coordinator.strategies import STRATEGIES

DEFAULTS = Config(run_id="")


def parse_args(argv: list[str]) -> Config:
    d = DEFAULTS
    parser = argparse.ArgumentParser(prog="python -m coordinator", description=__doc__)
    parser.add_argument("--start", default=d.start, help="start position ('' = empty board)")
    parser.add_argument("--depth", type=int, default=d.depth, help="split depth")
    parser.add_argument("--strategy", default=d.strategy)
    parser.add_argument("--workers", type=int, default=d.workers, help="workers to wait for")
    parser.add_argument("--board", default=d.board)
    parser.add_argument("--expect", choices=[o.value for o in Outcome], help="known result")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--run-id", help="default: date, time and strategy")
    parser.add_argument("--timeout", type=float, default=d.timeout, help="seconds")
    parser.add_argument("--log-dir", type=Path, default=d.log_dir)
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=calibration.DEFAULT_FILE,
        help="positions with known results, for strategies that calibrate (V0)",
    )
    parser.add_argument("--calibration-count", type=int, default=calibration.DEFAULT_COUNT)
    parser.add_argument(
        "--split-after",
        type=float,
        default=d.split_after,
        help="V2 and V3: seconds a task may run before it is stopped and split",
    )
    args = parser.parse_args(argv)
    needs_calibration = args.strategy in STRATEGIES and STRATEGIES[args.strategy].needs_calibration
    return Config(
        run_id=args.run_id or f"{stamp()}_{args.strategy}",
        start=args.start,
        depth=args.depth,
        strategy=args.strategy,
        workers=args.workers,
        board=args.board,
        expect=None if args.expect is None else Outcome(args.expect),
        bind=f"tcp://*:{args.port}",
        timeout=args.timeout,
        log_dir=args.log_dir,
        split_after=args.split_after,
        calibration=(
            calibration.load_positions(args.calibration_file, args.calibration_count)
            if needs_calibration
            else ()
        ),
    )


def main(argv: list[str]) -> int:
    """0: solved (and correct, if --expect was given); 1: wrong or timed out; 2: error."""
    try:
        summary = Coordinator(parse_args(argv)).run()
    except (CoordinatorError, ValueError, OSError) as e:
        print(f"coordinator: {e}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(summary), indent=2))
    if summary.result is None or summary.correct is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
