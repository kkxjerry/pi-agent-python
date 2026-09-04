from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pi_agent.agent import (
    AgentEndEvent,
    AgentStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from pi_agent.ai import AssistantMessage
from pi_agent.coding_agent.agent_session import AgentSession, AgentSessionEvent

from .metrics import Meter, MeterProvider
from .tracing import Span, Tracer, TracerProvider
from .types import SpanStatus


@dataclass(slots=True)
class AgentSessionInstrumentation:
    session: AgentSession
    tracer_provider: TracerProvider
    meter_provider: MeterProvider | None = None
    include_content: bool = False
    _unsubscribe: Callable[[], None] | None = field(default=None, init=False)
    _run_span: Span | None = field(default=None, init=False)
    _turn_span: Span | None = field(default=None, init=False)
    _tool_spans: dict[str, Span] = field(default_factory=dict, init=False)
    _tracer: Tracer = field(init=False, repr=False)
    _meter: Meter | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tracer = self.tracer_provider.get_tracer("pi_agent.agent_session")
        self._meter = (
            self.meter_provider.get_meter("pi_agent") if self.meter_provider is not None else None
        )
        self._unsubscribe = self.session.subscribe(self.on_event)

    async def on_event(self, event: AgentSessionEvent) -> None:
        if isinstance(event, AgentStartEvent):
            self._finish_open("cancelled")
            model = self.session.agent.model
            self._run_span = self._tracer.start_span(
                "agent.run",
                attributes={
                    "session.id": self.session.session_id,
                    "model.provider": model.provider,
                    "model.id": model.id,
                    "thinking.level": self.session.agent.thinking_level,
                },
            )
            return
        if isinstance(event, TurnStartEvent):
            if self._turn_span is not None:
                self._turn_span.end(status="cancelled")
            parent = self._run_span.context if self._run_span is not None else None
            self._turn_span = self._tracer.start_span("agent.turn", parent=parent)
            return
        if isinstance(event, ToolExecutionStartEvent):
            parent = (
                self._turn_span.context
                if self._turn_span is not None
                else self._run_span.context
                if self._run_span is not None
                else None
            )
            attributes: dict[str, Any] = {
                "tool.name": event.tool_name,
                "tool.call_id": event.tool_call_id,
            }
            if self.include_content:
                attributes["tool.arguments"] = event.args
            self._tool_spans[event.tool_call_id] = self._tracer.start_span(
                "agent.tool",
                kind="tool",
                parent=parent,
                attributes=attributes,
            )
            return
        if isinstance(event, ToolExecutionEndEvent):
            tool_span = self._tool_spans.pop(event.tool_call_id, None)
            if tool_span is not None:
                tool_span.set_attribute("tool.error", event.is_error)
                if self.include_content:
                    tool_span.set_attribute("tool.result", event.result.details)
                tool_span.end(status="error" if event.is_error else "ok")
            if self._meter is not None:
                self._meter.counter("tool.calls").add(
                    1,
                    {"tool.name": event.tool_name, "tool.error": event.is_error},
                )
            return
        if isinstance(event, TurnEndEvent):
            if self._turn_span is not None:
                _record_usage(self._turn_span, event.message)
                self._turn_span.end(status=_message_status(event.message))
                self._turn_span = None
            if self._meter is not None:
                usage = event.message.usage
                self._meter.counter("tokens.input").add(usage.input)
                self._meter.counter("tokens.output").add(usage.output)
                self._meter.counter("cost.total").add(usage.cost.total)
            return
        if isinstance(event, AgentEndEvent):
            for tool_span in self._tool_spans.values():
                tool_span.end(status="cancelled")
            self._tool_spans.clear()
            if self._turn_span is not None:
                self._turn_span.end(status="cancelled")
                self._turn_span = None
            if self._run_span is not None:
                final = next(
                    (
                        message
                        for message in reversed(event.messages)
                        if isinstance(message, AssistantMessage)
                    ),
                    None,
                )
                if final is not None:
                    _record_usage(self._run_span, final)
                    self._run_span.end(status=_message_status(final))
                else:
                    self._run_span.end(status="ok")
                self._run_span = None
            await self.tracer_provider.force_flush()
            if self.meter_provider is not None:
                await self.meter_provider.force_flush()

    async def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._finish_open("cancelled")
        await self.tracer_provider.force_flush()
        if self.meter_provider is not None:
            await self.meter_provider.force_flush()

    def _finish_open(self, status: SpanStatus) -> None:
        for tool_span in self._tool_spans.values():
            tool_span.end(status=status)
        self._tool_spans.clear()
        if self._turn_span is not None:
            self._turn_span.end(status=status)
            self._turn_span = None
        if self._run_span is not None:
            self._run_span.end(status=status)
            self._run_span = None


def instrument_agent_session(
    session: AgentSession,
    tracer_provider: TracerProvider,
    meter_provider: MeterProvider | None = None,
    *,
    include_content: bool = False,
) -> AgentSessionInstrumentation:
    return AgentSessionInstrumentation(
        session,
        tracer_provider,
        meter_provider,
        include_content,
    )


def _message_status(message: AssistantMessage) -> SpanStatus:
    if message.stop_reason == "aborted":
        return "cancelled"
    if message.stop_reason == "error":
        return "error"
    return "ok"


def _record_usage(span: Span, message: AssistantMessage) -> None:
    usage = message.usage
    span.set_attribute("usage.input_tokens", usage.input)
    span.set_attribute("usage.output_tokens", usage.output)
    span.set_attribute("usage.cache_read_tokens", usage.cache_read)
    span.set_attribute("usage.cache_write_tokens", usage.cache_write)
    span.set_attribute("usage.total_tokens", usage.total_tokens)
    span.set_attribute("usage.cost_total", usage.cost.total)


__all__ = ["AgentSessionInstrumentation", "instrument_agent_session"]
