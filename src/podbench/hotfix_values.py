"""Generate the deploy-time wiring for one workload claim."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from typing import Any

from . import __version__
from .hotfix_core import HotfixError, find_container
from .launcher import target_container_name, target_uid_gid
from .lifecycle_health import health_command, unwrap_probe
from .lifecycle_supervisor import supervisor
from .model import (
    HOTFIX_APP_PATH,
    HOTFIX_CLAIM_VOLUME,
    HOTFIX_HOLD_PATH,
    as_dict,
)

RESTART_WINDOW_SECONDS = 120
HOTFIX_CHART = "podbench-hotfix-claim"
HOTFIX_CHART_REPOSITORY = "oci://ghcr.io/epics-containers/charts"
SUPERVISOR_ARG = "podbench-supervisor"
# Comment lines bracketing the generated workload keys in values.yaml, so a
# later `hotfix enable` can find and replace its own output instead of
# refusing or duplicating it. Only the prefix is matched; the rest records
# which podbench wrote the block.
MARKER_BEGIN = "# podbench-hotfix: begin"
MARKER_END = "# podbench-hotfix: end"
_EDITABLE_FALLBACK = re.compile(
    rf"^if \[\[ -f {re.escape(HOTFIX_APP_PATH)}/\S+ \]\]; then\n"
    r"  exec .*\nelse\n  exec (?P<original>.*)\nfi\n?$",
    re.DOTALL,
)


def chart_version(version: str) -> str:
    """Translate the package's PEP 440 prerelease into a Helm SemVer tag."""
    development = ".dev" in version
    released = re.split(r"(?:\.dev|\+)", version, maxsplit=1)[0]
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", released)
    if match:
        base, phase, number = match.groups()
        number = str(max(1, int(number) - 1)) if development else number
        phase = {"a": "alpha", "b": "beta", "rc": "rc"}[phase]
        return f"{base}-{phase}.{number}"
    stable = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", released)
    if development and stable and int(stable.group(3)):
        major, minor, patch = stable.groups()
        return f"{major}.{minor}.{int(patch) - 1}"
    return released


def claim_for(app: str) -> str:
    return f"{app}-podbench-project"[:63].rstrip("-")


def entrypoint(container: Mapping[str, Any]) -> str:
    """Quote the pod's explicit command and args; image-only entrypoints need input.

    A pod already running a podbench supervisor reports that supervisor as its
    command. The application command is recovered from the supervisor's
    launch argument, so regenerating wiring never nests one supervisor in
    another.
    """
    command = container.get("command")
    if not isinstance(command, list) or not command:
        raise HotfixError(
            "the entrypoint is only in the image; use podbench hotfix enable "
            "SERVICE_DIRECTORY, or pass --entrypoint for generic output"
        )
    words = [str(word) for word in command]
    args = container.get("args")
    if isinstance(args, list):
        words.extend(str(word) for word in args)
    if wrapped := supervised_launch(words):
        return wrapped
    return shlex.join(words)


def supervised_launch(words: list[str]) -> str | None:
    """Return the application command behind a podbench supervisor, or None.

    Both supervisor generations run as ``bash -c SCRIPT podbench-supervisor
    LAUNCH``. LAUNCH is ``exec COMMAND`` for a one-line command, or the
    multi-line editable-checkout fallback whose ``else`` branch holds the
    original command.
    """
    if len(words) != 5 or words[:2] != ["bash", "-c"] or words[3] != SUPERVISOR_ARG:
        return None
    launch = words[4]
    if match := _EDITABLE_FALLBACK.match(launch):
        return match.group("original")
    return launch.removeprefix("exec ").strip()


def yaml_scalar(value: str) -> str:
    if re.search(r":(?:\s|$)|(?:^|\s)#|[\n\r\t]", value):
        return json.dumps(value)
    return value


def _liveness(container: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]] | None:
    probe = as_dict(container.get("livenessProbe"))
    if not probe:
        return None
    command = as_dict(probe.get("exec")).get("command")
    if not isinstance(command, list) or not command:
        # HTTP, TCP, and gRPC probes cannot see the hold file. Their failure
        # threshold is extended below instead of replacing the probe action.
        return None
    timings = {key: value for key, value in probe.items() if key != "exec"}
    return unwrap_probe(command), timings


