from __future__ import annotations

from typing import Any

from pi_agent.agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from pi_agent.ai import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    message_to_dict,
)
from pi_agent.ai.types import usage_to_dict


def agent_event_to_dict(event: AgentEvent) -> dict[str, Any]:
    if isinstance(event, AgentStartEvent):
        return {"type": "agent_start"}
    if isinstance(event, AgentEndEvent):
        return {
            "type": "agent_end",
            "messages": [_agent_message_to_dict(message) for message in event.messages],
        }
    if isinstance(event, TurnStartEvent):
        return {"type": "turn_start"}
    if isinstance(event, TurnEndEvent):
        return {
            "type": "turn_end",
            "message": message_to_dict(event.message),
            "toolResults": [message_to_dict(result) for result in event.tool_results],
        }
    if isinstance(event, MessageStartEvent):
        return {"type": "message_start", "message": _agent_message_to_dict(event.message)}
    if isinstance(event, MessageEndEvent):
        return {"type": "message_end", "message": _agent_message_to_dict(event.message)}
    if isinstance(event, MessageUpdateEvent):
        return {
            "type": "message_update",
            "message": message_to_dict(event.message),
            "assistantMessageEvent": assistant_event_to_dict(event.assistant_message_event),
        }
    if isinstance(event, ToolExecutionStartEvent):
        return {
            "type": "tool_execution_start",
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "args": event.args,
        }
    if isinstance(event, ToolExecutionUpdateEvent):
        return {
            "type": "tool_execution_update",
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "args": event.args,
            "partialResult": tool_result_to_dict(event.partial_result),
        }
    if isinstance(event, ToolExecutionEndEvent):
        return {
            "type": "tool_execution_end",
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "args": event.args,
            "result": tool_result_to_dict(event.result),
            "isError": event.is_error,
        }
    raise TypeError(f"Unsupported AgentEvent: {type(event).__name__}")


def assistant_event_to_dict(event: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"type": event.type}
    if hasattr(event, "content_index"):
        value["contentIndex"] = event.content_index
    if hasattr(event, "delta"):
        value["delta"] = event.delta
    if hasattr(event, "content"):
        value["content"] = event.content
    if hasattr(event, "partial"):
        value["partial"] = message_to_dict(event.partial)
    if hasattr(event, "tool_call"):
        value["toolCall"] = content_to_dict(event.tool_call)
    if hasattr(event, "reason"):
        value["reason"] = event.reason
    if hasattr(event, "message") and isinstance(event.message, AssistantMessage):
        value["message"] = message_to_dict(event.message)
    if hasattr(event, "error") and isinstance(event.error, AssistantMessage):
        value["error"] = message_to_dict(event.error)
    return value


def tool_result_to_dict(result: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "content": [content_to_dict(block) for block in result.content],
        "details": result.details,
        "terminate": result.terminate,
    }
    if result.usage is not None:
        value["usage"] = usage_to_dict(result.usage)
    if result.added_tool_names is not None:
        value["addedToolNames"] = list(result.added_tool_names)
    return value


def content_to_dict(content: Any) -> dict[str, Any]:
    if isinstance(content, TextContent):
        value: dict[str, Any] = {"type": "text", "text": content.text}
        if content.text_signature is not None:
            value["textSignature"] = content.text_signature
        return value
    if isinstance(content, ThinkingContent):
        value = {"type": "thinking", "thinking": content.thinking}
        if content.thinking_signature is not None:
            value["thinkingSignature"] = content.thinking_signature
        if content.redacted is not None:
            value["redacted"] = content.redacted
        return value
    if isinstance(content, ImageContent):
        return {"type": "image", "data": content.data, "mimeType": content.mime_type}
    if isinstance(content, ToolCall):
        value = {
            "type": "toolCall",
            "id": content.id,
            "name": content.name,
            "arguments": content.arguments,
        }
        if content.namespace is not None:
            value["namespace"] = content.namespace
        if content.thought_signature is not None:
            value["thoughtSignature"] = content.thought_signature
        return value
    raise TypeError(f"Unsupported content block: {type(content).__name__}")


def _agent_message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    return message_to_dict(message)
