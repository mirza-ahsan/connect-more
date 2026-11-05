"""JSON messages between the coordinator and the workers (docs/protocol.md, part B, version 1).

Both sides build and read messages only through this module, so they always agree on the format.
Every message is a frozen dataclass. `encode` turns one into the bytes sent over ZeroMQ, and
`decode` turns received bytes back into a message, checking every field on the way. Keep this
file and the document in step.
"""

import dataclasses
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, get_type_hints

PROTOCOL_VERSION = 1

TASK_ID = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
MOVES = re.compile(r"[1-9]*")  # "" is the empty board
BOARD = re.compile(r"([1-9])x([1-9][0-9]?)")


class Outcome(StrEnum):
    """The result of a position for the player to move, with perfect play. The same words are
    used in the solver's answer lines (part A) and in `result` messages (part B)."""

    WIN = "win"
    DRAW = "draw"
    LOSS = "loss"


OUTCOMES = tuple(outcome.value for outcome in Outcome)
STRATEGIES = ("V0", "V1", "V2", "V3")


class MessageError(ValueError):
    """A message that does not follow docs/protocol.md."""


# --- Worker -> coordinator -------------------------------------------------------------------


@dataclass(frozen=True)
class Hello:
    TYPE: ClassVar[str] = "hello"
    worker: str
    threads: int
    board: str
    table_mb: int
    commit: str
    dirty: bool
    wall_time: float
    mono_time: float


@dataclass(frozen=True)
class GiveMeWork:
    TYPE: ClassVar[str] = "give_me_work"
    free: int


@dataclass(frozen=True)
class Started:
    TYPE: ClassVar[str] = "started"
    task_id: str
    thread: int
    time: float


@dataclass(frozen=True)
class Result:
    TYPE: ClassVar[str] = "result"
    task_id: str
    outcome: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Stopped:
    TYPE: ClassVar[str] = "stopped"
    task_id: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Cancelled:
    TYPE: ClassVar[str] = "cancelled"
    task_id: str
    positions: int
    seconds: float
    time: float


@dataclass(frozen=True)
class Progress:
    TYPE: ClassVar[str] = "progress"
    positions: int
    time: float


@dataclass(frozen=True)
class ClockReply:
    TYPE: ClassVar[str] = "clock_reply"
    check_id: int
    wall_time: float
    mono_time: float


@dataclass(frozen=True)
class Error:
    TYPE: ClassVar[str] = "error"
    task_id: str | None
    message: str


# --- Coordinator -> worker -------------------------------------------------------------------


@dataclass(frozen=True)
class Welcome:
    TYPE: ClassVar[str] = "welcome"
    worker: str
    run_id: str
    strategy: str


@dataclass(frozen=True)
class Task:
    TYPE: ClassVar[str] = "task"
    task_id: str
    moves: str


@dataclass(frozen=True)
class NoWork:
    TYPE: ClassVar[str] = "no_work"


@dataclass(frozen=True)
class Stop:
    TYPE: ClassVar[str] = "stop"
    task_id: str


@dataclass(frozen=True)
class Cancel:
    TYPE: ClassVar[str] = "cancel"
    task_id: str


@dataclass(frozen=True)
class ClockCheck:
    TYPE: ClassVar[str] = "clock_check"
    check_id: int
    sent_time: float


@dataclass(frozen=True)
class RunOver:
    TYPE: ClassVar[str] = "run_over"


type Message = (
    Hello
    | GiveMeWork
    | Started
    | Result
    | Stopped
    | Cancelled
    | Progress
    | ClockReply
    | Error
    | Welcome
    | Task
    | NoWork
    | Stop
    | Cancel
    | ClockCheck
    | RunOver
)

WORKER_TO_COORDINATOR: tuple[type, ...] = (
    Hello,
    GiveMeWork,
    Started,
    Result,
    Stopped,
    Cancelled,
    Progress,
    ClockReply,
    Error,
)
COORDINATOR_TO_WORKER: tuple[type, ...] = (
    Welcome,
    Task,
    NoWork,
    Stop,
    Cancel,
    ClockCheck,
    RunOver,
)
BY_TYPE: dict[str, type] = {cls.TYPE: cls for cls in WORKER_TO_COORDINATOR + COORDINATOR_TO_WORKER}

