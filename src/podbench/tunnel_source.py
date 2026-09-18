"""Read the kubeconfig ``podbench tunnel`` starts from, and write its copy."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .access import AccessError, heading, kubectl, kubectl_out, note


@dataclass(frozen=True)
class Source:
    spec: str  # as typed
    host: str  # "" for a local file
    path: str  # without any host

    @property
    def stem(self) -> str:
        return Path(self.path).name.removesuffix(".kubeconfig")


def parse_source(spec: str) -> Source:
    """scp's rule: a colon before the first slash names a host.

    That makes ``box:k8s/a.kubeconfig`` remote and ``./k8s/a:b.kubeconfig`` local.
    """
    head, colon, tail = spec.partition(":")
    if colon and head and "/" not in head:
        if not tail:
            raise AccessError(f"no path after '{head}:'")
        return Source(spec, head, tail)
    return Source(spec, "", spec)


def fetch(source: Source) -> Path:
    """A local path to read the source from; the caller removes a remote copy.

    ``cat`` over ssh rather than scp: no sftp subsystem needed, and a missing file
    is reported in the far end's own words. It holds a live bearer token, so it
    lives in a 0600 temporary file and is never kept.
    """
    if not source.host:
        path = Path(source.path)
        if not os.access(path, os.R_OK):
            raise AccessError(f"cannot read kubeconfig: {source.spec}")
        return path
    fd, name = tempfile.mkstemp(prefix="podbench-kubeconfig.")
    with os.fdopen(fd, "wb") as handle:
        read = subprocess.run(
            ["ssh", source.host, f"cat -- {shlex.quote(source.path)}"],
            stdout=handle,
            check=False,
        )
    path = Path(name)
    if read.returncode != 0 or path.stat().st_size == 0:
        path.unlink()
        raise AccessError(f"could not read {source.spec}")
    heading(f"read {source.spec} over ssh (not kept here)")
    return path


@dataclass(frozen=True)
class Api:
    cluster: str
    context: str
    namespace: str
    server: str
    host: str
    port: int
    insecure: bool
    proxy: str


def split_server(server: str) -> tuple[str, int]:
    if not server.startswith("https://"):
        raise AccessError(f"server is {server!r}, and only https is tunnelled here")
    hostport = server.removeprefix("https://").split("/", 1)[0]
    if hostport.startswith("["):
        host, _, rest = hostport[1:].partition("]")
        return host, int(rest.removeprefix(":") or 443)
    host, colon, port = hostport.rpartition(":")
    return (host, int(port)) if colon else (hostport, 443)


def read_api(config: Path, spec: str) -> Api:
    """--minify reduces the file to its current context, so [0] is that one."""

    def field(path: str) -> str:
        return kubectl_out(
            "config", "view", "--minify", "-o", f"jsonpath={path}", kubeconfig=config
        )

    server = field("{.clusters[0].cluster.server}")
    if not server:
        raise AccessError(f"no server address in {spec}'s current context")
    host, port = split_server(server)
    return Api(
        cluster=field("{.clusters[0].name}"),
        context=kubectl_out("config", "current-context", kubeconfig=config).strip(),
        namespace=field("{.contexts[0].context.namespace}"),
        server=server,
        host=host,
        port=port,
        insecure=field("{.clusters[0].cluster['insecure-skip-tls-verify']}") == "true",
        proxy=field("{.clusters[0].cluster['proxy-url']}"),
    )


def write_copy(config: Path, out: Path, api: Api, local_port: int) -> None:
    """Change the address and nothing else.

    ``tls-server-name`` keeps the certificate valid against 127.0.0.1, which is
    what stops anyone reaching for insecure-skip-tls-verify. An insecure source
    has to restate it: set-cluster otherwise writes the flag's default over it.
    """
    raw = kubectl_out("config", "view", "--raw", "--minify", kubeconfig=config)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(raw)
    out.chmod(0o600)
    tls = (
        "--insecure-skip-tls-verify=true"
        if api.insecure
        else f"--tls-server-name={api.host}"
    )
    result = kubectl(
        "config",
        "set-cluster",
        api.cluster,
        f"--server=https://127.0.0.1:{local_port}",
        tls,
        kubeconfig=out,
    )
    if result.returncode != 0:
        raise AccessError(result.stderr.strip() or "kubectl config set-cluster failed")

    heading(f"{out}")
    note(
        f"context   {api.context}"
        + (f"  (namespace {api.namespace})" if api.namespace else "")
    )
    note(f"API       {api.host}:{api.port}  ->  127.0.0.1:{local_port}")
    note(
        "TLS       not verified (carried over from the source)"
        if api.insecure
        else f"TLS       verified as {api.host}, through the source's own CA"
    )
    if api.proxy:
        # Kept, because a SOCKS entry added on purpose looks like any other.
        print()
        print(f"  [warn] the source names proxy-url {api.proxy}, which now applies")
        print(f"         to 127.0.0.1:{local_port} and will not work. Drop it with:")
        print(
            f"           kubectl --kubeconfig {out} config set-cluster "
            f"{api.cluster} --proxy-url=''"
        )
