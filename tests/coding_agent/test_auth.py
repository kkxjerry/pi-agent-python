from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from pi_agent.coding_agent.auth import (
    AuthStorage,
    AuthStorageError,
    Credential,
    CredentialResolver,
)


@pytest.mark.asyncio
async def test_auth_storage_round_trip_redacts_public_view_and_uses_private_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "auth.json"
    storage = AuthStorage(path)
    credential = Credential(provider="fixture", kind="api_key", api_key="secret-value")

    await storage.set(credential)
    loaded = await storage.get("fixture")

    assert loaded == credential
    assert loaded is not None
    assert loaded.secret() == "secret-value"
    assert "secret-value" not in repr(loaded)
    assert "secret-value" not in json.dumps(loaded.public_dict())
    assert [item.provider for item in await storage.list()] == ["fixture"]
    assert await storage.remove("missing") is False
    assert await storage.remove("fixture") is True
    assert await storage.get("fixture") is None
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_expired_oauth_refresh_is_single_flight(tmp_path: Path) -> None:
    storage = AuthStorage(tmp_path / "auth.json")
    await storage.set(
        Credential(
            provider="fixture",
            kind="oauth",
            access_token="old",
            refresh_token="refresh",
            expires_at=time.time() - 10,
        )
    )
    calls = 0

    async def refresh(current: Credential) -> Credential:
        nonlocal calls
        calls += 1
        assert current.refresh_token == "refresh"
        await asyncio.sleep(0.02)
        return Credential(
            provider="fixture",
            kind="oauth",
            access_token="new",
            refresh_token="refresh-2",
            expires_at=time.time() + 3600,
        )

    first, second = await asyncio.gather(
        storage.resolve("fixture", refresh=refresh),
        storage.resolve("fixture", refresh=refresh),
    )

    assert calls == 1
    assert first is not None and first.secret() == "new"
    assert second is not None and second.secret() == "new"


@pytest.mark.asyncio
async def test_credential_resolver_precedence_is_explicit_then_environment_then_store(
    tmp_path: Path,
) -> None:
    storage = AuthStorage(tmp_path / "auth.json")
    await storage.set(Credential("fixture", "api_key", api_key="stored"))
    resolver = CredentialResolver(
        storage=storage,
        environment={"FIXTURE_API_KEY": "environment"},
    )

    assert await resolver.api_key("fixture", explicit="explicit") == "explicit"
    assert await resolver.api_key("fixture") == "environment"
    resolver.environment = {}
    assert await resolver.api_key("fixture") == "stored"


@pytest.mark.asyncio
async def test_corrupt_auth_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(AuthStorageError, match="could not read auth store"):
        await AuthStorage(path).list()
