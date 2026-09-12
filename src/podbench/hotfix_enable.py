"""Enable hotfix wiring in an epics-containers service chart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import __version__
from .hotfix_core import HotfixError
from .hotfix_values import (
    HOTFIX_CHART,
    HOTFIX_CHART_REPOSITORY,
    chart_version,
    entrypoint,
    value_blocks,
)
from .kubectl import Kubectl
from .model import HOTFIX_APP_PATH, as_dict

WORKLOAD_KEYS = (
    "volumes",
    "volumeMounts",
    "command",
    "args",
    "livenessProbe",
    "podSecurityContext",
)


def _pod_for(kube: Kubectl, app: str, requested: str | None) -> dict[str, Any]:
    if requested:
        return kube.get_pod(requested.removeprefix("pod/"))
    matches = []
    for pod in kube.list_pods():
        metadata = as_dict(pod.get("metadata"))
        labels = as_dict(metadata.get("labels"))
        if labels.get("app") == app and not metadata.get("deletionTimestamp"):
            matches.append(pod)
    if len(matches) != 1:
        names = ", ".join(str(as_dict(p.get("metadata")).get("name")) for p in matches)
        detail = f": {names}" if names else ""
        found = f"found {len(matches)}{detail}"
        raise HotfixError(
            f"expected one live pod with label app={app}, {found}; pass --from-pod"
        )
    return matches[0]


def _ioc_command(pod: dict[str, Any], container: str | None) -> str:
    from .hotfix_core import find_container
    from .launcher import target_container_name

    chosen = target_container_name(pod, container)
    original = entrypoint(find_container(pod, chosen))
    editable = f"{HOTFIX_APP_PATH}/ioc/start.sh"
    return "\n".join(
        [
            f"if [[ -f {editable} ]]; then",
            f"  exec bash {editable}",
            "else",
            f"  exec {original}",
            "fi",
        ]
    )


def _prefix(values: str, override: str | None) -> str | None:
    if override is not None:
        return override
    return (
        "ioc-instance" if re.search(r"(?m)^ioc-instance:\s*(?:#.*)?$", values) else None
    )


def _dependency(chart: str) -> tuple[str, bool]:
    if re.search(rf"(?m)^\s+- name:\s*{re.escape(HOTFIX_CHART)}\s*$", chart):
        return chart, False
    lines = chart.rstrip("\n").splitlines()
    start = next((i for i, line in enumerate(lines) if line == "dependencies:"), None)
    if start is None:
        raise HotfixError("Chart.yaml has no top-level dependencies list")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line[0].isspace() and not line.startswith("#"):
            end = index
            break
    block = [
        f"  - name: {HOTFIX_CHART}",
        f'    version: "{chart_version(__version__)}"',
        f'    repository: "{HOTFIX_CHART_REPOSITORY}"',
    ]
    if end and lines[end - 1]:
        block.insert(0, "")
    lines[end:end] = block
    return "\n".join(lines) + "\n", True


def _mapping_bounds(lines: list[str], key: str) -> tuple[int, int]:
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.fullmatch(rf"{re.escape(key)}:\s*", line)
        ),
        None,
    )
    if start is None:
        return len(lines), len(lines)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line[0].isspace() and not line.startswith("#"):
            end = index
            break
    return start, end


def _values(
    current: str, claim: list[str], workload: list[str], prefix: str | None
) -> tuple[str, bool]:
    if re.search(r"(?m)^podbench-hotfix-claim:\s*$", current):
        return current, False
    lines = current.rstrip("\n").splitlines()
    if prefix:
        start, end = _mapping_bounds(lines, prefix)
        section = lines[start + 1 : end] if start < len(lines) else []
        conflicts = [
            key
            for key in WORKLOAD_KEYS
            if any(re.match(rf"^  {re.escape(key)}:\s*", line) for line in section)
        ]
        if conflicts:
            raise HotfixError(
                "values.yaml already sets "
                + ", ".join(conflicts)
                + "; use hotfix values"
            )
        addition = [*[f"  {line}" for line in workload], ""]
        if start == len(lines):
            addition.insert(0, f"{prefix}:")
        elif end and lines[end - 1]:
            addition.insert(0, "")
        lines[end:end] = addition
    else:
        conflicts = [
            key
            for key in WORKLOAD_KEYS
            if re.search(rf"(?m)^{re.escape(key)}:\s*", current)
        ]
        if conflicts:
            raise HotfixError(
                "values.yaml already sets "
                + ", ".join(conflicts)
                + "; use hotfix values"
            )
        lines += ["", *workload]
    lines += ["", *claim]
    return "\n".join(lines).lstrip("\n") + "\n", True


def enable(
    kube: Kubectl,
    service: Path,
    *,
    app: str | None = None,
    from_pod: str | None = None,
    container: str | None = None,
    command: str | None = None,
    gid: int | None = None,
    size: str = "10Gi",
    values_prefix: str | None = None,
) -> list[str]:
    service = service.resolve()
    values_path = service / "values.yaml"
    chart_link = service / "Chart.yaml"
    if not values_path.is_file() or not chart_link.exists():
        raise HotfixError(f"{service} must contain values.yaml and Chart.yaml")
    release = app or service.name
    pod = _pod_for(kube, release, from_pod)
    current_values = values_path.read_text()
    prefix = _prefix(current_values, values_prefix)
    if command is None and prefix == "ioc-instance":
        command = _ioc_command(pod, container)
    claim, workload = value_blocks(
        pod,
        release,
        container_name=container,
        command=command,
        gid=gid,
        size=size,
    )
    chart_path = chart_link.resolve(strict=True)
    new_chart, chart_changed = _dependency(chart_path.read_text())
    new_values, values_changed = _values(current_values, claim, workload, prefix)
    if chart_changed:
        chart_path.write_text(new_chart)
    if values_changed:
        values_path.write_text(new_values)
    lines = [f"release: {release}", f"pod: {as_dict(pod.get('metadata')).get('name')}"]
    lines.append(f"{'updated' if chart_changed else 'unchanged'}: {chart_path}")
    lines.append(f"{'updated' if values_changed else 'unchanged'}: {values_path}")
    return lines
