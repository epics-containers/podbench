"""Initialize and restart a hotfix checkout."""

from __future__ import annotations

import json
import shlex

from .cli import console
from .hotfix_core import (
    MANIFEST,
    HotfixError,
    Target,
    exec_target,
    find_container,
    load_json,
    resolve_target,
)
from .kubectl import Kubectl
from .launcher import attach, running_seat
from .model import (
    HOTFIX_APP_PATH,
    HOTFIX_CHILD_PID_PATH,
    HOTFIX_HOLD_PATH,
    HOTFIX_PTRACE_PATH,
)


def _seat(kube: Kubectl, target: Target, pod: dict) -> str:
    current = running_seat(pod, target=target.container)
    if current:
        return current.name
    with console.status(f"Landing a seat in {target.pod.name}..."):
        return attach(kube, target.pod.name, target=target.container).seat.container


def _seat_run(
    kube: Kubectl,
    target: Target,
    seat: str,
    command: str,
    *,
    check: bool = True,
    timeout: float | None = 600.0,
):
    return kube.exec_(
        target.pod.name,
        ["bash", "-c", command],
        container=seat,
        check=check,
        timeout=timeout,
    )


def _sync_python() -> str:
    return (
        f"UV_PYTHON_INSTALL_DIR={HOTFIX_APP_PATH}/.python "
        "uv sync --managed-python && "
        "UV_LINK_MODE=copy uv pip install --python .venv/bin/python debugpy"
    )


def init(
    kube: Kubectl,
    pod_name: str,
    repo: str,
    *,
    ref: str | None = None,
    container: str | None = None,
) -> list[str]:
    target, pod = resolve_target(kube, pod_name, container)
    if not target.claim:
        raise HotfixError("the podbench volume does not name a PVC")
    if exec_target(
        kube, target, f"test -s {HOTFIX_CHILD_PID_PATH}", check=False
    ).returncode:
        raise HotfixError(
            "the hotfix supervisor is not running; deploy hotfix values first"
        )
    seat = _seat(kube, target, pod)
    if (
        _seat_run(kube, target, seat, f"test -f {MANIFEST}", check=False).returncode
        == 0
    ):
        raise HotfixError(f"{target.claim} is already initialized")
    branch = f"--branch {shlex.quote(ref)} " if ref else ""
    script = "\n".join(
        [
            "set -euo pipefail",
            # A fresh filesystem may contain lost+found. Keep it and refuse
            # everything else, including an incomplete earlier initialization.
            f"existing=$(find {HOTFIX_APP_PATH} -mindepth 1 -maxdepth 1 "
            "! -name lost+found -print -quit)",
            '[ -z "$existing" ] || { echo "claim is not empty; inspect and back up '
            'its contents before retrying init" >&2; exit 1; }',
            f"checkout=$(mktemp -d {HOTFIX_APP_PATH}/.podbench-clone.XXXXXX)",
            f'git clone {branch}{shlex.quote(repo)} "$checkout"',
            "shopt -s dotglob nullglob",
            f'mv -n -- "$checkout"/* {HOTFIX_APP_PATH}/',
            'rmdir "$checkout"',
            f"install -m 0755 /usr/local/lib/libpodbench-ptrace.so "
            f"{HOTFIX_PTRACE_PATH}",
            f"cd {HOTFIX_APP_PATH}",
            'mkdir -p "$HOME"',
            f"git config --global --add safe.directory {HOTFIX_APP_PATH}",
            f"[ ! -f pyproject.toml ] || {{ {_sync_python()}; }}",
            "git rev-parse HEAD",
        ]
    )
    result = _seat_run(kube, target, seat, script)
    commit = result.stdout.strip().splitlines()[-1]
    manifest = (
        json.dumps(
            {
                "version": 1,
                "repo": repo,
                "base_commit": commit,
                "base_image": target.image,
                "base_image_digest": target.image_digest,
                "container": target.container,
            },
            indent=2,
        )
        + "\n"
    )
    kube.exec_(
        target.pod.name,
        ["bash", "-c", f"cat > {MANIFEST}"],
        container=seat,
        stdin=manifest,
    )
    return [
        f"initialized {target.claim} from {repo} at {commit[:7]}",
        f"edit {HOTFIX_APP_PATH} in seat {seat}",
        f"run podbench hotfix restart {target.pod.name} when ready",
    ]


