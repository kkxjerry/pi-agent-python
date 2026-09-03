from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

ExtensionEventHandler: TypeAlias = Callable[[Any], Awaitable[None] | None]
ExtensionDisposer: TypeAlias = Callable[[], Awaitable[None] | None]
ExtensionCommandHandler: TypeAlias = Callable[
    [tuple[str, ...], "ExtensionCommandContext"], Awaitable[Any] | Any
]


class NamedTool(Protocol):
    name: str


@dataclass(frozen=True, slots=True)
class ExtensionDescriptor:
    name: str
    path: Path
    source: str = "explicit"
    priority: int = 0
    version: str | None = None
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("extension name must not be empty")
        object.__setattr__(self, "path", self.path.expanduser().resolve())
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ExtensionCommand:
    name: str
    handler: ExtensionCommandHandler
    description: str = ""
    argument_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("extension command name must not be empty")


@dataclass(frozen=True, slots=True)
class ExtensionCommandContext:
    extension_name: str
    cwd: Path
    services: Mapping[str, Any]
    session: Any = None


@dataclass(frozen=True, slots=True)
class ExtensionDiagnostic:
    level: Literal["info", "warning", "error"]
    code: str
    message: str
    extension: str | None = None
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class ExtensionPolicy:
    allowed_roots: tuple[Path, ...] = ()
    allowed_capabilities: frozenset[str] = frozenset()
    allow_all_capabilities: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_roots",
            tuple(path.expanduser().resolve() for path in self.allowed_roots),
        )


@dataclass(slots=True)
class ExtensionContribution:
    tools: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, ExtensionCommand] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    system_prompts: list[str] = field(default_factory=list)
    event_handlers: list[ExtensionEventHandler] = field(default_factory=list)
    disposers: list[ExtensionDisposer] = field(default_factory=list)


@dataclass(slots=True)
class ExtensionHandle:
    descriptor: ExtensionDescriptor
    module_name: str
    contribution: ExtensionContribution
    module: Any


class ExtensionActivationError(RuntimeError):
    def __init__(self, descriptor: ExtensionDescriptor, message: str) -> None:
        super().__init__(f"extension {descriptor.name!r} failed: {message}")
        self.descriptor = descriptor


class DuplicateContributionError(ExtensionActivationError):
    pass


__all__ = [
    "DuplicateContributionError",
    "ExtensionActivationError",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionCommandHandler",
    "ExtensionContribution",
    "ExtensionDescriptor",
    "ExtensionDiagnostic",
    "ExtensionDisposer",
    "ExtensionEventHandler",
    "ExtensionHandle",
    "ExtensionPolicy",
    "NamedTool",
]
