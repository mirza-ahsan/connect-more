"""Copies the PC's logs to Laptop 1 after each run, and backs every log up after a session.

The PC offers its logs folder, read only, on the messaging port while no run of its own is going
on (the worker role of scripts/run_experiment.py does this all session by itself):
    uv run python -m scripts.collect_logs serve
Laptop 1 copies what is new or has grown into logs/pc/ (scripts/run_experiment.py does this
after each run; do it once more at the end of a session, after the sensor logs have been stopped):
    uv run python -m scripts.collect_logs pull --peer 192.168.2.2
After a session, everything in logs/ is copied to a second place (a USB drive, another disk):
    uv run python -m scripts.collect_logs backup --to /run/media/$USER/<drive>/connect-more-backup

How copies work: ZeroMQ on TCP 5555, which the firewall already allows on both machines, so no admin
setup and no new tools are needed. This is a separate tool, not part of the run protocol in
docs/protocol.md. Each request is a JSON frame ({"op": "list"}, or {"op": "get", "path", "offset",
"size"}); each answer is a JSON frame and a data frame. A file is sent up to the size it had when
it was listed, in pieces of CHUNK bytes; the last piece comes with the SHA-256 of exactly those
bytes, which the receiving side checks. The same server answers {"op": "echo"} by sending its
second frame back (for scripts/network_check.py); `serve` from the command line also starts
`iperf3 -s` if iperf3 is installed, so one server on the PC serves both scripts.

Raw logs are never changed. Logs only ever grow, so a copy that is shorter than the original is
completed from where it ends; it replaces the old copy only once the whole of it matches the
original. If the original turns out to have been rewritten (the old copy is not its beginning),
the old copy is moved aside under another name and kept, and the new copy takes its place, so
later copies carry on from it. Paths that would
lead outside the logs folder are refused on both sides.
"""

import argparse
import hashlib
import json
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import zmq

from common.machines import PEER_DIR
from common.runlog import LOGS_DIR, stamp
from scripts.helpers import PORT, iperf_server, requester

CHUNK = 4 << 20  # bytes per piece
TIMEOUT_MS = 30_000
POLL_MS = 200
PARTIAL = ".partial"  # a copy being made; never offered, copied or backed up


def sha256(path: Path, size: int | None = None) -> str:
    """The SHA-256 of a file, or of its first `size` bytes."""
    digest = hashlib.sha256()
    left = path.stat().st_size if size is None else size
    with path.open("rb") as f:
        while left > 0 and (piece := f.read(min(1 << 20, left))):
            digest.update(piece)
            left -= len(piece)
    return digest.hexdigest()


def list_files(root: Path) -> dict[str, int]:
    """Every file under `root`, but copies being made: relative POSIX path -> size."""
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(PARTIAL)
    }


def inside(root: Path, relative: str) -> Path:
    """The file `relative` (a POSIX path) names under `root`; ValueError if it could lead
    anywhere else (backslashes, drive letters, '..', absolute paths, links out)."""
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or "\\" in relative
        or ":" in relative
        or ".." in parts
        or PurePosixPath(relative).is_absolute()
        or relative.endswith(PARTIAL)
    ):
        raise ValueError(f"not a path inside the logs folder: {relative!r}")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"not a path inside the logs folder: {relative!r}")
    return path


# --- Serving (the PC) -----------------------------------------------------------------------


def serve(root: Path, port: int = PORT, stop: threading.Event | None = None) -> None:
    """Answers list, get and echo requests for the files under `root` until `stop` is set (or
    Ctrl+C). Binds on every address, so both links work."""
    context = zmq.Context()
    try:
        socket = context.socket(zmq.REP)
        socket.bind(f"tcp://*:{port}")
        while stop is None or not stop.is_set():
            if socket.poll(POLL_MS):
                request, *frames = socket.recv_multipart()
                header, reply = _answer(root, request, frames)
                socket.send_multipart([json.dumps(header).encode(), reply])
    except KeyboardInterrupt:
        pass
    finally:
        context.destroy(linger=0)


def _answer(root: Path, request: bytes, frames: list[bytes]) -> tuple[dict[str, object], bytes]:
    try:
        ask = json.loads(request)
        op = ask.get("op")
        if op == "echo":
            return {}, frames[0] if frames else b""
        if op == "list":
            return {"files": list_files(root)}, b""
        if op == "get":
            path = inside(root, ask["path"])
            offset, size = int(ask["offset"]), int(ask["size"])
            if not 0 <= offset <= size <= path.stat().st_size:
                raise ValueError(f"bad range {offset}..{size} for {ask['path']}")
            with path.open("rb") as f:
                f.seek(offset)
                data = f.read(min(CHUNK, size - offset))
            header: dict[str, object] = {}
            if offset + len(data) == size:
                header["sha256"] = sha256(path, size)  # of exactly the bytes sent
            return header, data
        return {"error": f"unknown request {ask!r}"}, b""
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        return {"error": f"{type(e).__name__}: {e}"}, b""


# --- Pulling (Laptop 1) -----------------------------------------------------------------------


@dataclass
class Report:
    copied: list[str] = field(default_factory=list)
    unchanged: int = 0
    problems: list[str] = field(default_factory=list)


def _ask(socket: zmq.Socket, request: dict) -> tuple[dict, bytes]:
    socket.send(json.dumps(request).encode())
    header, data = socket.recv_multipart()
    answer = json.loads(header)
    if "error" in answer:
        raise OSError(answer["error"])
    return answer, data


