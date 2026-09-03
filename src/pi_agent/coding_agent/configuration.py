from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pi_agent.ai import Model, ModelCost, StreamOptions
from pi_agent.harness.compaction import CompactionSettings
from pi_agent.harness.resource_loader import ResourceLoader

from .settings import Settings, SettingsResolver


def load_settings(
    *,
    cwd: str | Path,
    home: str | Path | None = None,
    cli: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    environment: dict[str, str] | None = None,
) -> Settings:
    root = Path(cwd).expanduser().resolve()
    home_path = Path(home or Path.home()).expanduser().resolve()
    global_path = home_path / ".pi" / "settings.json"
    project_json = root / ".pi" / "settings.json"
    project_toml = root / ".pi" / "settings.toml"
    project_path = (
        project_json if project_json.exists() or not project_toml.exists() else project_toml
    )
    return SettingsResolver().resolve(
        global_path=global_path,
        project_path=project_path,
        environment=environment,
        cli=cli,
        runtime=runtime,
    )


def model_from_settings(settings: Settings) -> Model:
    return Model(
        api=str(settings.require("model.api")),
        provider=str(settings.require("model.provider")),
        id=str(settings.require("model.id")),
        name=str(settings.require("model.id")),
        base_url=str(settings.require("model.base_url")),
        reasoning=settings.require("thinking.level") != "off",
        cost=ModelCost(),
        context_window=int(settings.require("model.context_window")),
        max_tokens=int(settings.require("model.max_tokens")),
    )


def stream_options_from_settings(settings: Settings) -> StreamOptions:
    level = str(settings.require("thinking.level"))
    return StreamOptions(
        reasoning=cast(Any, None if level == "off" else level),
        timeout=float(settings.require("provider.timeout")),
        stream_idle_timeout=float(settings.require("provider.stream_idle_timeout")),
        max_retries=int(settings.require("provider.max_retries")),
        max_retry_delay=float(settings.require("provider.max_retry_delay")),
        transport=cast(Any, settings.require("provider.transport")),
    )


def compaction_settings_from_settings(settings: Settings) -> CompactionSettings:
    return CompactionSettings(
        enabled=bool(settings.require("compaction.enabled")),
        reserve_tokens=int(settings.require("compaction.reserve_tokens")),
        keep_recent_tokens=int(settings.require("compaction.keep_recent_tokens")),
        retry_count=int(settings.require("compaction.retry_count")),
    )


def resource_loader_from_settings(
    settings: Settings,
    *,
    cwd: str | Path,
    user_dir: str | Path | None = None,
) -> ResourceLoader:
    return ResourceLoader(
        cwd=cwd,
        user_dir=user_dir,
        explicit_paths=[Path(path) for path in settings.require("resources.paths")],
        include=list(settings.require("resources.include")),
        exclude=list(settings.require("resources.exclude")),
        follow_symlinks=bool(settings.require("resources.follow_symlinks")),
    )
