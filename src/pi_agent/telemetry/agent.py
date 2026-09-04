from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pi_agent.agent import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from pi_agent.ai import AssistantMessage, ToolResultMessage
from pi_agent.coding_agent.agent_session import (
    AgentSession,
    AgentSessionEvent,
    CompactionEndEvent,
    CompactionStartEvent,
)

from .tracer import Meter, Span, Tracer
from .types import SpanStatus


@dataclass(slots=True)
class AgentTelemetry:
    session: AgentSession
    tracer: Tracer
    meter: Meter
    _run_span: Span | None = field(default=None, init=False)
    _tool_spans: dict[str, Span] = field(default_factory=dict, init=False)
    _compaction_span: Span | None = field(default=None, init=False)
    _unsubscribe: Any = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def attach(self) -> AgentTelemetry:
        if self._unsubscribe is not None:
            return self
        self._unsubscribe = self.session.subscribe(self._on_event)
        return self

    async def close(self, *, shutdown_tracer: bool = False) -> None:
        if self._closed:
            return
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for span in tuple(self._tool_spans.values()):
            await span.end("cancelled")
        self._tool_spans.clear()
        if self._compaction_span is not None:
            await self._compaction_span.end("cancelled")
            self._compaction_span = None
        if self._run_span is not None:
            await self._run_span.end("cancelled")
            self._run_span = None
        if shutdown_tracer:
            await self.tracer.shutdown()
        self._closed = True

    async def _on_event(self, event: AgentSessionEvent) -> None:
        model = self.session.agent.model
        common = {
            "session.id": self.session.session_id,
            "model.provider": model.provider,
            "model.id": model.id,
        }
        if isinstance(event, AgentStartEvent):
            if self._run_span is not None:
                await self._run_span.end("cancelled")
            self._run_span = self.tracer.start_span(
                "agent.run",
                kind="internal",
                attributes=common,
            )
            await self.meter.record("agent.runs", 1, attributes=common)
            return
        if isinstance(event, ToolExecutionStartEvent):
            span = self.tracer.start_span(
                f"tool.{event.tool_name}",
                kind="tool",
                attributes={
                    **common,
                    "tool.name": event.tool_name,
                    "tool.call_id": event.tool_call_id,
                },
                parent=self._run_span,
            )
            span.add_event("tool.arguments", {"arguments": str(event.args)})
            self._tool_spans[event.tool_call_id] = span
            return
        if isinstance(event, ToolExecutionEndEvent):
            tool_span = (
                self._tool_spans.pop(event.tool_call_id)
                if event.tool_call_id in self._tool_spans
                else None
            )
            if tool_span is not None:
                tool_span.set_attribute("tool.error", event.is_error)
                await tool_span.end("error" if event.is_error else "ok")
            await self.meter.record(
                "tool.calls",
                1,
                attributes={
                    **common,
                    "tool.name": event.tool_name,
                    "tool.error": event.is_error,
                },
            )
            return
        if isinstance(event, MessageEndEvent):
            message = event.message
            if isinstance(message, (AssistantMessage, ToolResultMessage)):
                usage = message.usage
                if usage is not None:
                    attributes = {
                        **common,
                        "message.role": message.role,
                    }
                    await self.meter.record(
                        "tokens.input",
                        usage.input,
                        attributes=attributes,
                    )
                    await self.meter.record(
                        "tokens.output",
                        usage.output,
                        attributes=attributes,
                    )
                    await self.meter.record(
                        "tokens.total",
                        usage.total_tokens,
                        attributes=attributes,
                    )
                    await self.meter.record(
                        "cost.total",
                        usage.cost.total,
                        attributes=attributes,
                    )
            return
        if isinstance(event, CompactionStartEvent):
            self._compaction_span = self.tracer.start_span(
                "agent.compaction",
                attributes={**common, "compaction.reason": event.reason},
                parent=self._run_span,
            )
            return
        if isinstance(event, CompactionEndEvent):
            compaction_span = self._compaction_span
            self._compaction_span = None
            if compaction_span is not None:
                if event.result is not None:
                    compaction_span.set_attributes(
                        {
                            "compaction.tokens_before": event.result.tokens_before,
                            "compaction.tokens_after": event.result.tokens_after,
                        }
                    )
                if event.error_message:
                    compaction_span.set_attribute("compaction.error", event.error_message)
                await compaction_span.end(
                    "cancelled" if event.aborted else "error" if event.error_message else "ok"
                )
            return
        if isinstance(event, AgentEndEvent):
            run_span = self._run_span
            self._run_span = None
            if run_span is not None:
                final = next(
                    (
                        message
                        for message in reversed(event.messages)
                        if isinstance(message, AssistantMessage)
                    ),
                    None,
                )
                status: SpanStatus = (
                    "cancelled"
                    if final is not None and final.stop_reason == "aborted"
                    else "error"
                    if final is not None and final.stop_reason == "error"
                    else "ok"
                )
                if final is not None:
                    run_span.set_attributes(
                        {
                            "agent.stop_reason": final.stop_reason,
                            "agent.error": final.error_message,
                        }
                    )
                await run_span.end(status)
            return


def attach_agent_telemetry(
    session: AgentSession,
    tracer: Tracer,
    meter: Meter | None = None,
) -> AgentTelemetry:
    return AgentTelemetry(session, tracer, meter or Meter(tracer.exporter)).attach()
