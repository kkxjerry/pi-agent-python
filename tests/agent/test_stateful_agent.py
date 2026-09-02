from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from pi_agent.agent import (
    Agent,
    AgentEndEvent,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    default_convert_to_llm,
)
from pi_agent.ai import (
    AssistantMessage,
    AssistantMessageEventStream,
    CancellationToken,
    Context,
    DoneEvent,
    ErrorEvent,
    FauxProvider,
    Model,
    StartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCall,
    UserMessage,
)


def assistant_text(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text)], stop_reason="stop")


def config(model: Model, **kwargs: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm, **kwargs)


class GatedTextProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    def stream(
        self,
        model: Model,
        _context: Context,
        _options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = AssistantMessageEventStream()
        call = self.calls
        self.calls += 1

        async def run() -> None:
            try:
                if call == 0:
                    self.started.set()
                    await self.release.wait()
                final = AssistantMessage(
                    content=[TextContent(self.responses[call])],
                    api=model.api,
                    provider=model.provider,
                    model=model.id,
                    stop_reason="stop",
                )
                partial = copy.deepcopy(final)
                partial.content = []
                partial.stop_reason = "pending"
                stream.push(StartEvent(partial))
                partial.content.append(TextContent(""))
                stream.push(TextStartEvent(0, copy.deepcopy(partial)))
                partial.content[0].text = self.responses[call]
                stream.push(TextDeltaEvent(0, self.responses[call], copy.deepcopy(partial)))
                stream.push(TextEndEvent(0, self.responses[call], copy.deepcopy(partial)))
                stream.push(DoneEvent("stop", final))
            finally:
                stream.end()

        task = asyncio.create_task(run())
        stream.attach_task(task)
        return stream


class AbortThenTextProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    def stream(
        self,
        model: Model,
        _context: Context,
        options: StreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        stream = AssistantMessageEventStream()
        call = self.calls
        self.calls += 1

        async def run() -> None:
            try:
                if call == 0:
                    partial = AssistantMessage(
                        api=model.api,
                        provider=model.provider,
                        model=model.id,
                        stop_reason="pending",
                    )
                    stream.push(StartEvent(copy.deepcopy(partial)))
                    self.started.set()
                    assert options is not None and options.signal is not None
                    await options.signal.wait()
                    error = AssistantMessage(
                        api=model.api,
                        provider=model.provider,
                        model=model.id,
                        stop_reason="aborted",
                        error_message=options.signal.reason,
                    )
                    stream.push(ErrorEvent("aborted", error))
                else:
                    final = AssistantMessage(
                        content=[TextContent("reused")],
                        api=model.api,
                        provider=model.provider,
                        model=model.id,
                        stop_reason="stop",
                    )
                    stream.push(
                        StartEvent(
                            AssistantMessage(
                                api=model.api,
                                provider=model.provider,
                                model=model.id,
                                stop_reason="pending",
                            )
                        )
                    )
                    stream.push(
                        TextStartEvent(
                            0,
                            AssistantMessage(
                                content=[TextContent("")],
                                api=model.api,
                                provider=model.provider,
                                model=model.id,
                                stop_reason="pending",
                            ),
                        )
                    )
                    stream.push(TextDeltaEvent(0, "reused", copy.deepcopy(final)))
                    stream.push(TextEndEvent(0, "reused", copy.deepcopy(final)))
                    stream.push(DoneEvent("stop", final))
            finally:
                stream.end()

        task = asyncio.create_task(run())
        stream.attach_task(task)
        return stream


@pytest.mark.asyncio
async def test_stateful_agent_accumulates_messages_and_awaits_listener_barrier(
    model: Model,
) -> None:
    provider = FauxProvider([assistant_text("done")])
    agent = Agent(config(model), stream_fn=provider.stream)
    listener_order: list[str] = []

    async def listener(event: Any) -> None:
        if isinstance(event, AgentEndEvent):
            assert agent.is_streaming is True
            await asyncio.sleep(0)
            listener_order.append("agent-end-settled")

    agent.subscribe(listener)
    result = await agent.prompt("hello")

    assert [message.role for message in result] == ["user", "assistant"]
    assert [message.role for message in agent.messages] == ["user", "assistant"]
    assert listener_order == ["agent-end-settled"]
    assert agent.is_streaming is False


@pytest.mark.asyncio
async def test_stateful_agent_steering_queue_changes_the_next_turn(model: Model) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute(
        _call_id: str,
        _arguments: dict[str, Any],
        _signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        started.set()
        await release.wait()
        return AgentToolResult(content=[TextContent("worked")])

    tool = AgentTool(
        name="work",
        label="Work",
        description="Work",
        parameters={"type": "object"},
        execute=execute,
    )
    provider = FauxProvider(
        [
            AssistantMessage(content=[ToolCall("tool-1", "work", {})], stop_reason="toolUse"),
            assistant_text("steered"),
        ]
    )
    agent = Agent(config(model), stream_fn=provider.stream, tools=[tool])
    task = asyncio.create_task(agent.prompt("start"))
    await started.wait()
    agent.steer("steer-now")
    release.set()
    await task

    users = [message.content for message in agent.messages if isinstance(message, UserMessage)]
    assert users == ["start", "steer-now"]
    assert [message.role for message in provider.requests[1].context.messages] == [
        "user",
        "assistant",
        "toolResult",
        "user",
    ]


@pytest.mark.asyncio
async def test_stateful_agent_follow_up_queue_runs_after_a_text_stop(model: Model) -> None:
    provider = GatedTextProvider(["first", "second"])
    agent = Agent(config(model), stream_fn=provider.stream)
    task = asyncio.create_task(agent.prompt("start"))
    await provider.started.wait()
    agent.follow_up("next")
    provider.release.set()
    await task

    users = [message.content for message in agent.messages if isinstance(message, UserMessage)]
    assert users == ["start", "next"]
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_abort_finishes_with_aborted_message_and_agent_is_reusable(model: Model) -> None:
    provider = AbortThenTextProvider()
    agent = Agent(config(model), stream_fn=provider.stream)
    first = asyncio.create_task(agent.prompt("first"))
    await provider.started.wait()
    agent.abort("cancelled by test")
    first_result = await first

    assert isinstance(first_result[-1], AssistantMessage)
    assert first_result[-1].stop_reason == "aborted"
    assert agent.error_message == "cancelled by test"
    assert agent.is_streaming is False

    second_result = await agent.prompt("second")
    assert isinstance(second_result[-1], AssistantMessage)
    assert second_result[-1].content == [TextContent("reused")]
    assert agent.is_streaming is False


@pytest.mark.asyncio
async def test_listener_failure_is_recorded_without_breaking_the_run(model: Model) -> None:
    provider = FauxProvider([assistant_text("done")])
    agent = Agent(config(model), stream_fn=provider.stream)

    def broken(_event: Any) -> None:
        raise RuntimeError("listener failed")

    agent.subscribe(broken)
    result = await agent.prompt("hello")

    assert isinstance(result[-1], AssistantMessage)
    assert len(agent.listener_errors) > 0
    assert all(str(error) == "listener failed" for error in agent.listener_errors)
