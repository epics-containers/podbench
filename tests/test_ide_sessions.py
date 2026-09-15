"""Reconnects resolve fresh processes and release only their own session state."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from podbench import ide_prepare, ide_process


@pytest.fixture
def processes(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(
        ide_process,
        "Path",
        lambda p: (
            tmp_path / str(p).lstrip("/") if str(p).startswith("/proc") else Path(p)
        ),
    )

    def add(pid, *, namespace="mnt:[1]", argv=("app", "serve")):
        directory = proc / str(pid)
        (directory / "ns").mkdir(parents=True)
        (directory / "ns/mnt").symlink_to(namespace)
        (directory / "exe").symlink_to("/usr/bin/app")
        (directory / "cmdline").write_bytes(
            b"\0".join(a.encode() for a in argv) + b"\0"
        )
        return directory

    return add


INVOCATION = {
    "namespace": "mnt:[1]",
    "argv": ["app", "serve"],
    "executable": "/usr/bin/app",
}


@pytest.mark.parametrize("replaced", [True, False])
def test_snapshot_checks_process_identity_and_preserves_invocation(
    processes, monkeypatch, replaced
):
    directory = processes(100)
    (directory / "cwd").symlink_to("/app")
    (directory / "environ").write_bytes(b"KEY=value=extra\0EMPTY=\0")
    monkeypatch.setattr(
        ide_process,
        "process_start",
        Mock(side_effect=["start", "new" if replaced else "start"]),
    )
    if replaced:
        with pytest.raises(RuntimeError, match="process changed"):
            ide_process.snapshot(100)
    else:
        recorded = ide_process.snapshot(100)
        assert recorded == {
            **INVOCATION,
            "pid": 100,
            "start": "start",
            "cwd": "/app",
            "environment": {"KEY": "value=extra", "EMPTY": ""},
        }


def test_reconnect_resolves_replacement_not_saved_pid(processes):
    old = processes(100)
    processes(200, namespace="mnt:[2]")
    assert ide_process.resolve(INVOCATION) == 100
    (old / "cmdline").write_bytes(b"different\0")
    processes(300)
    assert ide_process.resolve(INVOCATION) == 300


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_process_is_rejected(processes, count):
    for pid in range(100, 100 + count):
        processes(pid)
    with pytest.raises(RuntimeError, match=f"found {count}"):
        ide_process.resolve(INVOCATION)


def test_binding_uses_surviving_supervisor(processes, tmp_path):
    supervisor = processes(100, argv=("supervisor",))
    processes(200)
    processes(50, namespace="mnt:[2]")
    assert ide_process.bind(tmp_path, "mnt:[1]") == supervisor / "root"
    assert (tmp_path / "target").read_text() == str(supervisor / "root")
    with pytest.raises(RuntimeError, match="no longer visible"):
        ide_process.bind(tmp_path, "mnt:[3]")


@pytest.fixture
def session(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "tmp/podbench-control").mkdir(parents=True)
    path = tmp_path / "process.json"
    path.write_text(json.dumps({"root": str(root), "python": True, "port": 5678}))
    mocks = SimpleNamespace(
        path=path,
        root=root,
        resolve=Mock(return_value=123),
        process_start=Mock(return_value="start"),
        action=Mock(return_value="r-owned"),
        inject=Mock(),
    )
    for name in ("resolve", "process_start", "action", "inject"):
        monkeypatch.setattr(ide_prepare, name, getattr(mocks, name))
    return mocks


@pytest.mark.parametrize("phase", ["acquire", "inject"])
def test_failed_preparation_releases_only_acquired_hold(session, phase):
    path, root = session.path, session.root
    failing = session.action if phase == "acquire" else session.inject
    failing.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError, match="failed"):
        ide_prepare.prepare(path, "attach")
    assert not path.with_suffix(".hold").exists()
    assert not path.with_suffix(".session").exists()
    if phase == "inject":
        session.action.assert_called_with(root, "release", token="r-owned")
    else:
        session.action.assert_called_once_with(root, "hold")


def test_failed_release_retains_token_for_retry(session):
    path = session.path
    ide_prepare.prepare(path, "attach")
    session.action.side_effect = RuntimeError("unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        ide_prepare.prepare(path, "release")
    assert path.with_suffix(".hold").read_text() == "r-owned"
    session.action.side_effect = None
    ide_prepare.prepare(path, "release")
    assert not path.with_suffix(".hold").exists()


@pytest.mark.parametrize("python", [True, False])
def test_process_replaced_during_preparation_is_not_recorded(
    session, monkeypatch, python
):
    path, root = session.path, session.root
    metadata = json.loads(path.read_text())
    metadata["python"] = python
    path.write_text(json.dumps(metadata))
    monkeypatch.setattr(
        ide_prepare.shutil, "copyfile", lambda src, dst: dst.write_text("exe")
    )
    session.process_start.side_effect = ["start", "replacement"]
    with pytest.raises(RuntimeError, match="process changed"):
        ide_prepare.prepare(path, "attach")
    assert not path.with_suffix(".session").exists()
    session.action.assert_called_with(root, "release", token="r-owned")


def test_reconnect_after_release_records_new_process(session):
    path = session.path
    ide_prepare.prepare(path, "attach")
    ide_prepare.prepare(path, "release")
    session.resolve.return_value = 456
    session.process_start.return_value = "new-start"
    ide_prepare.prepare(path, "attach")
    assert json.loads(path.with_suffix(".session").read_text()) == {
        "pid": 456,
        "start": "new-start",
    }
