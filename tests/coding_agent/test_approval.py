from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pi_agent.agent import AgentContext, AgentTool, AgentToolResult, BeforeToolCallContext
from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    FauxProvider,
    Model,
    TextContent,
    ToolCall,
)
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session
from pi_agent.coding_agent.approval import (
    ApprovalAuditSink,
    ApprovalDecision,
    ApprovalGate,
    ApprovalPolicy,
)


def context(name: str, arguments: dict[str, Any]) -> BeforeToolCallContext:
    call = ToolCall("call-1", name, arguments)
    assistant = AssistantMessage(content=[call], stop_reason="toolUse")
    return BeforeToolCallContext(
        assistant,
        call,
        arguments,
        AgentContext(system_prompt="", messages=[], tools=[]),
    )


@pytest.mark.asyncio
async def test_policy_allows_reads_prompts_writes_and_denies_dangerous_shell(
    tmp_path: Path,
) -> None:
    prompted: list[str] = []

    async def prompt(request):
        prompted.append(request.tool_name)
        return ApprovalDecision("allow_session", "approved for this session")

    gate = ApprovalGate(
        ApprovalPolicy(tmp_path),
        session_id="session",
        prompt=prompt,
    )
    assert await gate.before_tool_call(context("read", {"path": "a.py"}), None) is None
    write = context("write", {"path": "a.py", "content": "one"})
    assert await gate.before_tool_call(write, None) is None
    assert await gate.before_tool_call(write, None) is None
    assert prompted == ["write"]

    denied = await gate.before_tool_call(
        context("bash", {"command": "rm -rf /"}),
        None,
    )
    assert denied is not None and denied.block is True
    assert "destructive" in str(denied.reason)


@pytest.mark.asyncio
async def test_gate_fails_closed_for_missing_prompt_outside_path_and_audit_failure(
    tmp_path: Path,
) -> None:
    gate = ApprovalGate(ApprovalPolicy(tmp_path), session_id="session")
    write = await gate.before_tool_call(context("write", {"path": "a.py"}), None)
    assert write is not None and "no prompt" in str(write.reason)
    outside = await gate.before_tool_call(
        context("write", {"path": str(tmp_path.parent / "outside.txt")}),
        None,
    )
    assert outside is not None and "outside approved roots" in str(outside.reason)


@pytest.mark.asyncio
async def test_audit_sink_records_decision_without_arguments(tmp_path: Path) -> None:
    path = tmp_path / "approval.jsonl"
    gate = ApprovalGate(
        ApprovalPolicy(tmp_path),
        session_id="session",
        prompt=lambda _request: ApprovalDecision("allow_once", "ok"),
        audit_sink=ApprovalAuditSink(path),
    )
    assert (
        await gate.before_tool_call(
            context("write", {"path": "a.py", "content": "secret body"}),
            None,
        )
        is None
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["decision"] == "allow_once"
    assert "arguments" not in record
    assert "secret body" not in path.read_text(encoding="utf-8")


def settings(tmp_path: Path):
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
async def test_sdk_approval_gate_blocks_tool_before_execution(
    model: Model,
    tmp_path: Path,
) -> None:
    executed = False

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        nonlocal executed
        executed = True
        return AgentToolResult([TextContent("executed")])

    tool = AgentTool(
        name="write_custom",
        label="Write",
        description="mutate",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(
                content=[ToolCall("call-1", "write_custom", {})],
                stop_reason="toolUse",
            ),
            AssistantMessage(content=[TextContent("handled")], stop_reason="stop"),
        ]
    )
    gate = ApprovalGate(ApprovalPolicy(tmp_path), session_id="pending")
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=tmp_path,
                model=model,
                stream_fn=provider.stream,
                settings=settings(tmp_path),
                no_session=True,
                include_coding_tools=False,
                tools=[tool],
                approval_gate=gate,
            )
        )
    ).session

    await session.prompt("try mutation")

    assert executed is False
    assert gate.session_id == session.session_id
    await session.close()
