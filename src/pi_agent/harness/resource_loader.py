from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from .resource_types import (
    ContextFile,
    ExtensionResource,
    PackageResource,
    PromptTemplate,
    ResourceKind,
    ResourceSnapshot,
    ResourceSource,
    ResourceWarning,
    Skill,
    Theme,
)


class _NamedResource(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def source(self) -> ResourceSource: ...


T = TypeVar("T", bound=_NamedResource)


@dataclass(slots=True)
class ResourceLoaderConfig:
    cwd: Path
    user_root: Path = field(default_factory=lambda: Path.home() / ".pi" / "agent")
    project_dir_name: str = ".pi"
    context_file_names: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md")
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ("**/.git/**", "**/__pycache__/**")
    follow_symlinks: bool = False
    extra_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        self.cwd = self.cwd.expanduser().resolve()
        self.user_root = self.user_root.expanduser().resolve()
        self.extra_roots = tuple(path.expanduser().resolve() for path in self.extra_roots)
        if not self.project_dir_name or any(char in self.project_dir_name for char in ("/", "\\")):
            raise ValueError("project_dir_name must be a single directory name")


class ResourceLoader:
    """Discover user/project resources with deterministic source precedence."""

    def __init__(self, config: ResourceLoaderConfig) -> None:
        self.config = config
        self._generation = 0
        self._snapshot: ResourceSnapshot | None = None

    @property
    def snapshot(self) -> ResourceSnapshot | None:
        return self._snapshot

    def load(self) -> ResourceSnapshot:
        self._generation += 1
        warnings: list[ResourceWarning] = []
        roots = self._resource_roots()
        contexts = self._load_context_files(warnings)

        system_prompt: str | None = None
        system_source: ResourceSource | None = None
        appends: list[ContextFile] = []
        skills: list[Skill] = []
        prompts: list[PromptTemplate] = []
        themes: list[Theme] = []
        extensions: list[ExtensionResource] = []
        packages: list[PackageResource] = []

        for source in roots:
            system_path = source.root / "SYSTEM.md"
            if system_path.is_file() and self._allowed(system_path, source.root):
                content = self._read_text(system_path, warnings)
                if content is not None:
                    system_prompt = content
                    system_source = ResourceSource(
                        source.kind, source.root, system_path, source.priority
                    )
            append_path = source.root / "APPEND_SYSTEM.md"
            if append_path.is_file() and self._allowed(append_path, source.root):
                content = self._read_text(append_path, warnings)
                if content is not None:
                    appends.append(
                        ContextFile(
                            content,
                            ResourceSource(source.kind, source.root, append_path, source.priority),
                        )
                    )
            skills.extend(self._load_skills(source, warnings))
            prompts.extend(self._load_prompts(source, warnings))
            themes.extend(self._load_themes(source, warnings))
            extensions.extend(self._load_extensions(source, warnings))
            packages.extend(self._load_packages(source, warnings))

        snapshot = ResourceSnapshot(
            generation=self._generation,
            system_prompt=system_prompt,
            system_prompt_source=system_source,
            append_system_prompts=tuple(appends),
            context_files=tuple(contexts),
            skills=tuple(self._dedupe_named(skills, warnings, "skill")),
            prompt_templates=tuple(self._dedupe_named(prompts, warnings, "prompt")),
            themes=tuple(self._dedupe_named(themes, warnings, "theme")),
            extensions=tuple(self._dedupe_named(extensions, warnings, "extension")),
            packages=tuple(self._dedupe_named(packages, warnings, "package")),
            warnings=tuple(warnings),
        )
        self._snapshot = snapshot
        return snapshot

    def reload(self) -> ResourceSnapshot:
        return self.load()

    def _resource_roots(self) -> list[ResourceSource]:
        roots: list[ResourceSource] = []
        if self.config.user_root.is_dir():
            roots.append(ResourceSource("user", self.config.user_root, self.config.user_root, 100))
        for index, ancestor in enumerate(_ancestor_chain(self.config.cwd)):
            root = ancestor / self.config.project_dir_name
            if root.is_dir():
                roots.append(ResourceSource("project", root, root, 200 + index))
        for index, root in enumerate(self.config.extra_roots):
            if root.is_dir():
                roots.append(ResourceSource("explicit", root, root, 10_000 + index))
        return roots

    def _load_context_files(self, warnings: list[ResourceWarning]) -> list[ContextFile]:
        result: list[ContextFile] = []
        candidates: list[tuple[Path, ResourceKind, int]] = []
        for name in self.config.context_file_names:
            candidates.append((self.config.user_root / name, "user", 100))
        for index, ancestor in enumerate(_ancestor_chain(self.config.cwd)):
            for name in self.config.context_file_names:
                candidates.append((ancestor / name, "context", 200 + index))
        seen: set[Path] = set()
        for path, kind, priority in candidates:
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            if not self._allowed(path, path.parent):
                continue
            content = self._read_text(path, warnings)
            if content is not None:
                result.append(
                    ContextFile(content, ResourceSource(kind, path.parent, path, priority))
                )
        return result

    def _load_skills(self, source: ResourceSource, warnings: list[ResourceWarning]) -> list[Skill]:
        result: list[Skill] = []
        root = source.root / "skills"
        for path in self._walk_files(root, source.root, warnings):
            if path.name != "SKILL.md":
                continue
            parsed = self._read_frontmatter(path, warnings)
            if parsed is None:
                continue
            metadata, body = parsed
            name = str(metadata.get("name") or path.parent.name).strip()
            description = str(metadata.get("description") or "").strip()
            if not name:
                warnings.append(ResourceWarning("invalid_skill", "skill name is empty", path))
                continue
            if not description:
                warnings.append(
                    ResourceWarning(
                        "skill_description_missing",
                        f"skill {name!r} has no description",
                        path,
                    )
                )
            resources = tuple(
                item
                for item in sorted(path.parent.rglob("*"))
                if item.is_file()
                and item != path
                and (self.config.follow_symlinks or not item.is_symlink())
            )
            result.append(
                Skill(
                    name,
                    description,
                    body,
                    metadata,
                    resources,
                    ResourceSource(source.kind, source.root, path, source.priority),
                )
            )
        return result

    def _load_prompts(
        self, source: ResourceSource, warnings: list[ResourceWarning]
    ) -> list[PromptTemplate]:
        result: list[PromptTemplate] = []
        for path in self._walk_files(source.root / "prompts", source.root, warnings):
            if path.suffix.lower() != ".md":
                continue
            parsed = self._read_frontmatter(path, warnings)
            if parsed is None:
                continue
            metadata, body = parsed
            name = str(metadata.get("name") or path.stem).strip()
            if not name:
                warnings.append(ResourceWarning("invalid_prompt", "prompt name is empty", path))
                continue
            result.append(
                PromptTemplate(
                    name=name,
                    description=str(metadata.get("description") or "").strip(),
                    body=body,
                    argument_hint=(
                        str(metadata["argument-hint"])
                        if metadata.get("argument-hint") is not None
                        else None
                    ),
                    metadata=metadata,
                    source=ResourceSource(source.kind, source.root, path, source.priority),
                )
            )
        return result

    def _load_themes(self, source: ResourceSource, warnings: list[ResourceWarning]) -> list[Theme]:
        result: list[Theme] = []
        for path in self._walk_files(source.root / "themes", source.root, warnings):
            if path.suffix.lower() != ".json":
                continue
            value = self._read_json(path, warnings)
            if value is None:
                continue
            result.append(
                Theme(
                    str(value.get("name") or path.stem),
                    value,
                    ResourceSource(source.kind, source.root, path, source.priority),
                )
            )
        return result

    def _load_extensions(
        self, source: ResourceSource, warnings: list[ResourceWarning]
    ) -> list[ExtensionResource]:
        result: list[ExtensionResource] = []
        root = source.root / "extensions"
        for path in self._walk_files(root, source.root, warnings):
            if path.suffix != ".py" or path.name.startswith("_"):
                continue
            result.append(
                ExtensionResource(
                    path.stem,
                    ResourceSource(source.kind, source.root, path, source.priority),
                )
            )
        return result

    def _load_packages(
        self, source: ResourceSource, warnings: list[ResourceWarning]
    ) -> list[PackageResource]:
        result: list[PackageResource] = []
        root = source.root / "packages"
        for path in self._walk_files(root, source.root, warnings):
            if path.name not in {"pi-package.json", "package.json"}:
                continue
            value = self._read_json(path, warnings)
            if value is None:
                continue
            name = str(value.get("name") or path.parent.name).strip()
            result.append(
                PackageResource(
                    name,
                    value,
                    ResourceSource("package", source.root, path, source.priority),
                )
            )
        return result

    def _walk_files(
        self,
        root: Path,
        match_root: Path,
        warnings: list[ResourceWarning],
    ) -> list[Path]:
        if not root.is_dir():
            return []
        result: list[Path] = []
        for current, directories, files in os.walk(root, followlinks=self.config.follow_symlinks):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in sorted(directories):
                path = current_path / name
                if path.is_symlink() and not self.config.follow_symlinks:
                    warnings.append(
                        ResourceWarning("symlink_skipped", "resource symlink was skipped", path)
                    )
                    continue
                if self._allowed(path, match_root):
                    kept_directories.append(name)
            directories[:] = kept_directories
            for name in sorted(files):
                path = current_path / name
                if path.is_symlink() and not self.config.follow_symlinks:
                    warnings.append(
                        ResourceWarning("symlink_skipped", "resource symlink was skipped", path)
                    )
                    continue
                if self._allowed(path, match_root):
                    result.append(path)
        return result

    def _allowed(self, path: Path, root: Path) -> bool:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.as_posix()
        if self.config.include and not any(
            fnmatch.fnmatch(relative, pattern) for pattern in self.config.include
        ):
            return False
        return not any(fnmatch.fnmatch(relative, pattern) for pattern in self.config.exclude)

    @staticmethod
    def _read_text(path: Path, warnings: list[ResourceWarning]) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            warnings.append(ResourceWarning("read_error", str(exc), path))
            return None

    def _read_json(self, path: Path, warnings: list[ResourceWarning]) -> dict[str, Any] | None:
        content = self._read_text(path, warnings)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            warnings.append(ResourceWarning("invalid_json", str(exc), path))
            return None
        if not isinstance(value, dict):
            warnings.append(ResourceWarning("invalid_json", "resource must be an object", path))
            return None
        return cast(dict[str, Any], value)

    def _read_frontmatter(
        self, path: Path, warnings: list[ResourceWarning]
    ) -> tuple[dict[str, Any], str] | None:
        content = self._read_text(path, warnings)
        if content is None:
            return None
        try:
            return parse_frontmatter(content)
        except ValueError as exc:
            warnings.append(ResourceWarning("invalid_frontmatter", str(exc), path))
            return None

    @staticmethod
    def _dedupe_named(
        items: list[T], warnings: list[ResourceWarning], resource_type: str
    ) -> list[T]:
        selected: dict[str, T] = {}
        order: list[str] = []
        for item in items:
            name = item.name
            if name in selected:
                previous = selected[name].source
                current = item.source
                warnings.append(
                    ResourceWarning(
                        "resource_shadowed",
                        (
                            f"{resource_type} {name!r} from {previous.path} "
                            f"was shadowed by {current.path}"
                        ),
                        current.path,
                    )
                )
                order.remove(name)
            selected[name] = item
            order.append(name)
        return [selected[name] for name in order]


def _ancestor_chain(path: Path) -> tuple[Path, ...]:
    result = [path]
    result.extend(path.parents)
    result.reverse()
    return tuple(result)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    normalized = content.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, content
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter opening delimiter has no closing delimiter")
    header = normalized[4:end]
    body = normalized[end + 5 :]
    metadata: dict[str, Any] = {}
    for line_number, line in enumerate(header.splitlines(), start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"frontmatter line {line_number} has no ':' separator")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"frontmatter line {line_number} has an empty key")
        metadata[key] = _parse_scalar(raw_value.strip())
    return metadata, body


def _parse_scalar(value: str) -> Any:
    if not value:
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value
