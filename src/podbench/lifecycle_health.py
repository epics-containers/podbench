"""Translate configured startup health checks without suppressing readiness."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from .model import HOTFIX_HOLD_PATH


def health_command(container: Mapping[str, Any]) -> str:
    probe = container.get("readinessProbe") or container.get("livenessProbe") or {}
    if command := probe.get("exec", {}).get("command"):
        return shlex.join(
            [part.replace(HOTFIX_HOLD_PATH, "/proc/self/no-hold") for part in command]
        )
    if http := probe.get("httpGet"):
        port = _port(container, http["port"])
        host = http.get("host") or "127.0.0.1"
        scheme = http.get("scheme", "HTTP").lower()
        url = f"{scheme}://{host}:{port}{http.get('path', '/')}"
        headers = [
            word
            for item in http.get("httpHeaders", [])
            for word in ("-H", f"{item['name']}: {item['value']}")
        ]
        return shlex.join(
            [
                "curl",
                "--fail",
                "--silent",
                *(["--insecure"] if scheme == "https" else []),
                "--max-time",
                str(probe.get("timeoutSeconds", 1)),
                *headers,
                url,
            ]
        )
    if tcp := probe.get("tcpSocket"):
        host = tcp.get("host") or "127.0.0.1"
        return "exec 3<> " + shlex.quote(
            f"/dev/tcp/{host}/{_port(container, tcp['port'])}"
        )
    if grpc := probe.get("grpc"):
        return shlex.join(
            [
                "grpc_health_probe",
                f"-addr=127.0.0.1:{grpc['port']}",
                f"-service={grpc.get('service', '')}",
            ]
        )
    return "true"


def _port(container: Mapping[str, Any], value: str | int) -> int:
    if isinstance(value, int):
        return value
    return next(
        p["containerPort"] for p in container.get("ports", []) if p.get("name") == value
    )
