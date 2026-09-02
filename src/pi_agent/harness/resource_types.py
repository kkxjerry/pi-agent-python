from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ResourceKind = Literal["user", "project", "context", "explicit", "package"]


@dataclass(slots=True, frozen=True)
class ResourceSource:
    kind: ResourceKind
    root: Path
    path: Path
    priority: int


@dataclass(slots=True, frozen=True)
class ResourceWarning:
    code: str
    message: str
    path: Path | None = None


@dataclass(slots=True, frozen=True)
class ContextFile:
    content: str
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class Skill:
    name: str
    description: str
    body: str
    metadata: dict[str, Any]
    resources: tuple[Path, ...]
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class PromptTemplate:
    name: str
    description: str
    body: str
    argument_hint: str | None
    metadata: dict[str, Any]
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class Theme:
    name: str
    data: dict[str, Any]
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class ExtensionResource:
    name: str
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class PackageResource:
    name: str
    metadata: dict[str, Any]
    source: ResourceSource


@dataclass(slots=True, frozen=True)
class ResourceSnapshot:
    generation: int
    system_prompt: str | None
    system_prompt_source: ResourceSource | None
    append_system_prompts: tuple[ContextFile, ...]
    context_files: tuple[ContextFile, ...]
    skills: tuple[Skill, ...]
    prompt_templates: tuple[PromptTemplate, ...]
    themes: tuple[Theme, ...]
    extensions: tuple[ExtensionResource, ...]
    packages: tuple[PackageResource, ...]
    warnings: tuple[ResourceWarning, ...] = field(default_factory=tuple)

    def skill(self, name: str) -> Skill | None:
        return next((item for item in self.skills if item.name == name), None)

    def prompt_template(self, name: str) -> PromptTemplate | None:
        return next((item for item in self.prompt_templates if item.name == name), None)
