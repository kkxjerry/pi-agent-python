"""Stable Phase 21-24 product surface.

This module intentionally keeps extension/package imports above AgentSession and
avoids widening the low-level Agent Core API.
"""

from .auth import (
    AuthStorage,
    AuthStorageError,
    Credential,
    CredentialKind,
    CredentialResolver,
    RefreshCredential,
)
from .extensions import (
    DuplicateContributionError,
    ExtensionActivationError,
    ExtensionAPI,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionContribution,
    ExtensionDescriptor,
    ExtensionDiagnostic,
    ExtensionHandle,
    ExtensionHost,
    ExtensionPolicy,
    descriptor_from_path,
)
from .model_access import ModelAccess, PreparedModel
from .packages import (
    PackageError,
    PackageInstallReceipt,
    PackageIntegrityError,
    PackageLockEntry,
    PackageManager,
    PackageManifest,
    PackageValidationError,
    PackageVerification,
)
from .runtime import (
    CodingAgentRuntime,
    CodingAgentRuntimeOptions,
    RuntimeState,
    create_coding_agent_runtime,
)

__all__ = [
    "AuthStorage",
    "AuthStorageError",
    "CodingAgentRuntime",
    "CodingAgentRuntimeOptions",
    "Credential",
    "CredentialKind",
    "CredentialResolver",
    "DuplicateContributionError",
    "ExtensionAPI",
    "ExtensionActivationError",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionContribution",
    "ExtensionDescriptor",
    "ExtensionDiagnostic",
    "ExtensionHandle",
    "ExtensionHost",
    "ExtensionPolicy",
    "ModelAccess",
    "PackageError",
    "PackageInstallReceipt",
    "PackageIntegrityError",
    "PackageLockEntry",
    "PackageManager",
    "PackageManifest",
    "PackageValidationError",
    "PackageVerification",
    "PreparedModel",
    "RefreshCredential",
    "RuntimeState",
    "create_coding_agent_runtime",
    "descriptor_from_path",
]
