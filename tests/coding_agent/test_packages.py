from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pi_agent.coding_agent.packages import (
    PackageError,
    PackageManager,
    PackageValidationError,
)


def make_package(
    root: Path,
    *,
    name: str = "sample",
    version: str = "1.0.0",
    extension_body: str = "def activate(api):\n    pass\n",
    dependencies: tuple[str, ...] = (),
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "extensions").mkdir(exist_ok=True)
    (root / "extensions" / "main.py").write_text(extension_body, encoding="utf-8")
    (root / "skills" / "sample").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "sample" / "SKILL.md").write_text("skill", encoding="utf-8")
    (root / "pi-package.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "extensions": ["extensions/main.py"],
                "skills": ["skills"],
                "dependencies": list(dependencies),
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_local_package_install_lock_verify_and_remove(tmp_path: Path) -> None:
    source = make_package(tmp_path / "source")
    manager = PackageManager(tmp_path / "user")

    receipt = await manager.install(source)

    assert receipt.entry.name == "sample"
    assert receipt.entry.version == "1.0.0"
    assert manager.package_path("sample").is_dir()
    assert manager.lock_path.is_file()
    assert [item.name for item in await manager.list()] == ["sample"]
    assert (await manager.verify("sample")).valid is True
    descriptors = await manager.extension_descriptors()
    assert len(descriptors) == 1
    assert descriptors[0].source == "package:sample"
    assert descriptors[0].path.is_file()

    (manager.package_path("sample") / "extensions" / "main.py").write_text(
        "tampered", encoding="utf-8"
    )
    verification = await manager.verify("sample")
    assert verification.valid is False
    assert verification.error == "integrity mismatch"

    assert await manager.remove("sample") is True
    assert await manager.remove("sample") is False
    assert await manager.list() == ()


@pytest.mark.asyncio
async def test_unfinalized_update_can_restore_previous_package_and_lock(tmp_path: Path) -> None:
    first = make_package(tmp_path / "first", version="1.0.0")
    second = make_package(
        tmp_path / "second",
        version="2.0.0",
        extension_body="VALUE = 'second'\ndef activate(api):\n    pass\n",
    )
    manager = PackageManager(tmp_path / "user")
    await manager.install(first)

    receipt = await manager.update("sample", second, finalize=False)
    assert (await manager.get("sample")).version == "2.0.0"  # type: ignore[union-attr]

    await receipt.rollback()

    restored = await manager.get("sample")
    assert restored is not None and restored.version == "1.0.0"
    assert (await manager.verify("sample")).valid is True
    assert "VALUE" not in (manager.package_path("sample") / "extensions" / "main.py").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_dependencies_block_install_and_removal(tmp_path: Path) -> None:
    base = make_package(tmp_path / "base", name="base")
    child = make_package(tmp_path / "child", name="child", dependencies=("base",))
    manager = PackageManager(tmp_path / "user")

    with pytest.raises(PackageValidationError, match="dependencies are not installed: base"):
        await manager.install(child)

    await manager.install(base)
    await manager.install(child)
    with pytest.raises(PackageError, match="required by: child"):
        await manager.remove("base")


@pytest.mark.asyncio
async def test_package_paths_and_symlinks_fail_closed(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "pi-package.json").write_text(
        json.dumps(
            {
                "name": "unsafe",
                "version": "1",
                "extensions": ["../outside.py"],
            }
        ),
        encoding="utf-8",
    )
    manager = PackageManager(tmp_path / "user")
    with pytest.raises(PackageValidationError, match="unsafe path"):
        await manager.install(unsafe)

    if os.name != "nt":
        linked = make_package(tmp_path / "linked")
        target = linked / "target.txt"
        target.write_text("target", encoding="utf-8")
        (linked / "link.txt").symlink_to(target)
        with pytest.raises(PackageValidationError, match="symbolic link"):
            await manager.install(linked)


@pytest.mark.asyncio
async def test_remote_sources_are_not_implicitly_downloaded(tmp_path: Path) -> None:
    manager = PackageManager(tmp_path / "user")
    with pytest.raises(PackageValidationError, match="local package directories only"):
        await manager.install("https://example.invalid/package.tgz")


@pytest.mark.asyncio
async def test_stale_receipt_cannot_roll_back_a_newer_cross_process_update(tmp_path: Path) -> None:
    first = make_package(tmp_path / "first-pending", version="1.0.0")
    second = make_package(tmp_path / "second-committed", version="2.0.0")
    root = tmp_path / "user-concurrent"
    first_manager = PackageManager(root)
    second_manager = PackageManager(root)

    pending = await first_manager.install(first, finalize=False)
    await second_manager.install(second)

    with pytest.raises(PackageError, match="changed while an install transaction was pending"):
        await pending.rollback()

    current = await first_manager.get("sample")
    assert current is not None and current.version == "2.0.0"
    assert (await first_manager.verify("sample")).valid is True


@pytest.mark.asyncio
async def test_package_directory_extension_receives_unique_relative_name(tmp_path: Path) -> None:
    source = tmp_path / "directory-extension"
    (source / "extensions" / "nested").mkdir(parents=True)
    (source / "extensions" / "nested" / "__init__.py").write_text(
        "def activate(api):\n    pass\n",
        encoding="utf-8",
    )
    (source / "pi-package.json").write_text(
        json.dumps(
            {
                "name": "directory-package",
                "version": "1.0.0",
                "extensions": ["extensions/nested"],
            }
        ),
        encoding="utf-8",
    )
    manager = PackageManager(tmp_path / "directory-user")
    await manager.install(source)

    descriptors = await manager.extension_descriptors()
    assert descriptors[0].name == "directory-package:extensions:nested"
    assert descriptors[0].path.is_dir()


@pytest.mark.asyncio
async def test_staged_remove_can_restore_package_directory_and_lock(tmp_path: Path) -> None:
    source = make_package(tmp_path / "remove-source", version="1.0.0")
    manager = PackageManager(tmp_path / "remove-user")
    await manager.install(source)

    receipt = await manager.stage_remove("sample")

    assert receipt is not None
    assert await manager.get("sample") is None
    assert not manager.package_path("sample").exists()

    await receipt.rollback()

    restored = await manager.get("sample")
    assert restored is not None and restored.version == "1.0.0"
    assert (await manager.verify("sample")).valid is True
