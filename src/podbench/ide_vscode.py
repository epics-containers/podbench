"""Prepare the seat before opening VS Code, then verify remote extensions."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import time
from importlib.resources import files
from urllib.parse import quote

from .cli import console
from .doctor import ensure_include
from .ide_resources import ensure_headroom
from .kubectl import Kubectl, KubectlError, run_subprocess
from .launcher import attach, resolve_pod_name, target_container_name
from .ssh_agent import PYTHON
from .ssh_transport import missing_ssh_capabilities, read_public_key, wire_ssh


def _run(argv: list[str], *, stdin: str | None = None, timeout: float = 30) -> str:
    result = run_subprocess(argv, stdin=stdin, timeout=timeout)
    if result.returncode:
        raise KubectlError(result)
    return result.stdout.strip()


def open_vscode(
    kube: Kubectl,
    pod: str,
    *,
    target: str | None,
    image: str,
    identity: str,
    config_dir: str | None,
    code: str,
    timeout: float,
) -> None:
    for binary in (code, "ssh", "ssh-add", "git"):
        if shutil.which(binary) is None:
            raise KubectlError(f"{binary} is required on the workstation")
    read_public_key(identity)
    if not os.environ.get("SSH_AUTH_SOCK"):
        raise KubectlError(
            "start an SSH agent and load your Git key with ssh-add first"
        )
    _run(["ssh-add", "-l"])
    git_identity = {}
    for key in ("user.name", "user.email"):
        result = run_subprocess(["git", "config", "--get", key])
        if result.returncode not in (0, 1):
            raise KubectlError(result)
        if result.stdout.strip():
            git_identity[key] = result.stdout.strip()
    pod = resolve_pod_name(pod)
    target = target_container_name(kube.get_pod(pod), target)
    ensure_headroom(kube, pod, target, timeout)
    console.print("Preparing the debug seat and SSH...")
    session = attach(kube, pod, target=target, image=image, ssh=True, timeout=timeout)
    if missing_ssh_capabilities(kube, session.seat.pod, session.seat.container) != ():
        raise KubectlError(
            "seat lacks the capabilities needed for SSH; use a new compatible seat"
        )
    wiring = wire_ssh(
        kube,
        session.seat.pod,
        session.seat.container,
        identity=identity,
        config_dir=config_dir,
        forward_agent=True,
    )
    ensure_include(config_dir)
    # Send these small helpers so workstation changes also work with existing images.
    for module in ("ide_remote", "ide_python"):
        source = files("podbench").joinpath(f"{module}.py").read_text()
        kube.exec_(
            pod,
            [
                PYTHON,
                "-c",
                "import pathlib,sys; "
                "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())",
                f"/tmp/podbench-{module}.py",
            ],
            container=session.seat.container,
            stdin=source,
        )
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", wiring.alias]
    agent = _run([*ssh, "ssh-add -l"])
    if not agent:
        raise KubectlError("SSH agent forwarding did not reach the seat")
    result = _run(
        [*ssh, f"{PYTHON} /tmp/podbench-ide_remote.py prepare"],
        stdin=json.dumps(git_identity),
    )
    prepared = json.loads(result)
    for warning in prepared["warnings"]:
        console.print(f"warning: {warning}", style="yellow")
    _run([code, "--install-extension", "ms-vscode-remote.remote-ssh"], timeout=timeout)
    path = quote(prepared["workspace"], safe="/")
    uri = f"vscode-remote://ssh-remote+{wiring.alias}{path}"
    bootstrap = f"vscode-remote://ssh-remote+{wiring.alias}"
    bootstrap += quote(prepared["bootstrap"], safe="/")
    console.print("Opening VS Code and waiting for its remote server...")
    _run([code, "--new-window", "--folder-uri", bootstrap], timeout=timeout)
    deadline = time.monotonic() + timeout
    server = ""
    while time.monotonic() < deadline:
        server = _run([*ssh, f"{PYTHON} /tmp/podbench-ide_remote.py server"])
        if server:
            break
        time.sleep(2)
    if not server:
        raise KubectlError(
            "VS Code did not start its remote server; "
            "check the Remote-SSH window and rerun"
        )
    for extension in prepared["extensions"]:
        console.print(f"Installing {extension} in the seat...")
        _run(
            [*ssh, shlex.join([server, "--install-extension", extension])],
            timeout=timeout,
        )
    installed = (
        _run([*ssh, shlex.join([server, "--list-extensions"])]).lower().splitlines()
    )
    missing = set(prepared["extensions"]) - set(installed)
    if missing:
        raise KubectlError(
            f"remote extension installation incomplete: {', '.join(sorted(missing))}"
        )
    _run([code, "--reuse-window", "--file-uri", uri], timeout=timeout)
    console.print(f"Ready: {wiring.alias}; {prepared['count']} debug launchers.")
