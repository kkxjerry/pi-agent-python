from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable
from dataclasses import replace
from typing import Any, TypeGuard, TypeVar, cast

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

from .event_stream import AgentEventStream
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
    AgentContext,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
)

T = TypeVar("T")
_TRUNCATED_TOOL_TEMPLATE = (
    'Tool call "{name}" was not executed: the response hit the output token limit, '
    "so its arguments may be truncated. Re-issue the tool call with complete arguments."
)


def default_convert_to_llm(messages: list[AgentMessage]) -> list[Message]:
    """Filter application-specific messages at the model boundary."""

    return [message for message in messages if _is_llm_message(message)]


def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
) -> AgentEventStream:
    """Start a low-level prompt run and return its event stream immediately."""

    stream = AgentEventStream()
    runtime_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=[*context.messages, *prompts],
        tools=context.tools,
    )
    task = asyncio.create_task(
        _run_guarded(
            stream,
            prompts=prompts,
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
) -> AgentEventStream:
    """Continue from an existing user/tool-result tail without adding a prompt."""

    if not context.messages:
        raise ValueError("Cannot continue an empty context")
    if isinstance(context.messages[-1], AssistantMessage):
        raise ValueError("Cannot continue when the last message is an assistant message")
    stream = AgentEventStream()
    runtime_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=context.messages,
        tools=context.tools,
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
    stream = agent_loop(prompts, context, config, stream_fn=stream_fn, signal=signal)
    return await stream.result()


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    *,
    stream_fn: StreamFunction,
    signal: CancellationToken | None = None,
) -> list[AgentMessage]:
    stream = agent_loop_continue(context, config, stream_fn=stream_fn, signal=signal)
    return await stream.result()


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
    new_messages: list[AgentMessage] = list(prompts)
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

    stream.push(AgentStartEvent())
    stream.push(TurnStartEvent())
    if not is_continue:
        for prompt in prompts:
            stream.push(MessageStartEvent(copy.deepcopy(prompt)))
            stream.push(MessageEndEvent(copy.deepcopy(prompt)))

    while True:
        _raise_if_cancelled(active_signal)
        assistant = await _stream_assistant_response(
            stream,
            context=context,
            config=config,
            stream_fn=stream_fn,
            signal=active_signal,
            new_messages=new_messages,
        )

        tool_calls = [block for block in assistant.content if isinstance(block, ToolCall)]
        tool_results: list[ToolResultMessage] = []
        executions: list[AgentToolResult] = []

        if tool_calls:
            for call in tool_calls:
                if assistant.stop_reason == "length":
                    execution = _failed_tool_result(_TRUNCATED_TOOL_TEMPLATE.format(name=call.name))
                    stream.push(
                        ToolExecutionStartEvent(
                            tool_call_id=call.id,
                            tool_name=call.name,
                            args=copy.deepcopy(call.arguments),
                        )
                    )
                    stream.push(
                        ToolExecutionEndEvent(
                            tool_call_id=call.id,
                            tool_name=call.name,
                            result=copy.deepcopy(execution),
                            is_error=True,
                            args=copy.deepcopy(call.arguments),
                        )
                    )
                    result_message = _to_tool_result_message(call, execution, is_error=True)
                else:
                    result_message, execution, _is_error = await _execute_tool_call(
                        stream,
                        call=call,
                        tools=context.tools,
                        signal=active_signal,
                    )
                stream.push(MessageStartEvent(copy.deepcopy(result_message)))
                stream.push(MessageEndEvent(copy.deepcopy(result_message)))
                tool_results.append(result_message)
                executions.append(execution)
                if active_signal is not None and active_signal.cancelled:
                    break

            for result in tool_results:
                context.messages.append(result)
                new_messages.append(result)

        terminate = bool(executions) and all(execution.terminate for execution in executions)

        stream.push(TurnEndEvent(copy.deepcopy(assistant), copy.deepcopy(tool_results)))

        if assistant.stop_reason in {"error", "aborted"} or not tool_calls or terminate:
            stream.push(AgentEndEvent(copy.deepcopy(new_messages)))
            return

        stream.push(TurnStartEvent())


