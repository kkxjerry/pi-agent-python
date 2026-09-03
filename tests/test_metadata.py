from __future__ import annotations

import json
from importlib.resources import files as resource_files
from pathlib import Path

from pi_agent import UPSTREAM, __version__
from pi_agent.cli import main


def test_version_and_upstream_are_pinned() -> None:
    assert __version__ == "0.4.0.dev0"
    assert UPSTREAM.repository == "earendil-works/pi"
    assert UPSTREAM.tag == "v0.84.4"
    assert UPSTREAM.commit == "b79e4cc"
    assert len(UPSTREAM.archive_sha256) == 64


def test_metadata_cli_and_documents(capsys: object) -> None:
    assert main(["upstream", "--json"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(output)["tag"] == "v0.84.4"
    document = Path("UPSTREAM.md").read_text(encoding="utf-8")
    for value in (UPSTREAM.repository, UPSTREAM.tag, UPSTREAM.commit, UPSTREAM.archive_sha256):
        assert value in document


def test_packaged_manifest_and_parity_cli(capsys: object) -> None:
    repository = json.loads(Path("fixtures/manifest.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        resource_files("pi_agent.data")
        .joinpath("fixture_manifest.json")
        .read_text(encoding="utf-8")
    )
    assert packaged == repository
    assert main(["parity", "--json"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert json.loads(output) == {
        "byStatus": {"registered": 10, "upstream-execution": 20},
        "total": 30,
    }
