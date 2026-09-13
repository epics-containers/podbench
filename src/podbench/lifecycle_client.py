"""Private file protocol client; usable inside a seat without Kubernetes credentials."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

CONTROL = "tmp/podbench-control"
UPGRADE = (
    "lifecycle protocol unavailable or obsolete; update hotfix wiring and roll out"
)


def control_root() -> Path:
    binding = Path.home() / ".podbench/ide/target"
    if binding.is_file():
        root = Path(binding.read_text().strip())
        if (root / CONTROL / "version").is_file():
            return root
        raise RuntimeError(UPGRADE + "; reconnect the seat after pod replacement")
    roots = []
    own = Path("/proc/self/ns/mnt").readlink()
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            root = entry / "root"
            if (entry / "ns/mnt").readlink() != own and (
                root / CONTROL / "version"
            ).is_file():
                key = (entry / "ns/mnt").readlink()
                if not any(k == key for k, _ in roots):
                    roots.append((key, root))
        except OSError:
            continue
    if len(roots) != 1:
        raise RuntimeError(
            "cannot infer one bound lifecycle target; open podbench ide first"
        )
    return roots[0][1]


def request(root: Path, action: str, *, token: str = "") -> Path:
    control = root / CONTROL
    if (
        not (control / "version").is_file()
        or (control / "version").read_text().strip() != "1"
    ):
        raise RuntimeError(UPGRADE)
    directory = Path(tempfile.mkdtemp(prefix="r-", dir=control / "requests"))
    (directory / "action").write_text(action)
    if token:
        (directory / "token").write_text(token)
    return directory


def response(directory: Path, timeout: float = 135) -> None:
    (directory / "ready").touch()
    deadline = time.monotonic() + timeout
    while not (directory / "response").exists():
        if time.monotonic() > deadline:
            (directory / "cancel").touch()
            raise RuntimeError(
                "lifecycle request timed out; inspect podbench status before retrying"
            )
        time.sleep(0.05)
    message = (directory / "response").read_text().strip()
    if message != "ok":
        raise RuntimeError(message)


def action(root: Path, verb: str, *, token: str = "") -> str:
    directory = request(root, verb, token=token)
    try:
        response(directory)
        return directory.name
    finally:
        if (directory / "response").exists():
            shutil.rmtree(directory)


def relay(root: Path, argv: list[str], cwd: str, environment: dict[str, str]) -> int:
    directory = request(root, "launch")
    # The shell is private and is only interpreted by the target's supervisor.
    script = "cd -- " + shlex.quote(cwd) + " || exit 125\nexec "
    script += shlex.join(
        ["env", "-i", *[f"{k}={v}" for k, v in environment.items()], *argv]
    )
    (directory / "run").write_text(script + "\n")
    descriptors = {}
    for name in ("in", "out", "err"):
        os.mkfifo(directory / name, 0o600)
        descriptors[name] = os.open(directory / name, os.O_RDWR | os.O_NONBLOCK)
    stopped = threading.Event()

    def heartbeat():
        while not stopped.is_set():
            (directory / "heartbeat.new").write_text(str(int(time.time())))
            (directory / "heartbeat.new").replace(directory / "heartbeat")
            stopped.wait(1)

    def stdin():
        try:
            while not stopped.is_set():
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    break
                while data and not stopped.is_set():
                    try:
                        data = data[os.write(descriptors["in"], data) :]
                    except BlockingIOError:
                        stopped.wait(0.01)
        except OSError:
            pass
        finally:
            os.close(descriptors["in"])

    def output(name, stream):
        while True:
            try:
                data = os.read(descriptors[name], 65536)
            except BlockingIOError:
                data = b""
            if data:
                try:
                    stream.buffer.write(data)
                    stream.buffer.flush()
                except OSError:
                    (directory / "cancel").touch()
                    stopped.set()
                    break
            elif stopped.is_set():
                break
            else:
                stopped.wait(0.01)

    (directory / "heartbeat").write_text(str(int(time.time())))
    workers = [threading.Thread(target=heartbeat, daemon=True)]
    workers += [
        threading.Thread(target=output, args=(name, stream), daemon=True)
        for name, stream in [("out", sys.stdout), ("err", sys.stderr)]
    ]
    input_started = False
    try:
        response(directory)
        for worker in workers:
            worker.start()
        threading.Thread(target=stdin, daemon=True).start()
        input_started = True
        while not (directory / "exit").exists():
            time.sleep(0.05)
        return int((directory / "exit").read_text())
    finally:
        (directory / "cancel").touch()
        stopped.set()
        for worker in workers:
            if worker.ident:
                worker.join(2)
        if not input_started:
            os.close(descriptors["in"])
        for name in ("out", "err"):
            os.close(descriptors[name])
        # Retain the request until the supervisor has acknowledged termination.
        if (directory / "exit").exists():
            shutil.rmtree(directory)


def main() -> None:
    try:
        action(control_root(), sys.argv[1])
        print(f"application {sys.argv[1]} completed")
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    main()
