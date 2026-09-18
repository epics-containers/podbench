"""Shared plumbing for the cluster-access verbs: make-sa, delete-sa, tunnel."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.text import Text

from .cli import console, error_console
from .kubectl import CommandResult, KubectlError, run_subprocess

MARKS = {
    "ok": ("ok", "green"),
    "fail": ("FAIL", "bold red"),
    "info": ("info", "yellow"),
    "skip": ("--", "dim"),
}


class AccessError(RuntimeError):
    """A failure the verb reports in one line and exits 1 on."""


def require(*binaries: str) -> None:
    for binary in binaries:
        if shutil.which(binary) is None:
            raise AccessError(f"{binary} not on PATH")


def kubectl(
    *args: str,
    kubeconfig: Path | str | None = None,
    stdin: str | None = None,
    timeout: float | None = 30,
) -> CommandResult:
    argv = ["kubectl", *(["--kubeconfig", str(kubeconfig)] if kubeconfig else [])]
    try:
        return run_subprocess([*argv, *args], stdin=stdin, timeout=timeout)
    except KubectlError as error:
        raise AccessError(str(error)) from error


def kubectl_out(*args: str, kubeconfig: Path | str | None = None) -> str:
    """stdout of a kubectl call that must succeed."""
    result = kubectl(*args, kubeconfig=kubeconfig)
    if result.returncode != 0:
        raise AccessError(KubectlError(result).args[0])
    return result.stdout


def heading(text: str) -> None:
    console.print(Text.assemble(("==> ", "bold cyan"), (text, "bold")))


def note(text: str) -> None:
    console.print(f"    {text}")


def mark(kind: str, label: str, detail: str = "") -> None:
    word, style = MARKS[kind]
    line = Text.assemble("  [", (f"{word:<4}", style), "] ", f"{label:<46} ", detail)
    line.rstrip()
    console.print(line)


def fail(message: str) -> int:
    error_console.print(Text(message, style="bold red"))
    return 1
