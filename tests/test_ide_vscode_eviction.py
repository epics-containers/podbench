"""open_vscode warns before a seat and explains a pod lost while adding it."""

from typing import Any, cast

import pytest

from podbench import ide_vscode
from podbench.kubectl import CommandResult, KubectlError

POD = {"metadata": {"uid": "u1"}, "spec": {"containers": [{"name": "app"}]}}


class FakeKube:
    def get_pod(self, name):
        return POD


@pytest.fixture
def calls(monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(ide_vscode.shutil, "which", lambda binary: binary)
    monkeypatch.setattr(ide_vscode, "read_public_key", lambda identity: "key")
    monkeypatch.setattr(ide_vscode, "include_is_active", lambda config_dir: True)
    monkeypatch.setattr(
        ide_vscode,
        "run_subprocess",
        lambda argv, **kw: CommandResult(tuple(argv), 1, "", ""),
    )
    monkeypatch.setattr(ide_vscode, "resolve_pod_name", lambda pod: pod)
    monkeypatch.setattr(ide_vscode, "target_container_name", lambda live, t: "app")
    monkeypatch.setattr(
        ide_vscode,
        "warn_if_seat_may_not_fit",
        lambda live, target: seen.setdefault("warned", (live, target)),
    )
    return seen


def _open() -> None:
    ide_vscode.open_vscode(
        cast(Any, FakeKube()),
        "p",
        target=None,
        image="img",
        identity="id",
        config_dir=None,
        code="code",
        timeout=1,
        no_headroom=True,
    )


def _seat_fails(monkeypatch, explanation):
    def fail(*args, **kwargs):
        raise KubectlError("ssh exited 255")

    monkeypatch.setattr(ide_vscode, "_open_seat", fail)
    monkeypatch.setattr(
        ide_vscode, "explain_lost_pod", lambda kube, pod, uid: explanation
    )


def test_warns_before_opening_the_seat(monkeypatch, calls):
    monkeypatch.setattr(ide_vscode, "_open_seat", lambda *args, **kwargs: None)
    _open()
    assert calls["warned"] == (POD, "app")


def test_lost_pod_is_explained(monkeypatch, calls):
    _seat_fails(monkeypatch, "pod p was evicted: usage exceeds 2Gi")
    with pytest.raises(KubectlError, match="pod p was evicted") as caught:
        _open()
    assert "VS Code server went with it" in str(caught.value)
    assert str(caught.value.__cause__) == "ssh exited 255"


def test_other_failures_are_re_raised_unchanged(monkeypatch, calls):
    _seat_fails(monkeypatch, None)
    with pytest.raises(KubectlError, match="^ssh exited 255$"):
        _open()
