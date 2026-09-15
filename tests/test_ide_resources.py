"""Exercise resize policy through a stateful, resource-version-aware API double."""

import json
from copy import deepcopy
from fractions import Fraction
from types import SimpleNamespace

import pytest

from podbench import ide_resources as resources
from podbench.kubectl import KubectlError


@pytest.fixture
def api(monkeypatch):
    budget = {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    pod = {
        "metadata": {
            "uid": "original",
            "resourceVersion": "1",
            "ownerReferences": [{"controller": True, "kind": "ReplicaSet"}],
        },
        "spec": {"containers": [{"name": "app", "resources": budget}]},
        "status": {"qosClass": "Burstable", "containerStatuses": []},
    }
    state = SimpleNamespace(
        pod=pod, patches=[], ranges=[], polls=[], after_baseline=None
    )
    clock = [0]
    monkeypatch.setattr(resources.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        resources.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    def get_pod(name):
        if state.polls and any("spec" in p for p in state.patches):
            return deepcopy(state.polls.pop(0))
        return deepcopy(state.pod)

    def run(*args):
        if args[:2] == ("get", "limitrange"):
            return SimpleNamespace(stdout=json.dumps({"items": state.ranges}))
        assert args[:3] == ("patch", "pod", "example")
        patch = json.loads(args[args.index("-p") + 1])
        assert (
            patch["metadata"]["resourceVersion"]
            == state.pod["metadata"]["resourceVersion"]
        )
        state.patches.append(patch)
        state.pod["metadata"]["resourceVersion"] = str(len(state.patches) + 1)
        if "spec" in patch:
            assert "--subresource=resize" in args
            state.pod["spec"]["containers"][0]["resources"] = patch["spec"][
                "containers"
            ][0]["resources"]
            state.pod["status"]["containerStatuses"] = deepcopy(
                state.pod["spec"]["containers"]
            )
        else:
            state.pod["metadata"]["annotations"] = patch["metadata"]["annotations"]
            if state.after_baseline:
                state.after_baseline()
        return SimpleNamespace(stdout="{}")

    state.get_pod, state.run = get_pod, run
    return state


def reserve(api):
    resources.ensure_headroom(api, "example", "app", timeout=3)


def budget(api):
    return api.pod["spec"]["containers"][0]["resources"]


def test_reconnect_does_not_accumulate_headroom(api):
    original = deepcopy(budget(api))
    reserve(api)
    expected = {
        "requests": original["requests"],
        "limits": {"cpu": "3000m", "memory": "5Gi"},
    }
    assert budget(api) == expected
    assert json.loads(api.pod["metadata"]["annotations"][resources.BASELINE]) == {
        "app": original
    }
    reserve(api)
    assert budget(api) == expected and len(api.patches) == 2


@pytest.mark.parametrize("resource", ["cpu", "memory"])
def test_unlimited_resources_stay_unlimited(api, resource):
    del budget(api)["limits"][resource]
    reserve(api)
    assert resource not in budget(api)["limits"]
    reserve(api)
    assert len(api.patches) == 2


def test_guaranteed_requests_remain_equal_to_limits(api):
    api.pod["status"]["qosClass"] = "Guaranteed"
    budget(api)["requests"] = dict(budget(api)["limits"])
    reserve(api)
    assert (
        budget(api)["requests"]
        == budget(api)["limits"]
        == {"cpu": "3000m", "memory": "5Gi"}
    )


def test_sidecar_and_unrelated_resources_are_preserved(api):
    sidecar = {"name": "sidecar", "resources": {"requests": {"memory": "64Mi"}}}
    api.pod["spec"]["containers"].append(deepcopy(sidecar))
    budget(api)["limits"]["ephemeral-storage"] = "2Gi"
    reserve(api)
    assert api.pod["spec"]["containers"][1] == sidecar
    assert budget(api)["limits"]["ephemeral-storage"] == "2Gi"
    assert budget(api)["requests"] != budget(api)["limits"]


@pytest.mark.parametrize(
    "reason", ["LimitRange maximum exceeded", "resourceVersion conflict"]
)
def test_api_resize_rejection_is_propagated(api, reason):
    original = api.run

    def reject(*args):
        if "--subresource=resize" in args:
            raise KubectlError(reason)
        return original(*args)

    api.run = reject
    with pytest.raises(KubectlError, match=reason):
        reserve(api)
    assert len(api.patches) == 1


def test_limitrange_ratio_raises_only_required_requests(api):
    api.ranges = [
        {
            "spec": {
                "limits": [
                    {
                        "type": "Container",
                        "maxLimitRequestRatio": {"cpu": "3", "memory": "2"},
                    }
                ]
            }
        }
    ]
    reserve(api)
    assert budget(api)["requests"] == {"cpu": "1000m", "memory": "2560Mi"}
    reserve(api)
    assert len(api.patches) == 2


@pytest.mark.parametrize("kind", ["PodResizePending", "PodResizeInProgress"])
def test_waits_for_conditions_and_actual_allocation(api, kind):
    pending = deepcopy(api.pod)
    pending["status"]["conditions"] = [{"type": kind, "status": "True"}]
    pending["status"]["containerStatuses"] = [
        {
            "name": "app",
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "3", "memory": "5Gi"},
            },
        }
    ]
    unallocated = deepcopy(api.pod)
    api.polls = [pending, unallocated]
    reserve(api)
    assert not api.polls


