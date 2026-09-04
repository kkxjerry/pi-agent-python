"""Stable public API for the coding-agent product layer."""

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
from .approval import (
    ApprovalAuditSink,
    ApprovalDecision,
    ApprovalGate,
    ApprovalPolicy,
    ApprovalPrompt,
    ApprovalRequest,
    ApprovalRule,
    compose_before_tool_hooks,
)
from .auth import AuthStorage, Credential, CredentialResolver
from .configuration import (
    build_compaction_settings,
    build_resource_loader_config,
    session_directory,
)
from .extensions import ExtensionHost, ExtensionPolicy
from .images import ImageLimits, ImageProcessingError, ImageProcessor, ProcessedImage
from .model_access import ModelAccess, PreparedModel
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
from .packages import PackageManager, PackageManifest
from .runtime import (
    CodingAgentRuntime,
    CodingAgentRuntimeOptions,
    RuntimeState,
    create_coding_agent_runtime,
)
from .sdk import (
    CreateAgentSessionOptions,
    CreateAgentSessionResult,
    create_agent_session_only,
    create_model_summarizer,
)
from .sdk import create_agent_session as _create_agent_session
from .security import (
    ContainerMount,
    ContainerSandboxSpec,
    SecurityBoundaryError,
    SecurityPosture,
    build_container_command,
    container_security_posture,
    default_security_posture,
)
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
    "ApprovalAuditSink",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalPolicy",
    "ApprovalPrompt",
    "ApprovalRequest",
    "ApprovalRule",
    "AuthStorage",
    "CodingAgentRuntime",
    "CodingAgentRuntimeOptions",
    "CompactionEndEvent",
    "CompactionRunResult",
    "CompactionStartEvent",
    "ContainerMount",
    "ContainerSandboxSpec",
    "CreateAgentSessionOptions",
    "CreateAgentSessionResult",
    "Credential",
    "CredentialResolver",
    "EntryAppendedEvent",
    "ExtensionHost",
    "ExtensionPolicy",
    "ImageLimits",
    "ImageProcessingError",
    "ImageProcessor",
    "ModelAccess",
    "PackageManager",
    "PackageManifest",
    "PreparedModel",
    "PrintModeOptions",
    "ProcessedImage",
    "PromptOptions",
    "QueueUpdateEvent",
    "RpcServer",
    "RuntimeState",
    "SecurityBoundaryError",
    "SecurityPosture",
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
    "build_container_command",
    "build_resource_loader_config",
    "compose_before_tool_hooks",
    "container_security_posture",
    "create_agent_session",
    "create_agent_session_only",
    "create_coding_agent_runtime",
    "create_model_summarizer",
    "default_security_posture",
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
