"""Ownership boundaries preserve service YAML while generated wiring evolves."""

from __future__ import annotations

import pytest

from podbench.hotfix_core import HotfixError
from podbench.hotfix_enable import _dependency, _prefix, _values
from podbench.hotfix_yaml import load

CLAIM = ["podbench-hotfix-claim:", "  enabled: true", "  size: 10Gi"]
WORKLOAD = [
    "volumes:",
    "  - name: podbench-app",
    "    persistentVolumeClaim:",
    "      claimName: old-podbench-project",
    "volumeMounts:",
    "  - name: podbench-app",
    "    mountPath: /podbench/app",
    "command: [bash, -c]",
    "args: [example]",
    "podSecurityContext:",
    "  fsGroup: 1000",
]


def edit(text, workload=None, prefix="application"):
    return _values(text, CLAIM, WORKLOAD if workload is None else workload, prefix)[0]


@pytest.mark.parametrize(
    "lists",
    [
        "volumes: []\nvolumeMounts: []\n",
        "volumes:\n  - name: 'service-data' # keep volume\n    emptyDir: {}\n"
        "volumeMounts:\n  - name: service-data\n    mountPath: /data\n",
        'volumes: [{name: "service-data", emptyDir: {}}]\nvolumeMounts: []\n',
    ],
)
@pytest.mark.parametrize("prefix", [None, "application"])
def test_shared_lists_update_only_marked_entries(lists, prefix):
    current = "# service header\n" + lists
    if prefix:
        current = (
            prefix
            + ":\n"
            + "".join("  " + line + "\n" for line in current.splitlines())
        )
    first = edit(current, prefix=prefix)
    assert "# podbench: begin volumes\n" in first
    assert "# podbench: end volumes\n" in first
    replacement = [
        line.replace("old-podbench-project", "new-podbench-project")
        for line in WORKLOAD
    ]
    second = edit(first, replacement, prefix)
    assert "new-podbench-project" in second and "old-podbench-project" not in second
    before, after = load(current), load(second)
    if prefix:
        before, after = before[prefix], after[prefix]
    for key in ("volumes", "volumeMounts"):
        assert after[key][:-1] == before[key]
    assert "# service header" in second
    if "# keep volume" in current:
        assert "# keep volume" in second and "'service-data'" in second
    assert edit(second, replacement, prefix) == second


def test_service_probes_and_security_fields_survive():
    current = (
        "application:\n"
        "  readinessProbe: {exec: {command: [ready]}}\n"
        "  startupProbe: {exec: {command: [started]}}\n"
        "  podSecurityContext: {runAsUser: 1000, runAsNonRoot: true}\n"
    )
    first = edit(current)
    second = edit(
        first, [line.replace("fsGroup: 1000", "fsGroup: 2000") for line in WORKLOAD]
    )
    before, after = load(current)["application"], load(second)["application"]
    for key in ("readinessProbe", "startupProbe"):
        assert before[key] == after[key]
    assert after["podSecurityContext"] == {
        "runAsUser": 1000,
        "runAsNonRoot": True,
        "fsGroup": 2000,
    }
    assert (
        edit(
            second,
            [line.replace("fsGroup: 1000", "fsGroup: 2000") for line in WORKLOAD],
        )
        == second
    )


def test_comments_after_owned_entries_survive_replacement():
    first = edit("application:\n  image: 'my:tag' # image comment\n")
    first = first.replace(
        "  # podbench: begin command",
        "  # keep this service comment\n  # podbench: begin command",
    )
    second = edit(first)
    assert "# keep this service comment" in second
    assert "image: 'my:tag' # image comment" in second
    assert edit(second) == second


def test_removed_owned_list_does_not_remove_service_entries():
    first = edit("application:\n  volumes:\n    - name: service\n      emptyDir: {}\n")
    second = edit(first, ["podSecurityContext:", "  fsGroup: 1000"])
    assert load(second)["application"]["volumes"] == [
        {"name": "service", "emptyDir": {}}
    ]
    assert "podbench-app" not in second


@pytest.mark.parametrize(
    "entry",
    [
        "{name: podbench-app, emptyDir: {}}",
        "{name: podbench-app, "
        "persistentVolumeClaim: {claimName: old-podbench-project}}",
    ],
)
def test_unmarked_collisions_are_not_adopted(entry):
    with pytest.raises(HotfixError, match="unmarked volumes entry 'podbench-app'"):
        edit(f"application:\n  volumes: [{entry}]\n")


