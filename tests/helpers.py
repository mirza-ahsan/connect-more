"""Shared pieces for the Python tests: where the solvers are, skipping tests whose solver is not
built, and starting worker processes."""

import subprocess
import sys
from pathlib import Path

import pytest

from worker.agent import Config as WorkerConfig

ROOT = Path(__file__).resolve().parent.parent

# 7x6 is built by the release preset (the worker's default solver), the small boards by the
# small-boards preset.
SOLVERS = {
    "7x6": WorkerConfig().solver,
    **{b: ROOT / f"build/small-boards/bin/c4solve_{b}.exe" for b in ("4x4", "6x4", "6x6")},
}


def needs_solver(board: str = "7x6") -> pytest.MarkDecorator:
    preset = "release" if board == "7x6" else "small-boards"
    return pytest.mark.skipif(not SOLVERS[board].exists(), reason=f"run the {preset} preset first")


def start_worker(
    name: str,
    port: int,
    log_dir: Path,
    board: str = "7x6",
    threads: int = 2,
    table_mb: int = 16,
) -> subprocess.Popen[str]:
    """A worker process (python -m worker) connecting to a coordinator on this laptop."""
    args = [
        sys.executable,
        "-m",
        "worker",
        "--coordinator",
        "127.0.0.1",
        "--port",
        str(port),
        "--name",
        name,
        "--solver",
        str(SOLVERS[board]),
        "--threads",
        str(threads),
        "--table-mb",
        str(table_mb),
        "--log-dir",
        str(log_dir),
    ]
    return subprocess.Popen(
        args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
