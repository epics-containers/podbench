"""Small seat-side workspace author; invoked over the user's SSH session."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from podbench.gdb_support import thread_db_arguments
from podbench.ssh_agent import PYTHON

EXCLUDES = {
    f"**/{name}/**": True
    for name in ("proc", "sys", "dev", ".vscode-server", ".git", ".venv")
}


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
            arguments = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode(errors="replace")
                .strip()
            )
            if "debugpy/adapter" in arguments:
                continue
            start = (entry / "stat").read_text().rsplit(")", 1)[1].split()[19]
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


def prepare(identity: dict) -> dict:
    home = Path.home()  # SSH's NSS home, which may differ from kubectl exec's HOME.
    if not home.is_dir() or not os.access(home, os.W_OK):
        raise RuntimeError(f"SSH login home {home} is not writable")
    base = home / ".podbench/ide"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    bootstrap = base / "bootstrap"
    bootstrap.mkdir(exist_ok=True)
    for key, value in identity.items():
        if key in ("user.name", "user.email"):
            subprocess.run(["git", "config", "--global", key, value], check=True)
    folder = Path("/podbench/app")
    if not folder.is_dir():
        folder = home / "workspace"
        folder.mkdir(exist_ok=True)
    safe = subprocess.run(
        ["git", "config", "--global", "--get-all", "safe.directory"],
        capture_output=True,
        text=True,
        check=False,
    )
    if str(folder) not in safe.stdout.splitlines():
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", str(folder)],
            check=True,
        )
    settings = {
        "files.watcherExclude": EXCLUDES,
        "search.exclude": EXCLUDES,
        "search.followSymlinks": False,
        "python.analysis.exclude": list(EXCLUDES),
        "C_Cpp.files.exclude": EXCLUDES,
    }
    if (folder / ".venv/bin/python").is_file():
        settings["python.defaultInterpreterPath"] = str(folder / ".venv/bin/python")
    # The machine scope also covers the bootstrap window; the workspace file
    # carries only podbench's own settings.
    machine = home / ".vscode-server/data/Machine/settings.json"
    merged = settings
    if machine.exists():
        try:
            previous = json.loads(machine.read_text())
            if not isinstance(previous, dict):
                raise ValueError("expected a JSON object")
        except ValueError as error:
            raise RuntimeError(
                f"cannot safely merge {machine}; expected a JSON object"
            ) from error
        for key, value in settings.items():
            if isinstance(value, dict):
                previous[key] = {**previous.get(key, {}), **value}
            else:
                previous[key] = value
        merged = previous
    write_json(machine, merged)
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
                    "args": ["/tmp/podbench-ide_python.py", str(pid), start, str(port)],
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
                "#!/bin/sh\nset -eu\ncd "
                + shlex.quote(str(home))
                + "\n"
                + f"{PYTHON} -c "
                + shlex.quote(
                    "from pathlib import Path; import sys; "
                    f"s=Path('/proc/{pid}/stat').read_text()"
                    ".rsplit(')',1)[1].split()[19]; "
                    f"sys.exit(0 if s == {start!r} else "
                    "'Process restarted; rerun podbench ide vscode')"
                )
                + "\nexec "
                + shlex.join(
                    [
                        "/usr/bin/gdb",
                        *thread_db_arguments(pid),
                        "-iex",
                        f"set sysroot {root}",
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
    workspace = base / "podbench.code-workspace"
    write_json(
        workspace,
        {
            "folders": [{"path": str(folder)}],
            "settings": settings,
            "launch": {"version": "0.2.0", "configurations": configurations},
            "tasks": {"version": "2.0.0", "tasks": tasks},
        },
    )
    return {
        "workspace": str(workspace),
        "bootstrap": str(bootstrap),
        "extensions": sorted(extensions),
        "count": len(configurations),
        "warnings": warnings,
    }


def server_cli() -> str:
    # Select a running server, not a stale install left by a VS Code upgrade.
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = entry.read_bytes().decode().split("\0")
            for argument in arguments:
                if (
                    argument.endswith("/out/server-main.js")
                    and "/.vscode-server/" in argument
                ):
                    binary = Path(argument).parent.parent / "bin/code-server"
                    if binary.is_file():
                        return str(binary)
        except (OSError, UnicodeError):
            continue
    return ""


if __name__ == "__main__":
    try:
        print(
            json.dumps(prepare(json.load(sys.stdin)))
            if sys.argv[1] == "prepare"
            else server_cli()
        )
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
