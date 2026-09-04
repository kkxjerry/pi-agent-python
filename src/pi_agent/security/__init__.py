"""Explicit tool policy, approval, and audit primitives.

This package is a policy boundary, not an operating-system sandbox.
"""

from .approval import (
    ApprovalManager,
    ConsoleApprovalHandler,
    JsonlAuditSink,
    StaticApprovalHandler,
)
from .policy import ToolPolicy, canonical_arguments, classify_command, classify_request
from .types import (
    ApprovalGrant,
    ApprovalHandler,
    ApprovalResponse,
    ApprovalScope,
    DecisionEffect,
    PolicyDecision,
    PolicyRule,
    RiskLevel,
    SecurityAuditRecord,
    SecurityPolicyError,
    ToolDeniedError,
    ToolRequest,
)

__all__ = [
    "ApprovalGrant",
    "ApprovalHandler",
    "ApprovalManager",
    "ApprovalResponse",
    "ApprovalScope",
    "ConsoleApprovalHandler",
    "DecisionEffect",
    "JsonlAuditSink",
    "PolicyDecision",
    "PolicyRule",
    "RiskLevel",
    "SecurityAuditRecord",
    "SecurityPolicyError",
    "StaticApprovalHandler",
    "ToolDeniedError",
    "ToolPolicy",
    "ToolRequest",
    "canonical_arguments",
    "classify_command",
    "classify_request",
]
