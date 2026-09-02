from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeAlias

from .event_stream import AssistantMessageEventStream
from .types import Context, Model, StreamOptions


class Provider(Protocol):
    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream: ...


StreamFunction: TypeAlias = Callable[
    [Model, Context, StreamOptions | None], AssistantMessageEventStream
]


def provider_stream(provider: Provider) -> StreamFunction:
    return provider.stream
