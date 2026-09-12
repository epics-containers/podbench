"""The workstation entry point for a normal VS Code Remote-SSH session."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Annotated

import typer

from .cli import error_console, new_app, require_subcommand, run
from .kubectl import KubectlError
from .launcher import LauncherError, kubectl_for
from .model import DEFAULT_IMAGE, IMAGE_ENV
from .ssh_transport import DEFAULT_IDENTITY


def _build_app() -> typer.Typer:
    app = new_app()
    app.callback(invoke_without_command=True)(require_subcommand)

    @app.command()
    def vscode(
        pod: Annotated[str, typer.Argument(help="pod/NAME or bare NAME")],
        target: Annotated[
            str | None, typer.Option(help="application container")
        ] = None,
        namespace: Annotated[str | None, typer.Option("-n", "--namespace")] = None,
        context: Annotated[str | None, typer.Option()] = None,
        image: Annotated[str | None, typer.Option(help="debug image")] = None,
        identity: Annotated[
            str, typer.Option(help="SSH private key")
        ] = DEFAULT_IDENTITY,
        config_dir: Annotated[str | None, typer.Option()] = None,
        code: Annotated[str, typer.Option(help="local VS Code CLI")] = "code",
        timeout: Annotated[
            float, typer.Option(min=1, help="resize and server startup timeout")
        ] = 180,
    ) -> None:
        """Open a seat with Python/C++ debugging, source and forwarded Git identity."""
        from .ide_vscode import open_vscode

        open_vscode(
            kubectl_for(namespace, context=context),
            pod,
            target=target,
            image=image or os.environ.get(IMAGE_ENV, DEFAULT_IMAGE),
            identity=identity,
            config_dir=config_dir,
            code=code,
            timeout=timeout,
        )

    return app


def main(args: Sequence[str] | None = None) -> int:
    argv = list(args) if args is not None else None
    if argv and argv[0] == "ide":
        argv.pop(0)
    try:
        return run(_build_app(), argv, prog="podbench ide")
    except (KubectlError, LauncherError, OSError, ValueError) as error:
        error_console.print(f"podbench: {error}", style="red")
        return 2