async def _stream_assistant_response(
    stream: AgentEventStream,
    *,
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: StreamFunction,
    signal: CancellationToken | None,
    new_messages: list[AgentMessage],
) -> AssistantMessage:
    transformed: list[AgentMessage] = list(context.messages)
    if config.transform_context is not None:
        transformed = await _maybe_await(config.transform_context(transformed, signal))
    llm_messages = await _maybe_await(config.convert_to_llm(transformed))
    if not all(_is_llm_message(message) for message in llm_messages):
        raise TypeError("convert_to_llm must return only model Message values")

    options = replace(
        config.stream_options,
        headers=dict(config.stream_options.headers),
        env=dict(config.stream_options.env),
        metadata=dict(config.stream_options.metadata),
        sampling_params=dict(config.stream_options.sampling_params),
        signal=signal,
    )
    if config.get_api_key is not None:
        refreshed_key = await _maybe_await(config.get_api_key(config.model.provider))
        options.api_key = refreshed_key or options.api_key

    provider_context = Context(
        system_prompt=context.system_prompt or None,
        messages=list(llm_messages),
        tools=[tool.definition() for tool in context.tools] or None,
    )
    response = stream_fn(config.model, provider_context, options)
    inserted = False
    assistant_index = -1
    final: AssistantMessage | None = None

    async for provider_event in response:
        if isinstance(provider_event, StartEvent):
            partial = copy.deepcopy(provider_event.partial)
            context.messages.append(partial)
            new_messages.append(partial)
            assistant_index = len(context.messages) - 1
            inserted = True
            stream.push(MessageStartEvent(copy.deepcopy(partial)))
            continue

        if isinstance(provider_event, (DoneEvent, ErrorEvent)):
            terminal = (
                copy.deepcopy(provider_event.message)
                if isinstance(provider_event, DoneEvent)
                else copy.deepcopy(provider_event.error)
            )
            if not inserted:
                context.messages.append(copy.deepcopy(terminal))
                new_messages.append(copy.deepcopy(terminal))
                assistant_index = len(context.messages) - 1
                inserted = True
                stream.push(MessageStartEvent(copy.deepcopy(terminal)))
            context.messages[assistant_index] = terminal
            new_messages[-1] = terminal
            stream.push(MessageEndEvent(copy.deepcopy(terminal)))
            final = terminal
            continue

        partial = copy.deepcopy(provider_event.partial)
        if not inserted:
            context.messages.append(partial)
            new_messages.append(partial)
            assistant_index = len(context.messages) - 1
            inserted = True
            stream.push(MessageStartEvent(copy.deepcopy(partial)))
        else:
            context.messages[assistant_index] = partial
            new_messages[-1] = partial
        stream.push(MessageUpdateEvent(copy.deepcopy(partial), copy.deepcopy(provider_event)))

    if not response.done:
        raise RuntimeError("Provider stream ended without a terminal event")
    resolved = copy.deepcopy(await response.result())
    if final is None:
        if not inserted:
            context.messages.append(resolved)
            new_messages.append(resolved)
            stream.push(MessageStartEvent(copy.deepcopy(resolved)))
            assistant_index = len(context.messages) - 1
        context.messages[assistant_index] = resolved
        new_messages[-1] = resolved
        stream.push(MessageEndEvent(copy.deepcopy(resolved)))
        final = resolved
    return final


async def _execute_tool_call(
    stream: AgentEventStream,
    *,
    call: ToolCall,
    tools: list[AgentTool],
    signal: CancellationToken | None,
) -> tuple[ToolResultMessage, AgentToolResult, bool]:
    args = copy.deepcopy(call.arguments)
    stream.push(
        ToolExecutionStartEvent(
            tool_call_id=call.id,
            tool_name=call.name,
            args=copy.deepcopy(args),
        )
    )
    tool = next((candidate for candidate in tools if candidate.name == call.name), None)
    if tool is None:
        result = _failed_tool_result(f"Tool {call.name} not found")
        stream.push(ToolExecutionEndEvent(call.id, call.name, copy.deepcopy(result), True, args))
        return _to_tool_result_message(call, result, is_error=True), result, True

    try:
        _raise_if_cancelled(signal)
        if tool.prepare_arguments is not None:
            args = await _maybe_await(tool.prepare_arguments(args))
        validate_json_schema(args, cast(dict[str, Any], tool.parameters))

        def on_update(partial_result: AgentToolResult) -> None:
            stream.push(
                ToolExecutionUpdateEvent(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    args=copy.deepcopy(args),
                    partial_result=copy.deepcopy(partial_result),
                )
            )

        result = await tool.execute(call.id, args, signal, on_update)
        _raise_if_cancelled(signal)
        stream.push(
            ToolExecutionEndEvent(
                call.id,
                call.name,
                copy.deepcopy(result),
                False,
                copy.deepcopy(args),
            )
        )
        return _to_tool_result_message(call, result, is_error=False), result, False
    except asyncio.CancelledError as exc:
        result = _failed_tool_result(str(exc) or "Operation aborted")
    except ToolArgumentsError as exc:
        result = _failed_tool_result(f"Invalid arguments for tool {call.name}: {exc}")
    except Exception as exc:
        result = _failed_tool_result(str(exc))

    stream.push(
        ToolExecutionEndEvent(
            call.id,
            call.name,
            copy.deepcopy(result),
            True,
            copy.deepcopy(args),
        )
    )
    return _to_tool_result_message(call, result, is_error=True), result, True


def _to_tool_result_message(
    call: ToolCall,
    result: AgentToolResult,
    *,
    is_error: bool,
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call.id,
        tool_name=call.name,
        content=copy.deepcopy(result.content),
        details=copy.deepcopy(result.details),
        usage=copy.deepcopy(result.usage),
        added_tool_names=copy.deepcopy(result.added_tool_names),
        is_error=is_error,
    )


def _failed_tool_result(message: str) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(message)], details={})


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
    stream.push(MessageStartEvent(copy.deepcopy(error)))
    stream.push(MessageEndEvent(copy.deepcopy(error)))
    context.messages.append(error)
    stream.push(TurnEndEvent(copy.deepcopy(error), []))
    stream.push(AgentEndEvent([*copy.deepcopy(new_messages), copy.deepcopy(error)]))


def _raise_if_cancelled(signal: CancellationToken | None) -> None:
    if signal is not None:
        signal.raise_if_cancelled()


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_llm_message(message: object) -> TypeGuard[Message]:
    return isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage))
