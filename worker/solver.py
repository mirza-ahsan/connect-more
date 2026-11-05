"""Runs c4solve in service mode and talks to it (docs/protocol.md, part A, version 1).

The solver's answers are read by a background thread, turned into the dataclasses below, and
handed to a callback. Commands are written with the methods of SolverProcess.
"""

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.messages import OUTCOMES

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class Info:
    """A '#' line (build information), or a line the solver wrote to its error stream."""

    text: str


@dataclass(frozen=True)
class Ready:
    protocol: int
    board: str
    threads: int
    table_mb: int


@dataclass(frozen=True)
class Started:
    task_id: str
    thread: int
    time: float


@dataclass(frozen=True)
class Result:
    task_id: str
    outcome: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Stopped:
    task_id: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Cancelled:
    task_id: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Progress:
    positions: int
    time: float


@dataclass(frozen=True)
class Error:
    task_id: str | None  # None when the error is not about a task ("-" on the line)
    message: str


@dataclass(frozen=True)
class Bye:
    time: float


@dataclass(frozen=True)
class Exited:
    """Not a line: the solver's output ended because the process exited."""

    returncode: int | None


type Answer = (
    Info | Ready | Started | Result | Stopped | Cancelled | Progress | Error | Bye | Exited
)


class AnswerError(ValueError):
    """A line from the solver that does not follow docs/protocol.md."""


def _count(word: str) -> int:
    value = int(word)
    if value < 0:
        raise ValueError(f"negative count {word}")
    return value


def _seconds(word: str) -> float:
    value = float(word)
    if not value >= 0:  # also refuses NaN
        raise ValueError(f"bad time {word}")
    return value


def parse_answer(line: str) -> Answer:
    """Reads one line from the solver."""
    line = line.rstrip("\r\n")
    if line.startswith("#"):
        return Info(line[1:].strip())

    words = line.split(" ")
    name = words[0]
    try:
        if name == "error" and len(words) >= 3:
            task_id = None if words[1] == "-" else words[1]
            return Error(task_id, " ".join(words[2:]))
        expected = {
            "ready": 5,
            "started": 4,
            "result": 6,
            "stopped": 5,
            "cancelled": 5,
            "progress": 3,
            "bye": 2,
        }.get(name)
        if expected is None or len(words) != expected:
            raise AnswerError(f"unexpected solver line: {line!r}")
        if name == "ready":
            return Ready(int(words[1]), words[2], int(words[3]), int(words[4]))
        if name == "started":
            return Started(words[1], _count(words[2]), _seconds(words[3]))
        if name == "result":
            if words[2] not in OUTCOMES:
                raise AnswerError(f"unknown outcome in solver line: {line!r}")
            return Result(
                words[1], words[2], _count(words[3]), _seconds(words[4]), _seconds(words[5])
            )
        if name == "stopped":
            return Stopped(words[1], _count(words[2]), _seconds(words[3]), _seconds(words[4]))
        if name == "cancelled":
            return Cancelled(words[1], _count(words[2]), _seconds(words[3]), _seconds(words[4]))
        if name == "progress":
            return Progress(_count(words[1]), _seconds(words[2]))
        return Bye(_seconds(words[1]))
    except ValueError as e:
        if isinstance(e, AnswerError):
            raise
        raise AnswerError(f"bad number in solver line {line!r}: {e}") from e


class SolverProcess:
    """One running c4solve in service mode.

    `on_answer` is called from background threads (one reading the solver's output, one its
    error stream), never from two at once for the output stream; it must be quick and
    thread-safe. Lines that break the protocol arrive as Error(None, ...). The last call is
    always Exited.
    """

    def __init__(
        self,
        exe: Path,
        on_answer: Callable[[Answer], None],
        threads: int | None = None,
        table_mb: int | None = None,
    ) -> None:
        args = [str(exe)]
        if threads is not None:
            args += ["--threads", str(threads)]
        if table_mb is not None:
            args += ["--table-mb", str(table_mb)]
        self._on_answer = on_answer
        self._write_lock = threading.Lock()
        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread.start()
        self._stdout_thread.start()

    def _read_stdout(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        for line in stdout:
            try:
                answer = parse_answer(line)
            except AnswerError as e:
                answer = Error(None, str(e))
            self._on_answer(answer)
        # Output ended: wait for the stderr reader too, so Exited really is the last call.
        self._stderr_thread.join()
        self._on_answer(Exited(self._process.wait()))

    def _read_stderr(self) -> None:
        stderr = self._process.stderr
        assert stderr is not None
        for line in stderr:
            self._on_answer(Info("stderr: " + line.rstrip("\r\n")))

    def _send(self, line: str) -> None:
        stdin = self._process.stdin
        assert stdin is not None
        with self._write_lock:
            try:
                stdin.write(line + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass  # the solver has gone; Exited reports it

    def solve(self, task_id: str, moves: str) -> None:
        self._send(f"solve {task_id} {moves or '-'}")

    def stop(self, task_id: str) -> None:
        self._send(f"stop {task_id}")

    def cancel(self, task_id: str) -> None:
        self._send(f"cancel {task_id}")

    def quit(self) -> None:
        self._send("quit")

    def wait(self, timeout: float | None = None) -> int:
        """Waits for the process to exit and returns its exit code."""
        code = self._process.wait(timeout)
        self._stdout_thread.join(timeout)
        return code

    def kill(self) -> None:
        self._process.kill()
