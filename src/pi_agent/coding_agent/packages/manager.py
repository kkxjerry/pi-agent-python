from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from pi_agent.coding_agent.extensions import ExtensionDescriptor

from .types import (
    PackageError,
    PackageIntegrityError,
    PackageLockEntry,
    PackageManifest,
    PackageValidationError,
    PackageVerification,
)


@dataclass(slots=True)
class PackageInstallReceipt:
    manager: PackageManager
    entry: PackageLockEntry
    target: Path
    backup: Path | None
    previous: PackageLockEntry | None
    _finished: bool = False

    async def finalize(self) -> None:
        if self._finished:
            return
        await self.manager._finalize_receipt(self)
        self._finished = True

    async def rollback(self) -> None:
        if self._finished:
            return
        await self.manager._rollback_receipt(self)
        self._finished = True


@dataclass(slots=True)
class PackageRemoveReceipt:
    manager: PackageManager
    name: str
    target: Path
    backup: Path | None
    previous: PackageLockEntry
    _finished: bool = False

    async def finalize(self) -> None:
        if self._finished:
            return
        await self.manager._finalize_remove_receipt(self)
        self._finished = True

    async def rollback(self) -> None:
        if self._finished:
            return
        await self.manager._rollback_remove_receipt(self)
        self._finished = True


