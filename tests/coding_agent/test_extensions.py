from __future__ import annotations

from pi_agent.coding_agent import ExtensionHost, ExtensionPolicy
from pi_agent.coding_agent.extensions import ExtensionActivationError, descriptor_from_path


def test_extension_public_surface_remains_available() -> None:
    assert ExtensionHost.__name__ == "ExtensionHost"
    assert ExtensionPolicy.__name__ == "ExtensionPolicy"
    assert ExtensionActivationError.__name__ == "ExtensionActivationError"
    assert callable(descriptor_from_path)
