"""Exercise the shipped Bash runtime with real child processes."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "Charts/podbench-hotfix-claim/files/podbench.sh"


def eventually(check):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.05)
    pytest.fail("runtime did not reach the expected state within 10 seconds")


@pytest.fixture
def runtime(tmp_path):
    # Isolate protocol files and checkout paths without changing runtime logic.
    script = tmp_path / "podbench.sh"
    script.write_text(
        SCRIPT.read_text()
        .replace("/tmp/podbench", str(tmp_path / "podbench"))
        .replace("/podbench/app", str(tmp_path / "app"))
    )
    return script


@pytest.mark.parametrize("kind", ["python", "bash", "binary"])
def test_restart_preserves_context_and_holds(runtime, tmp_path, kind):
    record = tmp_path / "context.json"
    app = tmp_path / "record.py"
    app.write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps(dict(\n"
        "    pid=os.getpid(), args=sys.argv[2:], cwd=os.getcwd(),\n"
        "    value=os.environ['ORIGINAL_VALUE'], uid=os.getuid(),\n"
        "    gid=os.getgid(), mask=os.umask(0), state=os.environ['state'],\n"
        "    startup=os.environ['startup'], health=os.environ['health'])))\n"
        "while True: time.sleep(1)\n"
    )
    args = ["space separated", "'quotes'", "$literal; *", "line\nbreak", ""]
    command = [sys.executable, str(app), str(record), *args]
    if kind == "bash":
        wrapper = tmp_path / "start.sh"
        wrapper.write_text('exec "$@"\n')
        command = ["bash", str(wrapper), *command]
    elif kind == "binary":
        command = ["/usr/bin/env", *command]
    launch = "exec " + shlex.join(command)
    log = (tmp_path / "runtime.log").open("w")
    process = subprocess.Popen(
        [
            "bash",
            "-c",
            'umask 027; source "$1"; podbench_supervise "$2" true true',
            "test",
            str(runtime),
            launch,
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "ORIGINAL_VALUE": "spaces 'quotes' $literal\nnext",
            "state": "application state",
            "startup": "application startup",
            "health": "application health",
        },
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    control = tmp_path / "podbench-control"
    hold = tmp_path / "podbench-hold"

    def request(action, **files):
        directory = control / "requests" / str(time.monotonic_ns())
        directory.mkdir()
        (directory / "action").write_text(action)
        for name, value in files.items():
            (directory / name).write_text(value)
        (directory / "ready").touch()
        response = directory / "response"
        eventually(response.exists)
        return directory, response.read_text().strip()

    try:
        eventually(lambda: record.exists() and (control / "state").exists())
        before = json.loads(record.read_text())
        assert before == {
            "pid": before["pid"],
            "args": args,
            "cwd": str(tmp_path),
            "value": "spaces 'quotes' $literal\nnext",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "mask": 0o27,
            "state": "application state",
            "startup": "application startup",
            "health": "application health",
        }
        owner, response = request("hold")
        assert response == "ok" and hold.exists()
        assert request("restart")[1] == "ok"
        after = json.loads(record.read_text())
        assert before.pop("pid") != after.pop("pid")
        assert before == after
        assert hold.exists(), "restart must retain another client's hold"
        assert request("release", token=owner.name)[1] == "ok"
        assert not hold.exists()
        assert request("stop")[1] == "ok"
        assert hold.exists()
        assert not (tmp_path / "podbench-child.pid").exists()
        debugger, response = request(
            "launch",
            run="exec sleep 60\n",
            heartbeat=str(int(time.time())),
            **{"in": "", "out": "", "err": ""},
        )
        assert response == "ok"
        debug_pid = int((tmp_path / "podbench-child.pid").read_text())
        assert "Launch session owns" in request("restart")[1]
        (debugger / "cancel").touch()
        eventually(lambda: (debugger / "exit").exists())
        assert (debugger / "exit").read_text().strip() == "130"
        assert not Path(f"/proc/{debug_pid}").exists()
        assert hold.exists()
        assert request("start")[1] == "ok"
        assert not hold.exists()
        assert process.poll() is None, "child operations must retain the supervisor"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        log.close()


def test_source_only_defines_functions(runtime, tmp_path):
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; printf untouched', "test", str(runtime)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "untouched"
    assert not (tmp_path / "podbench-control").exists()


def test_python_helper_keeps_arguments_and_prefers_checkout(runtime, tmp_path):
    original = tmp_path / "python"
    original.write_text('#!/bin/bash\nprintf "original\\n"; printf "%s\\n" "$@"\n')
    original.chmod(0o755)
    command = [
        "bash",
        "-c",
        'source "$1"; shift; podbench_python "$@"',
        "test",
        str(runtime),
        str(original),
        "-m",
        "any_module",
        "a b",
        "$literal",
    ]
    assert subprocess.check_output(command, text=True).splitlines() == [
        "original",
        "-m",
        "any_module",
        "a b",
        "$literal",
    ]
    editable = tmp_path / "app/.venv/bin/python"
    editable.parent.mkdir(parents=True)
    editable.write_text(original.read_text().replace("original", "editable"))
    editable.chmod(0o755)
    assert subprocess.check_output(command, text=True).splitlines() == [
        "editable",
        "-m",
        "any_module",
        "a b",
        "$literal",
    ]


def test_script_helper_uses_original_until_checkout_exists(runtime, tmp_path):
    editable = tmp_path / "app/start.sh"
    command = [
        "bash",
        "-c",
        'source "$1"; shift; podbench_script "$@"',
        "test",
        str(runtime),
        str(editable),
        "printf",
        "%s",
        "original 'quoted'",
    ]
    assert subprocess.check_output(command, text=True) == "original 'quoted'"
    editable.parent.mkdir()
    editable.write_text("printf editable\n")
    assert subprocess.check_output(command, text=True) == "editable"
