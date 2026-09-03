"""Stable Python SDK for the coding-agent product layer."""

from __future__ import annotations

from .agent_session import (
    AgentRunMessages,
    AgentSession,
    AgentSessionEvent,
    AgentSessionState,
    AgentSettledEvent,
    CompactionEndEvent,
    CompactionRunResult,
    CompactionStartEvent,
    EntryAppendedEvent,
    PromptOptions,
    QueueUpdateEvent,
    SessionInfoChangedEvent,
    SessionStats,
    ThinkingLevelChangedEvent,
)
from .configuration import (
    build_compaction_settings,
    build_resource_loader_config,
    session_directory,
)
from .modes import (
    PrintModeOptions,
    RpcServer,
    parse_json_line,
    run_json_mode,
    run_print_mode,
    run_rpc_mode,
    run_rpc_stdio,
    serialize_json_line,
    to_json_event,
)
from .sdk import (
    CreateAgentSessionOptions,
    CreateAgentSessionResult,
    create_agent_session_only,
    create_model_summarizer,
)
from .sdk import create_agent_session as _create_agent_session
from .settings import (
    DEFAULT_ENV_KEYS,
    DEFAULT_SPECS,
    SettingOrigin,
    SettingsError,
    SettingSpec,
    SettingsResolver,
    SettingsSnapshot,
    SettingsStore,
    SettingsWarning,
    load_settings_file,
    save_settings_file,
)

# The Phase 17 draft exposed this shorter name. Keep it as a source-compatible
# alias while the canonical Phase 21 SDK uses CreateAgentSessionOptions.
AgentSessionOptions = CreateAgentSessionOptions
Settings = SettingsSnapshot
SettingSource = SettingOrigin


async def create_agent_session(
    options: CreateAgentSessionOptions | None = None,
) -> CreateAgentSessionResult:
    return await _create_agent_session(options)


__all__ = [
    "DEFAULT_ENV_KEYS",
    "DEFAULT_SPECS",
    "AgentRunMessages",
    "AgentSession",
    "AgentSessionEvent",
    "AgentSessionOptions",
    "AgentSessionState",
    "AgentSettledEvent",
    "CompactionEndEvent",
    "CompactionRunResult",
    "CompactionStartEvent",
    "CreateAgentSessionOptions",
    "CreateAgentSessionResult",
    "EntryAppendedEvent",
    "PrintModeOptions",
    "PromptOptions",
    "QueueUpdateEvent",
    "RpcServer",
    "SessionInfoChangedEvent",
    "SessionStats",
    "SettingOrigin",
    "SettingSource",
    "SettingSpec",
    "Settings",
    "SettingsError",
    "SettingsResolver",
    "SettingsSnapshot",
    "SettingsStore",
    "SettingsWarning",
    "ThinkingLevelChangedEvent",
    "build_compaction_settings",
    "build_resource_loader_config",
    "create_agent_session",
    "create_agent_session_only",
    "create_model_summarizer",
    "load_settings_file",
    "parse_json_line",
    "run_json_mode",
    "run_print_mode",
    "run_rpc_mode",
    "run_rpc_stdio",
    "save_settings_file",
    "serialize_json_line",
    "session_directory",
    "to_json_event",
]
