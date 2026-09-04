from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.coding_agent import PackageManager, PackageManifest


@pytest.mark.asyncio
async def test_package_public_surface_and_empty_store(tmp_path: Path) -> None:
    manager = PackageManager(tmp_path / "packages")
    assert await manager.list() == ()
    assert PackageManifest.__name__ == "PackageManifest"
