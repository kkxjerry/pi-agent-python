"""Append-only session trees and active-context reconstruction."""

from .context import active_branch, build_model_context
from .manager import SessionManager
from .store import (
    JsonlSessionStore,
    MemorySessionStore,
    SessionCorruptionError,
    SessionStore,
    validate_document,
)
from .types import (
    SessionDocument,
    SessionEntry,
    SessionEntryType,
    SessionHeader,
    SessionInfo,
    SessionTreeNode,
    new_id,
    timestamp_ms,
)

__all__ = [
    "JsonlSessionStore",
    "MemorySessionStore",
    "SessionCorruptionError",
    "SessionDocument",
    "SessionEntry",
    "SessionEntryType",
    "SessionHeader",
    "SessionInfo",
    "SessionManager",
    "SessionStore",
    "SessionTreeNode",
    "active_branch",
    "build_model_context",
    "new_id",
    "timestamp_ms",
    "validate_document",
]