class PackageManager:
    """Atomic local package manager with an integrity lock file.

    Phase 23 intentionally supports local directories only. Registry resolution,
    downloads, and arbitrary install scripts remain outside this trust boundary.
    """

    LOCK_VERSION = 1

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.packages_dir = self.root / "packages"
        self.lock_path = self.root / "packages.lock.json"
        self._process_lock_path = self.root / ".packages.lock"
        self._staging_dir = self.root / ".package-staging"
        self._backup_dir = self.root / ".package-backups"
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.RLock()

    async def install(
        self,
        source: str | Path,
        *,
        finalize: bool = True,
    ) -> PackageInstallReceipt:
        actual = _source_path(source)
        async with self._async_lock:
            receipt = await asyncio.to_thread(self._install_sync, actual)
        if finalize:
            await receipt.finalize()
        return receipt

    async def update(
        self,
        name: str,
        source: str | Path | None = None,
        *,
        finalize: bool = True,
    ) -> PackageInstallReceipt:
        if source is None:
            entry = await self.get(name)
            if entry is None:
                raise PackageError(f"package is not installed: {name}")
            source = entry.source
        receipt = await self.install(source, finalize=finalize)
        if receipt.entry.name != name:
            if not receipt._finished:
                await receipt.rollback()
            raise PackageValidationError(
                f"update source contains package {receipt.entry.name!r}; expected {name!r}"
            )
        return receipt

    async def stage_remove(self, name: str) -> PackageRemoveReceipt | None:
        async with self._async_lock:
            return await asyncio.to_thread(self._stage_remove_sync, name)

    async def remove(self, name: str, *, finalize: bool = True) -> bool:
        receipt = await self.stage_remove(name)
        if receipt is None:
            return False
        if finalize:
            await receipt.finalize()
        return True

    async def get(self, name: str) -> PackageLockEntry | None:
        async with self._async_lock:
            entries = await asyncio.to_thread(self._read_lock_sync)
            return entries.get(name)

    async def list(self) -> tuple[PackageLockEntry, ...]:
        async with self._async_lock:
            entries = await asyncio.to_thread(self._read_lock_sync)
            return tuple(entries[name] for name in sorted(entries))

    async def verify(self, name: str) -> PackageVerification:
        async with self._async_lock:
            return await asyncio.to_thread(self._verify_sync, name)

    async def verify_all(self) -> tuple[PackageVerification, ...]:
        entries = await self.list()
        values: list[PackageVerification] = []
        for entry in entries:
            values.append(await self.verify(entry.name))
        return tuple(values)

    async def extension_descriptors(self) -> tuple[ExtensionDescriptor, ...]:
        entries = await self.list()
        result: list[ExtensionDescriptor] = []
        for package_index, entry in enumerate(entries):
            root = self.package_path(entry.name)
            for extension_index, relative in enumerate(entry.manifest.extensions):
                path = (root / relative).resolve()
                valid_entry = path.is_file() or (path.is_dir() and (path / "__init__.py").is_file())
                if not path.is_relative_to(root.resolve()) or not valid_entry:
                    raise PackageIntegrityError(
                        f"package {entry.name!r} extension is missing or unsafe: {relative}"
                    )
                relative_name = Path(relative).with_suffix("").as_posix().replace("/", ":")
                name = f"{entry.name}:{relative_name}"
                result.append(
                    ExtensionDescriptor(
                        name=name,
                        path=path,
                        source=f"package:{entry.name}",
                        priority=20_000 + package_index * 100 + extension_index,
                        version=entry.version,
                        capabilities=entry.manifest.capabilities,
                        metadata={"package": entry.name, "relativePath": relative},
                    )
                )
        return tuple(result)

    async def resource_roots(self) -> tuple[Path, ...]:
        entries = await self.list()
        return tuple(self.package_path(entry.name) for entry in entries)

    def package_path(self, name: str) -> Path:
        return self.packages_dir / quote(name, safe="._-")

    def _install_sync(self, source: Path) -> PackageInstallReceipt:
        manifest = _load_manifest(source)
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            missing = [item for item in manifest.dependencies if item not in entries]
            if missing:
                raise PackageValidationError(
                    "package dependencies are not installed: " + ", ".join(sorted(missing))
                )
            self._staging_dir.mkdir(parents=True, exist_ok=True)
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            stage = Path(
                tempfile.mkdtemp(
                    prefix=f"{quote(manifest.name, safe='._-')}-",
                    dir=self._staging_dir,
                )
            )
            backup: Path | None = None
            target = self.package_path(manifest.name)
            previous = entries.get(manifest.name)
            try:
                _copy_package(source, stage)
                staged_manifest = _load_manifest(stage)
                if staged_manifest != manifest:
                    raise PackageValidationError("package manifest changed while being staged")
                integrity = _directory_integrity(stage)
                self.packages_dir.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup = self._backup_dir / (
                        f"{quote(manifest.name, safe='._-')}-{time.time_ns()}"
                    )
                    os.replace(target, backup)
                os.replace(stage, target)
                entry = PackageLockEntry(
                    name=manifest.name,
                    version=manifest.version,
                    source=str(source),
                    integrity=integrity,
                    installed_at=time.time_ns() // 1_000_000,
                    manifest=manifest,
                )
                entries[manifest.name] = entry
                self._write_lock_unlocked(entries)
                return PackageInstallReceipt(self, entry, target, backup, previous)
            except Exception:
                if target.exists() and (backup is not None or previous is None):
                    _remove_path(target)
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                _remove_path(stage)
                raise

    def _stage_remove_sync(self, name: str) -> PackageRemoveReceipt | None:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            if name not in entries:
                return None
            dependents = [
                item.name for item in entries.values() if name in item.manifest.dependencies
            ]
            if dependents:
                raise PackageError(
                    f"cannot remove {name!r}; required by: {', '.join(sorted(dependents))}"
                )
            target = self.package_path(name)
            backup = self._backup_dir / f"remove-{quote(name, safe='._-')}-{time.time_ns()}"
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            actual_backup: Path | None = None
            if target.exists():
                os.replace(target, backup)
                actual_backup = backup
            previous = entries.pop(name)
            try:
                self._write_lock_unlocked(entries)
            except Exception:
                entries[name] = previous
                if actual_backup is not None and actual_backup.exists():
                    os.replace(actual_backup, target)
                raise
            return PackageRemoveReceipt(
                self,
                name,
                target,
                actual_backup,
                previous,
            )

    def _verify_sync(self, name: str) -> PackageVerification:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            entry = entries.get(name)
            if entry is None:
                raise PackageError(f"package is not installed: {name}")
            target = self.package_path(name)
            if not target.is_dir():
                return PackageVerification(
                    name,
                    False,
                    entry.integrity,
                    None,
                    "installed package directory is missing",
                )
            try:
                actual = _directory_integrity(target)
            except Exception as exc:
                return PackageVerification(name, False, entry.integrity, None, str(exc))
            return PackageVerification(
                name,
                actual == entry.integrity,
                entry.integrity,
                actual,
                None if actual == entry.integrity else "integrity mismatch",
            )

    def _read_lock_sync(self) -> dict[str, PackageLockEntry]:
        with self._thread_lock, _file_lock(self._process_lock_path):
            return self._read_lock_unlocked()

    def _read_lock_unlocked(self) -> dict[str, PackageLockEntry]:
        if not self.lock_path.exists():
            return {}
        try:
            value = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PackageValidationError(f"could not read package lock: {exc}") from exc
        if not isinstance(value, dict) or value.get("version") != self.LOCK_VERSION:
            raise PackageValidationError("unsupported package lock format")
        raw_packages = value.get("packages", {})
        if not isinstance(raw_packages, dict):
            raise PackageValidationError("package lock packages must be an object")
        result: dict[str, PackageLockEntry] = {}
        for name, item in raw_packages.items():
            if not isinstance(name, str) or not isinstance(item, Mapping):
                raise PackageValidationError("package lock contains an invalid entry")
            entry = PackageLockEntry.from_dict(item)
            if entry.name != name:
                raise PackageValidationError(f"package lock key mismatch for {name!r}")
            result[name] = entry
        return result

    def _write_lock_unlocked(self, entries: Mapping[str, PackageLockEntry]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.LOCK_VERSION,
            "packages": {name: entries[name].to_dict() for name in sorted(entries)},
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=".packages.lock.", suffix=".tmp", dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.lock_path)
        except Exception:
            with suppress(OSError):
                os.close(fd)
            temporary.unlink(missing_ok=True)
            raise

    async def _finalize_receipt(self, receipt: PackageInstallReceipt) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._finalize_receipt_sync, receipt)

    def _finalize_receipt_sync(self, receipt: PackageInstallReceipt) -> None:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            self._assert_receipt_current(receipt, entries)
            if receipt.backup is not None:
                _remove_path(receipt.backup)

    async def _rollback_receipt(self, receipt: PackageInstallReceipt) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._rollback_receipt_sync, receipt)

    @staticmethod
    def _assert_receipt_current(
        receipt: PackageInstallReceipt,
        entries: Mapping[str, PackageLockEntry],
    ) -> None:
        current = entries.get(receipt.entry.name)
        if current != receipt.entry:
            raise PackageError(
                f"package {receipt.entry.name!r} changed while an install transaction was pending"
            )

    def _rollback_receipt_sync(self, receipt: PackageInstallReceipt) -> None:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            self._assert_receipt_current(receipt, entries)
            _remove_path(receipt.target)
            if receipt.backup is not None and receipt.backup.exists():
                receipt.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(receipt.backup, receipt.target)
            if receipt.previous is None:
                entries.pop(receipt.entry.name, None)
            else:
                entries[receipt.entry.name] = receipt.previous
            self._write_lock_unlocked(entries)

    async def _finalize_remove_receipt(self, receipt: PackageRemoveReceipt) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._finalize_remove_receipt_sync, receipt)

    def _finalize_remove_receipt_sync(self, receipt: PackageRemoveReceipt) -> None:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            if receipt.name in entries:
                raise PackageError(
                    f"package {receipt.name!r} changed while a removal transaction was pending"
                )
            if receipt.backup is not None:
                _remove_path(receipt.backup)

    async def _rollback_remove_receipt(self, receipt: PackageRemoveReceipt) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._rollback_remove_receipt_sync, receipt)

    def _rollback_remove_receipt_sync(self, receipt: PackageRemoveReceipt) -> None:
        with self._thread_lock, _file_lock(self._process_lock_path):
            entries = self._read_lock_unlocked()
            if receipt.name in entries:
                raise PackageError(
                    f"package {receipt.name!r} changed while a removal transaction was pending"
                )
            if receipt.target.exists():
                raise PackageError(
                    f"package path {receipt.target} changed while a removal transaction was pending"
                )
            if receipt.backup is not None and receipt.backup.exists():
                receipt.target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(receipt.backup, receipt.target)
            entries[receipt.name] = receipt.previous
            self._write_lock_unlocked(entries)


