from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pi_agent.agent import BeforeToolCallContext, BeforeToolCallResult
from pi_agent.ai import CancellationToken

RiskLevel: TypeAlias = Literal["low", "medium", "high", "critical"]
ApprovalAction: TypeAlias = Literal["allow_once", "allow_session", "deny"]
PolicyAction: TypeAlias = Literal["allow", "prompt", "deny"]


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    request_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk: RiskLevel
    reasons: tuple[str, ...]
    cwd: Path
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    action: ApprovalAction
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    action: PolicyAction
    risk: RiskLevel
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalRule:
    action: PolicyAction
    tool_pattern: str = "*"
    path_pattern: str | None = None
    command_pattern: str | None = None
    reason: str = "configured approval rule"

    def matches(self, tool_name: str, arguments: Mapping[str, Any], cwd: Path) -> bool:
        if not fnmatch.fnmatch(tool_name, self.tool_pattern):
            return False
        if self.path_pattern is not None:
            path = _argument_path(arguments, cwd)
            if path is None or not fnmatch.fnmatch(path.as_posix(), self.path_pattern):
                return False
        if self.command_pattern is not None:
            command = arguments.get("command")
            if not isinstance(command, str) or re.search(self.command_pattern, command) is None:
                return False
        return True


@dataclass(slots=True)
class ApprovalPolicy:
    cwd: Path
    allowed_roots: tuple[Path, ...] = ()
    allow_read_tools: bool = True
    prompt_write_tools: bool = True
    prompt_shell_tools: bool = True
    deny_outside_roots: bool = True
    rules: tuple[ApprovalRule, ...] = ()
    dangerous_command_patterns: tuple[str, ...] = (
        r"(?:^|\s)rm\s+-[^\n]*r[^\n]*f",
        r"(?:^|\s)(?:sudo|su)(?:\s|$)",
        r"(?:^|\s)(?:shutdown|reboot|halt)(?:\s|$)",
        r"(?:^|\s)mkfs(?:\.|\s)",
        r"(?:^|\s)dd\s+.*\bof=/dev/",
        r"(?:^|\s)git\s+(?:reset\s+--hard|clean\s+-[^\n]*f)",
    )

    def __post_init__(self) -> None:
        self.cwd = self.cwd.expanduser().resolve()
        self.allowed_roots = tuple(
            dict.fromkeys((self.cwd, *(path.expanduser().resolve() for path in self.allowed_roots)))
        )

    def evaluate(self, tool_name: str, arguments: Mapping[str, Any]) -> PolicyEvaluation:
        for rule in self.rules:
            if rule.matches(tool_name, arguments, self.cwd):
                return PolicyEvaluation(rule.action, _risk_for_action(rule.action), (rule.reason,))

        lowered = tool_name.lower()
        path = _argument_path(arguments, self.cwd)
        if (
            path is not None
            and self.deny_outside_roots
            and not any(_within(path, root) for root in self.allowed_roots)
        ):
            return PolicyEvaluation(
                "deny",
                "critical",
                (f"path is outside approved roots: {path}",),
            )
        if lowered in {"read", "grep", "find", "ls"}:
            return PolicyEvaluation(
                "allow" if self.allow_read_tools else "prompt",
                "low",
                ("read-only filesystem access",),
            )
        if lowered in {"write", "edit", "patch", "apply_patch"}:
            return PolicyEvaluation(
                "prompt" if self.prompt_write_tools else "allow",
                "medium",
                ("filesystem mutation",),
            )
        if lowered in {"bash", "shell", "exec", "command"}:
            command = arguments.get("command")
            if isinstance(command, str):
                matched = [
                    pattern
                    for pattern in self.dangerous_command_patterns
                    if re.search(pattern, command, flags=re.IGNORECASE)
                ]
                if matched:
                    return PolicyEvaluation(
                        "deny",
                        "critical",
                        ("command matches a destructive-operation rule",),
                    )
            return PolicyEvaluation(
                "prompt" if self.prompt_shell_tools else "allow",
                "high",
                ("arbitrary process execution",),
            )
        return PolicyEvaluation(
            "prompt",
            "high",
            ("unknown tool capability",),
        )


@dataclass(frozen=True, slots=True)
class ApprovalAuditRecord:
    timestamp_ms: int
    request_id: str
    session_id: str
    tool_call_id: str
    tool_name: str
    risk: RiskLevel
    policy_action: PolicyAction
    decision: ApprovalAction
    reasons: tuple[str, ...]
    decision_reason: str
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestampMs": self.timestamp_ms,
            "requestId": self.request_id,
            "sessionId": self.session_id,
            "toolCallId": self.tool_call_id,
            "toolName": self.tool_name,
            "risk": self.risk,
            "policyAction": self.policy_action,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "decisionReason": self.decision_reason,
            "fingerprint": self.fingerprint,
        }


