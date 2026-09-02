from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, cast

from .cancellation import CancellationToken

JsonValue: TypeAlias = str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
StopReason: TypeAlias = Literal[
    "pending", "stop", "length", "toolUse", "error", "aborted", "deferred"
]
ThinkingLevel: TypeAlias = Literal["minimal", "low", "medium", "high", "xhigh", "max"]
ToolChoice: TypeAlias = Literal["auto", "none"]
CacheRetention: TypeAlias = Literal["none", "short", "long"]
Transport: TypeAlias = Literal["sse", "auto"]


def timestamp_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True, slots=True)
class ModelCostTier:
    input_tokens_above: int
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(slots=True)
class ModelCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    tiers: tuple[ModelCostTier, ...] = ()


@dataclass(slots=True)
class UsageCost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass(slots=True)
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int | None = None
    reasoning: int | None = None
    total_tokens: int = 0
    cost: UsageCost = field(default_factory=UsageCost)

    @classmethod
    def zero(cls) -> Usage:
        return cls()


@dataclass(slots=True)
class Model:
    api: str
    provider: str
    id: str
    name: str
    base_url: str = ""
    reasoning: bool = False
    input: tuple[Literal["text", "image"], ...] = ("text",)
    cost: ModelCost = field(default_factory=ModelCost)
    context_window: int = 0
    max_tokens: int = 0
    headers: dict[str, str | None] = field(default_factory=dict)
    compat: dict[str, JsonValue] = field(default_factory=dict)
    sampling_params: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(slots=True)
class TextContent:
    text: str
    text_signature: str | None = None
    type: Literal["text"] = field(default="text", init=False)


@dataclass(slots=True)
class ThinkingContent:
    thinking: str
    thinking_signature: str | None = None
    redacted: bool | None = None
    type: Literal["thinking"] = field(default="thinking", init=False)


@dataclass(slots=True)
class ImageContent:
    data: str
    mime_type: str
    type: Literal["image"] = field(default="image", init=False)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, JsonValue]
    thought_signature: str | None = None
    namespace: str | None = None
    type: Literal["toolCall"] = field(default="toolCall", init=False)


AssistantContent: TypeAlias = TextContent | ThinkingContent | ToolCall
UserContent: TypeAlias = TextContent | ImageContent
ToolResultContent: TypeAlias = TextContent | ImageContent


@dataclass(slots=True)
class UserMessage:
    content: str | list[UserContent]
    timestamp: int = field(default_factory=timestamp_ms)
    role: Literal["user"] = field(default="user", init=False)


@dataclass(slots=True)
class AssistantMessage:
    content: list[AssistantContent] = field(default_factory=list)
    api: str = ""
    provider: str = ""
    model: str = ""
    response_model: str | None = None
    response_id: str | None = None
    usage: Usage = field(default_factory=Usage.zero)
    stop_reason: StopReason = "pending"
    error_message: str | None = None
    raw_stop_reason: str | None = None
    end_turn: bool | None = None
    diagnostics: list[dict[str, JsonValue]] = field(default_factory=list)
    timestamp: int = field(default_factory=timestamp_ms)
    role: Literal["assistant"] = field(default="assistant", init=False)


@dataclass(slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[ToolResultContent]
    details: Any = field(default_factory=dict)
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    is_error: bool = False
    timestamp: int = field(default_factory=timestamp_ms)
    role: Literal["toolResult"] = field(default="toolResult", init=False)


Message: TypeAlias = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, JsonValue]
    constrained_sampling: bool | dict[str, JsonValue] = False


@dataclass(slots=True)
class Context:
    messages: list[Message] = field(default_factory=list)
    system_prompt: str | None = None
    tools: list[ToolDefinition] | None = None


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    status: int
    headers: dict[str, str]


PayloadCallback: TypeAlias = Callable[
    [dict[str, Any], Model],
    Awaitable[dict[str, Any] | None] | dict[str, Any] | None,
]
ResponseCallback: TypeAlias = Callable[[ProviderResponse, Model], Awaitable[None] | None]


