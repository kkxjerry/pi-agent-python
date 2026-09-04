from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

DecisionEffect: TypeAlias = Literal["allow", "deny", "ask"]
ApprovalScope: TypeAlias = Literal["once", "session_exact", "session_pattern"]
RuleMatcher: TypeAlias = Literal["exact", "glob"]
RiskLevel: TypeAlias = Literal["low", "medium", "high", "critical"]


@dataclass(slots=True, frozen=True)
class ToolRequest:
    tool_name: str
    arguments: dict[str, Any]
    session_id: str | None = None
    cwd: str | None = None
    risk: RiskLevel = "medium"


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    effect: DecisionEffect
    reason: str
    rule_id: str | None = None
    risk: RiskLevel = "medium"


@dataclass(slots=True, frozen=True)
class ApprovalResponse:
    approved: bool
    scope: ApprovalScope = "once"
    pattern: str | None = None
    reason: str = ""


@dataclass(slots=True, frozen=True)
class PolicyRule:
    id: str
    effect: DecisionEffect
    tool: str = "*"
    matcher: RuleMatcher = "glob"
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    risk: RiskLevel | None = None


@dataclass(slots=True, frozen=True)
class ApprovalGrant:
    tool_name: str
    matcher: RuleMatcher
    arguments: dict[str, Any]
    pattern: str | None = None
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True, frozen=True)
class SecurityAuditRecord:
    timestamp: float
    request: ToolRequest
    decision: PolicyDecision
    approval: ApprovalResponse | None = None


class ApprovalHandler(Protocol):
    async def request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResponse: ...


class SecurityPolicyError(RuntimeError):
    pass


class ToolDeniedError(SecurityPolicyError):
    pass
