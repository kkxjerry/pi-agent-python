from __future__ import annotations

import json

from pi_agent.ai import AssistantMessage, Message, ToolResultMessage, message_to_dict


def estimate_tokens(value: str) -> int:
    """Cheap deterministic estimator used for control flow, never billing."""

    if not value:
        return 0
    byte_count = len(value.encode("utf-8", errors="replace"))
    return max(1, (byte_count + 3) // 4)


def estimate_message_tokens(message: Message) -> int:
    encoded = json.dumps(message_to_dict(message), ensure_ascii=False, separators=(",", ":"))
    return estimate_tokens(encoded) + 4


def estimate_context_tokens(messages: list[Message]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def calculate_context_tokens(messages: list[Message]) -> int:
    """Prefer provider usage when it clearly describes the latest context."""

    for message in reversed(messages):
        if isinstance(message, AssistantMessage) and message.usage.total_tokens > 0:
            estimated_tail = 0
            seen = False
            for tail in messages[messages.index(message) + 1 :]:
                if isinstance(tail, ToolResultMessage):
                    estimated_tail += estimate_message_tokens(tail)
                    seen = True
            return (
                message.usage.total_tokens + estimated_tail if seen else message.usage.total_tokens
            )
    return estimate_context_tokens(messages)
