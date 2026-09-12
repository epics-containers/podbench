"""On-demand Python attach task. Nothing is injected until the user starts debugging."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from podbench.gdb_support import thread_db_arguments


def listener(port: int) -> str:
    for table in ("tcp", "tcp6"):
        try:
            rows = Path(f"/proc/net/{table}").read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for row in rows:
            fields = row.split()
            if fields[1].rsplit(":", 1)[1] == f"{port:04X}" and fields[3] == "0A":
                return fields[9]
    return ""


def _debugpy_source() -> Path | None:
    """Find a debugpy package to copy: the image's copy, else the claim's."""
    candidates = [
        Path("/opt/podbench/debugpy/debugpy"),
        *Path("/podbench/app/.venv/lib").glob("python3.*/site-packages/debugpy"),
    ]
    return next((c for c in candidates if (c / "__init__.py").is_file()), None)


def inject(pid: int, start: str, port: int) -> None:
    proc = Path(f"/proc/{pid}")
    if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != start:
        raise RuntimeError("process restarted; rerun podbench ide vscode")
    state = Path.home() / f".podbench/ide/python-{pid}.json"
    if inode := listener(port):
        old = json.loads(state.read_text()) if state.exists() else {}
        if old == {"start": start, "port": port, "inode": inode}:
            return
        raise RuntimeError(
            f"port {port} is occupied by another listener; rerun after freeing it"
        )
    root = proc / "root"
    destination = root / "tmp" / f".podbench-debugpy-{os.getuid()}"
    # This /proc spelling exists in both mount namespaces. Do not resolve it.
    if not (destination / "debugpy/__init__.py").is_file():
        source = _debugpy_source()
        if source is None:
            raise RuntimeError(
                "no debugpy in the seat image or the hotfix environment; "
                "rebuild the seat image or run podbench hotfix init"
            )
        shutil.copytree(source, destination / "debugpy", dirs_exist_ok=True)
    environment = {
        **os.environ,
        "PYTHONPATH": str(destination),
        "TMPDIR": str(root / "tmp"),
    }
    tools = state.parent / f"python-tools-{pid}"
    tools.mkdir(exist_ok=True)
    gdb = tools / "gdb"
    program = tools / "executable"
    temporary = tools / "executable.new"
    shutil.copyfile(proc / "exe", temporary)
    temporary.replace(program)
    gdb.write_text(
        "#!/bin/sh\ncd /tmp\nexec "
        + shlex.join(
            [
                "/usr/bin/gdb",
                *thread_db_arguments(pid),
                "-iex",
                f"set sysroot {root}",
                "-se",
                str(program),
            ]
        )
        + ' "$@"\n'
    )
    gdb.chmod(0o700)
    environment["PATH"] = f"{tools}:{environment.get('PATH', '')}"
    hold = root / "tmp/podbench-hold"
    supervised = (root / "tmp/podbench-child.pid").is_file()
    held = hold.exists()
    if supervised:
        hold.touch()
    try:
        # GNU timeout kills the whole debugger process group if injection stalls.
        result = subprocess.run(
            [
                "timeout",
                "--kill-after=5",
                "45",
                sys.executable,
                "-m",
                "debugpy",
                "--listen",
                f"127.0.0.1:{port}",
                "--pid",
                str(pid),
            ],
            env=environment,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"debugpy injection failed ({result.returncode}); "
                "check ptrace permissions"
            )
        for _ in range(50):
            if inode := listener(port):
                state.write_text(
                    json.dumps({"start": start, "port": port, "inode": inode})
                )
                if supervised:
                    print(
                        "Restart the hotfix child after debugging to remove debugpy "
                        "and release the probe hold."
                    )
                return
            time.sleep(0.1)
        raise RuntimeError("debugpy did not open its listener")
    except BaseException:
        if supervised and not held:
            hold.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        inject(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
