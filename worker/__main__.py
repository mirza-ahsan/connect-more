"""Starts a worker: uv run python -m worker --coordinator 192.168.2.1 --name laptop2"""

import argparse
import sys
from pathlib import Path

from worker.agent import Config, Worker, WorkerError


def parse_args(argv: list[str]) -> Config:
    defaults = Config()
    parser = argparse.ArgumentParser(prog="python -m worker", description=__doc__)
    parser.add_argument("--coordinator", default=defaults.coordinator, help="coordinator address")
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--name", default=defaults.name, help="worker name, e.g. laptop2")
    parser.add_argument("--solver", type=Path, default=defaults.solver, help="c4solve executable")
    parser.add_argument("--threads", type=int, help="solver threads (default: all)")
    parser.add_argument("--table-mb", type=int, help="table size (default: the solver's)")
    parser.add_argument("--log-dir", type=Path, default=defaults.log_dir)
    parser.add_argument(
        "--silence-limit",
        type=float,
        default=defaults.silence_limit,
        help="seconds without a message from the coordinator before giving up",
    )
    args = parser.parse_args(argv)
    return Config(
        coordinator=args.coordinator,
        port=args.port,
        name=args.name,
        solver=args.solver,
        threads=args.threads,
        table_mb=args.table_mb,
        log_dir=args.log_dir,
        silence_limit=args.silence_limit,
    )


def main(argv: list[str]) -> int:
    config = parse_args(argv)
    try:
        return Worker(config).run()
    except WorkerError as e:
        print(f"worker {config.name}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
