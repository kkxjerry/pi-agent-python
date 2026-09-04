from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.tui import InteractiveApp, InteractiveAppOptions, MemoryTerminal, Size


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
async def test_memory_terminal_runs_real_agent_session(tmp_path: Path) -> None:
    model = Model(
        api="fixture",
        provider="fixture",
        id="fixture-model",
        name="Fixture",
        context_window=8192,
        max_tokens=1024,
    )
    provider = FauxProvider(
        [AssistantMessage(content=[TextContent("terminal answer")], stop_reason="stop")]
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
    terminal = MemoryTerminal(size=Size(60, 12))
    app = InteractiveApp(
        session,
        terminal=terminal,
        options=InteractiveAppOptions(initial_prompt="hello"),
    )

    status = await app.run()

    assert status == 0
    assert [message.role for message in session.transcript] == ["user", "assistant"]
    rendered = "".join(terminal.output)
    assert "terminal answer" in rendered
    assert terminal.flush_count > 0
    await session.close()
