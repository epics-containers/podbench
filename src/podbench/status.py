"""Show attach and hotfix state together."""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated, Any

import typer
from rich.table import Table

from .cli import console, error_console, new_app, run
from .hotfix_core import HotfixError
from .hotfix_status import hotfix_container, hotfix_state
from .kubectl import Kubectl, KubectlError, Runner
from .launcher import CONTAINER_BASE, LauncherError, kubectl_for
from .model import as_dict


def _items(value: object) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _seats(pod: dict[str, Any]) -> tuple[dict[str, list[str]], bool]:
    spec = as_dict(pod.get("spec"))
    status = as_dict(pod.get("status"))
    states = {
        str(item.get("name")): as_dict(item.get("state"))
        for item in _items(status.get("ephemeralContainerStatuses"))
    }
    seats: dict[str, list[str]] = defaultdict(list)
    healthy = True
    for item in _items(spec.get("ephemeralContainers")):
        name = str(item.get("name", ""))
        if not name.startswith(f"{CONTAINER_BASE}-"):
            continue
        target = str(item.get("targetContainerName") or "?")
        state = states.get(name, {})
        if as_dict(state.get("running")):
            label = name
        elif as_dict(state.get("waiting")):
            label = f"{name} (waiting)"
            healthy = False
        else:
            label = f"{name} (stopped)"
            healthy = False
        seats[target].append(label)
    return dict(seats), healthy


def _show(kube: Kubectl, pod_name: str | None) -> bool:
    pods = (
        [kube.get_pod(pod_name.removeprefix("pod/"))] if pod_name else kube.list_pods()
    )
    table = Table(box=None, pad_edge=False)
    for heading in ("POD", "TARGET", "SEAT", "HOTFIX"):
        table.add_column(heading)
    healthy = True
    rows = 0
    for pod in pods:
        name = str(as_dict(pod.get("metadata")).get("name", "?"))
        seats, seats_healthy = _seats(pod)
        hotfix = hotfix_container(pod)
        targets = set(seats)
        if hotfix:
            targets.add(hotfix)
        for target in sorted(targets):
            hotfix_label = "—"
            if hotfix == target:
                hotfix_label, hotfix_healthy = hotfix_state(kube, pod, target)
                healthy = healthy and hotfix_healthy
            table.add_row(
                name,
                target,
                ", ".join(seats.get(target, [])) or "—",
                hotfix_label,
            )
            rows += 1
        healthy = healthy and seats_healthy
    if rows:
        console.print(table)
    else:
        console.print(f"no Podbench activity in namespace {kube.namespace}")
    return healthy


def _build_app(runner: Runner | None = None) -> typer.Typer:
    app = new_app()

    @app.command()
    def status_command(
        pod: Annotated[str | None, typer.Argument(metavar="POD")] = None,
        namespace: Annotated[
            str | None,
            typer.Option("-n", "--namespace", metavar="NAMESPACE"),
        ] = None,
        context: Annotated[
            str | None, typer.Option("--context", metavar="NAME")
        ] = None,
        kubectl: Annotated[str, typer.Option("--kubectl", metavar="BIN")] = "kubectl",
    ) -> None:
        kube = kubectl_for(namespace, context=context, binary=kubectl, runner=runner)
        if not _show(kube, pod):
            raise typer.Exit(1)

    return app


def main(args: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    argv = list(sys.argv[1:] if args is None else args)
    if argv and argv[0] == "status":
        argv.pop(0)
    try:
        return run(_build_app(runner), argv, prog="podbench status")
    except (HotfixError, LauncherError, KubectlError, ValueError) as error:
        error_console.print(f"podbench: {error}", style="red")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
