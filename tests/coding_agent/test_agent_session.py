from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent, ToolCall
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.harness import ResourceLoader, ResourceLoaderConfig, create_local_execution_env
from pi_agent.harness.session import SessionRepository


def text(value: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(value)], stop_reason="stop")


def tool(call_id: str, name: str, arguments: dict[str, object]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(call_id, name, arguments)],
        stop_reason="toolUse",
    )


def settings(tmp_path: Path):
    return SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": True,
            "session.directory": str(tmp_path / "sessions"),
            "resources.user_root": str(tmp_path / "missing"),
            "compaction.enabled": False,
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
        },
    )


@pytest.mark.asyncio
async def test_agent_session_persists_tool_run_and_resumes(
    model: Model,
    tmp_path: Path,
) -> None:
    provider = FauxProvider(
        [
            tool("write-1", "write", {"path": "note.txt", "content": "hello\n"}),
            text("finished"),
        ]
    )
    repository = SessionRepository(tmp_path / "sessions")
    result = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=provider.stream,
            env=create_local_execution_env(tmp_path),
            settings=settings(tmp_path),
            session_repository=repository,
        )
    )
    session = result.session

    run = await session.prompt("Create note.txt")

    assert run.succeeded is True
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello\n"
    original_id = session.session_id
    assert [message.role for message in session.transcript][-1] == "assistant"
    await session.close()

    resumed = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=FauxProvider([text("continued")]).stream,
                env=create_local_execution_env(tmp_path),
                settings=settings(tmp_path),
                session_repository=repository,
                session_id=original_id,
            )
        )
    ).session
    assert resumed.session_id == original_id
    assert resumed.get_last_assistant_text() == "finished"
    await resumed.close()


@pytest.mark.asyncio
async def test_agent_session_model_thinking_and_resource_reload(
    model: Model,
    tmp_path: Path,
) -> None:
    root = tmp_path / ".pi"
    root.mkdir()
    (root / "SYSTEM.md").write_text("first system", encoding="utf-8")
    loader = ResourceLoader(ResourceLoaderConfig(cwd=tmp_path, user_root=tmp_path / "missing"))
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=FauxProvider([]).stream,
                no_session=True,
                settings=settings(tmp_path),
                resource_loader=loader,
            )
        )
    ).session
    replacement = Model(
        api=model.api,
        provider=model.provider,
        id="replacement",
        name="replacement",
        base_url=model.base_url,
        reasoning=True,
        context_window=model.context_window,
        max_tokens=model.max_tokens,
    )

    await session.switch_model(replacement)
    await session.set_thinking_level("high")
    (root / "SYSTEM.md").write_text("second system", encoding="utf-8")
    await session.reload_resources()

    state = session.get_state()
    assert state["model"]["id"] == "replacement"
    assert state["thinkingLevel"] == "high"
    assert "second system" in session.agent.system_prompt
    assert [entry["type"] for entry in session.get_entries()][-2:] == [
        "model_change",
        "thinking_level_change",
    ]
    await session.close()
