"""CLI entry point for the shared, bounded Python injector."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from .cli import console, error_console
from .debug_model import process_start
from .ide_python import inject as inject_python

DEBUGPY_PORT = 5678


def inject(pid: int, uid: int, gid: int) -> int:
    try:
        start = process_start(pid)
        if (os.geteuid(), os.getegid()) == (uid, gid):
            inject_python(pid, start, DEBUGPY_PORT)
        else:
            if shutil.which("setpriv") is None:
                raise RuntimeError("Python injection needs setpriv to change identity")
            # A target UID may not have an NSS home, or may inherit root's HOME.
            # Give the injector private scratch space writable by that identity.
            with tempfile.TemporaryDirectory(prefix="podbench-python-") as state:
                os.chown(state, uid, gid)
                result = subprocess.run(
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
                        state,
                    ],
                    timeout=60,
                    check=False,
                )
            if result.returncode:
                raise RuntimeError(f"Python injection exited {result.returncode}")
        console.print(f"debugpy injected into PID {pid} on port {DEBUGPY_PORT}")
        console.print("restart the application when debugging is complete")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        error_console.print(str(error))
        return 1