def _manifest(kube: Kubectl, target: Target) -> dict:
    result = exec_target(kube, target, f"cat {MANIFEST}", check=False)
    if result.returncode:
        raise HotfixError("no hotfix manifest; run hotfix init first")
    return load_json(result.stdout)


def restart(
    kube: Kubectl,
    pod_name: str,
    *,
    container: str | None = None,
    reinstall: bool = False,
    deadline: int = 120,
) -> list[str]:
    if deadline < 1:
        raise HotfixError("restart deadline must be at least one second")
    target, pod = resolve_target(kube, pod_name, container)
    _manifest(kube, target)
    if reinstall:
        sync = (
            f"cd {HOTFIX_APP_PATH} && "
            "if [ -f pyproject.toml ]; then "
            f"{_sync_python()}; "
            "else echo 'no pyproject.toml; nothing to reinstall'; fi"
        )
        seat = running_seat(kube.get_pod(target.pod.name), target=target.container)
        if not seat:
            raise HotfixError(
                f"--reinstall needs a seat for {target.container}; run podbench "
                f"attach {target.pod.name} --target {target.container} "
                f"-n {target.pod.namespace} first"
            )
        _seat_run(kube, target, seat.name, sync, timeout=600.0)
    before = exec_target(kube, target, f"cat {HOTFIX_CHILD_PID_PATH}").stdout.strip()
    spec = find_container(pod, target.container)
    probe = spec.get("readinessProbe") or spec.get("livenessProbe") or {}
    command = probe.get("exec", {}).get("command", [])
    # Run the real probe without its generated hold shortcut. Keep Kubernetes
    # probes held until startup completes, not merely until a new PID exists.
    health = (
        shlex.join(
            ["timeout", str(probe.get("timeoutSeconds", 1))]
            + [
                part.replace(HOTFIX_HOLD_PATH, "/proc/self/podbench-no-hold")
                for part in command
            ]
        )
        if command
        else "true"
    )
    script = "\n".join(
        [
            "set -eu",
            f"expires=$(( $(date +%s) + {deadline} ))",
            f"echo $expires > {HOTFIX_HOLD_PATH}",
            f"trap 'rm -f {HOTFIX_HOLD_PATH}' EXIT",
            f"child=$(cat {HOTFIX_CHILD_PID_PATH})",
            'kill -TERM -"$child" 2>/dev/null || kill -TERM "$child"',
            f"kill_at=$(( $(date +%s) + {min(10, max(1, deadline // 2))} ))",
            'while [ "$(date +%s)" -lt "$expires" ]; do',
            f"  new=$(cat {HOTFIX_CHILD_PID_PATH} 2>/dev/null || true)",
            '  if [ -n "$new" ] && [ "$new" != "$child" ]; then',
            f"    if {health} >/dev/null 2>&1; then exit 0; fi",
            '  elif [ "$(date +%s)" -ge "$kill_at" ]; then',
            '    kill -KILL -"$child" 2>/dev/null || '
            'kill -KILL "$child" 2>/dev/null || true',
            "  fi",
            "  sleep 0.1",
            "done",
            "exit 1",
        ]
    )
    exec_target(kube, target, script, timeout=float(deadline + 10))
    after = exec_target(kube, target, f"cat {HOTFIX_CHILD_PID_PATH}").stdout.strip()
    seat = running_seat(kube.get_pod(target.pod.name), target=target.container)
    state = "not measured"
    if seat:
        result = _seat_run(
            kube,
            target,
            seat.name,
            f"git -c safe.directory={HOTFIX_APP_PATH} "
            f"-C {HOTFIX_APP_PATH} status --short",
            check=False,
        )
        if result.returncode:
            state = "unknown (git status failed)"
        else:
            state = "clean" if not result.stdout.strip() else "modified"
    return [
        f"restarted {target.container}: pid {before} -> {after}",
        f"checkout is {state}",
    ]
