from __future__ import annotations

import copy
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field

from pi_agent.ai import (
    AssistantMessageEventStream,
    Context,
    Model,
    ProviderRegistry,
    StreamFunction,
    StreamOptions,
)

from .auth import CredentialResolver, RefreshCredential


@dataclass(frozen=True, slots=True)
class PreparedModel:
    model: Model
    stream_fn: StreamFunction
    options: StreamOptions

    def stream(
        self, context: Context
    ) -> AssistantMessageEventStream | Awaitable[AssistantMessageEventStream]:
        return self.stream_fn(self.model, context, self.options)


@dataclass(slots=True)
class ModelAccess:
    """Resolve a registered model and its credentialized stream options."""

    registry: ProviderRegistry
    credentials: CredentialResolver
    base_options: StreamOptions = field(default_factory=StreamOptions)

    async def prepare(
        self,
        *,
        provider: str | None = None,
        model_id: str,
        explicit_api_key: str | None = None,
        refresh: RefreshCredential | None = None,
        options: StreamOptions | None = None,
    ) -> PreparedModel:
        model = (
            self.registry.models.get(provider, model_id)
            if provider is not None
            else self.registry.models.resolve(model_id)
        )
        selected = copy.deepcopy(options or self.base_options)
        selected.api_key = await self.credentials.api_key(
            model.provider,
            explicit=explicit_api_key or selected.api_key,
            refresh=refresh,
        )
        return PreparedModel(model, self.registry.stream, selected)

    async def refresh_models(self, providers: Iterable[str]) -> tuple[Model, ...]:
        models: list[Model] = []
        for provider in providers:
            models.extend(await self.registry.refresh_models(provider))
        return tuple(sorted(models, key=lambda item: (item.provider, item.id)))


__all__ = ["ModelAccess", "PreparedModel"]
