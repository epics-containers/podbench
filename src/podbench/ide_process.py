"""Private process snapshots and replacement-safe matching within one container."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .debug_model import process_start


def snapshot(pid: int) -> dict:
    proc = Path(f"/proc/{pid}")
    start = process_start(pid)
    argv = [os.fsdecode(v) for v in (proc / "cmdline").read_bytes().split(b"\0")[:-1]]
    environment = dict(
        os.fsdecode(v).split("=", 1)
        for v in (proc / "environ").read_bytes().split(b"\0")
        if b"=" in v
    )
    result = {
        "pid": pid,
        "start": start,
        "argv": argv,
        "environment": environment,
        "executable": str((proc / "exe").readlink()),
        "cwd": str((proc / "cwd").readlink()),
        "namespace": str((proc / "ns/mnt").readlink()),
    }
    if process_start(pid) != start:
        raise RuntimeError("process changed while recording its invocation; retry")
    return result


def resolve(metadata: dict) -> int:
    matches = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if str((proc / "ns/mnt").readlink()) != metadata["namespace"]:
                continue
            argv = [
                os.fsdecode(v)
                for v in (proc / "cmdline").read_bytes().split(b"\0")[:-1]
            ]
            if (
                str((proc / "exe").readlink()) == metadata["executable"]
                and argv == metadata["argv"]
            ):
                matches.append(int(proc.name))
        except OSError:
            continue
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one matching process, found {len(matches)}; "
            "Start the application or resolve duplicate invocations"
        )
    return matches[0]


def bind(base: Path, namespace: str) -> Path:
    candidates = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if str((proc / "ns/mnt").readlink()) == namespace:
                candidates.append(proc)
        except OSError:
            continue
    if not candidates:
        raise RuntimeError("selected target container is no longer visible")
    # The supervisor is oldest in its namespace and survives application Stop.
    root = min(candidates, key=lambda p: int(p.name)) / "root"
    (base / "target").write_text(str(root))
    return root


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
