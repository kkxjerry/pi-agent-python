from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent
from pi_agent.coding_agent import (
    CodingAgentRuntime,
    CodingAgentRuntimeOptions,
    CreateAgentSessionOptions,
    SettingsResolver,
    create_agent_session,
)


def _settings(tmp_path: Path):
    return SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(tmp_path / "missing"),
            "compaction.enabled": False,
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
        },
    )


@pytest.mark.asyncio
async def test_single_session_runtime_starts_and_restores_agent(tmp_path: Path) -> None:
    model = Model(
        api="fixture",
        provider="fixture",
        id="fixture",
        name="fixture",
        context_window=8192,
        max_tokens=1024,
    )
    provider = FauxProvider(
        [AssistantMessage(content=[TextContent("after runtime")], stop_reason="stop")]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=provider.stream,
                settings=_settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    original_prompt = session.agent.system_prompt
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(package_root=tmp_path / "runtime"),
    )

    state = await runtime.start()
    assert state.started is True
    assert state.extensions == ()
    await runtime.close()
    assert session.agent.system_prompt == original_prompt

    result = await session.prompt("still usable")
    assert result.succeeded is True
    await session.close()
