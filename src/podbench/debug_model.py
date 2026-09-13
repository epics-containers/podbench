"""Process information shared by debugger launchers."""

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    gid: int
    state: str
    command: str


def read_process(path: Path) -> Process | None:
    try:
        fields = {
            key: value.strip()
            for line in (path / "status").read_text().splitlines()
            for key, separator, value in [line.partition(":")]
            if separator
        }
        raw = (path / "cmdline").read_bytes().rstrip(b"\0")
        arguments = raw.split(b"\0") if raw else []
        printable = [
            " ".join(argument.decode(errors="replace").split())
            for argument in arguments
        ]
        command = shlex.join(printable) if arguments else f"[{fields['Name']}]"
        return Process(
            pid=int(path.name),
            ppid=int(fields["PPid"]),
            uid=int(fields["Uid"].split()[0]),
            gid=int(fields["Gid"].split()[0]),
            state=fields["State"].split()[0],
            command=command,
        )
    except (OSError, KeyError, ValueError):
        return None


def process_start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def listener(port: int) -> str:
    for table in ("tcp", "tcp6"):
        try:
            rows = Path(f"/proc/net/{table}").read_text().splitlines()[1:]
        except FileNotFoundError:
            continue
        for row in rows:
            fields = row.split()
            if fields[1].rsplit(":", 1)[1] == f"{port:04X}" and fields[3] == "0A":
                return fields[9]
    return ""
