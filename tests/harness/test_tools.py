from __future__ import annotations

import base64
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from pi_agent.ai import ImageContent, TextContent
from pi_agent.harness import (
    ExecutionEnv,
    create_bash_tool,
    create_edit_tool,
    create_read_tool,
    create_write_tool,
)


async def run_tool(tool: Any, arguments: dict[str, Any]) -> Any:
    return await tool.execute("call-1", arguments, None, lambda _update: None)


@pytest.mark.asyncio
async def test_write_read_and_paginate_text(tmp_path: Path) -> None:
    env = ExecutionEnv.local(tmp_path)
    write = create_write_tool(env)
    read = create_read_tool(env, default_line_limit=2)

    written = await run_tool(write, {"path": "nested/example.txt", "content": "one\ntwo\nthree\n"})
    first = await run_tool(read, {"path": "nested/example.txt"})
    second = await run_tool(read, {"path": "nested/example.txt", "offset": 3, "limit": 2})

    assert (tmp_path / "nested/example.txt").read_text() == "one\ntwo\nthree\n"
    assert written.details["created"] is True
    assert isinstance(first.content[0], TextContent)
    assert first.content[0].text.startswith("one\ntwo\n")
    assert "Use offset=3" in first.content[0].text
    assert second.content == [TextContent("three\n")]


@pytest.mark.asyncio
async def test_read_supported_image_as_base64(tmp_path: Path) -> None:
    raw = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)
    (tmp_path / "image.png").write_bytes(raw)
    result = await run_tool(create_read_tool(ExecutionEnv.local(tmp_path)), {"path": "image.png"})

    assert result.content == [
        TextContent("Read image file [image/png]"),
        ImageContent(base64.b64encode(raw).decode("ascii"), "image/png"),
    ]


@pytest.mark.asyncio
async def test_edit_requires_unique_match_and_preserves_crlf(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_bytes(b"alpha\r\nbeta\r\n")
    tool = create_edit_tool(ExecutionEnv.local(tmp_path))

    result = await run_tool(
        tool,
        {"path": "example.txt", "oldText": "alpha\nbeta", "newText": "alpha\ngamma"},
    )
    assert result.details["replacements"] == 1
    assert path.read_bytes() == b"alpha\r\ngamma\r\n"

    path.write_text("x x")
    with pytest.raises(ValueError, match="matched 2 locations"):
        await run_tool(tool, {"path": "example.txt", "oldText": "x", "newText": "y"})


@pytest.mark.asyncio
async def test_bash_captures_output_exit_code_and_progress(tmp_path: Path) -> None:
    env = ExecutionEnv.local(tmp_path)
    tool = create_bash_tool(env)
    updates: list[Any] = []
    command = (
        f"{shlex.quote(sys.executable)} -c "
        '\'import sys; print("out"); print("err", file=sys.stderr)\''
    )

    result = await tool.execute("call-1", {"command": command}, None, updates.append)

    assert result.details["exitCode"] == 0
    assert "out" in result.details["stdout"]
    assert "err" in result.details["stderr"]
    assert "out" in result.content[0].text
    assert "err" in result.content[0].text
    assert updates


@pytest.mark.asyncio
async def test_bash_timeout_terminates_process_group(tmp_path: Path) -> None:
    env = ExecutionEnv.local(tmp_path)
    tool = create_bash_tool(env, default_timeout=0.03)
    command = f"{shlex.quote(sys.executable)} -c 'import time; time.sleep(10)'"

    with pytest.raises(TimeoutError, match="timed out"):
        await tool.execute("call-1", {"command": command}, None, lambda _update: None)
