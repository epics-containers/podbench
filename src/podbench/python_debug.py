"""CLI entry point for the shared, bounded Python injector."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .cli import console, error_console
from .debug_model import process_start
from .ide_python import inject as inject_python

DEBUGPY_PORT = 5678


def _run_as(command: list[str]) -> int:
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait(timeout=70)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise


def inject(pid: int, uid: int, gid: int) -> int:
    try:
        start = process_start(pid)
        if (os.geteuid(), os.getegid()) == (uid, gid):
            inject_python(pid, start, DEBUGPY_PORT)
        else:
            if shutil.which("setpriv") is None:
                raise RuntimeError("Python injection needs setpriv to change identity")
            # A target UID may not have an NSS home, or may inherit root's HOME.
            # Keep injection state so repeated attaches recognize their listener.
            state = Path(f"/tmp/podbench-python-{uid}")
            state.mkdir(mode=0o700, exist_ok=True)
            os.chown(state, uid, gid)
            returncode = _run_as(
                [
                    "setpriv",
                    f"--reuid={uid}",
                    f"--regid={gid}",
                    "--keep-groups",
                    sys.executable,
                    "-m",
                    "podbench.ide_python",
                    str(pid),
                    start,
                    str(DEBUGPY_PORT),
                    str(state),
                ]
            )
            if returncode:
                raise RuntimeError(f"Python injection exited {returncode}")
        console.print(f"debugpy injected into PID {pid} on port {DEBUGPY_PORT}")
        console.print("restart the application when debugging is complete")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        error_console.print(str(error))
        return 1
