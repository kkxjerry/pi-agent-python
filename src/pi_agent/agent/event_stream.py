from __future__ import annotations

from pi_agent.ai import EventStream

from .events import AgentEndEvent, AgentEvent
from .types import AgentMessage


class AgentEventStream(EventStream[AgentEvent, list[AgentMessage]]):
    def __init__(self) -> None:
        super().__init__(
            is_complete=lambda event: isinstance(event, AgentEndEvent),
            extract_result=lambda event: event.messages if isinstance(event, AgentEndEvent) else [],
        )
