from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pi_agent.ai import AssistantMessage, FauxProvider, Model, TextContent
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.tui import (
    CommandRouter,
    InteractiveTui,
    KeyEvent,
    MemoryTerminal,
    TuiOptions,
    default_commands,
)


def model() -> Model:
    return Model(
        api="openai-completions",
        provider="fixture",
        id="fixture-model",
        name="fixture-model",
        reasoning=True,
        context_window=32_000,
        max_tokens=4_000,
    )


def settings(tmp_path: Path):
    return SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(tmp_path / "missing"),
            "compaction.enabled": False,
            "compaction.reserve_tokens": 100,
            "compaction.keep_recent_tokens": 200,
            "extensions.enabled": False,
            "models.snapshot_file": str(tmp_path / "models.json"),
            "auth.file": str(tmp_path / "auth.json"),
        },
    )


@pytest.mark.asyncio
async def test_default_command_router_uses_agent_session(tmp_path: Path) -> None:
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model(),
                stream_fn=FauxProvider([]).stream,
                settings=settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    router = CommandRouter(default_commands())

    assert (await router.execute(session, "/session")).status == "ok"
    assert "fixture/fixture-model" in (await router.execute(session, "/model")).message
    assert (await router.execute(session, "/thinking high")).message == "thinking: high"
    assert (await router.execute(session, "/missing")).status == "error"
    assert (await router.execute(session, "/quit")).status == "exit"
    await session.close()


@pytest.mark.asyncio
async def test_interactive_tui_rejects_non_tty(tmp_path: Path) -> None:
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model(),
                stream_fn=FauxProvider([]).stream,
                settings=settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    app = InteractiveTui(session, terminal=MemoryTerminal(tty=False))
    with pytest.raises(RuntimeError, match="requires a TTY"):
        await app.run()
    await session.close()


@pytest.mark.asyncio
async def test_interactive_tui_runs_prompt_and_restores_terminal(tmp_path: Path) -> None:
    provider = FauxProvider(
        [AssistantMessage(content=[TextContent("interactive answer")], stop_reason="stop")]
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model(),
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
            )
        )
    ).session
    terminal = MemoryTerminal(width=60, height=16)
    app = InteractiveTui(
        session,
        terminal=terminal,
        options=TuiOptions(initial_prompt="hello", cwd=tmp_path),
    )

    task = asyncio.create_task(app.run())
    for _ in range(100):
        if session.get_last_assistant_text() == "interactive answer":
            break
        await asyncio.sleep(0.01)
    terminal.feed(KeyEvent("ctrl_d", ctrl=True))
    status = await asyncio.wait_for(task, timeout=3)

    assert status == 0
    assert session.get_last_assistant_text() == "interactive answer"
    assert provider.requests[0].context.messages[-1].content == "hello"
    output = terminal.output()
    assert "\x1b[?1049h" in output
    assert "\x1b[?1049l" in output
    await session.close()
