from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from pi_agent.ai import EventStream

from .events import AgentEndEvent, AgentEvent
from .types import AgentMessage

AgentEventSink = Callable[[AgentEvent], Awaitable[None] | None]


class AgentEventStream(EventStream[AgentEvent, list[AgentMessage]]):
    """Agent event stream with an optional awaited event sink.

    The public low-level API buffers events for async iteration. Stateful Agent
    runs instead use ``buffer_events=False`` and an awaited sink, matching the
    official direct ``runAgentLoop(..., emit)`` path without retaining every
    long-running event in memory.
    """

    def __init__(
        self,
        event_sink: AgentEventSink | None = None,
        *,
        buffer_events: bool = True,
    ) -> None:
        super().__init__(
            is_complete=lambda event: isinstance(event, AgentEndEvent),
            extract_result=lambda event: event.messages if isinstance(event, AgentEndEvent) else [],
        )
        self._event_sink = event_sink
        self._buffer_events = buffer_events

    async def emit(self, event: AgentEvent) -> None:
        if self._buffer_events:
            self.push(event)
        if self._event_sink is not None:
            pending = self._event_sink(event)
            if inspect.isawaitable(pending):
                await pending
        if not self._buffer_events and isinstance(event, AgentEndEvent):
            self.end(event.messages)
