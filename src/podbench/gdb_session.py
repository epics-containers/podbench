"""Keep supervised application probes held for the lifetime of an IDE GDB session."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

from .debug_model import process_start
from .gdb_support import thread_db_arguments


def run(pid: int, start: str, arguments: list[str]) -> int:
    """Run GDB for the recorded process lifetime, releasing only our own probe hold."""
    if process_start(pid) != start:
        raise RuntimeError("process restarted; rerun podbench ide vscode")
    root = Path(f"/proc/{pid}/root")
    hold = root / "tmp/podbench-hold"
    owned = (root / "tmp/podbench-child.pid").is_file() and not hold.exists()
    if owned:
        hold.touch()
    try:
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
    finally:
        if owned:
            hold.unlink(missing_ok=True)


def stop(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    try:
        sys.exit(run(int(sys.argv[1]), sys.argv[2], sys.argv[3:]))
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
