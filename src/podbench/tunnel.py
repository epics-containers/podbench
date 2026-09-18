"""``podbench tunnel``: reach a cluster's API through an ssh tunnel."""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from .access import AccessError, fail, heading, kubectl, mark, require
from .cli import new_app, run
from .tunnel_source import Api, Source, fetch, parse_source, read_api, write_copy

HELP = """Write a kubeconfig that reaches its API server through an ssh tunnel.

For a VPN that forwards ssh and nothing else: podbench reaches seats through
`kubectl exec`, so a reachable API server is the whole requirement.

KUBECONFIG may be local or scp-style [user@]host:path, in which case it is read
over ssh and never stored here; that host is then the default --ssh-host. The
tunnel exits from --ssh-host, which is the address the API server sees.

The copy is written beside a local source as NAME-tunnel.kubeconfig, or in the
current directory for a remote one. Everything but the address is carried
over, and TLS is still verified against the original name and CA. Re-running
reuses the tunnel; --stop closes it.
"""


def control_socket(source: Source) -> Path:
    """Keyed on the source so --stop finds it and two clusters get two tunnels.

    Kept short: a unix socket path is capped near 104 bytes, and ssh fails
    obscurely past it.
    """
    key = source.spec if source.host else str(Path(source.path).resolve())
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return Path.home() / ".podbench" / f"tun-{digest}.sock"


def _control(sock: Path, command: str) -> bool:
    argv = ["ssh", "-S", str(sock), "-O", command, "placeholder"]
    return subprocess.run(argv, capture_output=True, check=False).returncode == 0


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def choose_port(sock: Path) -> int:
    """6443, unless something other than our own tunnel already holds it."""
    if port_free(6443) or _control(sock, "check"):
        return 6443
    for port in range(6444, 6500):
        if port_free(port):
            return port
    raise AccessError("no free port in 6443-6499")


def _usage(out: Path, api: Api, source: Source) -> None:
    ns = f" -n {api.namespace}" if api.namespace else ""
    print()
    heading("use it with:")
    print(f"      export KUBECONFIG={out}")
    print(f"      podbench doctor{ns}")
    print(f"      podbench attach <pod>{ns} --context {api.context}")
    print()
    print("    Generated SSH aliases pin this kubeconfig path and context, so keep")
    print("    the file and the tunnel available for reconnections.")
    print()
    print("    close the tunnel with:")
    print(f"      podbench tunnel {source.spec} --stop")


def _verify(out: Path, api: Api, ssh_host: str, source: Source) -> bool:
    """Through the new kubeconfig only: reaching the cluster any other way
    proves nothing about this file."""
    print()
    result = kubectl("--request-timeout=15s", "version", "-o", "json", kubeconfig=out)
    try:
        version = json.loads(result.stdout)["serverVersion"]["gitVersion"]
    except (KeyError, TypeError, ValueError):
        mark("fail", "API server not reachable through the tunnel")
        print(
            f"\n  {ssh_host} may not reach {api.host}:{api.port}, or the token in\n"
            f"  {source.spec} may have expired. Ask the far end directly:\n"
            f"    ssh {ssh_host} 'exec 3<>/dev/tcp/{api.host}/{api.port}'"
            " && echo reachable",
            file=sys.stderr,
        )
        return False
    mark("ok", "API server reached through the tunnel", version)
    who = kubectl(
        "--request-timeout=15s",
        "auth",
        "whoami",
        "-o",
        "jsonpath={.status.userInfo.username}",
        kubeconfig=out,
    )
    if who.returncode == 0 and who.stdout.strip():
        mark("ok", "authenticated as", who.stdout.strip())
    else:
        mark("skip", "auth whoami unavailable (not fatal)")
    return True


def tunnel(
    spec: str,
    *,
    ssh_host: str | None,
    local_port: int | None,
    out: Path | None,
    config_only: bool,
    stop: bool,
) -> int:
    require("kubectl", "ssh")
    source = parse_source(spec)
    sock = control_socket(source)
    if stop:
        if _control(sock, "check"):
            _control(sock, "exit")
            heading(f"closed the tunnel for {source.stem}")
        else:
            heading(f"no tunnel running for {source.stem}")
        return 0

    config = fetch(source)
    try:
        if source.host and not ssh_host:
            ssh_host = source.host
            print(f"    --ssh-host defaults to {ssh_host}, the host it came from")
        api = read_api(config, source.spec)
        if not config_only and not ssh_host:
            raise AccessError(
                "--ssh-host is required: name a machine your ssh reaches that can"
                f" itself reach {api.host}:{api.port}"
            )
        port = local_port or choose_port(sock)
        if out is None:
            folder = Path(source.path).parent if not source.host else Path(".")
            out = folder / f"{source.stem}-tunnel.kubeconfig"
        out = out.absolute()
        write_copy(config, out, api, port)
    finally:
        if source.host:
            config.unlink(missing_ok=True)

    forward = f"127.0.0.1:{port}:{api.host}:{api.port}"
    if config_only:
        print()
        heading("--config-only: start the forward yourself with")
        print(f"    ssh -N -L {forward} {ssh_host or '<host>'}")
        _usage(out, api, source)
        return 0

    assert ssh_host
    sock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _control(sock, "check"):
        heading(f"reusing the tunnel already running for {source.stem}")
    else:
        # ExitOnForwardFailure, or ssh backgrounds happily on a refused forward
        # and the first kubectl call fails with an error that never says ssh.
        started = subprocess.run(
            ["ssh", "-fNT", "-M", "-S", str(sock), "-o", "ExitOnForwardFailure=yes"]
            + ["-L", forward, ssh_host],
            check=False,
        )
        if started.returncode != 0:
            raise AccessError(f"ssh could not open the tunnel through {ssh_host}")
        heading(f"tunnel up through {ssh_host}")
    if not _verify(out, api, ssh_host, source):
        return 1
    _usage(out, api, source)
    return 0


def _build_app() -> typer.Typer:
    app = new_app()

    @app.command(help=HELP)
    def tunnel_command(
        kubeconfig: Annotated[str, typer.Argument(metavar="[USER@HOST:]KUBECONFIG")],
        ssh_host: Annotated[
            str | None, typer.Option("--ssh-host", help="where the tunnel exits")
        ] = None,
        local_port: Annotated[
            int | None, typer.Option("--local-port", help="default 6443 or next free")
        ] = None,
        out: Annotated[
            Path | None, typer.Option("--out", help="kubeconfig to write")
        ] = None,
        config_only: Annotated[
            bool,
            typer.Option("--config-only", help="write it; print the ssh command"),
        ] = False,
        stop: Annotated[
            bool, typer.Option("--stop", help="close this kubeconfig's tunnel")
        ] = False,
    ) -> None:
        try:
            code = tunnel(
                kubeconfig,
                ssh_host=ssh_host,
                local_port=local_port,
                out=out,
                config_only=config_only,
                stop=stop,
            )
        except AccessError as error:
            code = fail(str(error))
        raise typer.Exit(code)

    return app


def main(args: Sequence[str] | None = None) -> int:
    argv = list(args) if args is not None else None
    if argv and argv[0] == "tunnel":
        argv.pop(0)
    return run(_build_app(), argv, prog="podbench tunnel")


if __name__ == "__main__":
    sys.exit(main())
