"""Product-level AgentSession, configuration, and headless modes."""

from .agent_session import AgentSession, AgentSessionOptions, create_agent_session
from .configuration import (
    compaction_settings_from_settings,
    load_settings,
    model_from_settings,
    resource_loader_from_settings,
    stream_options_from_settings,
)
from .events import agent_event_to_dict, assistant_event_to_dict, tool_result_to_dict
from .modes import RpcProtocolError, RpcServer, run_json_mode, run_print_mode, run_rpc_mode
from .settings import (
    DEFAULT_SPECS,
    SettingSource,
    SettingSpec,
    Settings,
    SettingsResolver,
    SettingsStore,
    flatten,
    load_settings_file,
    save_settings_file,
    unflatten,
)

__all__ = [
    "DEFAULT_SPECS",
    "AgentSession",
    "AgentSessionOptions",
    "RpcProtocolError",
    "RpcServer",
    "SettingSource",
    "SettingSpec",
    "Settings",
    "SettingsResolver",
    "SettingsStore",
    "agent_event_to_dict",
    "assistant_event_to_dict",
    "compaction_settings_from_settings",
    "create_agent_session",
    "flatten",
    "load_settings",
    "load_settings_file",
    "model_from_settings",
    "resource_loader_from_settings",
    "run_json_mode",
    "run_print_mode",
    "run_rpc_mode",
    "save_settings_file",
    "stream_options_from_settings",
    "tool_result_to_dict",
    "unflatten",
]
