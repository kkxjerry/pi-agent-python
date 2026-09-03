from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pi_agent.agent import MessageUpdateEvent
from pi_agent.ai import (
    AssistantMessage,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
    ToolCallStartEvent,
)
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.coding_agent.modes import (
    PrintModeOptions,
    parse_json_line,
    run_print_mode,
    serialize_json_line,
    to_json_event,
)


def settings(tmp_path: Path):
    return SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(tmp_path / "resources"),
            "compaction.enabled": False,
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
        },
    )


@pytest.mark.asyncio
async def test_print_mode_text_outputs_only_final_assistant_text(
    model: Model,
    tmp_path: Path,
) -> None:
    provider = FauxProvider(
        [AssistantMessage(content=[TextContent("final answer")], stop_reason="stop")]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                include_coding_tools=False,
            )
        )
    ).session
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = await run_print_mode(
        session,
        PrintModeOptions(initial_message="hello", stdout=stdout, stderr=stderr),
    )

    assert status == 0
    assert stdout.getvalue() == "final answer\n"
    assert stderr.getvalue() == ""


@pytest.mark.asyncio
async def test_print_mode_json_starts_with_session_header_and_streams_events(
    model: Model,
    tmp_path: Path,
) -> None:
    provider = FauxProvider([AssistantMessage(content=[TextContent("done")], stop_reason="stop")])
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                include_coding_tools=False,
            )
        )
    ).session
    stdout = io.StringIO()

    status = await run_print_mode(
        session,
        PrintModeOptions(
            output_mode="json",
            initial_message="hello",
            stdout=stdout,
            stderr=io.StringIO(),
        ),
    )

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert status == 0
    assert lines[0]["type"] == "session"
    assert lines[0]["id"] == session.session_id
    assert lines[1]["type"] == "agent_start"
    assert lines[-1]["type"] == "agent_settled"
    updates = [line for line in lines if line["type"] == "message_update"]
    assert updates
    assert all("partial" not in line["assistantMessageEvent"] for line in updates)


def test_json_event_tool_start_adds_stable_call_identity(model: Model) -> None:
    partial = AssistantMessage(
        content=[ToolCall("call-1", "read", {})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="pending",
    )
    event = MessageUpdateEvent(
        partial,
        ToolCallStartEvent(0, partial),
    )

    encoded = to_json_event(event)

    assert encoded["assistantMessageEvent"] == {
        "type": "toolcall_start",
        "contentIndex": 0,
        "id": "call-1",
        "toolName": "read",
    }
    assert encoded["usage"]["totalTokens"] == 0


def test_jsonl_helpers_enforce_one_object_per_line() -> None:
    line = serialize_json_line({"type": "response", "text": "a\nb"})
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert parse_json_line(line) == {"type": "response", "text": "a\nb"}

    with pytest.raises(ValueError, match="JSON object"):
        parse_json_line("[]\n")
    with pytest.raises(ValueError, match="must not be empty"):
        parse_json_line("\n")
