"""``podbench make-sa``: a namespace-confined ServiceAccount, and proof of it."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from ruamel.yaml import YAML

from .access import AccessError, fail, heading, kubectl, kubectl_out, note, require
from .agent_sa_checks import Mode, report_inherited, verify
from .agent_sa_rules import (
    Tiers,
    account_name,
    manifest,
    parse_tiers,
    snapshot_rules,
    tier_rules,
)
from .cli import new_app, run

HELP = """Provision agent-USER in NAMESPACE and prove it reaches nothing else.

Creates a ServiceAccount, Role and RoleBinding, writes a self-contained
kubeconfig (./NAMESPACE-agent-USER.kubeconfig unless --out says otherwise) and
runs confinement checks as the new account. Run it with your own credential;
it reads your current context for the API server and CA. Re-running is safe,
and is how an expired token is renewed.

With no permission flag the account gets a read-only copy of what you can read
in NAMESPACE, including secrets if you can read them.

--podbench grants podbench's own tiers, as the chart does: observe (attach),
iterate (`dev`), resize (`--resize`) and hotfix (patches a pod template, which
DEPLOYS CODE). Bare --podbench is observe; --podbench=iterate,resize adds to
it. No tier can run the e2e suite, which needs cluster-scoped access.

--all copies all of your permissions in NAMESPACE, writes and secrets
included, still as a Role only.
"""


def _parent_rules(namespace: str, *, read_only: bool) -> list[dict[str, Any]]:
    review = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectRulesReview",
        "spec": {"namespace": namespace},
    }
    result = kubectl("create", "-f", "-", "-o", "json", stdin=json.dumps(review))
    try:
        return snapshot_rules(json.loads(result.stdout), read_only=read_only)
    except (ValueError, json.JSONDecodeError) as error:
        raise AccessError(
            f"could not obtain a complete parent permission snapshot ({error});"
            " nothing applied"
        ) from error


def _cluster() -> dict[str, Any]:
    """The current context's cluster, with any CA file path embedded.

    kubeadm, k3s and minikube name the CA by path; the file handed over must not
    refer to a path that exists only on this machine.
    """
    config = json.loads(
        kubectl_out("config", "view", "--raw", "--minify", "--flatten", "-o", "json")
    )
    clusters = config.get("clusters") or []
    if not clusters:
        raise AccessError("could not resolve the cluster for the current context")
    source = clusters[0].get("cluster") or {}
    cluster: dict[str, Any] = {"server": source.get("server", "")}
    if source.get("certificate-authority-data"):
        cluster["certificate-authority-data"] = source["certificate-authority-data"]
    elif source.get("insecure-skip-tls-verify"):
        note("WARNING: cluster skips TLS verification; carrying that over")
        cluster["insecure-skip-tls-verify"] = True
    # Neither: the system trust store covers the server. Invent no CA.
    return cluster


def _write_kubeconfig(
    out: Path, cluster: dict[str, Any], account: str, namespace: str, token: str
) -> None:
    config = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "target", "cluster": cluster}],
        "users": [{"name": account, "user": {"token": token}}],
        "contexts": [
            {
                "name": account,
                "context": {
                    "cluster": "target",
                    "user": account,
                    "namespace": namespace,
                },
            }
        ],
        "current-context": account,
    }
    text = io.StringIO()
    YAML().dump(config, text)
    # It carries a live bearer token: never readable by anyone else, even briefly.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text.getvalue())
    out.chmod(0o600)


def token_expiry(token: str) -> datetime | None:
    """The token's own ``exp`` claim: the server silently clamps --duration."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        return datetime.fromtimestamp(int(claims["exp"]))
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def make_sa(
    namespace: str, *, tiers: Tiers, copy_all: bool, duration: str, out: Path | None
) -> int:
    require("kubectl")
    if copy_all and tiers.any:
        raise AccessError("--all cannot be combined with podbench tier flags")
    mode = Mode.TIERS if tiers.any else Mode.COPY if copy_all else Mode.READ
    account = account_name()
    out = (out or Path(f"{namespace}-{account}.kubeconfig")).absolute()
    if kubectl("get", "namespace", namespace).returncode != 0:
        raise AccessError(
            f"namespace {namespace!r} does not exist (or you cannot see it)"
        )

    heading(f"provisioning {account} in {namespace}")
    note(
        {
            Mode.TIERS: f"podbench tiers: {tiers}",
            Mode.COPY: f"permissions: copy of yours in {namespace}",
            Mode.READ: f"permissions: read-only copy of yours in {namespace}",
        }[mode]
    )
    rules = (
        tier_rules(tiers)
        if mode is Mode.TIERS
        else _parent_rules(namespace, read_only=mode is Mode.READ)
    )
    applied = kubectl(
        "apply", "-f", "-", stdin=json.dumps(manifest(account, namespace, rules))
    )
    print(applied.stdout, end="")
    if applied.returncode != 0:
        raise AccessError(applied.stderr.strip() or "kubectl apply failed")

    cluster = _cluster()
    token = kubectl_out(
        "-n", namespace, "create", "token", account, f"--duration={duration}"
    ).strip()
    _write_kubeconfig(out, cluster, account, namespace, token)
    heading(f"wrote {out}")
    expiry = token_expiry(token)
    heading(
        f"token expires {expiry:%Y-%m-%d %H:%M:%S} (asked for {duration})"
        if expiry
        else "token expiry could not be decoded"
    )

    print()
    passed = verify(out, namespace, tiers, mode)
    report_inherited(out)
    print()
    if not passed:
        return fail(
            "==> FAIL: confinement is not what was asked for. Do not hand this over."
        )
    heading(
        f"PASS: {account} reaches {namespace} and nothing else."
        if mode is Mode.TIERS
        else f"PASS: your permissions copied into a Role in {namespace}; checks passed."
    )
    note(f"use it with:  KUBECONFIG={out}")
    if mode is Mode.TIERS:
        note(f"check it with:  KUBECONFIG={out} podbench doctor -n {namespace}")
    return 0


def _build_app() -> typer.Typer:
    app = new_app()

    @app.command(help=HELP)
    def make_sa_command(
        namespace: Annotated[str, typer.Argument(metavar="NAMESPACE")],
        podbench: Annotated[
            list[str] | None,
            typer.Option(
                "--podbench", metavar="TIERS", help="observe,iterate,resize,hotfix,all"
            ),
        ] = None,
        podbench_all: Annotated[
            bool, typer.Option("--podbench-all", help="every podbench tier")
        ] = False,
        copy_all: Annotated[
            bool, typer.Option("--all", help="copy all your permissions in NAMESPACE")
        ] = False,
        duration: Annotated[
            str, typer.Option("--duration", help="requested token lifetime")
        ] = "24h",
        out: Annotated[
            Path | None, typer.Option("--out", help="kubeconfig to write")
        ] = None,
    ) -> None:
        try:
            tiers = parse_tiers([*(podbench or []), *(["all"] if podbench_all else [])])
            code = make_sa(
                namespace, tiers=tiers, copy_all=copy_all, duration=duration, out=out
            )
        except (AccessError, ValueError) as error:
            code = fail(str(error))
        raise typer.Exit(code)

    return app


def main(args: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if args is None else args)
    if argv and argv[0] == "make-sa":
        argv.pop(0)
    # Bare --podbench means the observe tier; click options need a value.
    argv = ["--podbench=observe" if arg == "--podbench" else arg for arg in argv]
    return run(_build_app(), argv, prog="podbench make-sa")


if __name__ == "__main__":
    sys.exit(main())
