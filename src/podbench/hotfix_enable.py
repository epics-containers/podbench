"""Enable hotfix wiring in an epics-containers service chart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from . import __version__
from .hotfix_blueapi import workload as blueapi_workload
from .hotfix_blueapi import write_template as write_blueapi_template
from .hotfix_core import HotfixError
from .hotfix_values import (
    HOTFIX_CHART,
    HOTFIX_CHART_REPOSITORY,
    chart_version,
    ioc_entrypoint,
    value_blocks,
)
from .hotfix_yaml import (
    detached,
    dump,
    load,
    mapping,
    mark,
    merge,
    unmark,
)
from .kubectl import Kubectl
from .model import as_dict


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
        raise HotfixError(
            f"expected one live pod with label app={app}, "
            f"found {len(matches)}{detail}; pass --from-pod"
        )
    return matches[0]


def _prefix(values: str, override: str | None) -> str | None:
    if override is not None:
        return override
    document = load(values)
    return next((key for key in ("ioc-instance", "blueapi") if key in document), None)


def _dependency(chart: str) -> tuple[str, bool]:
    document = load(chart)
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, CommentedSeq):
        raise HotfixError("Chart.yaml has no top-level dependencies list")
    if any(not isinstance(item, CommentedMap) for item in dependencies):
        raise HotfixError("Chart.yaml dependencies must be mappings")
    matching = [item for item in dependencies if item.get("name") == HOTFIX_CHART]
    if len(matching) > 1:
        raise HotfixError("duplicate podbench-hotfix-claim dependencies")
    wanted = DoubleQuotedScalarString(chart_version(__version__))
    if matching:
        if matching[0].get("version") == wanted:
            return chart, False
        matching[0]["version"] = wanted
    else:
        dependencies.append(
            CommentedMap(
                name=HOTFIX_CHART,
                version=wanted,
                repository=DoubleQuotedScalarString(HOTFIX_CHART_REPOSITORY),
            )
        )
    return dump(document), True


def _values(
    current: str, claim: list[str], workload: list[str], prefix: str | None
) -> tuple[str, bool]:
    load(current)  # Reject invalid/duplicate YAML before processing ownership.
    clean = unmark(current, prefix)
    document = load(clean)
    document.fa.set_block_style()
    target = mapping(document, prefix) if prefix else document
    if prefix:
        target = document[prefix] = detached(target)
    patch = load("\n".join(workload))
    owned = []
    merge(target, patch, owned)
    if HOTFIX_CHART not in document:
        document.update(load("\n".join(claim)))
    if not prefix:
        document.move_to_end(HOTFIX_CHART)
    new = mark(dump(document), prefix, owned)
    load(new)  # Never write an invalid generated document.
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
        command = ioc_entrypoint(pod, container)
    claim = ["podbench-hotfix-claim:", "  enabled: true", f"  size: {size}"]
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
