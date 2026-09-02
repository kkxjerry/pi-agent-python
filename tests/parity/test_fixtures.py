from __future__ import annotations

import json
from pathlib import Path

from pi_agent.parity import load_jsonl, validate_fixture


def test_manifest_and_executed_upstream_fixtures_are_consistent() -> None:
    manifest = json.loads(Path("fixtures/manifest.json").read_text(encoding="utf-8"))
    assert manifest["upstream"] == {
        "repository": "earendil-works/pi",
        "tag": "v0.84.4",
        "commit": "b79e4cc",
    }
    ids = [scenario["id"] for scenario in manifest["scenarios"]]
    assert len(ids) == len(set(ids)) == 30
    fixtures = [scenario for scenario in manifest["scenarios"] if "fixture" in scenario]
    assert len(fixtures) == 20
    for scenario in fixtures:
        records = load_jsonl(Path("fixtures") / scenario["fixture"])
        validate_fixture(records)
        assert records[0]["provenance"] == "upstream-execution"
        assert records[0]["scenario"] == scenario["id"]


def test_parallel_contract_distinguishes_completion_and_transcript_order() -> None:
    records = load_jsonl(Path("fixtures/upstream/parallel_out_of_order.jsonl"))
    events = [record["event"] for record in records if record["record"] == "event"]
    completion = [event["toolCallId"] for event in events if event["type"] == "tool_execution_end"]
    transcript = [
        event["toolCallId"]
        for event in events
        if event["type"] == "message_end" and event.get("role") == "toolResult"
    ]
    assert completion == ["tool-2", "tool-1"]
    assert transcript == ["tool-1", "tool-2"]


def test_contracts_preserve_pending_start_continue_and_error_text() -> None:
    for path in Path("fixtures/upstream").glob("*.jsonl"):
        events = [record["event"] for record in load_jsonl(path) if record["record"] == "event"]
        assistant_starts = [
            event
            for event in events
            if event["type"] == "message_start" and event.get("role") == "assistant"
        ]
        assert assistant_starts
        assert all(event["stopReason"] == "pending" for event in assistant_starts)

    continue_events = [
        record["event"]
        for record in load_jsonl(Path("fixtures/upstream/continue_existing_context.jsonl"))
        if record["record"] == "event"
    ]
    assert not any(event.get("role") == "user" for event in continue_events)

    expected = {
        "invalid_tool_name.jsonl": "Tool missing not found",
        "tool_throws_exception.jsonl": "fixture failure",
        "truncated_tool_call.jsonl": (
            'Tool call "echo" was not executed: the response hit the output token limit, '
            "so its arguments may be truncated. Re-issue the tool call with complete arguments."
        ),
    }
    for filename, text in expected.items():
        events = [
            record["event"]
            for record in load_jsonl(Path("fixtures/upstream") / filename)
            if record["record"] == "event"
        ]
        tool_messages = [
            event
            for event in events
            if event["type"] in {"message_start", "message_end"}
            and event.get("role") == "toolResult"
        ]
        assert [event.get("text") for event in tool_messages] == [text, text]
