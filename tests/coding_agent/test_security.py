from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.coding_agent import (
    ContainerMount,
    ContainerSandboxSpec,
    SecurityBoundaryError,
    build_container_command,
    default_security_posture,
)


def test_default_posture_does_not_claim_a_sandbox() -> None:
    posture = default_security_posture(approval_enabled=True)
    assert posture.process_sandboxed is False
    assert posture.approval_required is True
    assert posture.network_isolated is False
    assert any("not an operating-system sandbox" in note for note in posture.notes)


def test_container_command_is_shell_free_and_only_forwards_allowlisted_env(
    tmp_path: Path,
) -> None:
    extra = tmp_path / "readonly"
    extra.mkdir()
    spec = ContainerSandboxSpec(
        image="pi-agent:test",
        workspace=tmp_path,
        runtime="podman",
        environment_allowlist=("OPENAI_API_KEY",),
        mounts=(ContainerMount(extra, "/readonly", read_only=True),),
    )

    command = build_container_command(
        spec,
        ("--mode", "print", "hello; rm -rf /"),
        environment={"OPENAI_API_KEY": "secret", "UNSAFE": "not-forwarded"},
    )

    assert command[0:2] == ("podman", "run")
    assert "OPENAI_API_KEY=secret" in command
    assert not any("UNSAFE=" in value for value in command)
    assert command[-1] == "hello; rm -rf /"
    assert command.count("pi-py") == 1


def test_container_mount_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(SecurityBoundaryError, match="does not exist"):
        ContainerMount(tmp_path / "missing", "/missing")
