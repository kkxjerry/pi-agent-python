from __future__ import annotations

from typing import Any

from pi_agent.agent import (
    AgentEndEvent,
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from pi_agent.agent.types import AgentMessage
from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage


def project_message(message: AgentMessage) -> dict[str, Any]:
    """Project an agent message to the stable upstream parity view."""

    role = _message_role(message)
    projected: dict[str, Any] = {"role": role}
    text = _message_text(message)
    if text is not None and (
        text or not isinstance(message, AssistantMessage) or message.stop_reason != "pending"
    ):
        projected["text"] = text

    if isinstance(message, AssistantMessage):
        projected["stopReason"] = message.stop_reason
        projected["toolCallIds"] = [
            block.id for block in message.content if isinstance(block, ToolCall)
        ]
    elif isinstance(message, ToolResultMessage):
        projected.update(
            toolCallId=message.tool_call_id,
            toolName=message.tool_name,
            isError=message.is_error,
        )
    return projected


def project_event(event: AgentEvent) -> dict[str, Any]:
    """Project a Python AgentEvent exactly like the pinned TypeScript fixture runner."""

    if isinstance(event, (MessageStartEvent, MessageEndEvent)):
        return {"type": event.type, **project_message(event.message)}
    if isinstance(event, MessageUpdateEvent):
        return {
            "type": event.type,
            "assistantEventType": event.assistant_message_event.type,
        }
    if isinstance(event, (ToolExecutionStartEvent, ToolExecutionUpdateEvent)):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
        }
    if isinstance(event, ToolExecutionEndEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "isError": event.is_error,
        }
    if isinstance(event, TurnEndEvent):
        return {
            "type": event.type,
            "assistantStopReason": event.message.stop_reason,
            "toolResultIds": [result.tool_call_id for result in event.tool_results],
        }
    if isinstance(event, AgentEndEvent):
        return {
            "type": event.type,
            "messageRoles": [_message_role(message) for message in event.messages],
            "toolResultIds": [
                message.tool_call_id
                for message in event.messages
                if isinstance(message, ToolResultMessage)
            ],
        }
    return {"type": event.type}


def project_messages(messages: list[AgentMessage]) -> dict[str, Any]:
    return {
        "messageRoles": [_message_role(message) for message in messages],
        "toolResultIds": [
            message.tool_call_id for message in messages if isinstance(message, ToolResultMessage)
        ],
    }


def _message_role(message: AgentMessage) -> str:
    if isinstance(message, dict):
        return str(message.get("role", "custom"))
    return message.role


def _message_text(message: AgentMessage) -> str | None:
    if isinstance(message, dict):
        content = message.get("content")
        return content if isinstance(content, str) else None
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return message.content
        return "".join(item.text for item in message.content if isinstance(item, TextContent))
    if isinstance(message, (AssistantMessage, ToolResultMessage)):
        return "".join(item.text for item in message.content if isinstance(item, TextContent))
    return None
