from __future__ import annotations

import json
from pathlib import Path

import pytest

from pi_agent.coding_agent.settings import (
    SettingsError,
    SettingsResolver,
    SettingsStore,
    load_settings_file,
)


def test_settings_merge_precedence_and_provenance(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project.toml"
    global_path.write_text(
        json.dumps(
            {
                "model": {"provider": "global", "id": "global-model"},
                "provider": {"max_retries": 3},
                "ui": {"theme": "global-theme"},
            }
        ),
        encoding="utf-8",
    )
    project_path.write_text(
        '[model]\nprovider = "project"\n\n[ui]\ntheme = "project-theme"\n',
        encoding="utf-8",
    )

    snapshot = SettingsResolver().resolve(
        global_path=global_path,
        project_path=project_path,
        environ={"PI_MODEL_PROVIDER": "environment", "PI_PROVIDER_MAX_RETRIES": "4"},
        cli={"model": {"provider": "cli"}, "ui.theme": "cli-theme"},
        runtime={"model.provider": "runtime"},
    )

    assert snapshot.get("model.provider") == "runtime"
    assert snapshot.source("model.provider").layer == "runtime"
    assert snapshot.get("model.id") == "global-model"
    assert snapshot.source("model.id").layer == "global"
    assert snapshot.get("provider.max_retries") == 4
    assert snapshot.source("provider.max_retries").layer == "environment"
    assert snapshot.get("ui.theme") == "cli-theme"
    assert snapshot.nested()["model"]["provider"] == "runtime"


def test_defaults_are_copied_and_runtime_resolution_has_no_persistence_side_effect(
    tmp_path: Path,
) -> None:
    project = tmp_path / "settings.json"
    project.write_text('{"ui":{"keybindings":{"abort":"escape"}}}', encoding="utf-8")
    resolver = SettingsResolver()
    first = resolver.resolve(project_path=project, environ={})
    first.get("ui.keybindings")["abort"] = "changed"
    second = resolver.resolve(project_path=project, environ={}, runtime={"ui.theme": "runtime"})

    assert second.get("ui.keybindings") == {"abort": "escape"}
    assert second.get("ui.theme") == "runtime"
    assert load_settings_file(project) == {"ui": {"keybindings": {"abort": "escape"}}}


def test_unknown_and_invalid_settings_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"unknown":1}', encoding="utf-8")
    with pytest.raises(SettingsError, match="unknown setting"):
        SettingsResolver().resolve(project_path=path, environ={})

    relaxed = SettingsResolver().resolve(project_path=path, environ={}, strict_unknown=False)
    assert relaxed.warnings[0].code == "unknown_setting"

    with pytest.raises(SettingsError, match=r"provider\.max_retries"):
        SettingsResolver().resolve(environ={"PI_PROVIDER_MAX_RETRIES": "not-an-int"})
    with pytest.raises(SettingsError, match=r"thinking\.level"):
        SettingsResolver().resolve(cli={"thinking.level": "extreme"}, environ={})
    with pytest.raises(SettingsError, match="keep_recent_tokens"):
        SettingsResolver().resolve(
            cli={
                "compaction.reserve_tokens": 100,
                "compaction.keep_recent_tokens": 50,
            },
            environ={},
        )


def test_settings_store_never_cross_writes_global_and_project_targets(tmp_path: Path) -> None:
    global_path = tmp_path / "global.json"
    project_path = tmp_path / "project.json"
    global_path.write_text('{"model":{"provider":"global"}}\n', encoding="utf-8")
    store = SettingsStore(global_path=global_path, project_path=project_path)

    store.save_project({"model.provider": "project", "tools.enabled": ("read", "bash")})

    assert load_settings_file(global_path)["model"]["provider"] == "global"
    project = load_settings_file(project_path)
    assert project["model"]["provider"] == "project"
    assert project["tools"]["enabled"] == ["read", "bash"]


def test_environment_list_boolean_and_timeout_coercion() -> None:
    snapshot = SettingsResolver().resolve(
        environ={
            "PI_TOOLS": "read, edit ,bash",
            "PI_SESSION_ENABLED": "false",
            "PI_PROVIDER_TIMEOUT": "42.5",
            "PI_COMPACTION_RESERVE_TOKENS": "100",
            "PI_COMPACTION_KEEP_RECENT_TOKENS": "200",
        }
    )
    assert snapshot.get("tools.enabled") == ("read", "edit", "bash")
    assert snapshot.get("session.enabled") is False
    assert snapshot.get("provider.timeout_seconds") == 42.5


def test_toml_save_and_load_for_non_null_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    SettingsStore(global_path=path, project_path=tmp_path / "project.json").save_global(
        {
            "model.provider": "openai",
            "provider.max_retries": 2,
            "tools.enabled": ("read", "write"),
        }
    )
    loaded = load_settings_file(path)
    assert loaded["model"]["provider"] == "openai"
    assert loaded["provider"]["max_retries"] == 2
    assert loaded["tools"]["enabled"] == ["read", "write"]
