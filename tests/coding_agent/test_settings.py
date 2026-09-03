from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.coding_agent import SettingsResolver, SettingsStore, load_settings_file


def test_settings_layer_precedence_and_source_tracking(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project.toml"
    global_path.write_text(json.dumps({"model": {"id": "global"}, "theme": "light"}))
    project_path.write_text('[model]\nid = "project"\n\n[compaction]\nkeep_recent_tokens = 100\n')

    settings = SettingsResolver().resolve(
        global_path=global_path,
        project_path=project_path,
        environment={"PI_MODEL": "environment"},
        cli={"model": {"id": "cli"}},
        runtime={"model": {"id": "runtime"}},
    )

    assert settings.require("model.id") == "runtime"
    assert settings.source("model.id").layer == "runtime"
    assert settings.require("theme") == "light"
    assert settings.source("theme").layer == "global"
    assert settings.require("compaction.keep_recent_tokens") == 100
    assert settings.source("compaction.keep_recent_tokens").layer == "project"


def test_settings_reject_unknown_and_cross_field_errors() -> None:
    with pytest.raises(ValueError, match="Unknown setting"):
        SettingsResolver().resolve(cli={"unknown": True})
    with pytest.raises(ValueError, match="reserve_tokens"):
        SettingsResolver().resolve(
            cli={"model": {"context_window": 100}, "compaction": {"reserve_tokens": 100}}
        )


def test_settings_store_writes_json_and_toml_atomically(tmp_path: Path) -> None:
    store = SettingsStore(
        global_path=tmp_path / "global.json",
        project_path=tmp_path / "project.toml",
    )
    values = {
        "model": {"id": "test-model"},
        "keybindings": {"abort": "escape"},
        "tools": {"enabled": ["read", "bash"]},
    }

    global_path = store.save_global(values)
    project_path = store.save_project(values)

    assert load_settings_file(global_path) == values
    assert load_settings_file(project_path) == values
