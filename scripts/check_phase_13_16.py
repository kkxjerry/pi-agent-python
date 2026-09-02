#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent.ai import UserMessage  # noqa: E402
from pi_agent.coding_agent.settings import SettingsResolver  # noqa: E402
from pi_agent.harness.compaction import (  # noqa: E402
    CompactionSettings,
    compact_session,
)
from pi_agent.harness.resource_loader import (  # noqa: E402
    ResourceLoader,
    ResourceLoaderConfig,
)
from pi_agent.harness.session import SessionManager, reconstruct_context  # noqa: E402

REQUIRED = (
    "src/pi_agent/harness/session/types.py",
    "src/pi_agent/harness/session/store.py",
    "src/pi_agent/harness/session/tree.py",
    "src/pi_agent/harness/session/manager.py",
    "src/pi_agent/harness/compaction/compaction.py",
    "src/pi_agent/harness/compaction/branch.py",
    "src/pi_agent/harness/resource_loader.py",
    "src/pi_agent/harness/system_prompt.py",
    "src/pi_agent/coding_agent/settings.py",
    "src/pi_agent/coding_agent/configuration.py",
    "docs/audit/phase-13-16-evidence.md",
)


async def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"phase 13-16 gate failed: missing {', '.join(missing)}")

    with tempfile.TemporaryDirectory(prefix="pi-phase-13-16-") as directory:
        root = Path(directory)
        manager = SessionManager.create(root / "session.jsonl", cwd=str(root), session_id="gate")
        manager.append_message(UserMessage("old " * 100), entry_id="m1")
        manager.append_message(UserMessage("keep"), entry_id="m2")
        await compact_session(
            manager,
            CompactionSettings(context_window=60, reserve_tokens=10, keep_recent_tokens=10),
            lambda *_args: "gate summary",
            force=True,
        )
        context = reconstruct_context(manager.tree.active_path())
        if len(context.messages) != 2 or not context.messages[0].content.endswith("gate summary"):
            raise SystemExit("phase 13-16 gate failed: compacted context did not reconstruct")

        resource_root = root / "resources"
        skill = resource_root / "skills" / "gate" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: gate\ndescription: Gate skill\n---\nbody",
            encoding="utf-8",
        )
        snapshot = ResourceLoader(
            ResourceLoaderConfig(
                cwd=root,
                user_root=root / "missing",
                extra_roots=(resource_root,),
            )
        ).load()
        if snapshot.skill("gate") is None:
            raise SystemExit("phase 13-16 gate failed: resource discovery failed")

        settings_path = root / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "compaction": {
                        "reserve_tokens": 100,
                        "keep_recent_tokens": 200,
                    }
                }
            ),
            encoding="utf-8",
        )
        settings = SettingsResolver().resolve(project_path=settings_path, environ={})
        if settings.source("compaction.reserve_tokens").layer != "project":
            raise SystemExit("phase 13-16 gate failed: settings provenance failed")

    print(
        "phase 13-16 gate OK: session=jsonl-tree, compaction=append-only, "
        "resources=layered, settings=provenance-aware"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
