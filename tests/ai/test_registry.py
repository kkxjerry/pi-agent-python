from __future__ import annotations

from dataclasses import replace

import pytest

from pi_agent.ai import (
    AssistantMessage,
    Context,
    FauxProvider,
    Model,
    ModelRegistry,
    ProviderRegistry,
    TextContent,
)


def test_model_registry_resolves_provider_scoped_and_unique_ids(model: Model) -> None:
    registry = ModelRegistry()
    registry.register(model)
    assert registry.get(model.provider, model.id) is model
    assert registry.resolve(model.id) is model

    other = replace(model, provider="other", name="Other")
    registry.register(other)
    with pytest.raises(ValueError, match="Ambiguous model id"):
        registry.resolve(model.id)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(model)
    assert [item.provider for item in registry.list()] == ["fixture", "other"]


@pytest.mark.asyncio
async def test_provider_registry_routes_streams_and_refreshes_owned_models(model: Model) -> None:
    provider = FauxProvider([AssistantMessage(content=[TextContent("ok")], stop_reason="stop")])
    refreshed = replace(model, id="refreshed", name="Refreshed")
    calls = 0

    async def load_models() -> list[Model]:
        nonlocal calls
        calls += 1
        return [refreshed]

    registry = ProviderRegistry()
    registry.register(
        model.provider,
        provider,
        models=[model],
        model_loader=load_models,
    )
    stream = registry.stream(model, Context())
    assert (await stream.result()).content == [TextContent("ok")]

    assert await registry.refresh_models(model.provider) == [refreshed]
    assert calls == 1
    assert registry.models.list(model.provider) == [refreshed]
    with pytest.raises(KeyError, match="Unknown model"):
        registry.models.get(model.provider, model.id)


def test_provider_refresh_is_atomic_on_provider_mismatch(model: Model) -> None:
    registry = ProviderRegistry()
    registry.register(model.provider, FauxProvider(), models=[model])

    with pytest.raises(ValueError, match="provider mismatch"):
        registry.models.replace_provider_models(
            model.provider,
            [replace(model, provider="wrong")],
        )
    assert registry.models.list(model.provider) == [model]
