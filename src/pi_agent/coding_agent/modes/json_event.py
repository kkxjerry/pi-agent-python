from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from pi_agent.agent import (
    AgentEndEvent,
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
from pi_agent.ai import ToolCall, message_to_dict
from pi_agent.ai.events import assistant_event_to_dict
from pi_agent.ai.types import content_to_dict, usage_to_dict
from pi_agent.harness.compaction import CompactionResult
from pi_agent.harness.session import record_to_dict

from ..agent_session import (
    AgentSessionEvent,
    AgentSettledEvent,
    CompactionEndEvent,
    CompactionStartEvent,
    EntryAppendedEvent,
    QueueUpdateEvent,
    SessionInfoChangedEvent,
    ThinkingLevelChangedEvent,
)


def to_json_event(event: AgentSessionEvent) -> dict[str, Any]:
    """Convert a session event to the stable JSONL protocol representation."""

    if isinstance(event, AgentStartEvent | TurnStartEvent | AgentSettledEvent):
        return {"type": event.type}
    if isinstance(event, AgentEndEvent):
        return {
            "type": event.type,
            "messages": [_message_to_dict(message) for message in event.messages],
        }
    if isinstance(event, TurnEndEvent):
        return {
            "type": event.type,
            "message": message_to_dict(event.message),
            "toolResults": [message_to_dict(message) for message in event.tool_results],
        }
    if isinstance(event, MessageStartEvent | MessageEndEvent):
        return {"type": event.type, "message": _message_to_dict(event.message)}
    if isinstance(event, MessageUpdateEvent):
        assistant_event = assistant_event_to_dict(event.assistant_message_event)
        assistant_event.pop("partial", None)
        if assistant_event.get("type") == "toolcall_start":
            index = assistant_event.get("contentIndex")
            if isinstance(index, int) and 0 <= index < len(event.message.content):
                block = event.message.content[index]
                if isinstance(block, ToolCall):
                    assistant_event["id"] = block.id
                    assistant_event["toolName"] = block.name
        return {
            "type": event.type,
            "message": message_to_dict(event.message),
            "assistantMessageEvent": assistant_event,
            "usage": usage_to_dict(event.message.usage),
        }
    if isinstance(event, ToolExecutionStartEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "args": _json_safe(event.args),
        }
    if isinstance(event, ToolExecutionUpdateEvent):
        return {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "args": _json_safe(event.args),
            "partialResult": _tool_result_to_dict(event.partial_result),
        }
    if isinstance(event, ToolExecutionEndEvent):
        value: dict[str, Any] = {
            "type": event.type,
            "toolCallId": event.tool_call_id,
            "toolName": event.tool_name,
            "result": _tool_result_to_dict(event.result),
            "isError": event.is_error,
        }
        if event.args is not None:
            value["args"] = _json_safe(event.args)
        return value
    if isinstance(event, QueueUpdateEvent):
        value = {
            "type": event.type,
            "queue": event.queue,
            "action": event.action,
        }
        if event.message is not None:
            value["message"] = _message_to_dict(event.message)
        return value
    if isinstance(event, CompactionStartEvent):
        return {"type": event.type, "reason": event.reason}
    if isinstance(event, CompactionEndEvent):
        return {
            "type": event.type,
            "reason": event.reason,
            "result": _compaction_to_dict(event.result),
            "aborted": event.aborted,
            "willRetry": event.will_retry,
            "errorMessage": event.error_message,
        }
    if isinstance(event, EntryAppendedEvent):
        return {"type": event.type, "entry": record_to_dict(event.entry)}
    if isinstance(event, SessionInfoChangedEvent):
        return {
            "type": event.type,
            "sessionId": event.session_id,
            "name": event.name,
        }
    if isinstance(event, ThinkingLevelChangedEvent):
        return {"type": event.type, "thinkingLevel": event.thinking_level}
    raise TypeError(f"Unsupported AgentSession event: {type(event).__name__}")


def serialize_json_line(value: dict[str, Any]) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, separators=(",", ":")) + "\n"


def parse_json_line(line: str) -> dict[str, Any]:
    if "\n" in line.rstrip("\r\n") or "\r" in line.rstrip("\r\n"):
        raise ValueError("RPC input must contain exactly one JSON object per line")
    stripped = line.rstrip("\r\n")
    if not stripped:
        raise ValueError("RPC input line must not be empty")
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("RPC input line must be a JSON object")
    return value


def _message_to_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return cast(dict[str, Any], _json_safe(message))
    return message_to_dict(message)


def _tool_result_to_dict(result: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "content": [content_to_dict(content) for content in result.content],
        "details": _json_safe(result.details),
        "terminate": result.terminate,
    }
    if result.usage is not None:
        value["usage"] = usage_to_dict(result.usage)
    if result.added_tool_names is not None:
        value["addedToolNames"] = list(result.added_tool_names)
    return value


def _compaction_to_dict(result: CompactionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "summary": result.summary,
        "cutIndex": result.cut_index,
        "tokensBefore": result.tokens_before,
        "tokensAfter": result.tokens_after,
        "splitTurn": result.split_turn,
        "usage": usage_to_dict(result.usage),
        "fileOperations": {
            "read": list(result.file_operations.read),
            "modified": list(result.file_operations.modified),
        },
        "attempts": result.attempts,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_json_safe(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    return str(value)
