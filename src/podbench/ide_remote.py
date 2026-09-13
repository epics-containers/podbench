"""Small seat-side workspace author; invoked over the user's SSH session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .ide_launchers import launchers, write_json

EXCLUDES = {
    f"**/{name}/**": True
    for name in ("proc", "sys", "dev", ".vscode-server", ".git", ".venv")
}


def prepare(identity: dict) -> dict:
    """Write the seat's workspace and return paths and extensions for the client."""
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
    configurations, tasks, extensions, warnings = launchers(base, folder, home)
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