def test_obsolete_wiring_requires_manual_cleanup():
    current = (
        "application:\n  volumes:\n"
        "    - name: service-data\n      emptyDir: {}\n"
        "    - name: podbench-app\n      persistentVolumeClaim:\n"
        "        claimName: old-podbench-project\n"
        "  # keep this service comment\n"
        "  # podbench-hotfix: begin podbench 0.21.0\n"
        "  command: [bash, -c]\n  args: [old, podbench-supervisor, 'exec app']\n"
        "  # podbench-hotfix: end\n"
    )
    with pytest.raises(HotfixError, match="unmarked volumes entry"):
        edit(current)


@pytest.mark.parametrize(
    "marker",
    [
        "# podbench: begin volumes\n",
        "# podbench: end volumes\n",
        "# podbench: begin volumes\n# podbench: end args\n",
    ],
)
def test_malformed_markers_are_rejected(marker):
    with pytest.raises(HotfixError, match="marker"):
        edit(
            "application:\n"
            + "".join("  " + line + "\n" for line in marker.splitlines())
        )


def test_marker_text_in_scalar_is_not_ownership():
    current = (
        "application:\n  note: |\n"
        "    # podbench: begin volumes\n    hello\n    # podbench: end volumes\n"
    )
    result = edit(current)
    assert load(current)["application"]["note"] == load(result)["application"]["note"]
    assert edit(result) == result


def test_other_workload_markers_remain_untouched():
    first = edit("application:\n  image: one\nother:\n  image: two\n")
    both = edit(first, prefix="other")
    assert both.count("# podbench: begin volumes") == 2
    assert load(first)["application"] == load(both)["application"]
    assert edit(both, prefix="other") == both


def test_dependency_handles_quoted_names_inline_lists_and_missing_versions():
    current = (
        'dependencies: [{name: "podbench-hotfix-claim", '
        'repository: "oci://x"}] # keep\n'
    )
    result, changed = _dependency(current)
    assert changed and "# keep" in result
    assert load(result)["dependencies"][0]["version"]
    assert _dependency(result) == (result, False)
    assert _prefix('"blueapi": {}\n', None) == "blueapi"


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(HotfixError, match="duplicate"):
        edit("application:\n  volumes: []\n  volumes: []\n")


@pytest.mark.parametrize("application", ["*defaults", "{<<: *defaults}"])
def test_aliases_do_not_change_other_workloads(application):
    current = (
        "defaults: &defaults\n  volumes: []\n"
        "  podSecurityContext: {runAsUser: 1000}\n"
        f"application: {application}\nother: *defaults\n"
    )
    result = edit(current)
    before, after = load(current), load(result)
    assert before["defaults"] == after["defaults"] == after["other"]
    assert after["application"]["podSecurityContext"]["fsGroup"] == 1000
    assert edit(result) == result


def test_service_entry_after_generated_region_survives():
    first = edit("application: {}\n")
    current = first.replace(
        "    # podbench: end volumes\n",
        "    # podbench: end volumes\n"
        "    # keep later volume\n    - name: later\n      emptyDir: {}\n",
    )
    result = edit(current)
    assert load(result)["application"]["volumes"][0]["name"] == "later"
    assert "# keep later volume" in result
    assert edit(result) == result


def test_marker_must_not_cut_a_list_item_in_half():
    current = (
        "application:\n  volumes:\n"
        "    # podbench: begin volumes\n    - name: podbench-app\n"
        "    # podbench: end volumes\n      emptyDir: {}\n"
    )
    with pytest.raises(HotfixError, match="complete volumes"):
        edit(current)


def test_marker_must_not_include_an_unrelated_field():
    current = (
        "application:\n  # podbench: begin command\n"
        "  serviceSetting: keep\n  command: [old]\n  # podbench: end command\n"
    )
    with pytest.raises(HotfixError, match="complete command"):
        edit(current)


def test_flow_document_can_be_edited():
    result = edit('{application: {volumes: []}, other: {image: "keep"}}\n')
    assert load(result)["other"] == {"image": "keep"}
    assert edit(result) == result


def test_dropped_generated_fields_do_not_leave_null_parents():
    # A null parent would delete the chart's own default (e.g. its probes).
    probes = [
        "readinessProbe:",
        "  httpGet:",
        "  exec:",
        "    command: [old-ready]",
        "startupProbe:",
        "  exec:",
        "    command: [old-start]",
    ]
    old = edit("application:\n  image: keep\n", [*WORKLOAD, *probes])
    service = old.replace(
        "  startupProbe:\n", "  startupProbe:\n    failureThreshold: 18\n"
    )
    new = edit(service)
    application = load(new)["application"]
    assert "readinessProbe" not in application
    assert application["startupProbe"] == {"failureThreshold": 18}
    assert application["image"] == "keep"
    assert edit(new) == new