@dataclass(slots=True)
class StreamOptions:
    api_key: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    signal: CancellationToken | None = None
    headers: dict[str, str | None] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    sampling_params: dict[str, JsonValue] = field(default_factory=dict)
    session_id: str | None = None
    tool_choice: ToolChoice | None = None
    reasoning: ThinkingLevel | None = None
    transport: Transport = "auto"
    cache_retention: CacheRetention = "short"
    timeout: float = 600.0
    stream_idle_timeout: float | None = None
    max_retries: int = 2
    retry_base_delay: float = 0.5
    max_retry_delay: float = 60.0
    on_payload: PayloadCallback | None = None
    on_response: ResponseCallback | None = None


def calculate_cost(model: Model, usage: Usage) -> UsageCost:
    """Apply the highest matching request-wide tier and 1h cache-write pricing."""

    input_tokens = usage.input + usage.cache_read + usage.cache_write
    rates: ModelCost | ModelCostTier = model.cost
    matched_threshold = -1
    for tier in model.cost.tiers:
        if input_tokens > tier.input_tokens_above > matched_threshold:
            rates = tier
            matched_threshold = tier.input_tokens_above

    long_write = usage.cache_write_1h or 0
    short_write = usage.cache_write - long_write
    result = UsageCost(
        input=(usage.input * rates.input) / 1_000_000,
        output=(usage.output * rates.output) / 1_000_000,
        cache_read=(usage.cache_read * rates.cache_read) / 1_000_000,
        cache_write=(rates.cache_write * short_write + rates.input * 2 * long_write) / 1_000_000,
    )
    result.total = result.input + result.output + result.cache_read + result.cache_write
    usage.cost = result
    return result


def content_to_dict(content: AssistantContent | UserContent | ToolResultContent) -> dict[str, Any]:
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
    value = {
        "type": "toolCall",
        "id": content.id,
        "name": content.name,
        "arguments": content.arguments,
    }
    if content.thought_signature is not None:
        value["thoughtSignature"] = content.thought_signature
    if content.namespace is not None:
        value["namespace"] = content.namespace
    return value


def content_from_dict(value: dict[str, Any]) -> AssistantContent | UserContent:
    content_type = value.get("type")
    if content_type == "text":
        return TextContent(str(value.get("text", "")), cast(str | None, value.get("textSignature")))
    if content_type == "thinking":
        return ThinkingContent(
            thinking=str(value.get("thinking", "")),
            thinking_signature=cast(str | None, value.get("thinkingSignature")),
            redacted=cast(bool | None, value.get("redacted")),
        )
    if content_type == "image":
        return ImageContent(
            data=str(value.get("data", "")), mime_type=str(value.get("mimeType", ""))
        )
    if content_type == "toolCall":
        arguments = value.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("toolCall.arguments must be an object")
        return ToolCall(
            id=str(value.get("id", "")),
            name=str(value.get("name", "")),
            arguments=cast(dict[str, JsonValue], arguments),
            thought_signature=cast(str | None, value.get("thoughtSignature")),
            namespace=cast(str | None, value.get("namespace")),
        )
    raise ValueError(f"Unknown content type: {content_type!r}")


def usage_to_dict(usage: Usage) -> dict[str, Any]:
    value: dict[str, Any] = {
        "input": usage.input,
        "output": usage.output,
        "cacheRead": usage.cache_read,
        "cacheWrite": usage.cache_write,
        "totalTokens": usage.total_tokens,
        "cost": {
            "input": usage.cost.input,
            "output": usage.cost.output,
            "cacheRead": usage.cost.cache_read,
            "cacheWrite": usage.cost.cache_write,
            "total": usage.cost.total,
        },
    }
    if usage.cache_write_1h is not None:
        value["cacheWrite1h"] = usage.cache_write_1h
    if usage.reasoning is not None:
        value["reasoning"] = usage.reasoning
    return value


def usage_from_dict(value: dict[str, Any]) -> Usage:
    cost_value = value.get("cost", {})
    if not isinstance(cost_value, dict):
        cost_value = {}
    return Usage(
        input=int(value.get("input", 0)),
        output=int(value.get("output", 0)),
        cache_read=int(value.get("cacheRead", 0)),
        cache_write=int(value.get("cacheWrite", 0)),
        cache_write_1h=(int(value["cacheWrite1h"]) if "cacheWrite1h" in value else None),
        reasoning=(int(value["reasoning"]) if "reasoning" in value else None),
        total_tokens=int(value.get("totalTokens", 0)),
        cost=UsageCost(
            input=float(cost_value.get("input", 0)),
            output=float(cost_value.get("output", 0)),
            cache_read=float(cost_value.get("cacheRead", 0)),
            cache_write=float(cost_value.get("cacheWrite", 0)),
            total=float(cost_value.get("total", 0)),
        ),
    )


