"""What code a run used: the repository's state, and the solver's own build line.

The solver prints its build at start-up (`# commit 76208ebc0e2a, dirty false`, then compiler,
flags and board); `parse_commit` reads that line for the worker and the scripts. The Python side
(coordinator, worker, scripts) also affects results, so its logs record `git_state()` as well.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git_state() -> dict[str, object]:
    """The repository's commit and whether it has uncommitted changes, new files included, as the
    solver's own build check counts them (cmake/build_info.cmake); ignored files such as logs/ do
    not count. None and None if git cannot be asked (not installed, or it refuses the folder)."""

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "--no-optional-locks", *args],
                cwd=ROOT,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = git("rev-parse", "--short=12", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=normal")
    return {"commit": commit, "dirty": None if status is None else bool(status)}


def parse_commit(text: str) -> tuple[str, bool] | None:
    """The commit and dirty flag from a solver build line "commit 76208ebc0e2a, dirty false"
    (without the leading '#'); None for any other line."""
    words = text.replace(",", "").split()
    if len(words) == 4 and words[0] == "commit" and words[2] == "dirty":
        return words[1], words[3] != "false"
    return None