class ApprovalAuditSink:
    def __init__(self, path: str | Path, *, fsync: bool = False) -> None:
        self.path = Path(path).expanduser().resolve()
        self.fsync = fsync
        self._lock = threading.Lock()

    async def write(self, record: ApprovalAuditRecord) -> None:
        await asyncio.to_thread(self._write_sync, record)

    def _write_sync(self, record: ApprovalAuditRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as file:
            file.write(line)
            file.flush()
            if self.fsync:
                os.fsync(file.fileno())


ApprovalPrompt: TypeAlias = Callable[
    [ApprovalRequest], ApprovalDecision | Awaitable[ApprovalDecision]
]


class ApprovalGate:
    """Before-tool hook that fails closed and keeps session-scoped approvals."""

    def __init__(
        self,
        policy: ApprovalPolicy,
        *,
        session_id: str,
        prompt: ApprovalPrompt | None = None,
        audit_sink: ApprovalAuditSink | None = None,
    ) -> None:
        self.policy = policy
        self.session_id = session_id
        self.prompt = prompt
        self.audit_sink = audit_sink
        self._session_approvals: set[str] = set()
        self._lock = asyncio.Lock()

    async def before_tool_call(
        self,
        context: BeforeToolCallContext,
        signal: CancellationToken | None,
    ) -> BeforeToolCallResult | None:
        if signal is not None:
            signal.raise_if_cancelled()
        evaluation = self.policy.evaluate(context.tool_call.name, context.args)
        fingerprint = _fingerprint(context.tool_call.name, context.args)
        if evaluation.action == "allow" or fingerprint in self._session_approvals:
            decision = ApprovalDecision("allow_once", "approved by policy or session cache")
            await self._audit(context, evaluation, decision, fingerprint)
            return None
        if evaluation.action == "deny":
            decision = ApprovalDecision("deny", "; ".join(evaluation.reasons))
            await self._audit(context, evaluation, decision, fingerprint)
            return BeforeToolCallResult(block=True, reason=decision.reason)
        if self.prompt is None:
            decision = ApprovalDecision("deny", "approval is required but no prompt is configured")
            await self._audit(context, evaluation, decision, fingerprint)
            return BeforeToolCallResult(block=True, reason=decision.reason)

        request = ApprovalRequest(
            request_id=f"approval-{context.tool_call.id}",
            session_id=self.session_id,
            tool_call_id=context.tool_call.id,
            tool_name=context.tool_call.name,
            arguments=dict(context.args),
            risk=evaluation.risk,
            reasons=evaluation.reasons,
            cwd=self.policy.cwd,
            fingerprint=fingerprint,
        )
        async with self._lock:
            if fingerprint in self._session_approvals:
                return None
            try:
                response = self.prompt(request)
                decision = await response if inspect.isawaitable(response) else response
            except Exception as exc:
                decision = ApprovalDecision("deny", f"approval prompt failed: {exc}")
            if not isinstance(decision, ApprovalDecision):
                decision = ApprovalDecision("deny", "approval prompt returned an invalid decision")
            if decision.action == "allow_session":
                self._session_approvals.add(fingerprint)
            await self._audit(context, evaluation, decision, fingerprint)
            if decision.action == "deny":
                return BeforeToolCallResult(
                    block=True,
                    reason=decision.reason or "tool call was denied",
                )
            return None

    def clear_session_approvals(self) -> None:
        self._session_approvals.clear()

    async def _audit(
        self,
        context: BeforeToolCallContext,
        evaluation: PolicyEvaluation,
        decision: ApprovalDecision,
        fingerprint: str,
    ) -> None:
        if self.audit_sink is None:
            return
        record = ApprovalAuditRecord(
            timestamp_ms=time.time_ns() // 1_000_000,
            request_id=f"approval-{context.tool_call.id}",
            session_id=self.session_id,
            tool_call_id=context.tool_call.id,
            tool_name=context.tool_call.name,
            risk=evaluation.risk,
            policy_action=evaluation.action,
            decision=decision.action,
            reasons=evaluation.reasons,
            decision_reason=decision.reason,
            fingerprint=fingerprint,
        )
        try:
            await self.audit_sink.write(record)
        except Exception:
            # Audit failure must fail closed for prompted/denied operations. A
            # policy-approved read remains available because no secret payload
            # is persisted and the gate itself has already made its decision.
            if evaluation.action != "allow":
                raise


def compose_before_tool_hooks(
    first: Callable[..., Any] | None,
    gate: ApprovalGate | None,
) -> Callable[..., Any] | None:
    if first is None and gate is None:
        return None

    async def composed(
        context: BeforeToolCallContext,
        signal: CancellationToken | None,
    ) -> BeforeToolCallResult | None:
        result: BeforeToolCallResult | None = None
        if first is not None:
            value = first(context, signal)
            result = await value if inspect.isawaitable(value) else value
            if isinstance(result, BeforeToolCallResult) and result.block:
                return result
        if gate is not None:
            return await gate.before_tool_call(context, signal)
        return result if isinstance(result, BeforeToolCallResult) else None

    return composed


def _argument_path(arguments: Mapping[str, Any], cwd: Path) -> Path | None:
    for key in ("path", "file", "file_path", "filePath"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            path = Path(value).expanduser()
            return (cwd / path).resolve() if not path.is_absolute() else path.resolve()
    return None


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _fingerprint(tool_name: str, arguments: Mapping[str, Any]) -> str:
    payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool_name}\0{payload}".encode()).hexdigest()


def _risk_for_action(action: PolicyAction) -> RiskLevel:
    if action == "allow":
        return "low"
    if action == "prompt":
        return "medium"
    return "critical"


__all__ = [
    "ApprovalAction",
    "ApprovalAuditRecord",
    "ApprovalAuditSink",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalPolicy",
    "ApprovalPrompt",
    "ApprovalRequest",
    "ApprovalRule",
    "PolicyAction",
    "PolicyEvaluation",
    "RiskLevel",
    "compose_before_tool_hooks",
]