def message_to_dict(message: Message) -> dict[str, Any]:
    if isinstance(message, UserMessage):
        content: Any = message.content
        if isinstance(content, list):
            content = [content_to_dict(item) for item in content]
        return {"role": "user", "content": content, "timestamp": message.timestamp}
    if isinstance(message, ToolResultMessage):
        value: dict[str, Any] = {
            "role": "toolResult",
            "toolCallId": message.tool_call_id,
            "toolName": message.tool_name,
            "content": [content_to_dict(item) for item in message.content],
            "details": message.details,
            "isError": message.is_error,
            "timestamp": message.timestamp,
        }
        if message.usage is not None:
            value["usage"] = usage_to_dict(message.usage)
        if message.added_tool_names is not None:
            value["addedToolNames"] = message.added_tool_names
        return value
    value = {
        "role": "assistant",
        "content": [content_to_dict(item) for item in message.content],
        "api": message.api,
        "provider": message.provider,
        "model": message.model,
        "usage": usage_to_dict(message.usage),
        "stopReason": message.stop_reason,
        "timestamp": message.timestamp,
    }
    optional = {
        "responseModel": message.response_model,
        "responseId": message.response_id,
        "errorMessage": message.error_message,
        "rawStopReason": message.raw_stop_reason,
        "endTurn": message.end_turn,
        "diagnostics": message.diagnostics or None,
    }
    value.update({key: item for key, item in optional.items() if item is not None})
    return value


def message_from_dict(value: dict[str, Any]) -> Message:
    role = value.get("role")
    if role == "user":
        raw_content = value.get("content", "")
        user_content: str | list[UserContent]
        if isinstance(raw_content, list):
            user_content = [cast(UserContent, content_from_dict(item)) for item in raw_content]
        else:
            user_content = str(raw_content)
        return UserMessage(content=user_content, timestamp=int(value.get("timestamp", 0)))
    if role == "toolResult":
        raw_content = value.get("content", [])
        if not isinstance(raw_content, list):
            raise ValueError("toolResult.content must be a list")
        return ToolResultMessage(
            tool_call_id=str(value.get("toolCallId", "")),
            tool_name=str(value.get("toolName", "")),
            content=[cast(ToolResultContent, content_from_dict(item)) for item in raw_content],
            details=value.get("details", {}),
            usage=(
                usage_from_dict(value["usage"]) if isinstance(value.get("usage"), dict) else None
            ),
            added_tool_names=cast(list[str] | None, value.get("addedToolNames")),
            is_error=bool(value.get("isError", False)),
            timestamp=int(value.get("timestamp", 0)),
        )
    if role == "assistant":
        raw_content = value.get("content", [])
        if not isinstance(raw_content, list):
            raise ValueError("assistant.content must be a list")
        return AssistantMessage(
            content=[cast(AssistantContent, content_from_dict(item)) for item in raw_content],
            api=str(value.get("api", "")),
            provider=str(value.get("provider", "")),
            model=str(value.get("model", "")),
            response_model=cast(str | None, value.get("responseModel")),
            response_id=cast(str | None, value.get("responseId")),
            usage=usage_from_dict(cast(dict[str, Any], value.get("usage", {}))),
            stop_reason=cast(StopReason, value.get("stopReason", "pending")),
            error_message=cast(str | None, value.get("errorMessage")),
            raw_stop_reason=cast(str | None, value.get("rawStopReason")),
            end_turn=cast(bool | None, value.get("endTurn")),
            diagnostics=cast(list[dict[str, JsonValue]], value.get("diagnostics", [])),
            timestamp=int(value.get("timestamp", 0)),
        )
    raise ValueError(f"Unknown message role: {role!r}")


def message_text(message: Message) -> str:
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return message.content
        return "".join(item.text for item in message.content if isinstance(item, TextContent))
    return "".join(item.text for item in message.content if isinstance(item, TextContent))
