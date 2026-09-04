from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

ContainerRuntime: TypeAlias = Literal["docker", "podman"]
NetworkMode: TypeAlias = Literal["none", "bridge", "host"]


class SecurityBoundaryError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class ContainerMount:
    source: Path
    target: str
    read_only: bool = False

    def __post_init__(self) -> None:
        source = self.source.expanduser().resolve()
        object.__setattr__(self, "source", source)
        if not source.exists():
            raise SecurityBoundaryError(f"container mount source does not exist: {source}")
        if not self.target.startswith("/") or "\x00" in self.target:
            raise SecurityBoundaryError("container mount target must be an absolute POSIX path")
        if "," in str(source) or "," in self.target:
            raise SecurityBoundaryError("container mount paths must not contain commas")


@dataclass(slots=True, frozen=True)
class ContainerSandboxSpec:
    image: str
    workspace: Path
    runtime: ContainerRuntime = "docker"
    container_workspace: str = "/workspace"
    network: NetworkMode = "none"
    read_only_root: bool = True
    workspace_read_only: bool = False
    remove_after_exit: bool = True
    user: str | None = None
    environment_allowlist: tuple[str, ...] = ()
    mounts: tuple[ContainerMount, ...] = ()
    tmpfs: tuple[str, ...] = ("/tmp",)
    extra_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        workspace = self.workspace.expanduser().resolve()
        object.__setattr__(self, "workspace", workspace)
        if (
            not self.image
            or self.image.startswith("-")
            or any(character in self.image for character in ("\n", "\r", "\x00"))
        ):
            raise SecurityBoundaryError(
                "container image must be a non-empty single-line value and must not start with '-'"
            )
        if not workspace.is_dir():
            raise SecurityBoundaryError(f"workspace is not a directory: {workspace}")
        if not self.container_workspace.startswith("/"):
            raise SecurityBoundaryError("container_workspace must be an absolute POSIX path")
        if "," in str(workspace) or "," in self.container_workspace:
            raise SecurityBoundaryError("container workspace paths must not contain commas")
        for value in (*self.tmpfs, *self.extra_args, *self.environment_allowlist):
            if "\x00" in value or "\n" in value or "\r" in value:
                raise SecurityBoundaryError("container options must be single-line values")
        for name in self.environment_allowlist:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                raise SecurityBoundaryError(f"invalid environment variable name: {name!r}")


@dataclass(slots=True, frozen=True)
class SecurityPosture:
    process_sandboxed: bool
    approval_required: bool
    network_isolated: bool
    filesystem_isolated: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def default_security_posture(*, approval_enabled: bool = False) -> SecurityPosture:
    return SecurityPosture(
        process_sandboxed=False,
        approval_required=approval_enabled,
        network_isolated=False,
        filesystem_isolated=False,
        notes=(
            "The default runtime inherits the launching user's filesystem, "
            "process, and network permissions.",
            "Approval policy is a decision gate, not an operating-system sandbox.",
        ),
    )


def container_security_posture(spec: ContainerSandboxSpec) -> SecurityPosture:
    return SecurityPosture(
        process_sandboxed=True,
        approval_required=False,
        network_isolated=spec.network == "none",
        filesystem_isolated=spec.read_only_root,
        notes=(
            "Isolation strength depends on the selected container runtime and host configuration.",
            "Only explicitly allowlisted environment variables are forwarded.",
        ),
    )


def build_container_command(
    spec: ContainerSandboxSpec,
    agent_arguments: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Build a shell-free argv vector for running ``pi-py`` in a container."""

    values = os.environ if environment is None else environment
    command: list[str] = [spec.runtime, "run"]
    if spec.remove_after_exit:
        command.append("--rm")
    if spec.read_only_root:
        command.append("--read-only")
    command.extend(("--network", spec.network))
    command.extend(("--workdir", spec.container_workspace))
    if spec.user:
        command.extend(("--user", spec.user))
    workspace_mode = "ro" if spec.workspace_read_only else "rw"
    command.extend(
        (
            "--mount",
            _mount_argument(spec.workspace, spec.container_workspace, workspace_mode),
        )
    )
    for mount in spec.mounts:
        command.extend(
            (
                "--mount",
                _mount_argument(
                    mount.source,
                    mount.target,
                    "ro" if mount.read_only else "rw",
                ),
            )
        )
    for target in spec.tmpfs:
        if not target.startswith("/"):
            raise SecurityBoundaryError(f"tmpfs target must be absolute: {target}")
        command.extend(("--tmpfs", target))
    for name in spec.environment_allowlist:
        if name in values:
            command.extend(("--env", f"{name}={values[name]}"))
    command.extend(spec.extra_args)
    command.append(spec.image)
    command.append("pi-py")
    command.extend(agent_arguments)
    return tuple(command)


def _mount_argument(source: Path, target: str, mode: str) -> str:
    return f"type=bind,src={source},dst={target},{mode}"


__all__ = [
    "ContainerMount",
    "ContainerRuntime",
    "ContainerSandboxSpec",
    "NetworkMode",
    "SecurityBoundaryError",
    "SecurityPosture",
    "build_container_command",
    "container_security_posture",
    "default_security_posture",
]
