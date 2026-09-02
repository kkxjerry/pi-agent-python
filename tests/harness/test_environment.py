from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from pi_agent.ai import CancellationToken
from pi_agent.harness.environment import LocalExecutionEnv
from pi_agent.harness.result import get_or_raise
from pi_agent.harness.types import ShellExecOptions


@pytest.mark.asyncio
async def test_local_filesystem_contract_round_trip(tmp_path: Path) -> None:
    env = LocalExecutionEnv(str(tmp_path))

    absolute = get_or_raise(await env.absolute_path("nested/file.txt"))
    assert absolute == str(tmp_path / "nested" / "file.txt")
    get_or_raise(await env.write_file("nested/file.txt", "hello\nworld"))
    get_or_raise(await env.append_file("nested/file.txt", "\nagain"))
    assert get_or_raise(await env.read_text_file("nested/file.txt")) == "hello\nworld\nagain"
    assert get_or_raise(await env.read_text_lines("nested/file.txt", max_lines=2)) == [
        "hello",
        "world",
    ]

    info = get_or_raise(await env.file_info("nested/file.txt"))
    assert info.kind == "file"
    assert info.name == "file.txt"
    assert {entry.name for entry in get_or_raise(await env.list_dir("nested"))} == {"file.txt"}
    assert get_or_raise(await env.canonical_path("nested/file.txt")) == absolute

    get_or_raise(await env.rename_file("nested/file.txt", "renamed.txt"))
    assert get_or_raise(await env.exists("nested/file.txt")) is False
    assert get_or_raise(await env.exists("renamed.txt")) is True
    get_or_raise(await env.remove("renamed.txt"))
    assert get_or_raise(await env.exists("renamed.txt")) is False

    missing = await env.read_text_file("missing.txt")
    assert missing.ok is False
    assert missing.error.code == "not_found"


@pytest.mark.asyncio
async def test_filesystem_decodes_invalid_utf8_like_node_textdecoder(tmp_path: Path) -> None:
    env = LocalExecutionEnv(str(tmp_path))
    (tmp_path / "invalid.bin").write_bytes(b"a\xffb")

    result = get_or_raise(await env.read_text_file("invalid.bin"))

    assert result == "a\ufffdb"


@pytest.mark.asyncio
async def test_filesystem_cancellation_and_temp_cleanup(tmp_path: Path) -> None:
    env = LocalExecutionEnv(str(tmp_path))
    token = CancellationToken()
    token.cancel("stop")

    aborted = await env.write_file("blocked.txt", "no", token)
    assert aborted.ok is False
    assert aborted.error.code == "aborted"
    assert not (tmp_path / "blocked.txt").exists()

    temp_file = get_or_raise(await env.create_temp_file(prefix="pi-", suffix=".log"))
    temp_dir = get_or_raise(await env.create_temp_dir(prefix="pi-"))
    assert os.path.exists(temp_file)
    assert os.path.isdir(temp_dir)

    await env.cleanup()

    assert not os.path.exists(temp_file)
    assert not os.path.exists(temp_dir)


@pytest.mark.asyncio
async def test_shell_streams_output_and_returns_nonzero_without_throwing(tmp_path: Path) -> None:
    env = LocalExecutionEnv(str(tmp_path))
    stdout: list[str] = []
    stderr: list[str] = []

    result = await env.exec(
        "printf out; printf err >&2; exit 7",
        ShellExecOptions(on_stdout=stdout.append, on_stderr=stderr.append),
    )

    assert result.ok is True
    assert result.value.exit_code == 7
    assert result.value.stdout == "out"
    assert result.value.stderr == "err"
    assert "".join(stdout) == "out"
    assert "".join(stderr) == "err"


@pytest.mark.asyncio
async def test_shell_timeout_abort_and_callback_failure_are_structured(tmp_path: Path) -> None:
    env = LocalExecutionEnv(str(tmp_path))

    timed_out = await env.exec("sleep 1", ShellExecOptions(timeout=0.02))
    assert timed_out.ok is False
    assert timed_out.error.code == "timeout"

    token = CancellationToken()
    running = asyncio.create_task(env.exec("sleep 5", ShellExecOptions(signal=token)))
    await asyncio.sleep(0.02)
    token.cancel("cancelled by test")
    aborted = await running
    assert aborted.ok is False
    assert aborted.error.code == "aborted"
    assert str(aborted.error) == "cancelled by test"

    def fail_callback(_chunk: str) -> None:
        raise RuntimeError("consumer failed")

    callback_error = await env.exec(
        "printf output; sleep 1",
        ShellExecOptions(on_stdout=fail_callback),
    )
    assert callback_error.ok is False
    assert callback_error.error.code == "callback_error"
    assert str(callback_error.error) == "consumer failed"

    quick_callback_error = await env.exec(
        "printf output",
        ShellExecOptions(on_stdout=fail_callback),
    )
    assert quick_callback_error.ok is False
    assert quick_callback_error.error.code == "callback_error"

    await env.cleanup()
