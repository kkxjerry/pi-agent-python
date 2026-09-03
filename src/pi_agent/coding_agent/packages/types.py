from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

_PACKAGE_NAME = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*$")


class PackageError(RuntimeError):
    pass


class PackageValidationError(PackageError):
    pass


class PackageIntegrityError(PackageError):
    pass


@dataclass(frozen=True, slots=True)
class PackageManifest:
    name: str
    version: str
    extensions: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    themes: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _PACKAGE_NAME.fullmatch(self.name):
            raise PackageValidationError(f"invalid package name: {self.name!r}")
        if not self.version.strip():
            raise PackageValidationError("package version must not be empty")
        for field_name in ("extensions", "skills", "prompts", "themes"):
            for item in getattr(self, field_name):
                _validate_relative_path(item, field_name)
        for dependency in self.dependencies:
            if not _PACKAGE_NAME.fullmatch(dependency):
                raise PackageValidationError(f"invalid package dependency: {dependency!r}")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PackageManifest:
        package_value = value.get("pi")
        merged = dict(value)
        if isinstance(package_value, Mapping):
            merged.update(package_value)
        name = merged.get("name")
        version = merged.get("version")
        if not isinstance(name, str) or not name:
            raise PackageValidationError("pi-package.json requires a non-empty name")
        if not isinstance(version, str) or not version:
            raise PackageValidationError("pi-package.json requires a non-empty version")
        known = {
            "name",
            "version",
            "extensions",
            "skills",
            "prompts",
            "themes",
            "dependencies",
            "capabilities",
            "pi",
        }
        return cls(
            name=name,
            version=version,
            extensions=_string_tuple(merged.get("extensions", ()), "extensions"),
            skills=_string_tuple(merged.get("skills", ()), "skills"),
            prompts=_string_tuple(merged.get("prompts", ()), "prompts"),
            themes=_string_tuple(merged.get("themes", ()), "themes"),
            dependencies=_string_tuple(merged.get("dependencies", ()), "dependencies"),
            capabilities=_string_tuple(merged.get("capabilities", ()), "capabilities"),
            metadata={key: item for key, item in value.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
        }
        for field_name in (
            "extensions",
            "skills",
            "prompts",
            "themes",
            "dependencies",
            "capabilities",
        ):
            items = getattr(self, field_name)
            if items:
                value[field_name] = list(items)
        value.update(self.metadata)
        return value


@dataclass(frozen=True, slots=True)
class PackageLockEntry:
    name: str
    version: str
    source: str
    integrity: str
    installed_at: int
    manifest: PackageManifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "integrity": self.integrity,
            "installedAt": self.installed_at,
            "manifest": self.manifest.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PackageLockEntry:
        name = value.get("name")
        version = value.get("version")
        source = value.get("source")
        integrity = value.get("integrity")
        installed_at = value.get("installedAt")
        manifest = value.get("manifest")
        if not isinstance(name, str) or not name:
            raise PackageValidationError("package lock entry name must be a non-empty string")
        if not isinstance(version, str) or not version:
            raise PackageValidationError("package lock entry version must be a non-empty string")
        if not isinstance(source, str) or not source:
            raise PackageValidationError("package lock entry source must be a non-empty string")
        if not isinstance(integrity, str) or not integrity:
            raise PackageValidationError("package lock entry integrity must be a non-empty string")
        if isinstance(installed_at, bool) or not isinstance(installed_at, int) or installed_at < 0:
            raise PackageValidationError("package lock installedAt must be a non-negative integer")
        if not isinstance(manifest, Mapping):
            raise PackageValidationError("package lock manifest must be an object")
        parsed = PackageManifest.from_dict(manifest)
        if parsed.name != name or parsed.version != version:
            raise PackageValidationError("package lock entry does not match its manifest")
        return cls(name, version, source, integrity, installed_at, parsed)


@dataclass(frozen=True, slots=True)
class PackageVerification:
    name: str
    valid: bool
    expected_integrity: str
    actual_integrity: str | None
    error: str | None = None


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise PackageValidationError(f"package {field_name} must be a string array")
    return tuple(value)


def _validate_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts:
        raise PackageValidationError(f"package {field_name} contains unsafe path {value!r}")


__all__ = [
    "PackageError",
    "PackageIntegrityError",
    "PackageLockEntry",
    "PackageManifest",
    "PackageValidationError",
    "PackageVerification",
]
