"""Run the shipped supervisor against isolated control and checkout paths."""

from pathlib import Path

import pytest


@pytest.fixture
def runtime(tmp_path):
    source = (
        Path(__file__).parents[1] / "Charts/podbench-hotfix-claim/files/podbench.sh"
    )
    script = tmp_path / "podbench.sh"
    script.write_text(
        source.read_text()
        .replace("/tmp/podbench", str(tmp_path / "podbench"))
        .replace("/podbench/app", str(tmp_path / "app"))
    )
    return script
