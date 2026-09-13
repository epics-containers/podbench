"""Target-specific GDB startup settings."""

import os
from pathlib import Path

from .debug_model import Process


def _has_ptrace_capability() -> bool:
    try:
        status = Path("/proc/self/status").read_text().splitlines()
        effective = next(
            line.split()[1] for line in status if line.startswith("CapEff:")
        )
        return bool(int(effective, 16) & (1 << 19))
    except (FileNotFoundError, PermissionError, StopIteration, ValueError):
        return False


def ptrace_warning(process: Process) -> str | None:
    if _has_ptrace_capability():
        return None
    if os.geteuid() != process.uid:
        return "the seat lacks SYS_PTRACE and the process has a different UID"
    try:
        scope = int(Path("/proc/sys/kernel/yama/ptrace_scope").read_text())
    except (FileNotFoundError, PermissionError, ValueError):
        return None
    if scope > 0:
        return f"ptrace_scope={scope} and the seat lacks SYS_PTRACE"
    return None


def thread_db_arguments(pid: int) -> list[str]:
    """Point GDB at the target's thread library so it matches the target's libc."""
    root = Path(f"/proc/{pid}/root")
    machine = os.uname().machine
    directories = [
        root / "lib" / f"{machine}-linux-gnu",
        root / "usr/lib" / f"{machine}-linux-gnu",
        root / "lib64",
        root / "usr/lib64",
        root / "lib",
        root / "usr/lib",
    ]
    library = next(
        (
            directory / "libthread_db.so.1"
            for directory in directories
            if (directory / "libthread_db.so.1").is_file()
        ),
        None,
    )
    if library is None:
        return []
    return [
        "-iex",
        f"add-auto-load-safe-path {library}",
        "-iex",
        f"set libthread-db-search-path {library.parent}",
    ]
