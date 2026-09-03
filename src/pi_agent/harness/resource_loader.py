from __future__ import annotations

import fnmatch
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from .resource_types import (
    ExtensionResource,
    LoadedResources,
    PackageResource,
    PromptTemplate,
    ResourceOrigin,
    ResourceWarning,
    Skill,
    ThemeResource,
)

T = TypeVar("T")


class ResourceLoader:
    """Discover user, project, and explicit pi resources with source tracking."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        user_dir: str | Path | None = None,
        explicit_paths: list[str | Path] | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        follow_symlinks: bool = False,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.user_dir = Path(user_dir or "~/.pi").expanduser().resolve()
        self.explicit_paths = [Path(path).expanduser().resolve() for path in explicit_paths or []]
        self.include = include or ["**"]
        self.exclude = exclude or []
        self.follow_symlinks = follow_symlinks
        self._last = LoadedResources()

    @property
    def resources(self) -> LoadedResources:
        return self._last

    def load(self) -> LoadedResources:
        resources = LoadedResources()
        roots = self._roots()
        resources.roots = roots
        for origin in roots:
            self._load_root(origin, resources)
        self._load_context_chain(resources)
        self._last = resources
        return resources

    def reload(self) -> LoadedResources:
        return self.load()

    def _roots(self) -> list[ResourceOrigin]:
        roots: list[ResourceOrigin] = []
        priority = 0
        if self.user_dir.exists():
            roots.append(ResourceOrigin(self.user_dir, priority, "user"))
            priority += 1
        ancestors = list(reversed((self.cwd, *self.cwd.parents)))
        for ancestor in ancestors:
            root = ancestor / ".pi"
            if root.exists():
                roots.append(ResourceOrigin(root, priority, "project"))
                priority += 1
        for root in self.explicit_paths:
            roots.append(ResourceOrigin(root, priority, "explicit"))
            priority += 1
        return roots

    def _load_root(self, origin: ResourceOrigin, resources: LoadedResources) -> None:
        root = origin.path
        if not root.exists():
            resources.warnings.append(
                ResourceWarning(
                    "resource_root_missing", f"Resource root does not exist: {root}", root
                )
            )
            return
        self._load_system(root, origin, resources)
        self._load_skills(root / "skills", origin, resources)
        self._load_prompts(root / "prompts", origin, resources)
        self._load_themes(root / "themes", origin, resources)
        self._load_extensions(root / "extensions", origin, resources)
        self._load_packages(root / "packages", origin, resources)

    def _load_system(
        self,
        root: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        system = root / "SYSTEM.md"
        if self._eligible(system, root) and system.is_file():
            content = self._read_text(system, resources)
            if content is not None:
                resources.system_prompt = content
        appended = root / "APPEND_SYSTEM.md"
        if self._eligible(appended, root) and appended.is_file():
            content = self._read_text(appended, resources)
            if content is not None:
                resources.append_system_prompts.append(content)

    def _load_context_chain(self, resources: LoadedResources) -> None:
        directories = list(reversed((self.cwd, *self.cwd.parents)))
        seen: set[Path] = set()
        for directory in directories:
            for name in ("AGENTS.md", "CLAUDE.md"):
                path = directory / name
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                content = self._read_text(path, resources)
                if content is not None:
                    resources.context_files.append((path, content))

    def _load_skills(
        self,
        directory: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*/SKILL.md")):
            if not self._eligible(path, origin.path):
                continue
            content = self._read_text(path, resources)
            if content is None:
                continue
            frontmatter, body = _parse_frontmatter(content)
            name = str(frontmatter.get("name") or path.parent.name).strip()
            description = str(frontmatter.get("description") or "").strip()
            if not name or not description:
                resources.warnings.append(
                    ResourceWarning(
                        "invalid_skill",
                        f"Skill requires name and description: {path}",
                        path,
                    )
                )
                continue
            skill = Skill(
                name=name,
                description=description,
                content=body,
                file_path=path,
                disable_model_invocation=_as_bool(
                    frontmatter.get("disable-model-invocation")
                    or frontmatter.get("disableModelInvocation")
                ),
                origin=origin,
            )
            self._merge_named(resources.skills, name, skill, path, resources)

    def _load_prompts(
        self,
        directory: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.rglob("*.md")):
            if not self._eligible(path, origin.path):
                continue
            content = self._read_text(path, resources)
            if content is None:
                continue
            frontmatter, body = _parse_frontmatter(content)
            name = str(frontmatter.get("name") or path.stem).strip()
            prompt = PromptTemplate(
                name=name,
                description=(
                    str(frontmatter["description"]).strip()
                    if "description" in frontmatter
                    else None
                ),
                content=body,
                file_path=path,
                origin=origin,
            )
            self._merge_named(resources.prompts, name, prompt, path, resources)

    def _load_themes(
        self,
        directory: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.rglob("*.json")):
            if not self._eligible(path, origin.path):
                continue
            value = self._read_json(path, resources)
            if value is None:
                continue
            name = str(value.get("name") or path.stem)
            self._merge_named(
                resources.themes,
                name,
                ThemeResource(name, value, path, origin),
                path,
                resources,
            )

    def _load_extensions(
        self,
        directory: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.rglob("*.py")):
            if not self._eligible(path, origin.path):
                continue
            name = path.stem
            self._merge_named(
                resources.extensions,
                name,
                ExtensionResource(name, path, origin),
                path,
                resources,
            )

    def _load_packages(
        self,
        directory: Path,
        origin: ResourceOrigin,
        resources: LoadedResources,
    ) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*/pi-package.json")):
            if not self._eligible(path, origin.path):
                continue
            value = self._read_json(path, resources)
            if value is None:
                continue
            name = str(value.get("name") or path.parent.name)
            self._merge_named(
                resources.packages,
                name,
                PackageResource(name, value, path, origin),
                path,
                resources,
            )

    def _eligible(self, path: Path, root: Path) -> bool:
        if path.is_symlink() and not self.follow_symlinks:
            return False
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        included = any(
            pattern == "**" or fnmatch.fnmatch(relative, pattern) for pattern in self.include
        )
        excluded = any(fnmatch.fnmatch(relative, pattern) for pattern in self.exclude)
        return included and not excluded

    @staticmethod
    def _read_text(path: Path, resources: LoadedResources) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            resources.warnings.append(ResourceWarning("resource_read_error", str(exc), path))
            return None

    @staticmethod
    def _read_json(path: Path, resources: LoadedResources) -> dict[str, Any] | None:
        content = ResourceLoader._read_text(path, resources)
        if content is None:
            return None
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            resources.warnings.append(ResourceWarning("resource_json_error", str(exc), path))
            return None
        if not isinstance(value, dict):
            resources.warnings.append(
                ResourceWarning("resource_json_error", "JSON resource must be an object", path)
            )
            return None
        return value

    @staticmethod
    def _merge_named(
        target: dict[str, T],
        name: str,
        value: T,
        path: Path,
        resources: LoadedResources,
    ) -> None:
        previous = target.get(name)
        if previous is not None:
            previous_path = getattr(previous, "file_path", None)
            resources.warnings.append(
                ResourceWarning(
                    "resource_shadowed",
                    f"Resource {name!r} from {path} shadows {previous_path}",
                    path,
                    previous_path if isinstance(previous_path, Path) else None,
                )
            )
        target[name] = value


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end < 0:
        return {}, content
    raw = content[4:end]
    values: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        values[key.strip()] = _scalar(value.strip())
    return values, content[end + 5 :]


def _scalar(value: str) -> Any:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    return value


def _as_bool(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() in {"true", "yes", "1"})
