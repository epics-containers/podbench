"""Process information shared by debugger launchers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    gid: int
    state: str
    command: str
