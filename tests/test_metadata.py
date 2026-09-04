from __future__ import annotations

import tomllib
from pathlib import Path

from pi_agent import __version__
from pi_agent._upstream import UPSTREAM

ROOT = Path(__file__).resolve().parents[1]


def test_project_version_matches_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == "0.6.0.dev0"
    assert project["project"]["version"] == __version__


def test_upstream_baseline_remains_pinned() -> None:
    assert UPSTREAM.tag == "v0.84.4"
    assert UPSTREAM.commit == "b79e4cc"
