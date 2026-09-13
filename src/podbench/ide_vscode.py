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
from .doctor import include_is_active
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


def _upload_helpers(kube: Kubectl, pod: str, seat: str) -> str:
    modules = (
        "ide_remote",
        "ide_launchers",
        "ide_python",
        "gdb_support",
        "gdb_session",
        "debug_model",
        "ssh_agent",
    )
    sources = {
        f"{name}.py": files("podbench").joinpath(f"{name}.py").read_text()
        for name in modules
    }
    sources["__init__.py"] = '"""Private workstation IDE helpers."""\n'
    result = kube.exec_(
        pod,
        [
            PYTHON,
            "-c",
            "import json,pathlib,sys,tempfile; "
            "root=pathlib.Path(tempfile.mkdtemp(prefix='podbench-ide-')); "
            "package=root/'podbench'; package.mkdir(mode=0o700); "
            "[(package/name).write_text(source) "
            "for name,source in json.load(sys.stdin).items()]; "
            "print(root)",
        ],
        container=seat,
        stdin=json.dumps(sources),
    )
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
    no_headroom: bool = False,
    forward_agent: bool = False,
) -> None:
    for binary in (code, "ssh", "git", *(("ssh-add",) if forward_agent else ())):
        if shutil.which(binary) is None:
            raise KubectlError(f"{binary} is required on the workstation")
    read_public_key(identity)
    if not include_is_active(config_dir):
        raise KubectlError("SSH Include is not active; run podbench doctor --fix first")
    if forward_agent and not os.environ.get("SSH_AUTH_SOCK"):
        raise KubectlError(
            "start an SSH agent and load your Git key with ssh-add first"
        )
    if forward_agent:
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
    if no_headroom:
        console.print("Using existing pod resources (--no-headroom); skipping resize.")
    else:
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
        forward_agent=forward_agent,
        ide=True,
    )
    # Keep the complete helper dependency set private and independent of the
    # installed seat package, including when reconnecting to an older image.
    bundle = _upload_helpers(kube, pod, session.seat.container)
    helper = shlex.join(
        ["env", f"PYTHONPATH={bundle}", PYTHON, "-m", "podbench.ide_remote"]
    )
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", wiring.alias]
    if forward_agent and not _run([*ssh, "ssh-add -l"]):
        raise KubectlError("SSH agent forwarding did not reach the seat")
    result = _run(
        [*ssh, f"{helper} prepare"],
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
    # A plain folder opens first: the remote server must run before extensions
    # can be installed into it, and the workspace file waits for those extensions.
    console.print("Opening VS Code and waiting for its remote server...")
    _run([code, "--new-window", "--folder-uri", bootstrap], timeout=timeout)
    deadline = time.monotonic() + timeout
    server = ""
    while time.monotonic() < deadline:
        server = _run([*ssh, f"{helper} server"])
        if server:
            break
        time.sleep(2)
    if not server:
        raise KubectlError(
            "VS Code did not start its remote server; "
            "check the Remote-SSH window and rerun"
        )
    installed = set(
        _run([*ssh, shlex.join([server, "--list-extensions"])]).lower().splitlines()
    )
    for extension in prepared["extensions"]:
        if extension.lower() in installed:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KubectlError("timed out while installing remote extensions")
        console.print(f"Installing {extension} in the seat...")
        _run(
            [*ssh, shlex.join([server, "--install-extension", extension])],
            timeout=remaining,
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
