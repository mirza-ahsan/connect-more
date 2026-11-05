"""Small pieces shared by the experiment scripts.

Every script that produces results writes `build_info()` into its log first (CLAUDE.md: the commit,
dirty flag, compiler and flags, and the board).
"""

import subprocess
from pathlib import Path

from common.build import git_state
from worker import solver as s
from worker.agent import Config as WorkerConfig

SOLVER = WorkerConfig().solver  # the release build's 7x6 solver, as the worker uses it


def solver_header(solver: Path = SOLVER) -> list[str]:
    """The build lines a c4solve prints at start-up (commit, dirty flag, compiler, flags, board,
    table), without their '#': it is started in service mode with a tiny table and told to quit
    at once."""
    done = subprocess.run(
        [str(solver), "--threads", "1", "--table-mb", "1"],
        input="quit\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    answers = [s.parse_answer(line) for line in done.stdout.splitlines()]
    return [answer.text for answer in answers if isinstance(answer, s.Info)]


def build_info(solver: Path = SOLVER) -> dict[str, object]:
    """What a script's log starts with: the repository's state, and the solver's own build lines
    (or why they could not be read)."""
    info: dict[str, object] = {"repository": git_state(), "solver": str(solver)}
    try:
        info["solver_header"] = solver_header(solver)
    except (OSError, subprocess.SubprocessError) as e:
        info["solver_header"] = f"not available: {e}"
    return info