def _source_path(value: str | Path) -> Path:
    text = str(value)
    if "://" in text and not text.startswith("file://"):
        raise PackageValidationError("Phase 23 accepts local package directories only")
    actual = Path(text[7:] if text.startswith("file://") else text).expanduser().resolve()
    if not actual.is_dir():
        raise PackageValidationError(f"package source is not a directory: {actual}")
    return actual


def _load_manifest(root: Path) -> PackageManifest:
    path = root / "pi-package.json"
    if not path.is_file():
        raise PackageValidationError(f"package manifest is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError(f"invalid package manifest {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PackageValidationError("pi-package.json must contain an object")
    manifest = PackageManifest.from_dict(value)
    for relative in (*manifest.extensions, *manifest.skills, *manifest.prompts, *manifest.themes):
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root.resolve()) or not candidate.exists():
            raise PackageValidationError(
                f"package {manifest.name!r} references a missing or unsafe path: {relative}"
            )
    return manifest


def _copy_package(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise PackageValidationError(f"package contains a symbolic link: {relative}")
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _directory_integrity(root: Path) -> str:
    digest = hashlib.sha256()
    files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.relative_to(root).parts
        and path.suffix not in {".pyc", ".pyo"}
    ]
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise PackageIntegrityError(f"installed package contains a symbolic link: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as file:
            while block := file.read(1024 * 1024):
                digest.update(block)
    return f"sha256-{digest.hexdigest()}"


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        file.seek(0)
        if os.name == "nt":  # pragma: no cover - exercised in Windows CI
            import msvcrt

            api = cast(Any, msvcrt)
            api.locking(file.fileno(), api.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":  # pragma: no cover - exercised in Windows CI
                import msvcrt

                api = cast(Any, msvcrt)
                api.locking(file.fileno(), api.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


__all__ = ["PackageInstallReceipt", "PackageManager", "PackageRemoveReceipt"]
