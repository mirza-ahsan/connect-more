"""Small pieces shared by the experiment scripts.

Every script that produces results writes `build_info()` into its log first (CLAUDE.md: the commit,
dirty flag, compiler and flags, and the board).
"""

import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import zmq

from common.build import git_state
from worker import solver as s
from worker.agent import Config as WorkerConfig

SOLVER = WorkerConfig().solver  # the release build's 7x6 solver, as the worker uses it
PORT = WorkerConfig().port  # the messaging port, TCP 5555 (the firewall allows it on both)
SETTINGS = Path(__file__).with_name("experiment.toml")


def load_settings(path: Path = SETTINGS) -> dict:
    """The experiment settings (scripts/experiment.toml)."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


@contextmanager
def requester(endpoint: str, timeout_ms: int) -> Iterator[zmq.Socket]:
    """A ZeroMQ REQ socket connected to `endpoint` that gives up (zmq.Again) after `timeout_ms`
    instead of waiting for ever."""
    context = zmq.Context()
    try:
        socket = context.socket(zmq.REQ)
        socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        socket.setsockopt(zmq.SNDTIMEO, timeout_ms)
        socket.connect(endpoint)
        yield socket
    finally:
        context.destroy(linger=0)


@contextmanager
def iperf_server() -> Iterator[str]:
    """Runs `iperf3 -s` (TCP 5201) quietly while the block runs, if iperf3 is installed (nothing
    is installed here). Gives its state: "on", "not installed", or "failed to start" (for
    example when port 5201 is taken)."""
    program = shutil.which("iperf3")
    if program is None:
        yield "not installed"
        return
    server = subprocess.Popen([program, "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with suppress(subprocess.TimeoutExpired):
        server.wait(1.0)  # it only exits this soon if it could not start
    if server.poll() is not None:
        yield "failed to start"
        return
    try:
        yield "on"
    finally:
        server.terminate()
        server.wait(10)


def solver_header(solver: Path = SOLVER) -> list[str]:
    """The build lines a c4solve prints at start-up (version, commit and dirty flag, compiler and
    flags, board), without their '#'. It is started in service mode with a tiny table and told
    to quit at once; the lines about that start (table, threads, mode) describe this probe, not
    a run, so they are left out."""
    done = subprocess.run(
        [str(solver), "--threads", "1", "--table-mb", "1"],
        input="quit\n",
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    answers = [s.parse_answer(line) for line in done.stdout.splitlines()]
    lines = [answer.text for answer in answers if isinstance(answer, s.Info)]
    return [line for line in lines if line.split(" ", 1)[0] not in ("table", "threads", "mode")]


def build_info(solver: Path = SOLVER) -> dict:
    """What a script's log starts with: the repository's state, and the solver's own build lines
    (or why they could not be read)."""
    info: dict = {"repository": git_state(), "solver": str(solver)}
    try:
        info["solver_header"] = solver_header(solver)
    except (OSError, subprocess.SubprocessError) as e:
        info["solver_header"] = f"not available: {e}"
    return info
