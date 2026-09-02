from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from .event_stream import AssistantMessageEventStream
from .provider import Provider
from .types import Context, Model, StreamOptions

ModelLoader = Callable[[], Iterable[Model] | Awaitable[Iterable[Model]]]


class ModelRegistry:
    """Deterministic model catalog keyed by provider and model id."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], Model] = {}

    def register(self, model: Model, *, replace: bool = False) -> None:
        key = (model.provider, model.id)
        if key in self._models and not replace:
            raise ValueError(f"Model already registered: {model.provider}/{model.id}")
        self._models[key] = model

    def replace_provider_models(self, provider_id: str, models: Iterable[Model]) -> None:
        pending = list(models)
        for model in pending:
            if model.provider != provider_id:
                raise ValueError(
                    f"Model provider mismatch: expected {provider_id!r}, got {model.provider!r}"
                )
        retained = {key: model for key, model in self._models.items() if key[0] != provider_id}
        for model in pending:
            key = (provider_id, model.id)
            if key in retained:
                raise ValueError(f"Duplicate model: {provider_id}/{model.id}")
            retained[key] = model
        self._models = retained

    def get(self, provider_id: str, model_id: str) -> Model:
        try:
            return self._models[(provider_id, model_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown model: {provider_id}/{model_id}") from exc

    def resolve(self, model_id: str) -> Model:
        matches = [model for (_, candidate), model in self._models.items() if candidate == model_id]
        if not matches:
            raise KeyError(f"Unknown model id: {model_id}")
        if len(matches) > 1:
            providers = ", ".join(sorted(model.provider for model in matches))
            raise ValueError(f"Ambiguous model id {model_id!r}; providers: {providers}")
        return matches[0]

    def list(self, provider_id: str | None = None) -> list[Model]:
        values = [
            model
            for (provider, _), model in self._models.items()
            if provider_id is None or provider == provider_id
        ]
        return sorted(values, key=lambda model: (model.provider, model.id))


@dataclass(slots=True)
class _ProviderEntry:
    provider: Provider
    model_loader: ModelLoader | None = None
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProviderRegistry:
    """Routes model streams and lets each provider refresh its own catalog."""

    def __init__(self) -> None:
        self._providers: dict[str, _ProviderEntry] = {}
        self.models = ModelRegistry()

    def register(
        self,
        provider_id: str,
        provider: Provider,
        *,
        models: Iterable[Model] = (),
        model_loader: ModelLoader | None = None,
        replace: bool = False,
    ) -> None:
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        if provider_id in self._providers and not replace:
            raise ValueError(f"Provider already registered: {provider_id}")
        pending = list(models)
        for model in pending:
            if model.provider != provider_id:
                raise ValueError(
                    f"Model provider mismatch: expected {provider_id!r}, got {model.provider!r}"
                )
        self._providers[provider_id] = _ProviderEntry(provider, model_loader)
        self.models.replace_provider_models(provider_id, pending)

    def unregister(self, provider_id: str) -> None:
        if provider_id not in self._providers:
            raise KeyError(f"Unknown provider: {provider_id}")
        del self._providers[provider_id]
        self.models.replace_provider_models(provider_id, [])

    def get_provider(self, provider_id: str) -> Provider:
        try:
            return self._providers[provider_id].provider
        except KeyError as exc:
            raise KeyError(f"Unknown provider: {provider_id}") from exc

    async def refresh_models(self, provider_id: str) -> list[Model]:
        try:
            entry = self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"Unknown provider: {provider_id}") from exc
        if entry.model_loader is None:
            return self.models.list(provider_id)
        async with entry.refresh_lock:
            loaded = entry.model_loader()
            if inspect.isawaitable(loaded):
                loaded = await loaded
            pending = list(loaded)
            self.models.replace_provider_models(provider_id, pending)
            return self.models.list(provider_id)

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        return self.get_provider(model.provider).stream(model, context, options)
