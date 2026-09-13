"""Route debugger launchers into the original application container."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .ide_process import load
from .lifecycle_client import relay


def native_runtime(root: Path) -> str:
    """Stage GDB with its loader, libraries and data, independent of target libc."""
    location = "/tmp/podbench-debugger"
    destination = root / location.lstrip("/")
    if (destination / "run-gdb").is_file():
        return location + "/run-gdb"
    destination.mkdir(mode=0o700, exist_ok=True)
    libraries = destination / "lib"
    libraries.mkdir(exist_ok=True)
    output = subprocess.check_output(["ldd", "/usr/bin/gdb"], text=True)
    loader = ""
    for line in output.splitlines():
        words = line.split()
        paths = [w for w in words if w.startswith("/")]
        for path in paths:
            shutil.copy2(path, libraries / Path(path).name)
            if "ld-linux" in path or "ld-musl" in path:
                loader = Path(path).name
    if not loader:
        raise RuntimeError("cannot locate GDB dynamic loader in the seat")
    shutil.copy2("/usr/bin/gdb", destination / "gdb")
    for source, target in (
        ("/usr/share/gdb", "gdb-data"),
        ("/usr/lib/python3.11", "lib/python3.11"),
    ):
        if Path(source).is_dir():
            shutil.copytree(source, destination / target, dirs_exist_ok=True)
    wrapper = destination / "run-gdb"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.join(
            [
                "env",
                "-i",
                "PATH=/usr/bin:/bin",
                "HOME=/tmp",
                "SHELL=/bin/bash",
                f"PYTHONHOME={location}",
                f"{location}/lib/{loader}",
                "--library-path",
                f"{location}/lib",
                f"{location}/gdb",
                "--data-directory",
                f"{location}/gdb-data",
                "-nx",
            ]
        )
        + ' "$@"\n'
    )
    # Binary and GDB data have different destinations.
    wrapper.chmod(0o700)
    return location + "/run-gdb"


def run(metadata_path: str, kind: str, arguments: list[str]) -> int:
    metadata = load(metadata_path)
    root = Path(metadata["root"])
    if str((root.parent / "ns/mnt").readlink()) != metadata["namespace"]:
        raise RuntimeError("target container changed; reconnect the seat")
    environment = metadata["environment"]
    if kind == "python":
        # debugpy supplies its exact launcher path; copy that complete package so
        # the adapter and launcher use the same protocol and vendored pydevd.
        if not arguments or Path(arguments[0]).name != "launcher":
            raise RuntimeError("expected a debugpy launcher invocation")
        source = Path(arguments[0]).parent
        destination = root / "tmp/podbench-launch-debugpy/debugpy"
        shutil.copytree(source, destination, dirs_exist_ok=True)
        arguments[0] = "/tmp/podbench-launch-debugpy/debugpy/launcher"
        argv = [metadata["interpreter"], *arguments]
    else:
        debugger = native_runtime(root)
        # cpptools pipeTransport may pass its command as one shell argument.
        words = shlex.split(arguments[0]) if len(arguments) == 1 else arguments
        if words and words[0] == "gdb":
            words.pop(0)
        argv = [debugger, *words]
        # GDB starts with a clean runtime; restore the complete captured inferior
        # environment using an exec wrapper, after GDB has forked the inferior.
        words = ["env", "-i", *[f"{k}={v}" for k, v in environment.items()]]
        # Keep captured values out of GDB's command line. ANSI shell quoting
        # preserves embedded newlines without introducing GDB command boundaries.
        quoted = [
            "$'" + "".join(f"\\x{byte:02x}" for byte in os.fsencode(word)) + "'"
            if "\n" in word or "\r" in word
            else shlex.quote(word)
            for word in words
        ]
        commands = root / "tmp/podbench-debugger/inferior.gdb"
        commands.write_text("set exec-wrapper " + " ".join(quoted) + "\n")
        commands.chmod(0o600)
        argv += ["-ix", "/tmp/podbench-debugger/inferior.gdb"]
        environment = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    return relay(root, argv, metadata["cwd"], environment)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    try:
        sys.exit(run(sys.argv[1], sys.argv[2], sys.argv[3:]))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
