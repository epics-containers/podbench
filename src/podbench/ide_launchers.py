"""Generate application debugger launchers inside the seat."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
from pathlib import Path

from .debug_model import process_start, read_process
from .gdb_support import thread_db_arguments
from .ssh_agent import PYTHON


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def processes() -> list[dict]:
    own_namespace = Path("/proc/self/ns/mnt").readlink()
    found = []
    for entry in sorted(Path("/proc").glob("[0-9]*"), key=lambda p: int(p.name)):
        try:
            if (entry / "ns/mnt").readlink() == own_namespace:
                continue
            executable = str((entry / "exe").readlink())
            with (entry / "exe").open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            name = Path(executable).name
            if name in (
                "sh",
                "bash",
                "dash",
                "sleep",
                "tini",
                "pause",
                "timeout",
                "cat",
            ):
                continue
            process = read_process(entry)
            if process is None or "debugpy/adapter" in process.command:
                continue
            arguments = process.command
            start = process_start(process.pid)
            cwd = str((entry / "cwd").readlink())
            found.append(
                {
                    "pid": int(entry.name),
                    "start": start,
                    "name": name,
                    "executable": executable,
                    "python": name.startswith("python"),
                    "cwd": cwd,
                    "command": arguments[:100],
                }
            )
        except (OSError, IndexError):
            continue
    return found


def launchers(base: Path, folder: Path, home: Path) -> tuple[list, list, set, list]:
    configurations, tasks, extensions, warnings = [], [], set(), []
    for process in processes():
        pid, start = process["pid"], process["start"]
        root = f"/proc/{pid}/root"
        label = f"{pid}: {process['command']}"
        if process["python"]:
            state = base / f"python-{pid}.json"
            old = json.loads(state.read_text()) if state.exists() else {}
            if old.get("start") == start:
                port = old["port"]
            else:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                write_json(state, {"start": start, "port": port, "inode": ""})
            task = f"podbench: prepare Python {pid}"
            configurations.append(
                {
                    "name": f"Python {label}",
                    "type": "debugpy",
                    "request": "attach",
                    "connect": {"host": "127.0.0.1", "port": port},
                    "pathMappings": [
                        *(
                            [{"localRoot": str(folder), "remoteRoot": str(folder)}]
                            if str(folder) == "/podbench/app"
                            else []
                        ),
                        {"localRoot": root, "remoteRoot": "/"},
                    ],
                    "justMyCode": False,
                    "preLaunchTask": task,
                }
            )
            tasks.append(
                {
                    "label": task,
                    "type": "process",
                    "command": PYTHON,
                    "args": [
                        str(Path(__file__).with_name("ide_python.py")),
                        str(pid),
                        start,
                        str(port),
                    ],
                    "options": {
                        "env": {"PYTHONPATH": str(Path(__file__).parent.parent)}
                    },
                    "problemMatcher": [],
                }
            )
            extensions.update(("ms-python.python", "ms-python.debugpy"))
        else:
            # GDB canonicalizes /proc/PID/exe into the seat's own executable.
            # A private copy prevents silently loading a different binary.
            program = base / f"exe-{pid}"
            temporary = base / f"exe-{pid}.new"
            try:
                shutil.copyfile(f"/proc/{pid}/exe", temporary)
                temporary.replace(program)
                source_map = {
                    f"/{p.name}": str(p)
                    for p in Path(root).iterdir()
                    if p.is_dir() and p.name not in ("proc", "sys", "dev", "podbench")
                }
            except OSError:
                warnings.append(
                    f"PID {pid} exited or became unreadable; rerun to refresh"
                )
                continue
            wrapper = base / f"gdb-{pid}"
            wrapper.write_text(
                "#!/bin/sh\nexec "
                + shlex.join(
                    [
                        "env",
                        f"PYTHONPATH={Path(__file__).parent.parent}",
                        PYTHON,
                        "-m",
                        "podbench.gdb_session",
                        str(pid),
                        start,
                    ]
                )
                + ' "$@"\n'
            )
            wrapper.chmod(0o700)
            commands = thread_db_arguments(pid)[1::2] + [f"set sysroot {root}"]
            configurations.append(
                {
                    "name": f"C/C++ {label}",
                    "type": "cppdbg",
                    "request": "attach",
                    "processId": str(pid),
                    "program": str(program),
                    "MIMode": "gdb",
                    "targetArchitecture": {
                        "x86_64": "x64",
                        "aarch64": "arm64",
                        "armv7l": "arm",
                        "i686": "x86",
                    }.get(os.uname().machine, "x64"),
                    "miDebuggerPath": str(wrapper),
                    "cwd": str(home),
                    "sourceFileMap": source_map,
                    "setupCommands": [
                        {"text": text, "ignoreFailures": False} for text in commands
                    ],
                }
            )
            extensions.add("ms-vscode.cpptools")
    if not configurations:
        warnings.append(
            "No readable application processes found; "
            "verify process visibility and ptrace permissions"
        )
    return configurations, tasks, extensions, warnings
