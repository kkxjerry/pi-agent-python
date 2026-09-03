from __future__ import annotations

import json
import math

from pi_agent.ai import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

MESSAGE_OVERHEAD_TOKENS = 4
IMAGE_ESTIMATE_TOKENS = 1_024


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # The upstream estimator is deliberately approximate. UTF-8 bytes make the
    # estimate less English-centric while preserving a cheap deterministic gate.
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


def estimate_message_tokens(message: Message) -> int:
    total = MESSAGE_OVERHEAD_TOKENS
    if isinstance(message, UserMessage):
        if isinstance(message.content, str):
            return total + estimate_text_tokens(message.content)
        for user_block in message.content:
            total += _estimate_content(user_block)
        return total
    if isinstance(message, AssistantMessage):
        for assistant_block in message.content:
            total += _estimate_content(assistant_block)
        return total
    if isinstance(message, ToolResultMessage):
        total += estimate_text_tokens(message.tool_name)
        for result_block in message.content:
            total += _estimate_content(result_block)
        if message.details:
            total += estimate_text_tokens(
                json.dumps(message.details, ensure_ascii=False, sort_keys=True, default=str)
            )
        return total
    return total


def estimate_context_tokens(messages: list[Message] | tuple[Message, ...]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def calculate_context_tokens(messages: list[Message] | tuple[Message, ...]) -> int:
    """Prefer reported usage from the latest assistant turn, then estimate the tail."""

    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AssistantMessage):
            continue
        usage = message.usage
        reported = usage.input + usage.output + usage.cache_read + usage.cache_write
        if reported <= 0:
            continue
        return reported + estimate_context_tokens(messages[index + 1 :])
    return estimate_context_tokens(messages)


def _estimate_content(content: object) -> int:
    if isinstance(content, TextContent):
        return estimate_text_tokens(content.text)
    if isinstance(content, ThinkingContent):
        return estimate_text_tokens(content.thinking)
    if isinstance(content, ImageContent):
        return IMAGE_ESTIMATE_TOKENS
    if isinstance(content, ToolCall):
        return estimate_text_tokens(content.name) + estimate_text_tokens(
            json.dumps(content.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    return 0
