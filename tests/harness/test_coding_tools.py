from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken, TextContent
from pi_agent.harness import (
    create_bash_tool,
    create_edit_tool,
    create_local_execution_env,
    create_read_tool,
    create_write_tool,
)


async def execute(tool: AgentTool, arguments: dict[str, Any]) -> AgentToolResult:
    return await tool.execute("call-1", arguments, None, lambda _update: None)


@pytest.mark.asyncio
async def test_write_read_and_edit_tools_use_atomic_utf8_files(tmp_path: Path) -> None:
    env = create_local_execution_env(tmp_path)
    write = create_write_tool(env)
    read = create_read_tool(env)
    edit = create_edit_tool(env)

    written = await execute(write, {"path": "sample.txt", "content": "alpha\nbeta\ngamma\n"})
    assert (tmp_path / "sample.txt").read_text() == "alpha\nbeta\ngamma\n"
    assert written.details["existed"] is False

    selected = await execute(read, {"path": "sample.txt", "offset": 2, "limit": 2})
    assert selected.content == [TextContent("sample.txt (lines 2-3 of 3)\n2: beta\n3: gamma")]

    changed = await execute(
        edit,
        {
            "path": "sample.txt",
            "edits": [
                {"oldText": "alpha", "newText": "ALPHA"},
                {"oldText": "gamma", "newText": "GAMMA"},
            ],
        },
    )
    assert changed.details["changed"] is True
    assert (tmp_path / "sample.txt").read_text() == "ALPHA\nbeta\nGAMMA\n"


@pytest.mark.asyncio
async def test_edit_tool_is_all_or_nothing_and_rejects_ambiguous_matches(tmp_path: Path) -> None:
    env = create_local_execution_env(tmp_path)
    path = tmp_path / "sample.txt"
    path.write_text("same\nsame\nuntouched\n")
    edit = create_edit_tool(env)

    with pytest.raises(ValueError, match="matched 2 locations"):
        await execute(edit, {"path": "sample.txt", "oldText": "same", "newText": "changed"})
    assert path.read_text() == "same\nsame\nuntouched\n"

    with pytest.raises(ValueError, match="was not found"):
        await execute(
            edit,
            {
                "path": "sample.txt",
                "edits": [
                    {"oldText": "untouched", "newText": "changed"},
                    {"oldText": "missing", "newText": "never"},
                ],
            },
        )
    assert path.read_text() == "same\nsame\nuntouched\n"


@pytest.mark.asyncio
async def test_read_rejects_binary_files(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"hello\x00world")
    read = create_read_tool(create_local_execution_env(tmp_path))
    with pytest.raises(ValueError, match="Binary files"):
        await execute(read, {"path": "binary.bin"})


@pytest.mark.asyncio
async def test_bash_streams_progress_and_returns_exit_status(tmp_path: Path) -> None:
    env = create_local_execution_env(tmp_path)
    bash = create_bash_tool(env, default_timeout=5)
    updates: list[AgentToolResult] = []
    result = await bash.execute(
        "call-1",
        {"command": "printf 'out'; printf 'err' >&2; exit 3"},
        None,
        updates.append,
    )

    assert result.details["exitCode"] == 3
    assert result.details["stdout"] == "out"
    assert result.details["stderr"] == "err"
    assert "Process exited with code 3" in result.content[0].text
    assert {update.details["stream"] for update in updates} == {"stdout", "stderr"}


@pytest.mark.asyncio
async def test_local_shell_honors_timeout_and_cancellation(tmp_path: Path) -> None:
    env = create_local_execution_env(tmp_path)
    bash = create_bash_tool(env, default_timeout=1)
    with pytest.raises(TimeoutError, match="timed out"):
        await bash.execute(
            "call-1",
            {"command": "sleep 2", "timeout": 1},
            None,
            lambda _update: None,
        )

    token = CancellationToken()
    task = asyncio.create_task(
        bash.execute("call-2", {"command": "sleep 5"}, token, lambda _update: None)
    )
    await asyncio.sleep(0.05)
    token.cancel("cancel shell")
    with pytest.raises(asyncio.CancelledError):
        await task
