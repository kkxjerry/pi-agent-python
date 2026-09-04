from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pi_agent.coding_agent.approval import ApprovalDecision, ApprovalRequest
from pi_agent.tui.approval import TuiApprovalBroker


@pytest.mark.asyncio
async def test_tui_approval_broker_resolves_pending_request() -> None:
    broker = TuiApprovalBroker()
    request = ApprovalRequest(
        request_id="approval-1",
        session_id="session",
        tool_call_id="call-1",
        tool_name="write",
        arguments={"path": "a.py"},
        risk="medium",
        reasons=("filesystem mutation",),
        cwd=Path.cwd(),
        fingerprint="fingerprint",
    )
    task = asyncio.create_task(broker(request))
    await asyncio.sleep(0)
    assert broker.pending == request
    assert broker.decide(ApprovalDecision("allow_once", "ok")) is True
    assert await task == ApprovalDecision("allow_once", "ok")
    assert broker.pending is None


@pytest.mark.asyncio
async def test_tui_approval_broker_cancels_pending_request() -> None:
    broker = TuiApprovalBroker()
    request = ApprovalRequest(
        request_id="approval-2",
        session_id="session",
        tool_call_id="call-2",
        tool_name="bash",
        arguments={"command": "echo ok"},
        risk="high",
        reasons=("process execution",),
        cwd=Path.cwd(),
        fingerprint="fingerprint-2",
    )
    task = asyncio.create_task(broker(request))
    await asyncio.sleep(0)
    broker.cancel_all()
    assert (await task).action == "deny"
