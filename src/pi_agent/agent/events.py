from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from pi_agent.ai import AssistantMessage, AssistantMessageEvent, ToolResultMessage

from .types import AgentMessage, AgentToolResult


@dataclass(slots=True)
class AgentStartEvent:
    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass(slots=True)
class AgentEndEvent:
    messages: list[AgentMessage]
    type: Literal["agent_end"] = field(default="agent_end", init=False)


@dataclass(slots=True)
class TurnStartEvent:
    type: Literal["turn_start"] = field(default="turn_start", init=False)


@dataclass(slots=True)
class TurnEndEvent:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    type: Literal["turn_end"] = field(default="turn_end", init=False)


@dataclass(slots=True)
class MessageStartEvent:
    message: AgentMessage
    type: Literal["message_start"] = field(default="message_start", init=False)


@dataclass(slots=True)
class MessageUpdateEvent:
    message: AssistantMessage
    assistant_message_event: AssistantMessageEvent
    type: Literal["message_update"] = field(default="message_update", init=False)


@dataclass(slots=True)
class MessageEndEvent:
    message: AgentMessage
    type: Literal["message_end"] = field(default="message_end", init=False)


@dataclass(slots=True)
class ToolExecutionStartEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)


@dataclass(slots=True)
class ToolExecutionUpdateEvent:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    partial_result: AgentToolResult
    type: Literal["tool_execution_update"] = field(default="tool_execution_update", init=False)


@dataclass(slots=True)
class ToolExecutionEndEvent:
    tool_call_id: str
    tool_name: str
    result: AgentToolResult
    is_error: bool
    args: dict[str, Any] | None = None
    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)


AgentEvent: TypeAlias = (
    AgentStartEvent
    | AgentEndEvent
    | TurnStartEvent
    | TurnEndEvent
    | MessageStartEvent
    | MessageUpdateEvent
    | MessageEndEvent
    | ToolExecutionStartEvent
    | ToolExecutionUpdateEvent
    | ToolExecutionEndEvent
)
