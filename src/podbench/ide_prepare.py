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
    start = process_start(pid)
    token = ""
    created = False
    try:
        if (root / "tmp/podbench-control").exists() or (
            root / "tmp/podbench-child.pid"
        ).exists():
            try:
                owner.touch(exist_ok=False)
            except FileExistsError as error:
                raise RuntimeError(
                    "this Attach configuration already owns a session"
                ) from error
            created = True
            token = action(root, "hold")
            owner.write_text(token)
        if metadata["python"]:
            inject(
                pid,
                start,
                metadata["port"],
                path.parent,
                manage_hold=False,
            )
        else:
            shutil.copyfile(f"/proc/{pid}/exe", path.with_suffix(".exe.new"))
            path.with_suffix(".exe.new").replace(path.with_suffix(".exe"))
            # The C++ extension ends every session with `kill`; detach instead
            # so a supervised application survives Stop and Disconnect.
            path.with_suffix(".gdb").write_text(
                f"attach {pid}\ndefine hook-kill\ndetach\nend\n"
            )
        if process_start(pid) != start:
            raise RuntimeError("process changed during preparation; retry Attach")
        path.with_suffix(".session").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "start": start,
                }
            )
        )
    except BaseException:
        if token:
            action(root, "release", token=token)
        if created:
            owner.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        prepare(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
