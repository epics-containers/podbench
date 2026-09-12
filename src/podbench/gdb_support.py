"""Target-specific GDB startup settings."""

import os
from pathlib import Path


def thread_db_arguments(pid: int) -> list[str]:
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
