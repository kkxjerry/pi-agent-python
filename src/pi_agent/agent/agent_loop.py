from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from typing import Any, TypeGuard, TypeVar

from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    Context,
    DoneEvent,
    ErrorEvent,
    Message,
    Model,
    StartEvent,
    StreamFunction,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from .event_stream import AgentEventSink, AgentEventStream
from .events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .schema import ToolArgumentsError, validate_json_schema
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    CompletedTurnContext,
)

T = TypeVar("T")
_TRUNCATED_TOOL_TEMPLATE = (
    'Tool call "{name}" was not executed: the response hit the output token limit, '
    "so its arguments may be truncated. Re-issue the tool call with complete arguments."
)


@dataclass(slots=True)
class _PreparedToolCall:
    index: int
    call: ToolCall
    tool: AgentTool
    args: dict[str, Any]


@dataclass(slots=True)
class _ToolOutcome:
    index: int
    call: ToolCall
    args: dict[str, Any]
    result: AgentToolResult
    is_error: bool


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Filter application-only messages at the provider boundary."""

    return [message for message in messages if _is_llm_message(message)]


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
    event_sink: AgentEventSink | None = None,
) -> AgentEventStream:
    """Start a low-level prompt run and return its event stream immediately.

    The caller-owned context is never mutated. The returned ``agent_end`` messages
    contain only artifacts produced by this invocation, including the prompts.
    """

    stream = AgentEventStream(event_sink, buffer_events=event_sink is None)
    runtime_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *copy.deepcopy(prompts)],
        tools=list(context.tools),
    )
    task = asyncio.create_task(
        _run_guarded(
            stream,
            prompts=copy.deepcopy(prompts),
            context=runtime_context,
            config=config,
            stream_fn=stream_fn,
            signal=signal,
            is_continue=False,
        )
    )
    stream.attach_task(task)
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
    event_sink: AgentEventSink | None = None,
) -> AgentEventStream:
    """Continue a non-assistant transcript tail without adding a user prompt."""

    if not context.messages:
        raise ValueError("Cannot continue an empty context")
    if isinstance(context.messages[-1], AssistantMessage):
        raise ValueError("Cannot continue when the last message is an assistant message")
    stream = AgentEventStream(event_sink, buffer_events=event_sink is None)
    runtime_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=context.messages,
        tools=list(context.tools),
    )
    task = asyncio.create_task(
        _run_guarded(
            stream,
            prompts=[],
            context=runtime_context,
            config=config,
            stream_fn=stream_fn,
            signal=signal,
            is_continue=True,
        )
    )
    stream.attach_task(task)
    return stream


async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
) -> list[AgentMessage]:
    return await agent_loop(
        prompts,
        context,
        config,
        stream_fn=stream_fn,
        signal=signal,
    ).result()


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
) -> list[AgentMessage]:
    return await agent_loop_continue(
        context,
        config,
        stream_fn=stream_fn,
        signal=signal,
    ).result()


async def _run_guarded(
    stream: AgentEventStream,
    *,
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: StreamFunction,
    signal: CancellationToken | None,
    is_continue: bool,
) -> None:
    new_messages: list[AgentMessage] = list(copy.deepcopy(prompts))
    try:
        await _run_loop(
            stream,
            prompts=prompts,
            context=context,
            config=config,
            stream_fn=stream_fn,
            signal=signal,
            is_continue=is_continue,
            new_messages=new_messages,
        )
    except asyncio.CancelledError as exc:
        await _finish_after_loop_failure(
            stream,
            context,
            config.model,
            "aborted",
            str(exc) or "Operation aborted",
            new_messages,
        )
    except Exception as exc:
        await _finish_after_loop_failure(
            stream,
            context,
            config.model,
            "error",
            str(exc),
            new_messages,
        )
    finally:
        stream.end()


async def _run_loop(
    stream: AgentEventStream,
    *,
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: StreamFunction,
    signal: CancellationToken | None,
    is_continue: bool,
    new_messages: list[AgentMessage],
) -> None:
    active_signal = signal or config.stream_options.signal
    model = config.model
    thinking_level = config.thinking_level
    last_completed: CompletedTurnContext | None = None

    await stream.emit(AgentStartEvent())
    await stream.emit(TurnStartEvent())
    if not is_continue:
        for prompt in prompts:
            await stream.emit(MessageStartEvent(copy.deepcopy(prompt)))
            await stream.emit(MessageEndEvent(copy.deepcopy(prompt)))

    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages is not None:
        pending_messages = await config.get_steering_messages()

    while True:
        has_more_tool_calls = True
        while has_more_tool_calls or pending_messages:
            if last_completed is not None:
                if config.prepare_next_turn is not None:
                    update = await _maybe_await(config.prepare_next_turn(last_completed))
                    if update is not None:
                        context, model, thinking_level = _apply_turn_update(
                            update,
                            context=context,
                            model=model,
                            thinking_level=thinking_level,
                        )
                if not pending_messages and config.get_steering_messages is not None:
                    pending_messages = await config.get_steering_messages()
                await stream.emit(TurnStartEvent())

            if pending_messages:
                copied = copy.deepcopy(pending_messages)
                for message in copied:
                    await stream.emit(MessageStartEvent(message))
                    await stream.emit(MessageEndEvent(message))
                    context.messages.append(message)
                    new_messages.append(message)
                pending_messages = []

            _raise_if_cancelled(active_signal)
            assistant = await _stream_assistant_response(
                stream,
                context=context,
                config=config,
                model=model,
                thinking_level=thinking_level,
                stream_fn=stream_fn,
                signal=active_signal,
                new_messages=new_messages,
            )
            if assistant.stop_reason in {"error", "aborted"}:
                await stream.emit(TurnEndEvent(copy.deepcopy(assistant), []))
                await stream.emit(AgentEndEvent(copy.deepcopy(new_messages)))
                return

            tool_calls = [block for block in assistant.content if isinstance(block, ToolCall)]
            tool_results, executions = await _execute_tool_batch(
                stream,
                assistant=assistant,
                calls=tool_calls,
                context=context,
                config=config,
                signal=active_signal,
                new_messages=new_messages,
            )
            terminate = bool(executions) and all(outcome.result.terminate for outcome in executions)
            has_more_tool_calls = bool(tool_calls) and not terminate

            await stream.emit(TurnEndEvent(copy.deepcopy(assistant), copy.deepcopy(tool_results)))
            last_completed = CompletedTurnContext(
                message=copy.deepcopy(assistant),
                tool_results=copy.deepcopy(tool_results),
                context=context,
                new_messages=new_messages,
            )
            if config.should_stop_after_turn is not None and await _maybe_await(
                config.should_stop_after_turn(last_completed)
            ):
                await stream.emit(AgentEndEvent(copy.deepcopy(new_messages)))
                return
            if config.get_steering_messages is not None:
                pending_messages = await config.get_steering_messages()

        follow_up: list[AgentMessage] = []
        if config.get_follow_up_messages is not None:
            follow_up = await config.get_follow_up_messages()
        if follow_up:
            pending_messages = copy.deepcopy(follow_up)
            continue
        break

    await stream.emit(AgentEndEvent(copy.deepcopy(new_messages)))


async def _stream_assistant_response(
    stream: AgentEventStream,
    *,
    context: AgentContext,
    config: AgentLoopConfig,
    model: Model,
    thinking_level: Any,
    stream_fn: StreamFunction,
    signal: CancellationToken | None,
    new_messages: list[AgentMessage],
) -> AssistantMessage:
    transformed: list[AgentMessage] = list(copy.deepcopy(context.messages))
    if config.transform_context is not None:
        transformed = await _maybe_await(config.transform_context(transformed, signal))
    llm_messages = await _maybe_await(config.convert_to_llm(transformed))
    if not all(_is_llm_message(message) for message in llm_messages):
        raise TypeError("convert_to_llm must return only model Message values")

    options = replace(
        config.stream_options,
        headers=dict(config.stream_options.headers),
        metadata=dict(config.stream_options.metadata),
        sampling_params=dict(config.stream_options.sampling_params),
        signal=signal,
    )
    if thinking_level != "off":
        options.reasoning = thinking_level
    if config.get_api_key is not None:
        refreshed_key = await _maybe_await(config.get_api_key(model.provider))
        options.api_key = refreshed_key or options.api_key

    provider_context = Context(
        system_prompt=context.system_prompt or None,
        messages=list(llm_messages),
        tools=[tool.definition() for tool in context.tools] or None,
    )
    response_or_awaitable = stream_fn(model, provider_context, options)
    response = (
        await response_or_awaitable
        if inspect.isawaitable(response_or_awaitable)
        else response_or_awaitable
    )
    inserted = False
    assistant_index = -1
    final: AssistantMessage | None = None

    async for provider_event in response:
        if isinstance(provider_event, StartEvent):
            if inserted:
                raise RuntimeError("Provider emitted more than one start event")
            partial = copy.deepcopy(provider_event.partial)
            context.messages.append(partial)
            new_messages.append(copy.deepcopy(partial))
            assistant_index = len(context.messages) - 1
            inserted = True
            await stream.emit(MessageStartEvent(copy.deepcopy(partial)))
            continue

        if isinstance(provider_event, (DoneEvent, ErrorEvent)):
            terminal = copy.deepcopy(
                provider_event.message
                if isinstance(provider_event, DoneEvent)
                else provider_event.error
            )
            if not inserted:
                pending = copy.deepcopy(terminal)
                pending.stop_reason = "pending"
                pending.error_message = None
                context.messages.append(pending)
                new_messages.append(copy.deepcopy(pending))
                assistant_index = len(context.messages) - 1
                inserted = True
                await stream.emit(MessageStartEvent(copy.deepcopy(pending)))
            context.messages[assistant_index] = terminal
            new_messages[-1] = copy.deepcopy(terminal)
            await stream.emit(MessageEndEvent(copy.deepcopy(terminal)))
            final = terminal
            continue

        partial = copy.deepcopy(provider_event.partial)
        if not inserted:
            context.messages.append(partial)
            new_messages.append(copy.deepcopy(partial))
            assistant_index = len(context.messages) - 1
            inserted = True
            await stream.emit(MessageStartEvent(copy.deepcopy(partial)))
        else:
            context.messages[assistant_index] = partial
            new_messages[-1] = copy.deepcopy(partial)
        await stream.emit(MessageUpdateEvent(copy.deepcopy(partial), copy.deepcopy(provider_event)))

    if not response.done:
        raise RuntimeError("Provider stream ended without a terminal event")
    resolved = copy.deepcopy(await response.result())
    if final is None:
        if not inserted:
            pending = copy.deepcopy(resolved)
            pending.stop_reason = "pending"
            pending.error_message = None
            context.messages.append(pending)
            new_messages.append(copy.deepcopy(pending))
            await stream.emit(MessageStartEvent(copy.deepcopy(pending)))
            assistant_index = len(context.messages) - 1
        context.messages[assistant_index] = resolved
        new_messages[-1] = copy.deepcopy(resolved)
        await stream.emit(MessageEndEvent(copy.deepcopy(resolved)))
        final = resolved
    return final


async def _execute_tool_batch(
    stream: AgentEventStream,
    *,
    assistant: AssistantMessage,
    calls: list[ToolCall],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken | None,
    new_messages: list[AgentMessage],
) -> tuple[list[ToolResultMessage], list[_ToolOutcome]]:
    if not calls:
        return [], []
    if assistant.stop_reason == "length":
        outcomes: list[_ToolOutcome] = []
        tool_results: list[ToolResultMessage] = []
        for index, call in enumerate(calls):
            await stream.emit(
                ToolExecutionStartEvent(call.id, call.name, copy.deepcopy(call.arguments))
            )
            outcome = _ToolOutcome(
                index=index,
                call=call,
                args=copy.deepcopy(call.arguments),
                result=_failed_tool_result(_TRUNCATED_TOOL_TEMPLATE.format(name=call.name)),
                is_error=True,
            )
            await _emit_tool_end(stream, outcome)
            message = await _emit_tool_result_message(stream, outcome)
            context.messages.append(message)
            new_messages.append(copy.deepcopy(message))
            outcomes.append(outcome)
            tool_results.append(message)
        return tool_results, outcomes

    by_name = {tool.name: tool for tool in context.tools}
    effective_mode = config.tool_execution
    if any(
        (tool := by_name.get(call.name)) is not None and tool.execution_mode == "sequential"
        for call in calls
    ):
        effective_mode = "sequential"
    if effective_mode == "sequential":
        return await _execute_sequential_batch(
            stream,
            assistant=assistant,
            calls=calls,
            context=context,
            config=config,
            signal=signal,
            new_messages=new_messages,
        )
    return await _execute_parallel_batch(
        stream,
        assistant=assistant,
        calls=calls,
        context=context,
        config=config,
        signal=signal,
        new_messages=new_messages,
    )


async def _execute_sequential_batch(
    stream: AgentEventStream,
    *,
    assistant: AssistantMessage,
    calls: list[ToolCall],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken | None,
    new_messages: list[AgentMessage],
) -> tuple[list[ToolResultMessage], list[_ToolOutcome]]:
    outcomes: list[_ToolOutcome] = []
    messages: list[ToolResultMessage] = []
    for index, call in enumerate(calls):
        await stream.emit(
            ToolExecutionStartEvent(call.id, call.name, copy.deepcopy(call.arguments))
        )
        prepared_or_outcome = await _prepare_tool_call(
            index=index,
            call=call,
            assistant=assistant,
            context=context,
            config=config,
            signal=signal,
        )
        if isinstance(prepared_or_outcome, _PreparedToolCall):
            outcome = await _run_prepared_tool(
                prepared_or_outcome,
                assistant=assistant,
                context=context,
                config=config,
                signal=signal,
                stream=stream,
            )
        else:
            outcome = prepared_or_outcome
        await _emit_tool_end(stream, outcome)
        message = await _emit_tool_result_message(stream, outcome)
        context.messages.append(message)
        new_messages.append(copy.deepcopy(message))
        outcomes.append(outcome)
        messages.append(message)
        if signal is not None and signal.cancelled:
            break
    return messages, outcomes


async def _execute_parallel_batch(
    stream: AgentEventStream,
    *,
    assistant: AssistantMessage,
    calls: list[ToolCall],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken | None,
    new_messages: list[AgentMessage],
) -> tuple[list[ToolResultMessage], list[_ToolOutcome]]:
    outcomes: dict[int, _ToolOutcome] = {}
    prepared: list[_PreparedToolCall] = []
    for index, call in enumerate(calls):
        await stream.emit(
            ToolExecutionStartEvent(call.id, call.name, copy.deepcopy(call.arguments))
        )
        prepared_or_outcome = await _prepare_tool_call(
            index=index,
            call=call,
            assistant=assistant,
            context=context,
            config=config,
            signal=signal,
        )
        if isinstance(prepared_or_outcome, _PreparedToolCall):
            prepared.append(prepared_or_outcome)
        else:
            outcomes[index] = prepared_or_outcome
            await _emit_tool_end(stream, prepared_or_outcome)

    tasks = [
        asyncio.create_task(
            _run_prepared_tool(
                item,
                assistant=assistant,
                context=context,
                config=config,
                signal=signal,
                stream=stream,
            )
        )
        for item in prepared
    ]
    try:
        for completed in asyncio.as_completed(tasks):
            outcome = await completed
            outcomes[outcome.index] = outcome
            await _emit_tool_end(stream, outcome)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    ordered = [outcomes[index] for index in range(len(calls)) if index in outcomes]
    messages: list[ToolResultMessage] = []
    for outcome in ordered:
        message = await _emit_tool_result_message(stream, outcome)
        context.messages.append(message)
        new_messages.append(copy.deepcopy(message))
        messages.append(message)
    return messages, ordered


async def _prepare_tool_call(
    *,
    index: int,
    call: ToolCall,
    assistant: AssistantMessage,
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken | None,
) -> _PreparedToolCall | _ToolOutcome:
    args: dict[str, Any] = copy.deepcopy(call.arguments)
    tool = next((candidate for candidate in context.tools if candidate.name == call.name), None)
    if tool is None:
        return _ToolOutcome(
            index,
            call,
            args,
            _failed_tool_result(f"Tool {call.name} not found"),
            True,
        )
    try:
        _raise_if_cancelled(signal)
        if tool.prepare_arguments is not None:
            prepared = await _maybe_await(tool.prepare_arguments(args))
            if not isinstance(prepared, dict):
                raise ToolArgumentsError("prepared arguments must be an object")
            args = prepared
        validate_json_schema(args, tool.parameters)
        if config.before_tool_call is not None:
            decision = await _maybe_await(
                config.before_tool_call(
                    BeforeToolCallContext(
                        assistant_message=copy.deepcopy(assistant),
                        tool_call=copy.deepcopy(call),
                        args=copy.deepcopy(args),
                        context=context,
                    ),
                    signal,
                )
            )
            if decision is not None and decision.block:
                reason = decision.reason or f"Tool {call.name} was blocked"
                return _ToolOutcome(
                    index,
                    call,
                    args,
                    AgentToolResult(
                        content=[TextContent(reason)],
                        details={},
                        terminate=decision.terminate,
                    ),
                    True,
                )
        return _PreparedToolCall(index, call, tool, args)
    except asyncio.CancelledError as exc:
        return _ToolOutcome(
            index,
            call,
            args,
            _failed_tool_result(str(exc) or "Operation aborted"),
            True,
        )
    except ToolArgumentsError as exc:
        return _ToolOutcome(
            index,
            call,
            args,
            _failed_tool_result(f"Invalid arguments for tool {call.name}: {exc}"),
            True,
        )
    except Exception as exc:
        return _ToolOutcome(index, call, args, _failed_tool_result(str(exc)), True)


async def _run_prepared_tool(
    prepared: _PreparedToolCall,
    *,
    assistant: AssistantMessage,
    context: AgentContext,
    config: AgentLoopConfig,
    signal: CancellationToken | None,
    stream: AgentEventStream,
) -> _ToolOutcome:
    update_tasks: list[asyncio.Task[None]] = []
    accepting_updates = True

    def on_update(partial_result: AgentToolResult) -> None:
        if not accepting_updates:
            return
        update_tasks.append(
            asyncio.create_task(
                stream.emit(
                    ToolExecutionUpdateEvent(
                        tool_call_id=prepared.call.id,
                        tool_name=prepared.call.name,
                        args=copy.deepcopy(prepared.args),
                        partial_result=copy.deepcopy(partial_result),
                    )
                )
            )
        )

    is_error = False
    try:
        _raise_if_cancelled(signal)
        execution = prepared.tool.execute(
            prepared.call.id,
            copy.deepcopy(prepared.args),
            signal,
            on_update,
        )
        result = (
            await asyncio.wait_for(execution, timeout=prepared.tool.timeout)
            if prepared.tool.timeout is not None
            else await execution
        )
        is_error = result.is_error
        _raise_if_cancelled(signal)
    except TimeoutError:
        result = _failed_tool_result(
            f"Tool {prepared.call.name} timed out after {prepared.tool.timeout:g}s"
        )
        is_error = True
    except asyncio.CancelledError as exc:
        result = _failed_tool_result(str(exc) or "Operation aborted")
        is_error = True
    except Exception as exc:
        result = _failed_tool_result(str(exc))
        is_error = True
    finally:
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks)

    if config.after_tool_call is not None:
        try:
            override = await _maybe_await(
                config.after_tool_call(
                    AfterToolCallContext(
                        assistant_message=copy.deepcopy(assistant),
                        tool_call=copy.deepcopy(prepared.call),
                        args=copy.deepcopy(prepared.args),
                        result=copy.deepcopy(result),
                        is_error=is_error,
                        context=context,
                    ),
                    signal,
                )
            )
            if override is not None:
                result, is_error = _apply_after_tool_override(result, is_error, override)
        except Exception as exc:
            result = _failed_tool_result(str(exc))
            is_error = True
    return _ToolOutcome(
        prepared.index,
        prepared.call,
        prepared.args,
        result,
        is_error,
    )


def _apply_after_tool_override(
    result: AgentToolResult,
    is_error: bool,
    override: AfterToolCallResult,
) -> tuple[AgentToolResult, bool]:
    updated = copy.deepcopy(result)
    if override.content is not None:
        updated.content = copy.deepcopy(override.content)
    if override.replace_details or override.details is not None:
        updated.details = copy.deepcopy(override.details)
    if override.replace_usage or override.usage is not None:
        updated.usage = copy.deepcopy(override.usage)
    if override.terminate is not None:
        updated.terminate = override.terminate
    if override.is_error is not None:
        is_error = override.is_error
    return updated, is_error


async def _emit_tool_end(stream: AgentEventStream, outcome: _ToolOutcome) -> None:
    await stream.emit(
        ToolExecutionEndEvent(
            tool_call_id=outcome.call.id,
            tool_name=outcome.call.name,
            result=copy.deepcopy(outcome.result),
            is_error=outcome.is_error,
            args=copy.deepcopy(outcome.args),
        )
    )


async def _emit_tool_result_message(
    stream: AgentEventStream,
    outcome: _ToolOutcome,
) -> ToolResultMessage:
    message = ToolResultMessage(
        tool_call_id=outcome.call.id,
        tool_name=outcome.call.name,
        content=copy.deepcopy(outcome.result.content),
        details=copy.deepcopy(outcome.result.details),
        usage=copy.deepcopy(outcome.result.usage),
        added_tool_names=copy.deepcopy(outcome.result.added_tool_names),
        is_error=outcome.is_error,
    )
    await stream.emit(MessageStartEvent(copy.deepcopy(message)))
    await stream.emit(MessageEndEvent(copy.deepcopy(message)))
    return message


def _failed_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(message)], details={})


def _apply_turn_update(
    update: AgentLoopTurnUpdate,
    *,
    context: AgentContext,
    model: Model,
    thinking_level: Any,
) -> tuple[AgentContext, Model, Any]:
    return (
        update.context or context,
        update.model or model,
        update.thinking_level or thinking_level,
    )


async def _finish_after_loop_failure(
    stream: AgentEventStream,
    context: AgentContext,
    model: Model,
    reason: Any,
    message: str,
    new_messages: list[AgentMessage],
) -> None:
    error = AssistantMessage(
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason=reason,
        error_message=message,
        usage=Usage.zero(),
    )
    await stream.emit(MessageStartEvent(copy.deepcopy(error)))
    await stream.emit(MessageEndEvent(copy.deepcopy(error)))
    context.messages.append(error)
    await stream.emit(TurnEndEvent(copy.deepcopy(error), []))
    await stream.emit(AgentEndEvent([*copy.deepcopy(new_messages), copy.deepcopy(error)]))


def _raise_if_cancelled(signal: CancellationToken | None) -> None:
    if signal is not None:
        signal.raise_if_cancelled()


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_llm_message(message: object) -> TypeGuard[Message]:
    return isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage))


# Public harness hook. The implementation stays private so the low-level loop can
# evolve internally while extensions and tests depend on one stable entry point.
execute_tool_batch = _execute_tool_batch
