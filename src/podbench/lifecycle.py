"""Workstation and seat application lifecycle commands."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from typing import Annotated

import typer

from .cli import console, error_console, new_app, run
from .hotfix_core import HotfixError, exec_target, resolve_target
from .kubectl import Kubectl, KubectlError
from .launcher import LauncherError, kubectl_for
from .lifecycle_client import action, control_root


def operate(
    kube: Kubectl,
    pod: str,
    verb: str,
    container: str | None = None,
    *,
    deadline: int = 120,
) -> list[str]:
    target, _ = resolve_target(kube, pod, container)
    script = r"""
set -eu
control=/tmp/podbench-control
[ "$(cat "$control/version" 2>/dev/null)" = 1 ] || {
  echo 'update hotfix wiring and roll out: lifecycle protocol unavailable' >&2
  exit 1
}
request=$(mktemp -d "$control/requests/r-XXXXXXXX")
printf '%s\n' VERB > "$request/action"
printf '%s\n' DEADLINE > "$request/deadline"
touch "$request/ready"
expires=$((SECONDS + DEADLINE + 15))
while [ ! -f "$request/response" ]; do
  [ "$SECONDS" -lt "$expires" ] || {
    echo 'request timed out; inspect status' >&2; exit 1
  }
  sleep 0.1
done
result=$(cat "$request/response")
rm -rf "$request"
[ "$result" = ok ] || { echo "$result" >&2; exit 1; }
cat "$control/state"
""".replace("VERB", shlex.quote(verb)).replace("DEADLINE", str(deadline))
    result = exec_target(kube, target, script, timeout=float(deadline + 25))
    return [f"{target.container}: {result.stdout.strip()}"]


def main(args: Sequence[str]) -> int:
    verb, *argv = args
    app = new_app()

    @app.command()
    def command(
        pod: Annotated[str | None, typer.Argument()] = None,
        namespace: Annotated[str | None, typer.Option("-n", "--namespace")] = None,
        context: Annotated[str | None, typer.Option("--context")] = None,
        container: Annotated[
            str | None, typer.Option("--container", "--target")
        ] = None,
        kubectl: Annotated[str, typer.Option("--kubectl")] = "kubectl",
    ) -> None:
        if not pod and (namespace or context or container):
            raise ValueError("a pod is required with Kubernetes target options")
        message = {
            "start": "Starting application; waiting for health checks...",
            "stop": "Stopping application...",
            "restart": "Restarting application; waiting for health checks...",
        }[verb]
        with console.status(message):
            if pod:
                kube = kubectl_for(namespace, context=context, binary=kubectl)
                lines = operate(kube, pod, verb, container)
            else:
                action(control_root(), verb)
                lines = [f"application {verb} completed"]
        for line in lines:
            console.print(line)

    try:
        return run(app, argv, prog=f"podbench {verb}")
    except (
        OSError,
        RuntimeError,
        HotfixError,
        LauncherError,
        KubectlError,
        ValueError,
    ) as error:
        error_console.print(f"podbench: {error}", style="red")
        return 2