def value_blocks(
    pod: Mapping[str, Any],
    app: str,
    *,
    container_name: str | None = None,
    command: str | None = None,
    gid: int | None = None,
    size: str = "10Gi",
) -> tuple[list[str], list[str]]:
    """Return separate YAML lines for the claim dependency and workload settings."""
    chosen = target_container_name(pod, container_name)
    container = find_container(pod, chosen)
    command = command or entrypoint(container)
    _, discovered_gid = target_uid_gid(pod, chosen)
    gid = gid if gid is not None else discovered_gid
    if gid is None:
        raise HotfixError("the target gid is not reported; pass --gid")
    claim = claim_for(app)
    claim_lines = [
        "podbench-hotfix-claim:",
        "  enabled: true",
        f"  size: {size}",
    ]
    launch = command if "\n" in command else f"exec {command}"
    launch_lines = (
        ["  - |", *[f"    {line}" for line in launch.splitlines()]]
        if "\n" in launch
        else [f"  - {yaml_scalar(launch)}"]
    )
    live_probe = container.get("livenessProbe", {})
    health = health_command(container)
    safe = not live_probe or _liveness(container) is not None
    workload = [
        "volumes:",
        f"  - name: {HOTFIX_CLAIM_VOLUME}",
        "    persistentVolumeClaim:",
        f"      claimName: {claim}",
        "volumeMounts:",
        f"  - name: {HOTFIX_CLAIM_VOLUME}",
        f"    mountPath: {HOTFIX_APP_PATH}",
        "command: [bash, -c]",
        "args:",
        "  - |",
        *[f"    {line}" for line in supervisor(health=health, safe=safe).splitlines()],
        "  - podbench-supervisor",
        *launch_lines,
    ]
    if live := _liveness(container):
        original, timings = live
        wrapped = f"[ -e {HOTFIX_HOLD_PATH} ] && exit 0; exec {shlex.join(original)}"
        workload += [
            "livenessProbe:",
            "  exec:",
            "    command: [bash, -c, " + json.dumps(wrapped) + "]",
        ]
        workload += [f"  {key}: {json.dumps(value)}" for key, value in timings.items()]
    elif probe := as_dict(container.get("livenessProbe")):
        period = probe.get("periodSeconds", 10)
        period = period if isinstance(period, int) and period > 0 else 10
        failures = probe.get("failureThreshold", 3)
        failures = failures if isinstance(failures, int) else 3
        restart_failures = (RESTART_WINDOW_SECONDS + period - 1) // period + 1
        workload += [
            "livenessProbe:",
            f"  failureThreshold: {max(failures, restart_failures)}",
        ]
    workload += ["podSecurityContext:", f"  fsGroup: {gid}"]
    return claim_lines, marked(workload)


def marked(workload: list[str]) -> list[str]:
    """Bracket generated workload lines with the markers hotfix enable rewrites."""
    return [f"{MARKER_BEGIN} podbench {__version__}", *workload, MARKER_END]


def render_values(
    pod: Mapping[str, Any],
    app: str,
    *,
    container_name: str | None = None,
    command: str | None = None,
    gid: int | None = None,
    size: str = "10Gi",
    values_prefix: str | None = None,
) -> str:
    claim_lines, workload = value_blocks(
        pod,
        app,
        container_name=container_name,
        command=command,
        gid=gid,
        size=size,
    )
    lines = [
        "",
        "",
        "# Add this dependency under `dependencies:` in Chart.yaml.",
        f"  - name: {HOTFIX_CHART}",
        f'    version: "{chart_version(__version__)}"',
        f'    repository: "{HOTFIX_CHART_REPOSITORY}"',
        "",
        "",
        "# Add this block to values.yaml.",
        *claim_lines,
        "",
    ]
    if values_prefix:
        lines += [f"{values_prefix}:", *[f"  {line}" for line in workload]]
    else:
        lines += workload
    return "\n".join(lines) + "\n"
