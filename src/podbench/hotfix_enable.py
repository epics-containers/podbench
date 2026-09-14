"""Enable hotfix wiring in an epics-containers service chart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import __version__
from .hotfix_blueapi import workload as blueapi_workload
from .hotfix_blueapi import write_template as write_blueapi_template
from .hotfix_core import HotfixError
from .hotfix_values import (
    HOTFIX_CHART,
    HOTFIX_CHART_REPOSITORY,
    MARKER_BEGIN,
    MARKER_END,
    chart_version,
    entrypoint,
    marked,
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
# Keys an earlier `hotfix enable` may have written without markers. Present
# together with a claim block, they are treated as generated and replaced.
GENERATED_KEYS = (*WORKLOAD_KEYS, "readinessProbe", "startupProbe", "debug")


def _pod_for(
    kube: Kubectl, app: str, requested: str | None, container: str | None = None
) -> dict[str, Any]:
    if requested:
        return kube.get_pod(requested.removeprefix("pod/"))
    matches = []
    for pod in kube.list_pods():
        metadata = as_dict(pod.get("metadata"))
        labels = as_dict(metadata.get("labels"))
        matches_release = labels.get("app") == app or (
            labels.get("app.kubernetes.io/instance") == app
        )
        containers = as_dict(pod.get("spec")).get("containers")
        container_list = containers if isinstance(containers, list) else []
        matches_container = container is None or any(
            as_dict(item).get("name") == container for item in container_list
        )
        if (
            matches_release
            and matches_container
            and not metadata.get("deletionTimestamp")
        ):
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
    for key in ("ioc-instance", "blueapi"):
        if re.search(rf"(?m)^{key}:\s*(?:#.*)?$", values):
            return key
    return None


def _dependency(chart: str) -> tuple[str, bool]:
    """Add the claim chart dependency, or pin an existing one to this version."""
    lines = chart.rstrip("\n").splitlines()
    wanted = f'    version: "{chart_version(__version__)}"'
    name = next(
        (
            i
            for i, line in enumerate(lines)
            if re.fullmatch(rf"\s+- name:\s*{re.escape(HOTFIX_CHART)}\s*", line)
        ),
        None,
    )
    if name is not None:
        for index in range(name + 1, len(lines)):
            line = lines[index]
            if re.match(r"\s+- name:", line) or not line.startswith(" "):
                break
            if re.match(r"\s+version:", line):
                if line == wanted:
                    return chart, False
                lines[index] = wanted
                return "\n".join(lines) + "\n", True
        return chart, False
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
        wanted,
        f'    repository: "{HOTFIX_CHART_REPOSITORY}"',
    ]
    if end and lines[end - 1]:
        block.insert(0, "")
    lines[end:end] = block
    return "\n".join(lines) + "\n", True


def _mapping_bounds(lines: list[str], key: str) -> tuple[int, int]:
    """Find a top-level block's start and exclusive end, or EOF when absent."""
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.fullmatch(rf"{re.escape(key)}:\s*(?:#.*)?", line)
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


def _block_end(lines: list[str], start: int, end: int, indent: str) -> int:
    """Exclusive end of the YAML block whose key sits on lines[start]."""
    for index in range(start + 1, end):
        line = lines[index]
        if line and not line.startswith(f"{indent} ") and not line.startswith("#"):
            return index
    return end


def _generated_span(
    lines: list[str], start: int, end: int, indent: str
) -> tuple[int, int] | None:
    """Return the marked region inside lines[start:end], or None."""
    begin = next(
        (
            i
            for i in range(start, end)
            if lines[i].startswith(f"{indent}{MARKER_BEGIN}")
        ),
        None,
    )
    if begin is None:
        return None
    stop = next(
        (i for i in range(begin, end) if lines[i] == f"{indent}{MARKER_END}"), None
    )
    if stop is None:
        raise HotfixError(f"values.yaml has '{MARKER_BEGIN}' without '{MARKER_END}'")
    return begin, stop + 1


