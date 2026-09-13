"""Generate stable Launch and Attach configurations from private process snapshots."""

from __future__ import annotations

import hashlib
import json
import shlex
import socket
from pathlib import Path

from .ide_process import snapshot
from .ssh_agent import PYTHON


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def processes(namespace: str | None = None) -> list[dict]:
    own = str(Path("/proc/self/ns/mnt").readlink())
    found = []
    for proc in sorted(Path("/proc").glob("[0-9]*"), key=lambda p: int(p.name)):
        try:
            ns = str((proc / "ns/mnt").readlink())
            if ns == own or (namespace and ns != namespace):
                continue
            name = (proc / "exe").readlink().name
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
            with (proc / "exe").open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            item = snapshot(int(proc.name))
            if any("debugpy" in arg or "stdio-socket" in arg for arg in item["argv"]):
                continue
            item.update(name=name, python=name.startswith("python"))
            found.append(item)
        except (OSError, RuntimeError, ValueError):
            continue
    return found


def helper(path: Path, module: str, *arguments: str) -> str:
    path.write_text(
        "#!/bin/sh\nexec "
        + shlex.join(
            [
                "env",
                f"PYTHONPATH={Path(__file__).parent.parent}",
                PYTHON,
                "-m",
                f"podbench.{module}",
                *arguments,
            ]
        )
        + ' "$@"\n'
    )
    path.chmod(0o700)
    return str(path)


def task(label: str, module: str, *arguments: str) -> dict:
    return {
        "label": label,
        "type": "process",
        "command": PYTHON,
        "args": ["-m", f"podbench.{module}", *arguments],
        "options": {"env": {"PYTHONPATH": str(Path(__file__).parent.parent)}},
        "problemMatcher": [],
    }


def python_invocation(process: dict) -> dict:
    argv = process["argv"]
    # Preserve venv and symlink spelling: /proc/exe alone loses interpreter identity.
    interpreter = argv[0]
    if not interpreter.startswith("/"):
        for directory in process["environment"].get("PATH", "").split(":"):
            candidate = Path(directory) / interpreter
            if (Path(process["root"]) / str(candidate).lstrip("/")).is_file():
                interpreter = str(candidate)
                break
    process["interpreter"] = interpreter
    flags, index = [], 1
    while index < len(argv):
        word = argv[index]
        if word in ("-m", "-c"):
            return {
                "module" if word == "-m" else "code": argv[index + 1],
                "args": argv[index + 2 :],
                "pythonArgs": flags,
            }
        if word == "--":
            index += 1
            break
        if not word.startswith("-"):
            break
        flags.append(word)
        if word in ("-W", "-X"):
            index += 1
            flags.append(argv[index])
        index += 1
    if index >= len(argv) or argv[index] == "-":
        raise ValueError("interactive/stdin Python has no replayable program")
    return {
        "program": str(Path(process["cwd"]) / argv[index]),
        "args": argv[index + 1 :],
        "pythonArgs": flags,
    }


def launchers(base: Path, folder: Path, home: Path) -> tuple[list, list, set, list]:
    configurations, tasks, extensions, warnings = [], [], set(), []
    root = Path((base / "target").read_text()) if (base / "target").exists() else None
    namespace = str((root.parent / "ns/mnt").readlink()) if root else None
    lifecycle = bool(
        root
        and (root / "tmp/podbench-control/version").is_file()
        and (root / "tmp/podbench-control/version").read_text().strip() == "1"
    )
    if root and (root / "tmp/podbench-child.pid").exists() and not lifecycle:
        warnings.append("Launch requires updated hotfix wiring and a rollout")
    if lifecycle:
        tasks += [
            task(f"Podbench: {verb.title()}", "lifecycle_client", verb)
            for verb in ("start", "stop")
        ]
    seen = set()
    for process in processes(namespace):
        digest = hashlib.sha256(
            json.dumps([process["executable"], process["argv"]]).encode()
        ).hexdigest()[:12]
        if digest in seen:
            warnings.append(f"ambiguous invocation: {shlex.join(process['argv'])}")
            continue
        seen.add(digest)
        state = base / f"process-{digest}.json"
        process["root"] = str(root or Path(f"/proc/{process['pid']}/root"))
        label = shlex.join(process["argv"])
        prepare, release = f"Podbench: prepare {digest}", f"Podbench: release {digest}"
        tasks += [
            task(prepare, "ide_prepare", str(state), "attach"),
            task(release, "ide_prepare", str(state), "release"),
        ]
        common = {
            "name": f"Podbench: Attach — {label}",
            "preLaunchTask": prepare,
            "postDebugTask": release,
        }
        mapping = [
            {"localRoot": str(folder), "remoteRoot": str(folder)},
            {"localRoot": process["root"], "remoteRoot": "/"},
        ]
        if process["python"]:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                process["port"] = probe.getsockname()[1]
            configurations.append(
                {
                    **common,
                    "type": "debugpy",
                    "request": "attach",
                    "debugAdapterPython": PYTHON,
                    "connect": {"host": "127.0.0.1", "port": process["port"]},
                    "pathMappings": mapping,
                    "justMyCode": False,
                }
            )
            extensions.update(("ms-python.python", "ms-python.debugpy"))
            if lifecycle:
                try:
                    invocation = python_invocation(process)
                    wrapper = helper(
                        state.with_suffix(".python"), "ide_relay", str(state), "python"
                    )
                    configurations.append(
                        dict(
                            name=f"Podbench: Launch — {label}",
                            type="debugpy",
                            request="launch",
                            **invocation,
                            python=process["interpreter"],
                            debugLauncherPython=wrapper,
                            debugAdapterPython=PYTHON,
                            envFile="/dev/null",
                            cwd=str(home),
                            console="integratedTerminal",
                            justMyCode=False,
                            pathMappings=mapping,
                        )
                    )
                except (ValueError, IndexError) as error:
                    warnings.append(f"{label}: {error}")
        else:
            wrapper = helper(
                state.with_suffix(".gdb-wrapper"), "gdb_session", str(state)
            )
            source_map = {"/": process["root"] + "/"}
            source_map[str(folder)] = str(folder)
            native = {
                "type": "cppdbg",
                "request": "launch",
                "MIMode": "gdb",
                "cwd": str(home),
                "sourceFileMap": source_map,
            }
            # Use customizable launch to attach without cppdbg's elevation prompt.
            configurations.append(
                {
                    **native,
                    **common,
                    "program": str(state.with_suffix(".exe")),
                    "miDebuggerPath": wrapper,
                    "customLaunchSetupCommands": [
                        {
                            "text": "-interpreter-exec console "
                            + json.dumps("source " + str(state.with_suffix(".gdb"))),
                            "ignoreFailures": False,
                        }
                    ],
                    "launchCompleteCommand": "None",
                }
            )
            extensions.add("ms-vscode.cpptools")
            if lifecycle:
                pipe = helper(
                    state.with_suffix(".pipe"), "ide_relay", str(state), "native"
                )
                configurations.append(
                    {
                        **native,
                        "name": f"Podbench: Launch — {label}",
                        "program": process["executable"],
                        "args": process["argv"][1:],
                        "cwd": process["cwd"],
                        "pipeTransport": {
                            "pipeProgram": pipe,
                            "pipeArgs": [],
                            "debuggerPath": "gdb",
                            "pipeCwd": str(home),
                        },
                        "stopAtEntry": False,
                    }
                )
        write_json(state, process)
    if not configurations:
        warnings.append(
            "No readable application processes found; Start and reopen IDE "
            "to capture initial metadata"
        )
    return configurations, tasks, extensions, warnings
