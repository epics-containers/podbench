"""``podbench delete-sa``: remove an agent account made by ``make-sa``."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from .access import AccessError, fail, heading, kubectl, kubectl_out, require
from .agent_sa_rules import LABEL_SELECTOR, account_name
from .cli import new_app, run

HELP = """Remove agent-USER's ServiceAccount, Role and RoleBinding from NAMESPACE.

--user removes somebody else's account; --all removes every account make-sa
created there, matched by label so a hand-made agent-* account is left alone.
Any token Secret bound to the account, and its kubeconfig in --dir, go too.
Tokens from `kubectl create token` are bound to the account's UID, so they are
invalid the moment it is deleted.
"""

NAMES = "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}"


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _denied(namespace: str, what: str) -> AccessError:
    who = kubectl("auth", "whoami", "-o", "name").stdout.strip() or "unknown"
    context = kubectl("config", "current-context").stdout.strip() or "unknown"
    return AccessError(
        f"cannot {what} in {namespace!r} as {who!r} (context {context!r})"
    )


def _listed(namespace: str, what: str, *args: str) -> list[str]:
    result = kubectl("-n", namespace, *args)
    if result.returncode != 0:
        raise _denied(namespace, what)
    return _lines(result.stdout)


def _existing(namespace: str, name: str) -> bool:
    """Look before deleting: a Forbidden delete of an account that was never
    here reads as a permission problem, when it is usually the wrong namespace.
    """
    result = kubectl("-n", namespace, "get", f"serviceaccount/{name}", "-o", "name")
    if result.returncode == 0:
        return True
    if "NotFound" in result.stderr:
        return False
    error = _denied(namespace, f"read serviceaccount/{name}")
    raise AccessError(f"{error}; was it made in another namespace?")


def _managed(namespace: str) -> list[str]:
    return _listed(
        namespace,
        "list managed ServiceAccounts",
        "get",
        "serviceaccounts",
        "-l",
        LABEL_SELECTOR,
        "-o",
        NAMES,
    )


def _token_secrets(namespace: str, account: str) -> list[str]:
    """Long-lived token Secrets, bound to the account by annotation."""
    annotation = "kubernetes\\.io/service-account\\.name"
    return _listed(
        namespace,
        f"list token Secrets for serviceaccount/{account}",
        "get",
        "secrets",
        "--field-selector",
        "type=kubernetes.io/service-account-token",
        "-o",
        f"jsonpath={{range .items[?(@.metadata.annotations['{annotation}']"
        f"=='{account}')]}}{{.metadata.name}}{{'\\n'}}{{end}}",
    )


def delete_sa(
    namespace: str, *, user: str | None, every: bool, yes: bool, directory: Path
) -> int:
    require("kubectl")
    if every and user:
        raise AccessError("--all and --user are mutually exclusive")
    if every:
        accounts, wanted = _managed(namespace), "account made by make-sa"
    else:
        wanted = account_name(user)
        accounts = [wanted] if _existing(namespace, wanted) else []
    if not accounts:
        print(f"no {wanted} in {namespace}")
        return 0

    print(f"about to delete from namespace {namespace!r}:")
    for name in accounts:
        print(f"  serviceaccount/{name}  role/{name}  rolebinding/{name}")
    if not yes and not typer.confirm("proceed?", default=False):
        print("aborted")
        return 1

    for name in accounts:
        # --ignore-not-found so a partial earlier teardown still finishes.
        print(
            kubectl_out(
                "-n",
                namespace,
                "delete",
                "--ignore-not-found",
                f"serviceaccount/{name}",
                f"role.rbac.authorization.k8s.io/{name}",
                f"rolebinding.rbac.authorization.k8s.io/{name}",
            ),
            end="",
        )
        for secret in _token_secrets(namespace, name):
            print(f"  removing bound token secret/{secret}")
            kubectl_out(
                "-n", namespace, "delete", "--ignore-not-found", f"secret/{secret}"
            )
        # A useless credential left behind is how a stale token gets handed on.
        config = directory / f"{namespace}-{name}.kubeconfig"
        if config.is_file():
            config.unlink()
            print(f"  removed {config.absolute()}")

    print()
    heading(f"remaining in {namespace}:")
    left = _managed(namespace)
    for name in left:
        print(f"  {name}")
    if not left:
        print("  no accounts created by make-sa")
    print("done. Any token issued to a deleted account is invalid as of now.")
    return 0


def _build_app() -> typer.Typer:
    app = new_app()

    @app.command(help=HELP)
    def delete_sa_command(
        namespace: Annotated[str, typer.Argument(metavar="NAMESPACE")],
        user: Annotated[
            str | None, typer.Option("--user", help="whose account to remove")
        ] = None,
        every: Annotated[
            bool, typer.Option("--all", help="every account make-sa created here")
        ] = False,
        yes: Annotated[bool, typer.Option("-y", "--yes", help="do not ask")] = False,
        directory: Annotated[
            Path, typer.Option("--dir", help="where make-sa wrote the kubeconfig")
        ] = Path("."),
    ) -> None:
        try:
            code = delete_sa(
                namespace, user=user, every=every, yes=yes, directory=directory
            )
        except AccessError as error:
            code = fail(str(error))
        raise typer.Exit(code)

    return app


def main(args: Sequence[str] | None = None) -> int:
    argv = list(args) if args is not None else None
    if argv and argv[0] == "delete-sa":
        argv.pop(0)
    return run(_build_app(), argv, prog="podbench delete-sa")


if __name__ == "__main__":
    sys.exit(main())