def _generated_blocks(
    lines: list[str], start: int, end: int, indent: str
) -> list[tuple[int, int, str]]:
    """Blocks of generated keys inside lines[start:end], as (start, end, key)."""
    found = []
    for i in range(start, end):
        for key in GENERATED_KEYS:
            if re.match(rf"^{indent}{re.escape(key)}:", lines[i]):
                found.append((i, _block_end(lines, i, end, indent), key))
    return found


def _values(
    current: str, claim: list[str], workload: list[str], prefix: str | None
) -> tuple[str, bool]:
    """Insert or replace hotfix blocks while preserving surrounding text.

    The claim block marks a file an earlier enable has wired. With it present,
    the generated workload is replaced in place: a marked region as one unit,
    or, for output from a podbench that wrote no markers, each generated key
    block where it sits, with the new marked block taking the first one's
    place. Without a claim block, generated keys belong to the user and are
    refused.
    """
    lines = current.rstrip("\n").splitlines()
    has_claim = bool(re.search(r"(?m)^podbench-hotfix-claim:\s*$", current))
    if workload and not workload[0].startswith(MARKER_BEGIN):
        workload = marked(workload)
    indent = "  " if prefix else ""
    if prefix:
        start, end = _mapping_bounds(lines, prefix)
        section = (start + 1, end) if start < len(lines) else (0, 0)
    else:
        start, end = 0, len(lines)
        # Top-level layout: the claim block is not part of the workload.
        section = (0, _mapping_bounds(lines, "podbench-hotfix-claim")[0])
    body = [f"{indent}{line}" if line else line for line in workload]
    span = _generated_span(lines, *section, indent)
    blocks = [] if span else _generated_blocks(lines, *section, indent)
    if span is not None:
        lines[span[0] : span[1]] = body
    elif blocks and not has_claim:
        keys = ", ".join(dict.fromkeys(key for _, _, key in blocks))
        raise HotfixError(f"values.yaml already sets {keys}; use hotfix values")
    elif blocks:
        first = blocks[0][0]
        for block_start, block_end, _ in reversed(blocks):
            del lines[block_start:block_end]
        lines[first:first] = body
    elif prefix:
        addition = [*body, ""]
        if start == len(lines):
            addition.insert(0, f"{prefix}:")
        elif end and lines[end - 1]:
            addition.insert(0, "")
        lines[end:end] = addition
    else:
        lines += ["", *body]
    if not has_claim:
        lines += ["", *claim]
    new = "\n".join(lines).lstrip("\n").rstrip("\n") + "\n"
    return new, new != current


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
    """Use live pod details to edit local chart wiring for review and deployment."""
    service = service.resolve()
    values_path = service / "values.yaml"
    chart_link = service / "Chart.yaml"
    if not values_path.is_file() or not chart_link.exists():
        raise HotfixError(f"{service} must contain values.yaml and Chart.yaml")
    release = app or service.name
    current_values = values_path.read_text()
    prefix = _prefix(current_values, values_prefix)
    pod = _pod_for(
        kube,
        release,
        from_pod,
        container or ("blueapi" if prefix == "blueapi" else None),
    )
    if command is None and prefix == "ioc-instance":
        command = _ioc_command(pod, container)
    claim = [
        "podbench-hotfix-claim:",
        "  enabled: true",
        f"  size: {size}",
    ]
    if prefix == "blueapi" and command is None:
        workload = blueapi_workload(pod, release, container, gid)
    else:
        claim, workload = value_blocks(
            pod,
            release,
            container_name=container,
            command=command,
            gid=gid,
            size=size,
        )
    # Service charts may share Chart.yaml through a symlink; edit its referent.
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
    if prefix == "blueapi" and command is None:
        template, changed = write_blueapi_template(service)
        lines.append(f"{'updated' if changed else 'unchanged'}: {template}")
    return lines
