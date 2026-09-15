"""Generated native launchers name the target architecture for cppdbg."""

from pathlib import Path

import pytest

from podbench import ide_launchers


def elf(path: Path, machine: int) -> Path:
    path.write_bytes(b"\x7fELF\x02\x01\x01" + bytes(11) + machine.to_bytes(2, "little"))
    return path


@pytest.mark.parametrize(
    ("machine", "expected"), [(62, "x64"), (183, "arm64"), (8, None)]
)
def test_architecture_from_elf_machine(tmp_path, machine, expected):
    assert ide_launchers.architecture(elf(tmp_path / "exe", machine)) == expected


def test_unreadable_executable_has_no_architecture(tmp_path):
    assert ide_launchers.architecture(tmp_path / "missing") is None


def test_native_attach_names_target_architecture(tmp_path, monkeypatch):
    process = {
        "pid": 117,
        "executable": "/epics/ioc/bin/linux-x86_64/ioc",
        "argv": ["/epics/ioc/bin/linux-x86_64/ioc", "st.cmd"],
        "cwd": "/epics/ioc",
        "name": "ioc",
        "python": False,
    }
    monkeypatch.setattr(ide_launchers, "processes", lambda namespace: [dict(process)])
    monkeypatch.setattr(
        ide_launchers,
        "architecture",
        lambda path: "x64" if path == Path("/proc/117/exe") else None,
    )
    configurations, _, extensions, _ = ide_launchers.launchers(
        tmp_path, tmp_path / "app", tmp_path
    )
    (attach,) = configurations
    assert attach["type"] == "cppdbg"
    assert attach["targetArchitecture"] == "x64"
    assert "ms-vscode.cpptools" in extensions
