"""Exercise supervisor failure recovery and debugger streams with real processes."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


def eventually(check):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    pytest.fail("supervisor did not reach the expected state")


def gone(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


@pytest.fixture
def supervise(runtime, tmp_path):
    @contextmanager
    def run(startup="exec sleep 60", health="true", safe="true"):
        control = tmp_path / "podbench-control"

        def request(action, *, wait=True, **files):
            directory = control / "requests" / str(time.monotonic_ns())
            directory.mkdir()
            for name, value in {"action": action, **files}.items():
                (directory / name).write_text(value)
            (directory / "ready").touch()
            if wait:
                eventually(lambda: (directory / "response").exists())
            return directory

        with (tmp_path / "runtime.log").open("w") as log:
            process = subprocess.Popen(
                [
                    "bash",
                    "-c",
                    'source "$1"; podbench_supervise "$2" "$3" "$4"',
                    "test",
                    str(runtime),
                    startup,
                    health,
                    safe,
                ],
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                eventually(lambda: (control / "state").exists())
                yield SimpleNamespace(
                    process=process,
                    control=control,
                    request=request,
                    hold=tmp_path / "podbench-hold",
                    pidfile=tmp_path / "podbench-child.pid",
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    return run


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_or_cancelled_start_can_recover(supervise, tmp_path, cancel):
    healthy = tmp_path / "healthy"
    with supervise(health=f"test -f {shlex.quote(str(healthy))}") as app:
        app.request("stop")
        request = app.request("start", deadline="1", wait=False)
        if cancel:
            eventually(lambda: app.pidfile.exists())
            (request / "cancel").touch()
        eventually(lambda: (request / "response").exists())
        assert ("cancelled" if cancel else "health check failed") in (
            request / "response"
        ).read_text()
        assert (app.control / "state").read_text().strip() == (
            "stopped" if cancel else "failed"
        )
        assert app.hold.exists() and not app.pidfile.exists()
        healthy.touch()
        assert (app.request("start") / "response").read_text().strip() == "ok"
        assert not app.hold.exists()


def test_term_resistant_health_check_is_bounded(supervise, tmp_path):
    pidfile = tmp_path / "probe.pid"
    health = (
        f"echo $$ > {shlex.quote(str(pidfile))}; "
        "trap '' TERM; while :; do sleep 1; done"
    )
    try:
        with supervise(health=health) as app:
            request = app.request("restart", deadline="1")
            assert "health check failed" in (request / "response").read_text()
            assert app.process.poll() is None
            eventually(lambda: gone(int(pidfile.read_text())))
    finally:
        # Also clean up the intentionally stubborn probe when testing old code.
        if pidfile.exists() and not gone(pid := int(pidfile.read_text())):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


def test_term_resistant_child_is_killed_before_restart(supervise, tmp_path):
    ready = tmp_path / "ready"
    program = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r}, 'w').close(); time.sleep(60)"
    )
    with supervise(
        startup="exec " + shlex.join([sys.executable, "-c", program])
    ) as app:
        eventually(ready.exists)
        pid = int(app.pidfile.read_text())
        request = app.request("restart")
        assert (request / "response").read_text().strip() == "ok"
        assert gone(pid) and int(app.pidfile.read_text()) != pid
        assert app.process.poll() is None and not app.hold.exists()


def test_normal_exit_is_propagated(supervise):
    with supervise(startup="sleep 0.2; exit 7") as app:
        assert app.process.wait(timeout=10) == 7
        assert not app.pidfile.exists()


@pytest.mark.parametrize("ending", ["exit", "heartbeat", "shutdown"])
def test_debugger_completion_and_cleanup(supervise, tmp_path, ending):
    child_pid = tmp_path / "inferior.pid"
    inferior = "import os,time; print(os.getpid(), flush=True); time.sleep(60)"
    # The inferior starts a new session, outside the debugger's process group.
    launch = (
        f"setsid {shlex.join([sys.executable, '-c', inferior])} "
        f"> {shlex.quote(str(child_pid))} &\n"
    )
    launch += "wait\n"
    with supervise() as app:
        app.request("stop")
        debugger = app.request(
            "launch",
            run="exit 7\n" if ending == "exit" else launch,
            heartbeat=str(int(time.time())),
            **{"in": "", "out": "", "err": ""},
        )
        pid = None
        try:
            if ending != "exit":
                eventually(lambda: child_pid.exists() and child_pid.read_text().strip())
                pid = int(child_pid.read_text())
            if ending == "shutdown":
                app.process.terminate()
                app.process.wait(timeout=10)
            else:
                if ending == "heartbeat":
                    (debugger / "heartbeat").write_text("0")
                eventually(lambda: (debugger / "exit").exists())
                assert (debugger / "exit").read_text().strip() == (
                    "7" if ending == "exit" else "130"
                )
                assert (app.control / "state").read_text().strip() == "stopped"
                assert app.hold.exists()
            if pid is not None:
                eventually(lambda: gone(pid))
            assert not app.pidfile.exists()
        finally:
            if pid is not None and not gone(pid):
                os.kill(pid, signal.SIGKILL)


def test_unsafe_and_cancelled_requests_leave_running_child_alone(supervise):
    with supervise(safe="false") as app:
        original = app.pidfile.read_text()
        for action in ("stop", "restart", "launch", "hold"):
            assert "hold-aware" in (app.request(action) / "response").read_text()
        cancelled = app.request("stop", cancel="")
        assert "cancelled" in (cancelled / "response").read_text()
        assert (app.request("start") / "response").read_text().strip() == "ok"
        assert app.pidfile.read_text() == original and not app.hold.exists()


def test_debugger_uses_real_fifo_streams(supervise, tmp_path):
    with supervise() as app:
        app.request("stop")
        script = (
            "from pathlib import Path\n"
            "import os,sys\n"
            "import podbench.lifecycle_client as client\n"
            "client.CONTROL = 'podbench-control'\n"
            "sys.exit(client.relay(Path(sys.argv[1]), "
            "['bash', '-c', 'read line; echo out:$line; echo err:$line >&2; exit 7'], "
            "sys.argv[1], dict(os.environ)))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            input="hello\n",
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 7, result.stderr
        assert result.stdout == "out:hello\n" and result.stderr == "err:hello\n"
        eventually(lambda: (app.control / "state").read_text().strip() == "stopped")
        assert app.hold.exists() and not app.pidfile.exists()
