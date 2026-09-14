"""Regenerating hotfix wiring must replace earlier output, not nest or refuse it."""

from __future__ import annotations

import pytest

from podbench import __version__
from podbench.hotfix_core import HotfixError
from podbench.hotfix_enable import _dependency, _values
from podbench.hotfix_values import (
    MARKER_BEGIN,
    MARKER_END,
    entrypoint,
    supervised_launch,
)
from podbench.lifecycle_health import unwrap_probe

CLAIM = ["podbench-hotfix-claim:", "  enabled: true", "  size: 10Gi"]
WORKLOAD_V1 = [
    "command: [bash, -c]",
    "args:",
    "  - old",
    "podSecurityContext:",
    "  fsGroup: 1",
]
WORKLOAD_V2 = [
    "command: [bash, -c]",
    "args:",
    "  - new",
    "podSecurityContext:",
    "  fsGroup: 2",
]

LEGACY = """\
ioc-instance:
  image: ghcr.io/example/ioc:1

  volumes:
    - name: podbench-app
      persistentVolumeClaim:
        claimName: x-podbench-project
  volumeMounts:
    - name: podbench-app
      mountPath: /podbench/app
  command: [bash, -c]
  args:
    - |
      while :; do
        setsid bash -c "$1" &
      done
    - podbench-supervisor
    - exec bash /epics/ioc/start.sh
  livenessProbe:
    exec:
      command:
        - bash
        - -c
        - "[ -e /tmp/podbench-hold ] && exit 0; exec bash /epics/ioc/liveness.sh"
    periodSeconds: 30
  podSecurityContext:
    fsGroup: 36261

podbench-hotfix-claim:
  enabled: true
  size: 10Gi
"""


def test_fresh_insert_adds_markers_and_claim() -> None:
    text, changed = _values(
        "ioc-instance:\n  image: x\n", CLAIM, WORKLOAD_V1, "ioc-instance"
    )
    assert changed
    assert f"  {MARKER_BEGIN} podbench {__version__}" in text
    assert f"  {MARKER_END}" in text
    assert text.endswith("podbench-hotfix-claim:\n  enabled: true\n  size: 10Gi\n")
    assert (
        text.index("  image: x")
        < text.index(MARKER_BEGIN)
        < text.index("podbench-hotfix-claim:")
    )


def test_rerun_replaces_marked_block_and_is_idempotent() -> None:
    first, _ = _values(
        "ioc-instance:\n  image: x\n", CLAIM, WORKLOAD_V1, "ioc-instance"
    )
    second, changed = _values(first, CLAIM, WORKLOAD_V2, "ioc-instance")
    assert changed
    assert "  - new" in second and "  - old" not in second
    assert (
        second.count(MARKER_BEGIN) == 1 and second.count("podbench-hotfix-claim:") == 1
    )
    third, changed = _values(second, CLAIM, WORKLOAD_V2, "ioc-instance")
    assert not changed and third == second


def test_legacy_unmarked_block_is_upgraded_in_place() -> None:
    text, changed = _values(LEGACY, CLAIM, WORKLOAD_V2, "ioc-instance")
    assert changed
    assert "while :; do" not in text, "the legacy supervisor loop must be replaced"
    assert "  image: ghcr.io/example/ioc:1" in text
    assert text.count("podbench-hotfix-claim:") == 1
    assert text.index(MARKER_BEGIN) < text.index("  - new") < text.index(MARKER_END)
    assert text.index(MARKER_END) < text.index("podbench-hotfix-claim:")


def test_user_owned_keys_without_claim_are_refused() -> None:
    with pytest.raises(HotfixError, match="already sets command, args"):
        _values(
            "ioc-instance:\n  command: [x]\n  args: [y]\n",
            CLAIM,
            WORKLOAD_V2,
            "ioc-instance",
        )


def test_top_level_layout_replaces_between_start_and_claim() -> None:
    first, _ = _values("image: x\n", CLAIM, WORKLOAD_V1, None)
    second, changed = _values(first, CLAIM, WORKLOAD_V2, None)
    assert changed and "  - old" not in second
    assert second.startswith("image: x\n")
    assert second.count("podbench-hotfix-claim:") == 1


def test_dependency_pins_existing_entry_to_current_version() -> None:
    chart = (
        "dependencies:\n  - name: podbench-hotfix-claim\n"
        '    version: "0.1.0"\n    repository: "oci://x"\n'
    )
    text, changed = _dependency(chart)
    assert changed and '"0.1.0"' not in text
    again, changed = _dependency(text)
    assert not changed and again == text


def test_entrypoint_unwraps_both_supervisor_generations() -> None:
    legacy = [
        "bash",
        "-c",
        "while :; do ...; done",
        "podbench-supervisor",
        "if [[ -f /podbench/app/ioc/start.sh ]]; then\n"
        "  exec bash /podbench/app/ioc/start.sh\n"
        "else\n  exec bash -c /epics/ioc/start.sh\nfi\n",
    ]
    assert supervised_launch(legacy) == "bash -c /epics/ioc/start.sh"
    current = [
        "bash",
        "-c",
        "startup=...",
        "podbench-supervisor",
        "exec python -m app serve",
    ]
    assert supervised_launch(current) == "python -m app serve"
    assert supervised_launch(["python", "-m", "app"]) is None
    container = {"command": ["bash", "-c"], "args": current[2:]}
    assert entrypoint(container) == "python -m app serve"


def test_unwrap_probe_recovers_original_once() -> None:
    wrapped = [
        "bash",
        "-c",
        "[ -e /tmp/podbench-hold ] && exit 0; exec bash /epics/ioc/liveness.sh",
    ]
    assert unwrap_probe(wrapped) == ["bash", "/epics/ioc/liveness.sh"]
    assert unwrap_probe(["bash", "/epics/ioc/liveness.sh"]) == [
        "bash",
        "/epics/ioc/liveness.sh",
    ]


def test_legacy_upgrade_keeps_user_keys_between_generated_blocks() -> None:
    current = (
        "blueapi:\n  volumes:\n    - name: podbench-app\n"
        "  ingress:\n    enabled: true\n"
        "  debug:\n    enabled: false\n"
        "\npodbench-hotfix-claim:\n  enabled: true\n  size: 10Gi\n"
    )
    text, changed = _values(current, CLAIM, WORKLOAD_V2, "blueapi")
    assert changed
    assert "  ingress:\n    enabled: true\n" in text
    assert "  debug:" not in text.split(MARKER_END)[1]
    assert text.count("podbench-app") == 0 or "  - new" in text
