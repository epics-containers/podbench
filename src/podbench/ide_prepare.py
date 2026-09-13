"""Session preparation resolves live processes and manages owned probe holds."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .debug_model import process_start
from .ide_process import load, resolve
from .ide_python import inject
from .lifecycle_client import action


def prepare(path: Path, operation: str) -> None:
    metadata = load(path)
    root = Path(metadata["root"])
    owner = path.with_suffix(".hold")
    if operation == "release":
        if owner.exists():
            action(root, "release", token=owner.read_text())
            owner.unlink()
        return
    pid = resolve(metadata)
    token = ""
    if (root / "tmp/podbench-control/version").exists():
        if owner.exists():
            raise RuntimeError("this Attach configuration already owns a session")
        token = action(root, "hold")
        owner.write_text(token)
    try:
        if metadata["python"]:
            inject(
                pid,
                process_start(pid),
                metadata["port"],
                path.parent,
                manage_hold=not bool(token),
            )
        else:
            shutil.copyfile(f"/proc/{pid}/exe", path.with_suffix(".exe.new"))
            path.with_suffix(".exe.new").replace(path.with_suffix(".exe"))
            path.with_suffix(".gdb").write_text(f"attach {pid}\n")
        path.with_suffix(".session").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "start": process_start(pid),
                }
            )
        )
    except BaseException:
        if token:
            action(root, "release", token=token)
            owner.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        prepare(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
