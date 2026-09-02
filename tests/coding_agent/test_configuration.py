from __future__ import annotations

from pathlib import Path

from pi_agent.coding_agent.configuration import (
    build_compaction_settings,
    build_resource_loader_config,
    session_directory,
)
from pi_agent.coding_agent.settings import SettingsResolver


def test_resolved_settings_build_runtime_configs(tmp_path: Path) -> None:
    snapshot = SettingsResolver().resolve(
        environ={},
        runtime={
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
            "resources.user_root": str(tmp_path / "resources"),
            "resources.project_dir_name": ".agent",
            "resources.include": ["skills/**"],
            "resources.exclude": ["**/private/**"],
            "resources.follow_symlinks": True,
            "session.directory": str(tmp_path / "sessions"),
        },
    )
    compaction = build_compaction_settings(snapshot, context_window=1_000)
    resources = build_resource_loader_config(snapshot, cwd=tmp_path / "repo")

    assert compaction.threshold == 900
    assert compaction.keep_recent_tokens == 200
    assert resources.user_root == (tmp_path / "resources").resolve()
    assert resources.project_dir_name == ".agent"
    assert resources.follow_symlinks is True
    assert session_directory(snapshot) == tmp_path / "sessions"
