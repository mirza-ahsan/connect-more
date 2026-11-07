"""Tests for common/messages.py: the coordinator <-> worker JSON messages (docs/protocol.md, B)."""

import json
from pathlib import Path

import pytest

from common import messages as m

# One valid example of every message type.
EXAMPLES: list[m.Message] = [
    m.Hello("pc", 8, "7x6", 1024, "76208ebc0e2a", False, 1793600000.12, 5123.4),
    m.GiveMeWork(16),
    m.Started("t17", 3, 12.004),
    m.Result("t17", "loss", 31799719, 2.541, 14.545),
    m.Stopped("t17", 400, 0.5, 13.0),
    m.Cancelled("t18", 0, 0.0, 13.1),
    m.Progress(8104577, 1.0),
    m.ClockReply(4, 1793600001.5, 5124.8),
    m.Error("t9", "no task with this id"),
    m.Error(None, "solver exited"),
    m.Welcome("pc", "2026-11-04_V1_cable_r1", "V1"),
    m.Task("t17", "4453"),
    m.Task("t0", ""),
    m.NoWork(),
    m.Stop("t17"),
    m.Cancel("t17"),
    m.ClockCheck(4, 100.25),
    m.RunOver(),
]


def raw(message: m.Message) -> dict[str, object]:
    return json.loads(m.encode(message))


def broken(message: m.Message, **changes: object) -> bytes:
    """The message's JSON with some fields changed (None removes a field)."""
    data = raw(message)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data).encode()


def test_every_type_has_an_example() -> None:
    assert {type(e) for e in EXAMPLES} == set(m.BY_TYPE.values())


@pytest.mark.parametrize("message", EXAMPLES, ids=lambda e: type(e).__name__)
def test_round_trip(message: m.Message) -> None:
    data = m.encode(message)
    assert m.decode(data) == message
    assert m.decode(data.decode()) == message  # str works as well as bytes
    fields = raw(message)
    assert fields["type"] == message.TYPE
    assert fields["v"] == m.PROTOCOL_VERSION


def test_directions_cover_every_type_once() -> None:
    both = m.WORKER_TO_COORDINATOR + m.COORDINATOR_TO_WORKER
    assert len(both) == len(set(both)) == len(m.BY_TYPE)


def test_expect_filters_by_direction() -> None:
    task = m.encode(m.Task("t1", "44"))
    assert m.decode(task, expect=m.COORDINATOR_TO_WORKER) == m.Task("t1", "44")
    with pytest.raises(m.MessageError, match="not expected"):
        m.decode(task, expect=m.WORKER_TO_COORDINATOR)


def test_examples_in_protocol_document_decode() -> None:
    """Every JSON example in docs/protocol.md must be a valid message."""
    doc = Path(__file__).resolve().parent.parent / "docs" / "protocol.md"
    text = doc.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith('{"type"')]
    assert len(lines) >= 6
    for line in lines:
        m.decode(line)


def test_float_fields_accept_whole_numbers() -> None:
    message = m.decode(broken(m.Progress(5, 1.5), time=2))
    assert isinstance(message, m.Progress)
    assert message.time == 2.0
    assert isinstance(message.time, float)


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"not json", "not valid JSON"),
        (b"\xff\xfe", "not valid JSON"),
        (b"[1, 2]", "JSON object"),
        (broken(m.NoWork(), v=2), "protocol version"),
        (broken(m.NoWork(), v=None), "protocol version"),
        (broken(m.NoWork(), v="1"), "protocol version"),
        (broken(m.NoWork(), type="dance"), "unknown message type"),
        (broken(m.NoWork(), type=None), "unknown message type"),
        (broken(m.NoWork(), type=5), "unknown message type"),
        (broken(m.GiveMeWork(1), free=None), "missing fields"),
        (broken(m.GiveMeWork(1), extra=1), "unknown fields"),
        (broken(m.GiveMeWork(1), free="3"), "wrong type"),
        (broken(m.GiveMeWork(1), free=2.5), "wrong type"),
        (broken(m.GiveMeWork(1), free=True), "wrong type"),
        (broken(m.GiveMeWork(1), free=-1), "negative"),
        (broken(m.Progress(1, 1.0), time="soon"), "wrong type"),
        (broken(m.Progress(1, 1.0), time=False), "wrong type"),
        (broken(m.Progress(1, 1.0), time=-0.5), "negative"),
        (broken(m.Hello("a", 8, "7x6", 1, "c", False, 1.0, 1.0), dirty=0), "wrong type"),
        (broken(m.Hello("a", 8, "7x6", 1, "c", False, 1.0, 1.0), threads=0), "threads"),
        (broken(m.Hello("a", 8, "7x6", 1, "c", False, 1.0, 1.0), board="7 by 6"), "board"),
        (broken(m.Result("t1", "win", 1, 1.0, 1.0), outcome="WIN"), "outcome"),
        (broken(m.Welcome("a", "r", "V1"), strategy="V4"), "strategy"),
        (broken(m.Task("t1", "44"), moves="408"), "moves"),
        (broken(m.Task("t1", "44"), moves="-"), "moves"),
        (broken(m.Task("t1", "44"), task_id="has space"), "task_id"),
        (broken(m.Task("t1", "44"), task_id="x" * 65), "task_id"),
        (broken(m.Task("t1", "44"), task_id=""), "task_id"),
        (broken(m.Error(None, "x"), task_id=7), "wrong type"),
    ],
)
def test_bad_messages_are_refused(data: bytes, problem: str) -> None:
    with pytest.raises(m.MessageError, match=problem):
        m.decode(data)


def test_encode_refuses_bad_messages() -> None:
    with pytest.raises(m.MessageError, match="outcome"):
        m.encode(m.Result("t1", "maybe", 1, 1.0, 1.0))
    with pytest.raises(m.MessageError, match="task_id"):
        m.encode(m.Stop("bad id"))
    with pytest.raises(m.MessageError, match="not a protocol message"):
        m.encode("hello")  # pyrefly: ignore[bad-argument-type]


def test_messages_are_frozen_and_picklable() -> None:
    import pickle

    message = m.Task("t1", "4453")
    assert pickle.loads(pickle.dumps(message)) == message
    with pytest.raises(AttributeError):
        message.moves = "1"  # pyrefly: ignore[read-only]
