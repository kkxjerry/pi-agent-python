#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent import __version__  # noqa: E402
from pi_agent.agent import Agent, AgentLoopConfig, AgentTool  # noqa: E402
from pi_agent.harness import AgentHarness, create_coding_tools  # noqa: E402

REQUIRED = [
    "src/pi_agent/agent/agent.py",
    "src/pi_agent/agent/queues.py",
    "src/pi_agent/harness/environment.py",
    "src/pi_agent/harness/harness.py",
    "src/pi_agent/harness/result.py",
    "src/pi_agent/harness/truncate.py",
    "src/pi_agent/harness/tools/read.py",
    "src/pi_agent/harness/tools/write.py",
    "src/pi_agent/harness/tools/edit.py",
    "src/pi_agent/harness/tools/bash.py",
    "tests/agent/test_parallel_hooks_and_queues.py",
    "tests/agent/test_stateful_agent.py",
    "tests/harness/test_coding_tools.py",
    "tests/harness/test_harness.py",
    "tests/parity/test_phase8_9_parity.py",
    "docs/audit/phase-8-12-evidence.md",
]


def fail(message: str) -> None:
    raise SystemExit(f"phase 8-12 gate failed: {message}")


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail(f"missing files: {', '.join(missing)}")
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios", [])
    executed = [
        scenario
        for scenario in scenarios
        if isinstance(scenario, dict) and scenario.get("status") == "upstream-execution"
    ]
    if len(executed) != 20:
        fail(f"expected twenty executed upstream fixtures, got {len(executed)}")
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branch != "develop":
        fail(f"work must be on develop, got {branch!r}")
    try:
        version = tuple(int(part) for part in __version__.split(".")[:2])
    except ValueError:
        fail(f"could not parse project version {__version__!r}")
    if version < (0, 3):
        fail(f"expected a Phase 8-12-or-later development version, got {__version__}")
    if not all((Agent, AgentLoopConfig, AgentTool, AgentHarness, create_coding_tools)):
        fail("public runtime imports are incomplete")
    print(
        "phase 8-12 gate OK: "
        f"version={__version__}, upstream_execution={len(executed)}, "
        "parallel/stateful/tools/harness=present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
