from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pi_agent.ai import (
    AssistantMessage,
    AssistantMessageEventStream,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    UserMessage,
)
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.coding_agent.modes.rpc import RpcServer, run_rpc_mode


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


class GatedProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.requests: list[Context] = []

    def stream(
        self,
        model: Model,
        context: Context,
        _options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = AssistantMessageEventStream()
        call = self.calls
        self.calls += 1
        self.requests.append(context)

        async def run() -> None:
            try:
                if call == 0:
                    self.started.set()
                    await self.release.wait()
                text = "first" if call == 0 else "followed"
                partial = AssistantMessage(
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                    stop_reason="pending",
                )
                stream.push(StartEvent(partial))
                partial.content.append(TextContent(""))
                stream.push(TextStartEvent(0, partial))
                partial.content[0].text = text
                stream.push(TextDeltaEvent(0, text, partial))
                stream.push(TextEndEvent(0, text, partial))
                stream.push(
                    DoneEvent(
                        "stop",
                        AssistantMessage(
                            content=[TextContent(text)],
                            api=model.api,
                            provider=model.provider,
                            model=model.id,
                            stop_reason="stop",
                        ),
                    )
                )
            finally:
                stream.end()

        task = asyncio.create_task(run())
        stream.attach_task(task)
        return stream


@pytest.mark.asyncio
async def test_rpc_prompt_runs_in_background_and_accepts_follow_up(
    model: Model,
    tmp_path: Path,
) -> None:
    provider = GatedProvider()
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
    output: list[dict[str, Any]] = []
    server = RpcServer(session, output.append)
    server.start()

    await server.handle_request({"id": 1, "type": "prompt", "message": "start"})
    assert output[0] == {
        "type": "response",
        "command": "prompt",
        "success": True,
        "id": 1,
        "data": {"accepted": True},
    }
    await provider.started.wait()

    await server.handle_request({"id": 2, "type": "get_state"})
    state_response = next(item for item in output if item.get("id") == 2)
    assert state_response["data"]["isStreaming"] is True

    await server.handle_request({"id": 3, "type": "follow_up", "message": "next"})
    queued_response = next(item for item in output if item.get("id") == 3)
    assert queued_response["data"] == {"queued": "follow_up"}

    provider.release.set()
    await server.close()

    assert provider.calls == 2
    users = [
        message.content
        for message in provider.requests[1].messages
        if isinstance(message, UserMessage)
    ]
    assert users == ["start", "next"]
    event_types = [item.get("type") for item in output if item.get("type") != "response"]
    assert "agent_start" in event_types
    assert event_types[-1] == "agent_settled"


@pytest.mark.asyncio
async def test_rpc_reports_invalid_json_unknown_commands_and_state(
    model: Model,
    tmp_path: Path,
) -> None:
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unused")),
                settings=settings(tmp_path),
                include_coding_tools=False,
            )
        )
    ).session
    output: list[dict[str, Any]] = []
    server = RpcServer(session, output.append)
    server.start()

    await server.handle_line("not json\n")
    await server.handle_request({"id": "x", "type": "missing"})
    await server.handle_request(
        {
            "id": "i",
            "type": "prompt",
            "message": "image",
            "images": [{"data": "not-base64", "mimeType": "image/png"}],
        }
    )
    await server.handle_request({"id": "s", "type": "get_state"})
    await server.handle_request({"id": "n", "type": "set_session_name", "name": "demo"})
    await server.close()

    assert output[0]["success"] is False
    assert next(item for item in output if item.get("id") == "x")["success"] is False
    assert next(item for item in output if item.get("id") == "i")["success"] is False
    state = next(item for item in output if item.get("id") == "s")["data"]
    assert state["sessionId"] == session.session_id
    assert state["isIdle"] is True
    renamed = next(item for item in output if item.get("id") == "n")
    assert renamed["success"] is True
    assert session.session_name == "demo"


@pytest.mark.asyncio
async def test_run_rpc_mode_uses_lf_delimited_json_objects(
    model: Model,
    tmp_path: Path,
) -> None:
    from pi_agent.ai import FauxProvider

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
    written: list[str] = []

    async def reader() -> AsyncIterator[str]:
        yield json.dumps({"id": 1, "type": "prompt", "message": "hello"}) + "\n"
        yield json.dumps({"id": 2, "type": "get_state"}) + "\n"

    await run_rpc_mode(session, reader(), written.append)

    assert written
    assert all(line.endswith("\n") and line.count("\n") == 1 for line in written)
    decoded = [json.loads(line) for line in written]
    assert any(item.get("id") == 1 and item.get("success") is True for item in decoded)
    assert any(item.get("type") == "agent_end" for item in decoded)


@pytest.mark.asyncio
async def test_rpc_shutdown_stops_before_reading_later_commands(
    model: Model,
    tmp_path: Path,
) -> None:
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unused")),
                settings=settings(tmp_path),
                include_coding_tools=False,
            )
        )
    ).session
    written: list[str] = []
    consumed: list[int] = []

    async def reader() -> AsyncIterator[str]:
        consumed.append(1)
        yield json.dumps({"id": 1, "type": "shutdown"}) + "\n"
        consumed.append(2)
        yield json.dumps({"id": 2, "type": "get_state"}) + "\n"

    await run_rpc_mode(session, reader(), written.append)

    decoded = [json.loads(line) for line in written]
    assert consumed == [1]
    assert decoded == [
        {
            "type": "response",
            "command": "shutdown",
            "success": True,
            "id": 1,
            "data": {"shutdown": True},
        }
    ]
