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

from podbench.debug_model import listener, process_start
from podbench.gdb_support import thread_db_arguments
from podbench.lifecycle_client import action


def _debugpy_source() -> Path | None:
    """Find debugpy in the image, claim, or installed VS Code debugger."""
    candidates = [
        Path("/opt/podbench/debugpy/debugpy"),
        *Path("/podbench/app/.venv/lib").glob("python3.*/site-packages/debugpy"),
        *sorted(
            (Path.home() / ".vscode-server/extensions").glob(
                "ms-python.debugpy-*/bundled/libs/debugpy"
            ),
            reverse=True,
        ),
    ]
    return next((c for c in candidates if (c / "__init__.py").is_file()), None)


def inject(
    pid: int,
    start: str,
    port: int,
    state_dir: Path | None = None,
    *,
    manage_hold: bool = True,
) -> None:
    """Inject debugpy into the recorded process, or reuse its known listener.

    Successful injection keeps supervised probes held until a hotfix restart.
    Failed injection releases only the token acquired by this call.
    """
    proc = Path(f"/proc/{pid}")
    if process_start(pid) != start:
        raise RuntimeError("process changed during preparation; retry Attach")
    state = (state_dir or Path.home() / ".podbench/ide") / f"python-{pid}.json"
    state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if inode := listener(port):
        # A port alone is insufficient: another process may now own the listener.
        old = json.loads(state.read_text()) if state.exists() else {}
        if old == {"start": start, "port": port, "inode": inode}:
            return
        raise RuntimeError(
            f"port {port} is occupied by another listener; rerun after freeing it"
        )
    if state.exists() and json.loads(state.read_text()).get("start") == start:
        # debugpy.listen() runs once per process and its adapter exits on Disconnect.
        raise RuntimeError(
            f"debugpy is already loaded in PID {pid} from an earlier session; "
            "run podbench restart to attach again"
        )
    root = proc / "root"
    destination = root / "tmp" / f".podbench-debugpy-{os.getuid()}"
    # This /proc spelling exists in both mount namespaces. Do not resolve it.
    if not (destination / "debugpy/__init__.py").is_file():
        source = _debugpy_source()
        if source is None:
            raise RuntimeError(
                "no debugpy in the seat image, hotfix environment, or VS Code "
                "debugger extension; rebuild the image or rerun podbench ide vscode"
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
    # Copy the target binary because GDB resolves /proc/PID/exe in the seat.
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
    token = ""
    if manage_hold and (
        (root / "tmp/podbench-control").exists()
        or (root / "tmp/podbench-child.pid").exists()
    ):
        token = action(root, "hold-child")
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
                if token:
                    print(
                        "Restart the hotfix child after debugging to remove debugpy "
                        "and release the probe hold."
                    )
                return
            time.sleep(0.1)
        raise RuntimeError("debugpy did not open its listener")
    except BaseException:
        if token:
            action(root, "release", token=token)
        raise


if __name__ == "__main__":
    try:
        inject(
            int(sys.argv[1]),
            sys.argv[2],
            int(sys.argv[3]),
            Path(sys.argv[4]) if len(sys.argv) > 4 else None,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
