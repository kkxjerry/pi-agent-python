from __future__ import annotations

from pi_agent.ai import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from pi_agent.harness.compaction import CompactionSettings, prepare_compaction


def test_product_compaction_uses_the_same_safe_cut_contract() -> None:
    messages = [
        UserMessage("inspect"),
        AssistantMessage(
            content=[ToolCall("call-1", "read", {"path": "a.py"})],
            stop_reason="toolUse",
        ),
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="read",
            content=[TextContent("x" * 400)],
        ),
        UserMessage("continue"),
    ]
    preparation = prepare_compaction(
        messages,
        CompactionSettings(context_window=100, reserve_tokens=10, keep_recent_tokens=20),
        force=True,
    )

    summarized = list(preparation.messages_to_summarize)
    kept = list(preparation.messages_to_keep)
    assert not (
        summarized
        and isinstance(summarized[-1], AssistantMessage)
        and kept
        and isinstance(kept[0], ToolResultMessage)
    )
    assert preparation.file_operations.read == ("a.py",)
