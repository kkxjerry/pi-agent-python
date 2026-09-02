from __future__ import annotations

from pi_agent.parity import normalize_value


def test_normalizer_removes_nondeterminism_but_preserves_tool_ids_and_order() -> None:
    value = {
        "timestamp": 123,
        "sessionId": "deadbeef-dead-beef-dead-beefdeadbeef",
        "toolCallId": "tool-2",
        "items": [{"toolCallId": "tool-1"}, {"toolCallId": "tool-2"}],
    }
    normalized = normalize_value(value)
    assert normalized["timestamp"] == "<timestamp>"
    assert normalized["sessionId"] == "<sessionId>"
    assert normalized["toolCallId"] == "tool-2"
    assert [item["toolCallId"] for item in normalized["items"]] == ["tool-1", "tool-2"]
