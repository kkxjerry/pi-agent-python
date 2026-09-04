from __future__ import annotations

import asyncio
from pathlib import Path

from pi_agent.coding_agent import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalPolicy,
    ApprovalRequest,
)


async def prompt(request: ApprovalRequest) -> ApprovalDecision:
    print(f"{request.risk}: {request.tool_name}")
    print("; ".join(request.reasons))
    response = input("Allow once? [y/N] ").strip().lower()
    return (
        ApprovalDecision("allow_once", "approved by operator")
        if response == "y"
        else ApprovalDecision("deny", "denied by operator")
    )


async def main() -> None:
    gate = ApprovalGate(
        ApprovalPolicy(Path.cwd()),
        session_id="example-session",
        prompt=prompt,
    )
    print(
        "Created approval gate for "
        f"{gate.session_id}; pass it through "
        "CreateAgentSessionOptions(approval_gate=gate)."
    )


if __name__ == "__main__":
    asyncio.run(main())