def pull(peer: str, logs: Path = LOGS_DIR, port: int = PORT) -> Report:
    """Copies every file of the peer's logs folder that is missing here or has grown since it was
    last copied, into logs/pc/. Raises OSError if the peer does not answer."""
    into = logs / PEER_DIR
    report = Report()
    try:
        with requester(f"tcp://{peer}:{port}", TIMEOUT_MS) as socket:
            remote, _ = _ask(socket, {"op": "list"})
            for relative, size in remote["files"].items():
                try:
                    target = inside(into, relative)
                    have = target.stat().st_size if target.exists() else 0
                    if target.exists() and have == size:
                        report.unchanged += 1
                    elif have > size:
                        report.problems.append(f"{relative}: the copy here is larger; left alone")
                    else:
                        problem = _fetch(socket, relative, size, target)
                        report.copied.append(relative)
                        if problem:
                            report.problems.append(f"{relative}: {problem}")
                except (OSError, ValueError) as e:
                    report.problems.append(f"{relative}: {e}")
    except zmq.ZMQError as e:  # no answer (zmq.Again) or a broken connection
        raise OSError(f"{peer} did not answer: {e}") from e
    return report


def _fetch(socket: zmq.Socket, relative: str, size: int, target: Path) -> str | None:
    """Copies `size` bytes of a remote file into `target`, carrying on after the bytes already
    here. Returns a problem to report (the original was rewritten), or None."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + PARTIAL)
    try:
        if target.exists():
            shutil.copyfile(target, partial)  # what is here already, to carry on from
        expected = _download(socket, relative, size, partial)
        if sha256(partial) == expected:
            partial.replace(target)
            return None
        if not target.exists():
            raise OSError("the copy does not match the original")
        # The old copy is not the beginning of the original: it was rewritten. Keep both.
        partial.unlink()
        expected = _download(socket, relative, size, partial)
        if sha256(partial) != expected:
            raise OSError("the copy does not match the original")
        kept = _aside(target, "rewritten")
        partial.replace(target)
        return f"the original was rewritten; the old copy is kept as {kept.name}"
    finally:
        partial.unlink(missing_ok=True)


def _aside(path: Path, why: str) -> Path:
    """Renames a file to a new name next to it (kept, never deleted); returns the new path."""
    kept = path.with_name(f"{path.stem}.{why}-{stamp()}{path.suffix}")
    path.rename(kept)
    return kept


def _download(socket: zmq.Socket, relative: str, size: int, partial: Path) -> str:
    """Appends the remote file's bytes from the end of `partial` up to `size`; returns the
    SHA-256 the server gave for those `size` bytes."""
    with partial.open("ab") as f:
        while True:
            request = {"op": "get", "path": relative, "offset": f.tell(), "size": size}
            header, data = _ask(socket, request)
            f.write(data)
            if "sha256" in header:
                return header["sha256"]
            if not data:
                raise OSError("the file stopped before its end")


# --- Backing up (after a session) -------------------------------------------------------------


def backup(logs: Path, to: Path) -> Report:
    """Copies every file under `logs` that is missing in `to` or has grown, and checks each copy's
    SHA-256. Nothing in `to` is deleted or overwritten by different content (a file there that is
    not the beginning of the one here is moved aside under another name and kept), and nothing in
    `logs` is changed."""
    report = Report()
    for relative, size in list_files(logs).items():
        source, target = inside(logs, relative), to.joinpath(*PurePosixPath(relative).parts)
        have = target.stat().st_size if target.exists() else None
        if have == size:
            report.unchanged += 1
            continue
        if have is not None and (have > size or sha256(source, have) != sha256(target)):
            kept = _aside(target, "replaced")
            report.problems.append(
                f"{relative}: differed from the backup; old one kept as {kept.name}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PARTIAL)
        try:
            shutil.copyfile(source, partial)
            if sha256(partial) != sha256(source):
                report.problems.append(f"{relative}: changed while it was copied; run backup again")
                continue
            partial.replace(target)
            report.copied.append(relative)
        finally:
            partial.unlink(missing_ok=True)
    return report


def print_report(what: str, report: Report) -> int:
    print(f"collect_logs: {what}: {len(report.copied)} copied, {report.unchanged} already there")
    for problem in report.problems:
        print(f"  problem: {problem}", file=sys.stderr)
    return 1 if report.problems else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.collect_logs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--logs", type=Path, default=LOGS_DIR, help="this machine's logs folder")
    parser.add_argument("--port", type=int, default=PORT)
    roles = parser.add_subparsers(dest="role", required=True)
    roles.add_parser("serve", help="The PC: offer the logs folder until Ctrl+C")
    pull_parser = roles.add_parser("pull", help="Laptop 1: copy the PC's new logs")
    pull_parser.add_argument("--peer", required=True, help="The PC's address")
    backup_parser = roles.add_parser("backup", help="copy every log to a second place")
    backup_parser.add_argument("--to", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.role == "serve":
        with iperf_server() as iperf:
            print(
                f"collect_logs: offering {args.logs} and an echo on TCP {args.port}; iperf3 "
                f"server: {iperf}; Ctrl+C to stop"
            )
            serve(args.logs, args.port)
        return 0
    if args.role == "pull":
        try:
            return print_report(f"from {args.peer}", pull(args.peer, args.logs, args.port))
        except OSError as e:
            print(f"collect_logs: {e}; is 'serve' (or the worker role) running there?")
            return 1
    if args.to.resolve().is_relative_to(args.logs.resolve()):
        parser.error("the backup must go outside the logs folder")
    return print_report(f"backup to {args.to}", backup(args.logs, args.to))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
