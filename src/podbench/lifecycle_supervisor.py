"""Compact invocation of the chart-managed Bash lifecycle runtime."""

from __future__ import annotations

import shlex

RUNTIME_PATH = "/podbench/runtime"
RUNTIME_VOLUME = "podbench-runtime"
RUNTIME_SCRIPT = f"{RUNTIME_PATH}/podbench.sh"


def supervisor(
    command: str = '"$1"', *, health: str = "true", safe: bool = True
) -> str:
    """Source the shared functions and supervise a command in its startup context."""
    return (
        f"source {RUNTIME_SCRIPT}; "
        f"podbench_supervise {command} {shlex.quote(health)} "
        f"{'true' if safe else 'false'}"
    )


def runtime_volumes(app: str) -> list[str]:
    return [
        f"  - name: {RUNTIME_VOLUME}",
        "    configMap:",
        f"      name: {runtime_name(app)}",
    ]


def runtime_mounts() -> list[str]:
    return [
        f"  - name: {RUNTIME_VOLUME}",
        f"    mountPath: {RUNTIME_PATH}",
        "    readOnly: true",
    ]


def runtime_name(app: str) -> str:
    return f"{app}-podbench-runtime"[:63].rstrip("-")