# Each message's fields and their types, worked out once: decode needs them for every message.
FIELD_TYPES: dict[type, dict[str, object]] = {
    cls: {f.name: get_type_hints(cls)[f.name] for f in dataclasses.fields(cls)}
    for cls in BY_TYPE.values()
}


# --- Checking --------------------------------------------------------------------------------


def _matches(value: object, kind: object) -> bool:
    """True if a JSON value has the Python type of a field. bool never counts as a number."""
    if kind is bool:
        return isinstance(value, bool)
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is float:
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind is str:
        return isinstance(value, str)
    if kind == str | None:
        return value is None or isinstance(value, str)
    raise TypeError(f"field type {kind!r} is not supported")


def _check_values(name: str, values: dict[str, Any]) -> None:
    """Checks the rules from docs/protocol.md that go beyond the field types."""

    task_id = values.get("task_id")
    if isinstance(task_id, str) and not TASK_ID.fullmatch(task_id):
        raise MessageError(f"{name}: invalid task_id {task_id!r}")
    if "moves" in values and not MOVES.fullmatch(values["moves"]):
        raise MessageError(f"{name}: invalid moves {values['moves']!r}")
    if "outcome" in values and values["outcome"] not in OUTCOMES:
        raise MessageError(f"{name}: outcome must be one of {OUTCOMES}, not {values['outcome']!r}")
    if "strategy" in values and values["strategy"] not in STRATEGIES:
        raise MessageError(f"{name}: strategy must be one of {STRATEGIES}")
    if "board" in values and not BOARD.fullmatch(values["board"]):
        raise MessageError(f"{name}: invalid board {values['board']!r}")
    if "threads" in values and values["threads"] < 1:
        raise MessageError(f"{name}: threads must be at least 1")
    for field in ("free", "positions", "thread", "table_mb", "check_id"):
        if field in values and values[field] < 0:
            raise MessageError(f"{name}: {field} must not be negative")
    for field in ("seconds", "time"):
        if field in values and values[field] < 0:
            raise MessageError(f"{name}: {field} must not be negative")


def encode(message: Message) -> bytes:
    """The bytes to send for a message. Checks it first, so a bad message is never sent."""
    if type(message) not in BY_TYPE.values():
        raise MessageError(f"not a protocol message: {message!r}")
    values = dataclasses.asdict(message)
    _check_values(type(message).__name__, values)
    data = {"type": message.TYPE, "v": PROTOCOL_VERSION, **values}
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def as_dict(message: Message) -> dict[str, Any]:
    """A message as a plain dict with its type first, for log lines."""
    return {"type": message.TYPE, **dataclasses.asdict(message)}


def parse_board(board: str) -> tuple[int, int]:
    """Width and height from a board size such as "7x6"."""
    match = BOARD.fullmatch(board)
    if match is None:
        raise ValueError(f"invalid board size {board!r}; expected WIDTHxHEIGHT, e.g. 7x6")
    return int(match.group(1)), int(match.group(2))


def decode(data: bytes | str, expect: tuple[type, ...] | None = None) -> Message:
    """Reads one received message. Raises MessageError if it breaks any rule of the protocol,
    or if `expect` is given and the message is not one of those types."""
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        raw = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise MessageError(f"not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise MessageError("a message must be a JSON object")

    if raw.get("v") != PROTOCOL_VERSION:
        raise MessageError(f"protocol version {raw.get('v')!r}, expected {PROTOCOL_VERSION}")
    type_name = raw.get("type")
    cls = BY_TYPE.get(type_name) if isinstance(type_name, str) else None
    if cls is None:
        raise MessageError(f"unknown message type {raw.get('type')!r}")
    if expect is not None and cls not in expect:
        raise MessageError(f"message type {cls.TYPE!r} is not expected here")

    hints = FIELD_TYPES[cls]
    fields = set(hints)
    given = set(raw) - {"type", "v"}
    if given != fields:
        missing = sorted(fields - given)
        extra = sorted(given - fields)
        raise MessageError(f"{cls.TYPE}: missing fields {missing}, unknown fields {extra}")

    values: dict[str, Any] = {}
    for name in fields:
        value = raw[name]
        if not _matches(value, hints[name]):
            raise MessageError(f"{cls.TYPE}: field {name!r} has the wrong type ({value!r})")
        values[name] = float(value) if hints[name] is float else value

    _check_values(cls.__name__, values)
    return cls(**values)
