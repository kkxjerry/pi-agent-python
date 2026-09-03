#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent import __version__  # noqa: E402
from pi_agent.coding_agent import (  # noqa: E402
    AgentSession,
    RpcServer,
    SettingsResolver,
    create_agent_session,
    run_json_mode,
    run_print_mode,
    run_rpc_mode,
)
from pi_agent.harness import (  # noqa: E402
    CompactionController,
    ResourceLoader,
    SessionManager,
)

REQUIRED = [
    "src/pi_agent/harness/session/types.py",
    "src/pi_agent/harness/session/store.py",
    "src/pi_agent/harness/session/context.py",
    "src/pi_agent/harness/session/manager.py",
    "src/pi_agent/harness/compaction/types.py",
    "src/pi_agent/harness/compaction/compaction.py",
    "src/pi_agent/harness/compaction/controller.py",
    "src/pi_agent/harness/resource_loader.py",
    "src/pi_agent/harness/resource_types.py",
    "src/pi_agent/coding_agent/settings.py",
    "src/pi_agent/coding_agent/configuration.py",
    "src/pi_agent/coding_agent/agent_session.py",
    "src/pi_agent/coding_agent/events.py",
    "src/pi_agent/coding_agent/modes/print.py",
    "src/pi_agent/coding_agent/modes/json.py",
    "src/pi_agent/coding_agent/modes/rpc.py",
    "tests/session/test_session_manager.py",
    "tests/harness/test_compaction.py",
    "tests/harness/test_resource_loader.py",
    "tests/coding_agent/test_settings.py",
    "tests/coding_agent/test_agent_session.py",
    "tests/coding_agent/test_modes.py",
    "docs/audit/phase-17-20-evidence.md",
]


def fail(message: str) -> None:
    raise SystemExit(f"phase 17-20 gate failed: {message}")


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail(f"missing files: {', '.join(missing)}")
    if not __version__.startswith("0.4."):
        fail(f"expected a Phase 17-20 development version, got {__version__}")
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text(encoding="utf-8"))
    scenarios = manifest.get("scenarios", [])
    executed = [
        item
        for item in scenarios
        if isinstance(item, dict) and item.get("status") == "upstream-execution"
    ]
    if len(executed) < 20:
        fail(f"expected at least twenty executed upstream fixtures, got {len(executed)}")
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branch != "develop":
        fail(f"work must be on develop, got {branch!r}")
    public = (
        AgentSession,
        SessionManager,
        CompactionController,
        ResourceLoader,
        SettingsResolver,
        RpcServer,
        create_agent_session,
        run_print_mode,
        run_json_mode,
        run_rpc_mode,
    )
    if not all(public):
        fail("Phase 17-20 public imports are incomplete")
    print(
        "phase 17-20 gate OK: "
        f"version={__version__}, upstream_execution={len(executed)}, "
        "agent_session/print/json/rpc=present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
