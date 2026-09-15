"""Keep generated workload wiring compact and upgradeable."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from podbench.hotfix_enable import enable
from podbench.hotfix_values import entrypoint, ioc_entrypoint, value_blocks
from podbench.kubectl import Kubectl
from podbench.lifecycle_supervisor import supervisor


@pytest.fixture
def pod():
    return {
        "metadata": {"name": "test-0"},
        "spec": {
            "securityContext": {"runAsUser": 1000, "runAsGroup": 1000},
            "containers": [
                {
                    "name": "app",
                    "command": ["bash", "-c"],
                    "args": ["/opt/app/start.sh"],
                    "livenessProbe": {"exec": {"command": ["/opt/app/health"]}},
                }
            ],
        },
    }


def test_generic_values_mount_runtime_and_keep_arguments_short(pod):
    _, lines = value_blocks(pod, "test")
    invocation = json.loads(lines[lines.index("args:") + 1].removeprefix("  - "))
    assert len(invocation) < 160
    assert "source /podbench/runtime/podbench.sh; podbench_supervise" in invocation
    assert "name: test-podbench-runtime" in "\n".join(lines)
    assert "mountPath: /podbench/runtime" in "\n".join(lines)
    assert "while :; do" not in "\n".join(lines)
    assert (
        entrypoint(
            {
                "command": ["bash", "-c"],
                "args": [
                    invocation,
                    "podbench-supervisor",
                    "exec bash -c /opt/app/start.sh",
                ],
            }
        )
        == "bash -c /opt/app/start.sh"
    )


def test_compact_script_fallback_survives_regeneration(pod):
    original = "bash -c '/opt/app/start.sh --name \\\"a b\\\"'"
    launch = f"podbench_script /podbench/app/start.sh {original}"
    assert (
        entrypoint(
            {
                "command": ["bash", "-c"],
                "args": [supervisor(), "podbench-supervisor", launch],
            }
        )
        == launch
    )
    pod["spec"]["containers"][0].update(
        command=["bash", "-c"], args=[supervisor(), "podbench-supervisor", launch]
    )
    assert ioc_entrypoint(pod, "app") == launch
    _, lines = value_blocks(pod, "test")
    assert launch in "\n".join(lines)


@pytest.mark.parametrize("prefix", ["ioc-instance", "blueapi", "application"])
def test_enable_is_idempotent_for_chart_adapters(tmp_path, pod, prefix):
    (tmp_path / "Chart.yaml").write_text(
        f"dependencies:\n  - name: {prefix}\n    version: 1.0.0\n"
    )
    values = tmp_path / "values.yaml"
    values.write_text(f"{prefix}:\n  image: example/app:1\n")
    kube = Mock(spec=Kubectl)
    kube.get_pod.return_value = pod
    options = {
        "app": "test",
        "from_pod": "test-0",
        "container": "app",
        "values_prefix": prefix,
    }
    enable(kube, tmp_path, **options)
    before = {path: path.read_text() for path in tmp_path.rglob("*.yaml")}
    enable(kube, tmp_path, **options)
    assert before == {path: path.read_text() for path in tmp_path.rglob("*.yaml")}
    assert "name: test-podbench-runtime" in values.read_text()
    assert "while :; do" not in "\n".join(before.values())
    if prefix == "blueapi":
        wrapper = tmp_path / "templates/podbench-wrapper.yaml"
        assert "podbench_python /app/.venv/bin/python -m blueapi" in wrapper.read_text()
        assert "source /podbench/runtime/podbench.sh" in wrapper.read_text()
    elif prefix == "ioc-instance":
        assert "podbench_script /podbench/app/ioc/start.sh" in values.read_text()
