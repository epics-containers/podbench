"""Seat ephemeral-storage warnings and eviction explanations."""

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from podbench import ide_storage as storage
from podbench.kubectl import KubectlError

STORAGE = "ephemeral-storage"


def _pod(limit=None, volumes=(), sidecar_limit=None):
    resources = {"limits": {"ephemeral-storage": limit}} if limit else {}
    spec = {
        "containers": [{"name": "app", "resources": resources}],
        "volumes": list(volumes),
    }
    if sidecar_limit:
        spec["initContainers"] = [
            {"name": "setup", "resources": {"limits": {"ephemeral-storage": "9Gi"}}},
            {
                "name": "sidecar",
                "restartPolicy": "Always",
                "resources": {"limits": {"ephemeral-storage": sidecar_limit}},
            },
        ]
    return {"metadata": {"uid": "u1"}, "spec": spec}


def _empty_dir(size=None, medium=None):
    empty_dir = {k: v for k, v in (("sizeLimit", size), ("medium", medium)) if v}
    return {"name": "v", "emptyDir": empty_dir}


def _gib(n):
    return storage.quantity(f"{n}Gi")


def test_budget_sums_running_container_limits_and_disk_empty_dirs():
    pod = _pod(
        "2Gi",
        [_empty_dir("1Gi"), _empty_dir("4Gi", medium="Memory"), {"name": "pvc"}],
        sidecar_limit="1Gi",
    )
    # As the kubelet's PodLimits: max(app + sidecar = 3Gi, one-shot init 9Gi).
    assert storage.storage_budget(pod) == (_gib(9), _gib(1), False)


def test_budget_one_shot_init_below_running_total_does_not_count():
    pod = _pod("2Gi", sidecar_limit="1Gi")
    pod["spec"]["initContainers"][0]["resources"]["limits"][STORAGE] = "1Gi"
    assert storage.storage_budget(pod) == (_gib(3), 0, False)


def test_budget_without_limits_is_not_evictable():
    assert storage.storage_budget(_pod(volumes=[_empty_dir()])) == (
        None,
        0,
        True,
    )


def _warned(capsys, pod):
    storage.warn_if_seat_may_not_fit(pod, "app")
    return capsys.readouterr().out


@pytest.mark.parametrize(
    "pod",
    [
        _pod(),  # no limit: the kubelet never evicts for total usage
        _pod("4Gi", [_empty_dir("2Gi")]),  # 2Gi left for a ~1.5Gi seat
    ],
)
def test_no_warning_when_the_seat_fits(capsys, pod):
    assert _warned(capsys, pod) == ""


def test_warns_when_empty_dirs_leave_too_little(capsys):
    out = _warned(capsys, _pod("2Gi", [_empty_dir("1Gi")]))
    assert "ephemeral-storage limit is 2Gi" in out
    assert "sizeLimits of 1Gi" in out
    assert "raise app's limits.ephemeral-storage" in out


def test_warns_when_an_empty_dir_is_unbounded(capsys):
    out = _warned(capsys, _pod("8Gi", [_empty_dir()]))
    assert "emptyDir volumes without a sizeLimit" in out


def test_warns_for_the_issue_269_shape(capsys):
    # BlueAPI: venv emptyDir sizeLimit 5Gi plus 500Mi and 5Mi under a 2Gi limit.
    volumes = [_empty_dir("5Gi"), _empty_dir("500Mi"), _empty_dir("5Mi")]
    out = _warned(capsys, _pod("2Gi", volumes))
    assert "emptyDir sizeLimits of" in out


def test_warning_without_empty_dirs_omits_them(capsys):
    out = _warned(capsys, _pod("1Gi"))
    assert "ephemeral-storage limit is 1Gi; a new VS Code seat" in out
    assert "emptyDir" not in out


class FakeKube:
    def __init__(self, pod=None, events=(), get_error=None):
        self.pod, self.events, self.calls = pod, list(events), []
        self.get_error = get_error

    def get_pod(self, name):
        if self.get_error:
            raise KubectlError(self.get_error)
        if self.pod is None:
            raise KubectlError(f'Error from server (NotFound): pods "{name}" not found')
        return self.pod

    def run(self, *args, check=True):
        self.calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"items": self.events}))


def _explain(kube: FakeKube) -> str | None:
    return storage.explain_lost_pod(cast(Any, kube), "p", "u1")


def test_same_running_pod_is_not_lost():
    kube = FakeKube({"metadata": {"uid": "u1"}, "status": {"phase": "Running"}})
    assert _explain(kube) is None
    assert kube.calls == []


def test_evicted_pod_still_present_reports_its_message():
    kube = FakeKube(
        {
            "metadata": {"uid": "u1"},
            "status": {"reason": "Evicted", "message": "usage exceeds 2Gi"},
        }
    )
    assert _explain(kube) == ("pod p was evicted: usage exceeds 2Gi")


@pytest.mark.parametrize("replacement", [None, {"metadata": {"uid": "u2"}}])
def test_replaced_pod_is_explained_from_eviction_events(replacement):
    kube = FakeKube(replacement, [{"message": "Pod ephemeral local storage ..."}])
    assert _explain(kube) == ("pod p was evicted: Pod ephemeral local storage ...")
    assert "involvedObject.uid=u1,reason=Evicted" in kube.calls[0]


def test_replaced_pod_without_events_says_it_was_replaced():
    kube = FakeKube({"metadata": {"uid": "u2"}})
    assert _explain(kube) == ("pod p was replaced (it is no longer uid u1)")


def test_deleted_pod_without_events_says_it_no_longer_exists():
    assert _explain(FakeKube()) == "pod p no longer exists (it was uid u1)"


def test_unreadable_pod_is_not_reported_as_lost():
    # An expired token must surface as itself, not as a phantom eviction.
    kube = FakeKube(get_error="error: You must be logged in (Unauthorized)")
    assert _explain(kube) is None
    assert kube.calls == []