@pytest.mark.parametrize("failure", ["infeasible", "replaced", "timeout"])
def test_allocation_failure_is_reported(api, failure):
    live = deepcopy(api.pod)
    message = {
        "infeasible": "infeasible",
        "replaced": "replaced",
        "timeout": "not allocated",
    }[failure]
    if failure == "infeasible":
        live["status"]["conditions"] = [
            {"type": "PodResizePending", "status": "True", "reason": "Infeasible"}
        ]
    elif failure == "replaced":
        live["metadata"]["uid"] = "replacement"
    api.polls = [live] * 3
    with pytest.raises(KubectlError, match=message):
        reserve(api)


def test_replacement_after_baseline_is_not_resized(api):
    api.after_baseline = lambda: api.pod["metadata"].update(uid="replacement")
    with pytest.raises(KubectlError, match="replaced"):
        reserve(api)
    assert not any("spec" in p for p in api.patches)


def test_concurrent_budget_increase_is_preserved(api):
    api.after_baseline = lambda: budget(api)["limits"].update(cpu="8")
    with pytest.raises(KubectlError, match="specification changed"):
        reserve(api)
    assert resources.quantity(budget(api)["limits"]["cpu"]) == 8
    assert not any("spec" in p for p in api.patches)


@pytest.mark.parametrize(
    "unsafe", ["unowned", "BestEffort", "pod-budget", "restart", "claims", "argo"]
)
def test_unsafe_resize_rejected_before_writes(api, unsafe):
    if unsafe == "unowned":
        api.pod["metadata"]["ownerReferences"] = []
    elif unsafe == "BestEffort":
        api.pod["status"]["qosClass"] = unsafe
    elif unsafe == "pod-budget":
        api.pod["spec"]["resources"] = {"limits": {"cpu": "1"}}
    elif unsafe == "restart":
        api.pod["spec"]["containers"][0]["resizePolicy"] = [
            {"restartPolicy": "RestartContainer"}
        ]
    elif unsafe == "claims":
        budget(api)["claims"] = [{"name": "device"}]
    else:
        api.pod["metadata"]["annotations"] = {
            "argocd.argoproj.io/tracking-id": "app:/Pod:ns/example"
        }
    with pytest.raises(KubectlError):
        reserve(api)
    assert not api.patches


@pytest.mark.parametrize(
    "value,expected",
    [
        ("100m", Fraction(1, 10)),
        ("1Gi", 1024**3),
        ("1.5G", 1500000000),
        ("1e3", 1000),
        ("1u", Fraction(1, 10**6)),
    ],
)
def test_quantities(value, expected):
    assert resources.quantity(value) == expected
