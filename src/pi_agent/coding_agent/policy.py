"""Compatibility names for the consolidated approval policy implementation."""

from .approval import (
    ApprovalAction,
    ApprovalAuditRecord,
    ApprovalAuditSink,
    ApprovalDecision,
    ApprovalGate,
    ApprovalPolicy,
    ApprovalPrompt,
    ApprovalRequest,
    ApprovalRule,
    PolicyAction,
    PolicyEvaluation,
    RiskLevel,
    compose_before_tool_hooks,
)

ApprovalResponse = ApprovalDecision
PolicyDecision = PolicyEvaluation
ToolPolicy = ApprovalPolicy
ToolRequest = ApprovalRequest

__all__ = [
    "ApprovalAction",
    "ApprovalAuditRecord",
    "ApprovalAuditSink",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalPolicy",
    "ApprovalPrompt",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalRule",
    "PolicyAction",
    "PolicyDecision",
    "PolicyEvaluation",
    "RiskLevel",
    "ToolPolicy",
    "ToolRequest",
    "compose_before_tool_hooks",
]
