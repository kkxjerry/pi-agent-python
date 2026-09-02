from __future__ import annotations

import pytest

from pi_agent.ai import (
    AssistantMessage,
    ImageContent,
    Model,
    ModelCost,
    ModelCostTier,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_from_dict,
    message_to_dict,
)
from pi_agent.ai.types import calculate_cost


def test_messages_round_trip_with_camel_case_wire_names(model: Model) -> None:
    messages = [
        UserMessage([TextContent("hello"), ImageContent("YWJj", "image/png")], timestamp=1),
        AssistantMessage(
            content=[
                ThinkingContent("inspect", thinking_signature="sig"),
                TextContent("answer", text_signature="text-sig"),
                ToolCall("call-1", "read", {"path": "README.md"}, namespace="coding"),
            ],
            api=model.api,
            provider=model.provider,
            model=model.id,
            response_model="actual-model",
            response_id="response-1",
            usage=Usage(input=2, output=3, total_tokens=5),
            stop_reason="toolUse",
            diagnostics=[{"type": "provider_note", "value": 1}],
            timestamp=2,
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[TextContent("contents")],
            details={"bytes": 8},
            is_error=False,
            timestamp=3,
        ),
    ]

    encoded = [message_to_dict(message) for message in messages]
    assert encoded[1]["stopReason"] == "toolUse"
    assert encoded[2]["toolCallId"] == "call-1"
    assert [message_to_dict(message_from_dict(value)) for value in encoded] == encoded


def test_calculate_cost_uses_per_million_token_prices(model: Model) -> None:
    usage = Usage(input=1_000_000, output=500_000, cache_read=200_000, cache_write=100_000)
    cost = calculate_cost(model, usage)
    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 0.1
    assert cost.cache_write == 0.15
    assert cost.total == 2.25


def test_calculate_cost_selects_request_wide_tier_and_long_cache_rate(model: Model) -> None:
    model.cost = ModelCost(
        input=1.0,
        output=2.0,
        cache_read=0.5,
        cache_write=1.5,
        tiers=(
            ModelCostTier(
                input_tokens_above=1_000_000,
                input=2.0,
                output=4.0,
                cache_read=1.0,
                cache_write=3.0,
            ),
        ),
    )
    usage = Usage(
        input=600_000,
        output=100_000,
        cache_read=300_000,
        cache_write=200_000,
        cache_write_1h=50_000,
    )

    cost = calculate_cost(model, usage)

    assert cost.input == pytest.approx(1.2)
    assert cost.output == pytest.approx(0.4)
    assert cost.cache_read == pytest.approx(0.3)
    assert cost.cache_write == pytest.approx(0.65)
    assert cost.total == pytest.approx(2.55)
    assert usage.cost is cost
