"""Inject debugpy into a running Python process through GDB."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .cli import console, error_console
from .model import HOTFIX_APP_PATH, HOTFIX_HOLD_PATH

DEBUGPY_PORT = 5678
SHARED_TMP = f"{HOTFIX_APP_PATH}/.podbench-tmp"


def _listening() -> bool:
    port = f"{DEBUGPY_PORT:04X}"
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            rows = table.read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if fields[1].rsplit(":", 1)[-1] == port and fields[3] == "0A":
                return True
    return False


def inject(pid: int, uid: int, gid: int) -> int:
    python = Path(f"{HOTFIX_APP_PATH}/.venv/bin/python")
    hold = Path(f"/proc/{pid}/root{HOTFIX_HOLD_PATH}")
    if not python.is_file():
        error_console.print(
            f"no Python hotfix environment at {python}; run hotfix init first"
        )
        return 1
    if shutil.which("gdb") is None or shutil.which("setpriv") is None:
        error_console.print("Python injection needs gdb and setpriv in the seat")
        return 1
    if _listening():
        error_console.print(f"port {DEBUGPY_PORT} is already in use")
        return 1
    try:
        hold.touch()
        command = [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--keep-groups",
            "env",
            f"TMPDIR={SHARED_TMP}",
            str(python),
            "-Xfrozen_modules=off",
            "-m",
            "debugpy",
            "--listen",
            str(DEBUGPY_PORT),
            "--pid",
            str(pid),
        ]
        setup = subprocess.run([*command[:5], "mkdir", "-p", SHARED_TMP], check=False)
        if setup.returncode:
            raise RuntimeError("could not create the shared injection directory")
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise RuntimeError(f"debugpy injection exited {result.returncode}")
        for _ in range(25):
            if _listening():
                console.print(f"debugpy injected into PID {pid} on port {DEBUGPY_PORT}")
                console.print("run podbench hotfix restart when debugging is complete")
                return 0
            time.sleep(0.2)
        raise RuntimeError("debugpy did not open its listener")
    except (OSError, RuntimeError) as error:
        hold.unlink(missing_ok=True)
        error_console.print(str(error))
        return 1
