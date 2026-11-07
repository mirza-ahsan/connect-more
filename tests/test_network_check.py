"""Tests for scripts/network_check.py, on this machine only. The echo side is the PC's real
server (scripts/collect_logs.py), run in a thread."""

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import zmq

from scripts import collect_logs
from scripts import network_check as nc
from tests.helpers import free_port, read_log


@pytest.fixture
def echo_port(tmp_path: Path) -> Iterator[int]:
    port = free_port()
    stop = threading.Event()
    thread = threading.Thread(target=collect_logs.serve, args=(tmp_path, port, stop), daemon=True)
    thread.start()
    yield port
    stop.set()
    thread.join(5)


def test_summary() -> None:
    assert nc.summary([3.0, 1.0, 2.0]) == {
        "count": 3,
        "min": 1.0,
        "median": 2.0,
        "mean": 2.0,
        "p95": 3.0,
        "max": 3.0,
    }
    assert nc.summary([])["median"] is None


def test_zmq_round_trips_and_transfer(echo_port: int) -> None:
    endpoint = f"tcp://127.0.0.1:{echo_port}"
    trips = nc.zmq_round_trips(endpoint, count=50)
    assert len(trips) == 50 and all(0 < t < 1 for t in trips)
    transfer = nc.zmq_transfer(endpoint, size=1000, count=5)
    assert transfer["bytes_each_way"] == 5000 and transfer["mbit_s"] > 0


def test_no_echo_side_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nc, "TIMEOUT_MS", 200)
    context = zmq.Context()
    port = context.socket(zmq.REP).bind_to_random_port("tcp://127.0.0.1")  # bound, never answers
    try:
        with pytest.raises(zmq.Again):
            nc.zmq_round_trips(f"tcp://127.0.0.1:{port}", count=1)
    finally:
        context.destroy(linger=0)


def test_ping_parses_linux_output() -> None:
    result = nc.ping("127.0.0.1", count=1)
    assert result["count"] == 1 and result["lost"] == 0 and result["times_ms"][0] >= 0


def test_ping_leaves_out_duplicate_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    output = (
        "PING 192.168.2.2 (192.168.2.2) 56(84) bytes of data.\n"
        "64 bytes from 192.168.2.2: icmp_seq=1 ttl=64 time=0.412 ms\n"
        "64 bytes from 192.168.2.2: icmp_seq=1 ttl=64 time=0.415 ms (DUP!)\n"
        "64 bytes from 192.168.2.2: icmp_seq=3 ttl=64 time=1.25 ms\n"
    )
    done = subprocess.CompletedProcess([], 0, stdout=output, stderr="")
    monkeypatch.setattr(nc.subprocess, "run", lambda *args, **kwargs: done)
    result = nc.ping("192.168.2.2", count=3)
    assert result["times_ms"] == [0.412, 1.25] and result["lost"] == 1


def test_measure_writes_a_log(
    echo_port: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nc, "PING_COUNT", 1)
    monkeypatch.setattr(nc, "ROUND_TRIPS", 20)
    monkeypatch.setattr(nc, "BULK_COUNT", 2)
    monkeypatch.setattr(nc, "iperf3", lambda peer: {"status": "not installed", "mbit_s": {}})
    argv = ["--link", "local", "--peer", "127.0.0.1", "--port", str(echo_port)]
    assert nc.main([*argv, "--log-dir", str(tmp_path / "logs")]) == 0
    (log,) = (tmp_path / "logs/network").glob("*_local.jsonl")
    records = {r["event"]: r for r in read_log(log)}
    assert list(records) == [
        "network_check_start",
        "ping",
        "zmq_round_trips",
        "zmq_transfer",
        "iperf3",
    ]
    assert records["network_check_start"]["build"]["repository"]["commit"]
    assert records["zmq_round_trips"]["count"] == 20
    assert records["iperf3"]["status"] == "not installed"
