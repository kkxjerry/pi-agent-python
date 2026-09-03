from __future__ import annotations

from pathlib import Path

from pi_agent.harness.compaction import CompactionSettings
from pi_agent.harness.resource_loader import ResourceLoaderConfig

from .settings import SettingsSnapshot


def build_compaction_settings(
    settings: SettingsSnapshot,
    *,
    context_window: int,
) -> CompactionSettings:
    return CompactionSettings(
        context_window=context_window,
        reserve_tokens=int(settings.require("compaction.reserve_tokens")),
        keep_recent_tokens=int(settings.require("compaction.keep_recent_tokens")),
    )


def build_resource_loader_config(
    settings: SettingsSnapshot,
    *,
    cwd: str | Path,
) -> ResourceLoaderConfig:
    return ResourceLoaderConfig(
        cwd=Path(cwd),
        user_root=Path(str(settings.require("resources.user_root"))).expanduser(),
        project_dir_name=str(settings.require("resources.project_dir_name")),
        include=tuple(settings.require("resources.include")),
        exclude=tuple(settings.require("resources.exclude")),
        follow_symlinks=bool(settings.require("resources.follow_symlinks")),
    )


def session_directory(settings: SettingsSnapshot) -> Path:
    return Path(str(settings.require("session.directory"))).expanduser()
