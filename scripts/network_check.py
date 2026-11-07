"""Measures a network link between the two machines (methodology: "Network measurements"; RQ4).

Run on Laptop 1 at the start of every session, once per link, while the PC runs its worker role
(scripts/run_experiment.py) or `uv run python -m scripts.collect_logs serve`; either one answers
the echo requests and runs `iperf3 -s` if iperf3 is installed:
    uv run python -m scripts.network_check --link cable
(--peer gives the PC's address; by default it comes from scripts/experiment.toml.)

It records, in logs/network/<date-time>_<link>.jsonl:
- ping: the round-trip times Linux `ping` reports, in milliseconds (its raw output is logged);
- the round trip of one short message through ZeroMQ on the messaging port (TCP 5555), the same
  library and port the experiments use, with a message the size of a `task`; every sample is kept.
  It is a plain request and reply (REQ/REP, answered by a thread of the PC's worker role), so
  it is the network and library part of a message's cost: a lower bound, without the decoding
  and handling a real message gets on each side;
- ZeroMQ transfer speed: 1 MiB messages echoed back, counted in both directions;
- iperf3 (TCP 5201, both directions), only if iperf3 is installed on this machine; otherwise
  "not installed" is recorded. Nothing is installed by this script.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import zmq

from common import messages as m
from common.machines import PEER
from common.runlog import LOGS_DIR, RunLog, log_path, stamp
from scripts.helpers import PORT, build_info, load_settings, requester

PING_COUNT = 20
ROUND_TRIPS = 500
WARM_UP = 10  # messages sent first and not counted, so that connecting is not measured
BULK_BYTES = 1 << 20  # one MiB per transfer message
BULK_COUNT = 20
TIMEOUT_MS = 5000
ECHO = json.dumps({"op": "echo"}).encode()
# A message as big as a real `task` (task id and moves of a depth-4 task from a long start).
SAMPLE = m.encode(m.Task("p" + "4" * 20, "4" * 20))


def ping(peer: str, count: int) -> dict:
    """Runs Linux `ping -c count -n peer` (numeric output, C locale); keeps its output and the
    round-trip time of each reply ("time=0.123 ms"), in ms. Duplicate replies ("DUP!") are left
    out, so they count neither as replies nor as times."""
    try:
        done = subprocess.run(
            ["ping", "-c", str(count), "-n", peer],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=count * 5 + 30,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (subprocess.TimeoutExpired, OSError) as e:  # too slow, or no ping installed
        return {"output": str(e), "times_ms": [], **summary([]), "lost": None}
    replies = [line for line in done.stdout.splitlines() if "DUP!" not in line]
    times = [float(found[1]) for line in replies if (found := re.search(r"time=([\d.]+) ms", line))]
    return {"output": done.stdout, "times_ms": times, **summary(times), "lost": count - len(times)}


def summary(values: list[float]) -> dict[str, float | None]:
    """Count, minimum, median, mean, 95th percentile and maximum (None when there are none)."""
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p95": ordered[round(0.95 * (len(ordered) - 1))],
        "max": ordered[-1],
    }


def _echo(socket: zmq.Socket, payload: bytes) -> bytes:
    socket.send_multipart([ECHO, payload])
    return socket.recv_multipart()[1]


def zmq_round_trips(endpoint: str, count: int) -> list[float]:
    """Seconds for each of `count` task-sized messages to go to the echo side and back."""
    with requester(endpoint, TIMEOUT_MS) as socket:
        for _ in range(WARM_UP):
            _echo(socket, SAMPLE)
        times = []
        for _ in range(count):
            sent = time.perf_counter()
            reply = _echo(socket, SAMPLE)
            times.append(time.perf_counter() - sent)
            if reply != SAMPLE:
                raise RuntimeError("the echo side sent back something else")
        return times


def zmq_transfer(endpoint: str, size: int, count: int) -> dict[str, float]:
    """Megabits per second through ZeroMQ: `count` messages of `size` bytes echoed back, both
    directions counted."""
    payload = bytes(size)
    with requester(endpoint, TIMEOUT_MS) as socket:
        _echo(socket, payload)  # connecting is not counted
        start = time.perf_counter()
        for _ in range(count):
            _echo(socket, payload)
        seconds = time.perf_counter() - start
    mbit_s = 2 * size * count * 8 / seconds / 1e6
    return {"bytes_each_way": size * count, "seconds": seconds, "mbit_s": mbit_s}


def iperf3(peer: str, seconds: int = 5) -> dict:
    """iperf3 in both directions (this machine sending, then receiving), if it is installed:
    status, Mbit/s for each direction that worked, and iperf3's own reports (or errors)."""
    program = shutil.which("iperf3")
    if program is None:
        return {"status": "not installed", "mbit_s": {}}
    mbit_s: dict[str, float] = {}
    reports: dict[str, object] = {}
    for direction, extra in (("send", []), ("receive", ["-R"])):
        try:
            done = subprocess.run(
                [program, "-c", peer, "-t", str(seconds), "-J", *extra],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=seconds + 60,
            )
        except subprocess.TimeoutExpired as e:
            reports[direction] = {"error": str(e)}
            continue
        try:
            reports[direction] = report = json.loads(done.stdout)
            mbit_s[direction] = report["end"]["sum_received"]["bits_per_second"] / 1e6
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            reports[direction] = {"error": f"{e}", "output": done.stdout + done.stderr}
    status = "ok" if len(mbit_s) == 2 else "failed (is the PC's server running?)"
    return {"status": status, "mbit_s": mbit_s, "reports": reports}


def measure(peer: str, link: str, port: int, log_dir: Path) -> int:
    endpoint = f"tcp://{peer}:{port}"
    with RunLog(log_path("network", f"{stamp()}_{link}", log_dir)) as log:
        log.write("network_check_start", peer=peer, link=link, port=port, build=build_info())
        print(f"network_check: {link} link to {peer}")

        pinged = ping(peer, PING_COUNT)
        log.write("ping", **pinged)
        print(f"  ping: median {pinged['median']} ms, lost {pinged['lost']} of {PING_COUNT}")

        try:
            trips = zmq_round_trips(endpoint, ROUND_TRIPS)
            transfer = zmq_transfer(endpoint, BULK_BYTES, BULK_COUNT)
        except (zmq.Again, RuntimeError) as e:
            log.write("zmq_failed", error=f"{type(e).__name__}: {e}")
            print(f"  ZeroMQ: no echo from {endpoint} (is the PC's server running?)")
            return 1
        stats = summary(trips)
        log.write("zmq_round_trips", seconds=trips, message_bytes=len(SAMPLE), **stats)
        log.write("zmq_transfer", **transfer)
        print(
            f"  ZeroMQ round trip ({len(SAMPLE)} bytes): median "
            f"{statistics.median(trips) * 1e6:.0f} us; transfer {transfer['mbit_s']:.0f} Mbit/s"
        )

        throughput = iperf3(peer)
        log.write("iperf3", **throughput)
        speeds = ", ".join(f"{d} {v:.0f} Mbit/s" for d, v in throughput["mbit_s"].items())
        print(f"  iperf3: {throughput['status']} {speeds}")
        print(f"  log: {log.path}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.network_check",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--link", required=True, choices=["cable", "wifi", "local"])
    parser.add_argument("--peer", help="The PC's address (default: from the settings)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--log-dir", type=Path, default=LOGS_DIR)
    args = parser.parse_args(argv)
    peer = args.peer or load_settings()["network"][PEER].get(args.link)
    if not peer:
        parser.error(f"no address for the PC on {args.link}: give --peer")
    return measure(peer, args.link, args.port, args.log_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
