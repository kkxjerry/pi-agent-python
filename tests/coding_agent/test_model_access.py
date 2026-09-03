from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.ai import FauxProvider, Model, ProviderRegistry, StreamOptions
from pi_agent.coding_agent.auth import AuthStorage, Credential, CredentialResolver
from pi_agent.coding_agent.model_access import ModelAccess


@pytest.mark.asyncio
async def test_model_access_combines_registry_selection_and_credentials(tmp_path: Path) -> None:
    model = Model(api="fixture", provider="fixture", id="model", name="Model")
    provider = FauxProvider([])
    registry = ProviderRegistry()
    registry.register("fixture", provider, models=[model])
    storage = AuthStorage(tmp_path / "auth.json")
    await storage.set(Credential("fixture", "api_key", api_key="stored-key"))
    access = ModelAccess(
        registry,
        CredentialResolver(storage=storage, environment={}),
        StreamOptions(temperature=0.2),
    )

    prepared = await access.prepare(model_id="model")

    assert prepared.model is model
    assert prepared.options.api_key == "stored-key"
    assert prepared.options.temperature == 0.2
    assert prepared.stream_fn == registry.stream


@pytest.mark.asyncio
async def test_model_access_requires_provider_for_ambiguous_model_id(tmp_path: Path) -> None:
    registry = ProviderRegistry()
    registry.register(
        "one",
        FauxProvider([]),
        models=[Model(api="fixture", provider="one", id="same", name="One")],
    )
    registry.register(
        "two",
        FauxProvider([]),
        models=[Model(api="fixture", provider="two", id="same", name="Two")],
    )
    access = ModelAccess(
        registry,
        CredentialResolver(storage=AuthStorage(tmp_path / "auth.json"), environment={}),
    )

    with pytest.raises(ValueError, match="Ambiguous model id"):
        await access.prepare(model_id="same")

    prepared = await access.prepare(provider="two", model_id="same", explicit_api_key="key")
    assert prepared.model.provider == "two"
    assert prepared.options.api_key == "key"
