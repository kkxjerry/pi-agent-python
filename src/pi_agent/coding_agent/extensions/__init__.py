"""Python-native extension contracts and transactional activation."""

from .host import ExtensionAPI, ExtensionHost, descriptor_from_path
from .types import (
    DuplicateContributionError,
    ExtensionActivationError,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionContribution,
    ExtensionDescriptor,
    ExtensionDiagnostic,
    ExtensionDisposer,
    ExtensionEventHandler,
    ExtensionHandle,
    ExtensionPolicy,
    NamedTool,
)

__all__ = [
    "DuplicateContributionError",
    "ExtensionAPI",
    "ExtensionActivationError",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionContribution",
    "ExtensionDescriptor",
    "ExtensionDiagnostic",
    "ExtensionDisposer",
    "ExtensionEventHandler",
    "ExtensionHandle",
    "ExtensionHost",
    "ExtensionPolicy",
    "NamedTool",
    "descriptor_from_path",
]
