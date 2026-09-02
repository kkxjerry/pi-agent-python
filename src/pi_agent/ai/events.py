from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .types import AssistantMessage, StopReason, ToolCall, message_to_dict


@dataclass(slots=True)
class StartEvent:
    partial: AssistantMessage
    type: Literal["start"] = field(default="start", init=False)


@dataclass(slots=True)
class TextStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["text_start"] = field(default="text_start", init=False)


@dataclass(slots=True)
class TextDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["text_delta"] = field(default="text_delta", init=False)


@dataclass(slots=True)
class TextEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["text_end"] = field(default="text_end", init=False)


@dataclass(slots=True)
class ThinkingStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["thinking_start"] = field(default="thinking_start", init=False)


@dataclass(slots=True)
class ThinkingDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["thinking_delta"] = field(default="thinking_delta", init=False)


@dataclass(slots=True)
class ThinkingEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["thinking_end"] = field(default="thinking_end", init=False)


@dataclass(slots=True)
class ToolCallStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["toolcall_start"] = field(default="toolcall_start", init=False)


@dataclass(slots=True)
class ToolCallDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["toolcall_delta"] = field(default="toolcall_delta", init=False)


@dataclass(slots=True)
class ToolCallEndEvent:
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage
    type: Literal["toolcall_end"] = field(default="toolcall_end", init=False)


@dataclass(slots=True)
class DoneEvent:
    reason: Literal["stop", "length", "toolUse", "deferred"]
    message: AssistantMessage
    type: Literal["done"] = field(default="done", init=False)


@dataclass(slots=True)
class ErrorEvent:
    reason: Literal["aborted", "error"]
    error: AssistantMessage
    type: Literal["error"] = field(default="error", init=False)


AssistantMessageEvent: TypeAlias = (
    StartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | DoneEvent
    | ErrorEvent
)


def assistant_event_to_dict(event: AssistantMessageEvent) -> dict[str, Any]:
    value: dict[str, Any] = {"type": event.type}
    if isinstance(event, StartEvent):
        value["partial"] = message_to_dict(event.partial)
    elif isinstance(event, (TextStartEvent, ThinkingStartEvent, ToolCallStartEvent)):
        value.update(contentIndex=event.content_index, partial=message_to_dict(event.partial))
    elif isinstance(event, (TextDeltaEvent, ThinkingDeltaEvent, ToolCallDeltaEvent)):
        value.update(
            contentIndex=event.content_index,
            delta=event.delta,
            partial=message_to_dict(event.partial),
        )
    elif isinstance(event, (TextEndEvent, ThinkingEndEvent)):
        value.update(
            contentIndex=event.content_index,
            content=event.content,
            partial=message_to_dict(event.partial),
        )
    elif isinstance(event, ToolCallEndEvent):
        value.update(
            contentIndex=event.content_index,
            toolCall={
                "type": "toolCall",
                "id": event.tool_call.id,
                "name": event.tool_call.name,
                "arguments": event.tool_call.arguments,
            },
            partial=message_to_dict(event.partial),
        )
    elif isinstance(event, DoneEvent):
        value.update(reason=event.reason, message=message_to_dict(event.message))
    else:
        value.update(reason=event.reason, error=message_to_dict(event.error))
    return value


def is_terminal_event(event: AssistantMessageEvent) -> bool:
    return isinstance(event, (DoneEvent, ErrorEvent))


def event_result(event: AssistantMessageEvent) -> AssistantMessage:
    if isinstance(event, DoneEvent):
        return event.message
    if isinstance(event, ErrorEvent):
        return event.error
    raise ValueError(f"Non-terminal event has no result: {event.type}")


def normal_stop_reason(reason: StopReason) -> Literal["stop", "length", "toolUse", "deferred"]:
    if reason in {"stop", "length", "toolUse", "deferred"}:
        return reason
    raise ValueError(f"Not a normal stop reason: {reason}")
