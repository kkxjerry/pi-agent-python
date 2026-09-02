#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent import UPSTREAM  # noqa: E402
from pi_agent.parity import load_jsonl, validate_fixture  # noqa: E402

REQUIRED = [
    "AGENTS.md",
    "UPSTREAM.md",
    "SCOPE.md",
    "ARCHITECTURE.md",
    "PARITY.md",
    "LICENSES/upstream-pi-MIT.txt",
    "docs/upstream/package-map.md",
    "docs/upstream/event-model.md",
    "docs/upstream/agent-loop.md",
    "docs/upstream/session-model.md",
    "docs/upstream/compaction.md",
    "docs/upstream/extensions.md",
    "docs/upstream/rpc-protocol.md",
    "docs/upstream/tui-model.md",
    "docs/upstream/source-map.json",
    "docs/audit/phase-0-3-evidence.md",
    "fixtures/manifest.json",
    "src/pi_agent/data/fixture_manifest.json",
    "tools/upstream-fixtures/package.json",
    "tools/upstream-fixtures/package-lock.json",
    "tools/upstream-fixtures/src/capture.ts",
    "pyproject.toml",
    ".github/workflows/ci.yml",
]


def fail(message: str) -> None:
    raise SystemExit(f"phase 0-3 gate failed: {message}")


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail(f"missing files: {', '.join(missing)}")
    document = (ROOT / "UPSTREAM.md").read_text(encoding="utf-8")
    for expected in (UPSTREAM.repository, UPSTREAM.tag, UPSTREAM.commit, UPSTREAM.archive_sha256):
        if expected not in document:
            fail(f"UPSTREAM.md missing {expected}")
    source_map = json.loads((ROOT / "docs/upstream/source-map.json").read_text())
    baseline = {"repository": UPSTREAM.repository, "tag": UPSTREAM.tag, "commit": UPSTREAM.commit}
    if source_map.get("upstream") != baseline:
        fail("source map baseline drift")
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    packaged = json.loads((ROOT / "src/pi_agent/data/fixture_manifest.json").read_text())
    if manifest != packaged or manifest.get("upstream") != baseline:
        fail("fixture manifest drift")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 30:
        fail("exactly 30 scenarios are required")
    ids = [item.get("id") for item in scenarios if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        fail("duplicate scenario IDs")
    fixture_count = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict) or "fixture" not in scenario:
            continue
        path = ROOT / "fixtures" / str(scenario["fixture"])
        records = load_jsonl(path)
        validate_fixture(records, path=path)
        metadata = records[0]
        if metadata.get("scenario") != scenario.get("id") or metadata.get("upstream") != baseline:
            fail(f"fixture metadata drift: {path}")
        if scenario.get("status") != "upstream-execution":
            fail(f"manifest does not mark executed fixture: {path}")
        if metadata.get("provenance") != "upstream-execution":
            fail(f"fixture lacks executed provenance: {path}")
        if metadata.get("capturedBy") != "tools/upstream-fixtures@0.0.0":
            fail(f"fixture capture identity drift: {path}")
        fixture_count += 1
    if fixture_count != 10:
        fail("exactly ten executed upstream fixtures are required at this milestone")
    package = json.loads((ROOT / "tools/upstream-fixtures/package.json").read_text())
    dependencies = package.get("dependencies", {})
    for name in ("@earendil-works/pi-agent-core", "@earendil-works/pi-ai"):
        if dependencies.get(name) != "0.84.4":
            fail(f"{name} is not exactly pinned")
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if branch != "develop":
        fail(f"work must be on develop, got {branch!r}")
    print(
        "phase 0-3 gate OK: "
        f"baseline={UPSTREAM.tag}/{UPSTREAM.commit}, scenarios={len(scenarios)}, "
        f"upstream_execution={fixture_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
