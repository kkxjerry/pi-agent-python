from __future__ import annotations

import asyncio
import copy
import fnmatch
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken

from .policy import ToolPolicy, canonical_arguments, classify_request
from .types import (
    ApprovalGrant,
    ApprovalHandler,
    ApprovalResponse,
    PolicyDecision,
    SecurityAuditRecord,
    ToolDeniedError,
    ToolRequest,
)

AuditSink = Callable[[SecurityAuditRecord], None]


@dataclass(slots=True)
class StaticApprovalHandler(ApprovalHandler):
    response: ApprovalResponse
    requests: list[ToolRequest] = field(default_factory=list)

    async def request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResponse:
        del decision
        self.requests.append(copy.deepcopy(request))
        return self.response


class ConsoleApprovalHandler(ApprovalHandler):
    def __init__(
        self,
        *,
        stdin: TextIO = sys.stdin,
        stdout: TextIO = sys.stderr,
    ) -> None:
        self.stdin = stdin
        self.stdout = stdout

    async def request_approval(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResponse:
        return await asyncio.to_thread(self._request_sync, request, decision)

    def _request_sync(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
    ) -> ApprovalResponse:
        self.stdout.write(
            f"\nTool approval required [{decision.risk}]: {request.tool_name}\n"
            f"Arguments: {canonical_arguments(_redact_sensitive(request.arguments))}\n"
            f"Reason: {decision.reason}\n"
            "Allow? [y/N/a=always exact/p=pattern]: "
        )
        self.stdout.flush()
        answer = self.stdin.readline().strip().casefold()
        if answer in {"y", "yes"}:
            return ApprovalResponse(True, "once")
        if answer in {"a", "always"}:
            # Exact means structural equality. It is deliberately not stored as
            # an fnmatch pattern, even when argument strings contain * or ?.
            return ApprovalResponse(True, "session_exact")
        if answer in {"p", "pattern"}:
            self.stdout.write("Argument glob pattern: ")
            self.stdout.flush()
            pattern = self.stdin.readline().strip()
            if not pattern:
                return ApprovalResponse(False, reason="empty approval pattern")
            return ApprovalResponse(True, "session_pattern", pattern=pattern)
        return ApprovalResponse(False, reason="user denied tool call")


class ApprovalManager:
    def __init__(
        self,
        policy: ToolPolicy | None = None,
        handler: ApprovalHandler | None = None,
        *,
        session_id: str | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.policy = policy or ToolPolicy()
        self.handler = handler or StaticApprovalHandler(ApprovalResponse(False))
        self.session_id = session_id
        self.audit_sink = audit_sink
        self._grants: list[ApprovalGrant] = []
        self._audit: list[SecurityAuditRecord] = []
        self._lock = asyncio.Lock()

    @property
    def grants(self) -> tuple[ApprovalGrant, ...]:
        return tuple(copy.deepcopy(self._grants))

    @property
    def audit_records(self) -> tuple[SecurityAuditRecord, ...]:
        return tuple(copy.deepcopy(self._audit))

    def clear_grants(self) -> None:
        self._grants.clear()

    async def authorize(self, request: ToolRequest) -> PolicyDecision:
        normalized = ToolRequest(
            tool_name=request.tool_name,
            arguments=copy.deepcopy(request.arguments),
            session_id=request.session_id or self.session_id,
            cwd=request.cwd,
            risk=classify_request(request),
        )
        grant = self._matching_grant(normalized)
        if grant is not None:
            decision = PolicyDecision(
                "allow",
                f"matched session {grant.matcher} approval",
                risk=normalized.risk,
            )
            self._record(normalized, decision, None)
            return decision

        decision = self.policy.evaluate(normalized)
        if decision.effect == "allow":
            self._record(normalized, decision, None)
            return decision
        if decision.effect == "deny":
            self._record(normalized, decision, None)
            raise ToolDeniedError(decision.reason)

        async with self._lock:
            # Another concurrent call may have established a session grant.
            grant = self._matching_grant(normalized)
            if grant is not None:
                allowed = PolicyDecision(
                    "allow",
                    f"matched session {grant.matcher} approval",
                    risk=normalized.risk,
                )
                self._record(normalized, allowed, None)
                return allowed
            response = await self.handler.request_approval(normalized, decision)
            final = PolicyDecision(
                "allow" if response.approved else "deny",
                response.reason or ("approved by user" if response.approved else "denied by user"),
                risk=normalized.risk,
            )
            if response.approved:
                self._remember(normalized, response)
                self._record(normalized, final, response)
                return final
            self._record(normalized, final, response)
            raise ToolDeniedError(final.reason)

    def guard_tool(self, tool: AgentTool) -> AgentTool:
        async def execute(
            call_id: str,
            arguments: dict[str, Any],
            signal: CancellationToken | None,
            update: Callable[[AgentToolResult], None],
        ) -> AgentToolResult:
            await self.authorize(
                ToolRequest(
                    tool.name,
                    copy.deepcopy(arguments),
                    session_id=self.session_id,
                )
            )
            return await tool.execute(call_id, arguments, signal, update)

        return AgentTool(
            name=tool.name,
            label=tool.label,
            description=tool.description,
            parameters=copy.deepcopy(tool.parameters),
            execute=execute,
            prepare_arguments=tool.prepare_arguments,
            execution_mode=tool.execution_mode,
            timeout=tool.timeout,
            constrained_sampling=copy.deepcopy(tool.constrained_sampling),
        )

    def guard_tools(self, tools: Sequence[AgentTool]) -> list[AgentTool]:
        return [self.guard_tool(tool) for tool in tools]

    def _matching_grant(self, request: ToolRequest) -> ApprovalGrant | None:
        for grant in reversed(self._grants):
            if grant.tool_name != request.tool_name:
                continue
            if grant.matcher == "exact" and grant.arguments == request.arguments:
                return grant
            if (
                grant.matcher == "glob"
                and grant.pattern is not None
                and fnmatch.fnmatchcase(
                    canonical_arguments(request.arguments),
                    grant.pattern,
                )
            ):
                return grant
        return None

    def _remember(self, request: ToolRequest, response: ApprovalResponse) -> None:
        if response.scope == "once":
            return
        if response.scope == "session_exact":
            self._grants.append(
                ApprovalGrant(
                    request.tool_name,
                    "exact",
                    copy.deepcopy(request.arguments),
                )
            )
            return
        if response.scope == "session_pattern":
            if not response.pattern:
                raise ValueError("session_pattern approval requires an explicit pattern")
            self._grants.append(
                ApprovalGrant(
                    request.tool_name,
                    "glob",
                    {},
                    pattern=response.pattern,
                )
            )

    def _record(
        self,
        request: ToolRequest,
        decision: PolicyDecision,
        approval: ApprovalResponse | None,
    ) -> None:
        import time

        record = SecurityAuditRecord(time.time(), request, decision, approval)
        self._audit.append(record)
        if self.audit_sink is not None:
            self.audit_sink(record)


class JsonlAuditSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def __call__(self, record: SecurityAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "timestamp": record.timestamp,
            "request": {
                "tool": record.request.tool_name,
                "arguments": _redact_sensitive(record.request.arguments),
                "sessionId": record.request.session_id,
                "cwd": record.request.cwd,
                "risk": record.request.risk,
            },
            "decision": {
                "effect": record.decision.effect,
                "reason": record.decision.reason,
                "ruleId": record.decision.rule_id,
                "risk": record.decision.risk,
            },
            "approval": (
                {
                    "approved": record.approval.approved,
                    "scope": record.approval.scope,
                    "pattern": record.approval.pattern,
                    "reason": record.approval.reason,
                }
                if record.approval is not None
                else None
            ),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


def _redact_sensitive(value: Any, *, key: str = "") -> Any:
    normalized = key.casefold().replace("-", "_")
    if normalized in {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
    }:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_sensitive(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item) for item in value)
    return value
