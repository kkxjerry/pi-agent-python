from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent, ToolCall
from pi_agent.coding_agent import AgentSessionOptions, create_agent_session
from pi_agent.harness import ResourceLoader, SummaryResult, create_local_execution_env


def text(value: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(value)], stop_reason="stop")


def tool(call_id: str, name: str, arguments: dict[str, Any]) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCall(call_id, name, arguments)],
        stop_reason="toolUse",
    )


@pytest.mark.asyncio
async def test_agent_session_persists_vertical_tool_run_and_resumes(
    model: Model,
    tmp_path: Path,
) -> None:
    path = tmp_path / "session.jsonl"
    provider = FauxProvider(
        [
            tool("write-1", "write", {"path": "note.txt", "content": "hello\n"}),
            tool("read-1", "read", {"path": "note.txt"}),
            text("finished"),
        ]
    )
    session = await create_agent_session(
        AgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=provider.stream,
            env=create_local_execution_env(tmp_path),
            session_path=path,
            resource_loader=ResourceLoader(cwd=tmp_path, user_dir=tmp_path / "missing"),
        )
    )

    result = await session.prompt("Create and read note.txt")

    assert result.succeeded is True
    assert (tmp_path / "note.txt").read_text() == "hello\n"
    assert [message.role for message in session.transcript] == [
        "user",
        "assistant",
        "toolResult",
        "assistant",
        "toolResult",
        "assistant",
    ]
    original_id = session.session_manager.info.id
    await session.close()

    resumed = await create_agent_session(
        AgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=FauxProvider([text("continued")]).stream,
            env=create_local_execution_env(tmp_path),
            session_path=path,
            resource_loader=ResourceLoader(cwd=tmp_path, user_dir=tmp_path / "missing"),
        )
    )
    assert resumed.session_manager.info.id == original_id
    assert [message.role for message in resumed.transcript][-1] == "assistant"
    await resumed.close()


@pytest.mark.asyncio
async def test_agent_session_explicit_compaction_and_tree_navigation(
    model: Model,
    tmp_path: Path,
) -> None:
    async def summarizer(
        _messages: list[Any], _previous: str | None, _signal: Any
    ) -> SummaryResult:
        return SummaryResult("stable summary")

    session = await create_agent_session(
        AgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=FauxProvider([text("first"), text("branch")]).stream,
            no_session=True,
            summarizer=summarizer,
            resource_loader=ResourceLoader(cwd=tmp_path, user_dir=tmp_path / "missing"),
        )
    )
    await session.prompt("start")
    first_leaf = session.session_manager.current_id
    compacted = await session.compact()

    assert compacted is not None
    assert compacted["summary"] == "stable summary"
    assert session.session_manager.active_entries()[-1].type == "compaction"
    await session.navigate_tree(first_leaf)
    await session.prompt("alternate")

    tree = session.get_tree()
    assert tree
    assert session.get_state()["session"]["entryCount"] >= 4
    await session.close()


@pytest.mark.asyncio
async def test_agent_session_model_thinking_and_resource_reload(
    model: Model,
    tmp_path: Path,
) -> None:
    root = tmp_path / ".pi"
    root.mkdir()
    (root / "SYSTEM.md").write_text("first system")
    loader = ResourceLoader(cwd=tmp_path, user_dir=tmp_path / "missing")
    session = await create_agent_session(
        AgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=FauxProvider([]).stream,
            no_session=True,
            resource_loader=loader,
        )
    )
    replacement = Model(
        api=model.api,
        provider=model.provider,
        id="replacement",
        name="replacement",
        base_url=model.base_url,
        context_window=model.context_window,
        max_tokens=model.max_tokens,
    )

    await session.switch_model(replacement)
    await session.set_thinking_level("high")
    (root / "SYSTEM.md").write_text("second system")
    await session.reload_resources()

    state = session.get_state()
    assert state["model"]["id"] == "replacement"
    assert state["thinkingLevel"] == "high"
    assert "second system" in session.agent.system_prompt
    assert [entry.type for entry in session.session_manager.active_entries()][-2:] == [
        "model_change",
        "thinking_level_change",
    ]
    await session.close()
