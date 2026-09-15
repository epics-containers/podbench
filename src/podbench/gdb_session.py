"""Run GDB and release the session hold acquired by IDE preparation."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

from .debug_model import process_start
from .gdb_support import thread_db_arguments
from .ide_prepare import prepare
from .ide_process import load


def run(pid: int, start: str, arguments: list[str]) -> int:
    """Run GDB for the recorded process lifetime."""
    if process_start(pid) != start:
        raise RuntimeError("process changed during preparation; retry Attach")
    root = Path(f"/proc/{pid}/root")
    with subprocess.Popen(
        [
            "/usr/bin/gdb",
            *thread_db_arguments(pid),
            "-iex",
            f"set sysroot {root}",
            *arguments,
        ]
    ) as debugger:
        try:
            return debugger.wait()
        except BaseException:
            debugger.terminate()
            try:
                debugger.wait(timeout=5)
            except subprocess.TimeoutExpired:
                debugger.kill()
            raise


def stop(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    try:
        metadata_path = Path(sys.argv[1])
        try:
            session = load(metadata_path.with_suffix(".session"))
            sys.exit(run(session["pid"], session["start"], sys.argv[2:]))
        finally:
            prepare(metadata_path, "release")
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
