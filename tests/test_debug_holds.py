"""Only the supervisor owns probe files; clients own protocol tokens."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from podbench import ide_prepare, ide_python
from podbench.lifecycle_client import UPGRADE


@pytest.fixture
def injection(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    root = proc / "root"
    package = root / "tmp/.podbench-debugpy-1000/debugpy"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (proc / "exe").write_text("target executable")
    monkeypatch.setattr(
        ide_python, "Path", lambda p: proc if p == "/proc/123" else Path(p)
    )
    monkeypatch.setattr(ide_python.os, "getuid", lambda: 1000)
    monkeypatch.setattr(ide_python, "process_start", lambda pid: "start")
    monkeypatch.setattr(ide_python, "thread_db_arguments", lambda pid: [])
    monkeypatch.setattr(ide_python, "listener", Mock(side_effect=[None, "inode"]))
    run = Mock(return_value=SimpleNamespace(returncode=0))
    action = Mock(return_value="r-owned")
    monkeypatch.setattr(ide_python.subprocess, "run", run)
    monkeypatch.setattr(ide_python, "action", action)
    return SimpleNamespace(root=root, state=tmp_path / "state", run=run, action=action)


@pytest.mark.parametrize("success", [True, False])
def test_cli_injection_owns_child_scoped_hold(injection, success):
    app = injection
    (app.root / "tmp/podbench-control").mkdir()
    app.run.return_value.returncode = 0 if success else 1
    if success:
        ide_python.inject(123, "start", 5678, app.state)
        app.action.assert_called_once_with(app.root, "hold-child")
    else:
        with pytest.raises(RuntimeError, match="injection failed"):
            ide_python.inject(123, "start", 5678, app.state)
        assert app.action.call_args_list[-1].args == (app.root, "release")
        assert app.action.call_args_list[-1].kwargs == {"token": "r-owned"}
    assert not (app.root / "tmp/podbench-hold").exists()


def test_plain_injection_needs_no_supervisor(injection):
    ide_python.inject(123, "start", 5678, injection.state)
    injection.action.assert_not_called()
    injection.run.assert_called_once()


@pytest.mark.parametrize("changed", [None, "start", "inode", "port"])
def test_reconnect_reuses_only_its_recorded_listener(injection, monkeypatch, changed):
    app = injection
    app.state.mkdir()
    recorded = {"start": "start", "inode": "inode", "port": 5678}
    if changed:
        recorded[changed] = "different"
    (app.state / "python-123.json").write_text(json.dumps(recorded))
    monkeypatch.setattr(ide_python, "listener", lambda port: "inode")
    if changed:
        with pytest.raises(RuntimeError, match="another listener"):
            ide_python.inject(123, "start", 5678, app.state)
    else:
        ide_python.inject(123, "start", 5678, app.state)
    app.run.assert_not_called()
    app.action.assert_not_called()


@pytest.mark.parametrize("port", [5678, 6000])
def test_reattach_after_disconnect_asks_for_restart(injection, monkeypatch, port):
    # debugpy's adapter exits on Disconnect, but listen() cannot run twice.
    app = injection
    app.state.mkdir()
    (app.root / "tmp/podbench-control").mkdir()
    recorded = {"start": "start", "inode": "inode", "port": 5678}
    (app.state / "python-123.json").write_text(json.dumps(recorded))
    monkeypatch.setattr(ide_python, "listener", lambda port: None)
    with pytest.raises(RuntimeError, match="already loaded in PID 123.*restart"):
        ide_python.inject(123, "start", port, app.state)
    app.run.assert_not_called()
    app.action.assert_not_called()


def test_obsolete_supervisor_is_not_held_directly(injection):
    (injection.root / "tmp/podbench-child.pid").write_text("123")
    injection.action.side_effect = RuntimeError(UPGRADE)
    with pytest.raises(RuntimeError, match="obsolete"):
        ide_python.inject(123, "start", 5678, injection.state)
    injection.run.assert_not_called()
    assert not (injection.root / "tmp/podbench-hold").exists()


@pytest.mark.parametrize("supervised", [True, False])
def test_ide_owns_session_hold_and_releases_once(tmp_path, monkeypatch, supervised):
    root = tmp_path / "root"
    if supervised:
        (root / "tmp/podbench-control").mkdir(parents=True)
    metadata = tmp_path / "process.json"
    metadata.write_text(json.dumps({"root": str(root), "python": True, "port": 5678}))
    monkeypatch.setattr(ide_prepare, "resolve", lambda _: 123)
    monkeypatch.setattr(ide_prepare, "process_start", lambda _: "start")
    action, inject = Mock(return_value="r-session"), Mock()
    monkeypatch.setattr(ide_prepare, "action", action)
    monkeypatch.setattr(ide_prepare, "inject", inject)
    ide_prepare.prepare(metadata, "attach")
    inject.assert_called_once_with(123, "start", 5678, tmp_path, manage_hold=False)
    if supervised:
        action.assert_called_once_with(root, "hold")
        with pytest.raises(RuntimeError, match="already owns"):
            ide_prepare.prepare(metadata, "attach")
        assert metadata.with_suffix(".hold").read_text() == "r-session"
    ide_prepare.prepare(metadata, "release")
    ide_prepare.prepare(metadata, "release")
    if supervised:
        assert action.call_count == 2
        action.assert_called_with(root, "release", token="r-session")
    else:
        action.assert_not_called()
