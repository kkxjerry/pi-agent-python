from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ResourceKind = Literal[
    "system",
    "append_system",
    "context",
    "skill",
    "prompt",
    "theme",
    "extension",
    "package",
]


@dataclass(frozen=True, slots=True)
class ResourceWarning:
    code: str
    message: str
    path: Path | None = None
    replaced_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ResourceOrigin:
    path: Path
    priority: int
    scope: Literal["user", "project", "explicit"]


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    content: str
    file_path: Path
    disable_model_invocation: bool = False
    origin: ResourceOrigin | None = None


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    content: str
    description: str | None = None
    file_path: Path | None = None
    origin: ResourceOrigin | None = None


@dataclass(frozen=True, slots=True)
class ThemeResource:
    name: str
    data: dict[str, Any]
    file_path: Path
    origin: ResourceOrigin


@dataclass(frozen=True, slots=True)
class ExtensionResource:
    name: str
    file_path: Path
    origin: ResourceOrigin


@dataclass(frozen=True, slots=True)
class PackageResource:
    name: str
    manifest: dict[str, Any]
    file_path: Path
    origin: ResourceOrigin


@dataclass(slots=True)
class LoadedResources:
    system_prompt: str | None = None
    append_system_prompts: list[str] = field(default_factory=list)
    context_files: list[tuple[Path, str]] = field(default_factory=list)
    skills: dict[str, Skill] = field(default_factory=dict)
    prompts: dict[str, PromptTemplate] = field(default_factory=dict)
    themes: dict[str, ThemeResource] = field(default_factory=dict)
    extensions: dict[str, ExtensionResource] = field(default_factory=dict)
    packages: dict[str, PackageResource] = field(default_factory=dict)
    warnings: list[ResourceWarning] = field(default_factory=list)
    roots: list[ResourceOrigin] = field(default_factory=list)

    def model_skills(self) -> list[Skill]:
        return [skill for skill in self.skills.values() if not skill.disable_model_invocation]
