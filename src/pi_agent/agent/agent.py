from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import overload

from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    ImageContent,
    Model,
    StreamFunction,
    StreamOptions,
    TextContent,
    UserMessage,
)

from .agent_loop import agent_loop, agent_loop_continue, default_convert_to_llm
from .events import (
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from .queues import MessageQueue
from .types import (
    AfterToolCall,
    AgentContext,
    AgentLoopConfig,
    AgentMessage,
    AgentThinkingLevel,
    AgentTool,
    BeforeToolCall,
    ConvertToLlm,
    GetApiKey,
    PrepareNextTurn,
    QueueMode,
    ShouldStopAfterTurn,
    ToolExecutionMode,
    TransformContext,
)

AgentListener = Callable[..., Awaitable[None] | None]


def _empty_model() -> Model:
    return Model(api="", provider="", id="", name="")


class AgentState:
    """Mutable public state owned by :class:`Agent`.

    Assigning ``tools`` or ``messages`` copies the top-level list, matching the
    pinned TypeScript implementation. The getter intentionally exposes the
    active list so event listeners can observe the exact reduced message object.
    """

    __slots__ = (
        "_error_message",
        "_is_streaming",
        "_messages",
        "_pending_tool_calls",
        "_streaming_message",
        "_tools",
        "model",
        "system_prompt",
        "thinking_level",
    )

    def __init__(
        self,
        *,
        system_prompt: str = "",
        model: Model | None = None,
        thinking_level: AgentThinkingLevel = "off",
        tools: list[AgentTool] | None = None,
        messages: list[AgentMessage] | None = None,
        is_streaming: bool = False,
        streaming_message: AgentMessage | None = None,
        pending_tool_calls: set[str] | frozenset[str] | None = None,
        error_message: str | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.model = model or _empty_model()
        self.thinking_level = thinking_level
        self._tools = list(tools or [])
        self._messages = list(messages or [])
        self._is_streaming = is_streaming
        self._streaming_message = streaming_message
        self._pending_tool_calls = set(pending_tool_calls or ())
        self._error_message = error_message

    @property
    def tools(self) -> list[AgentTool]:
        return self._tools

    @tools.setter
    def tools(self, value: list[AgentTool]) -> None:
        self._tools = list(value)

    @property
    def messages(self) -> list[AgentMessage]:
        return self._messages

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        self._messages = list(value)

    @property
    def is_streaming(self) -> bool:
        return self._is_streaming

    @property
    def streaming_message(self) -> AgentMessage | None:
        return self._streaming_message

    @property
    def pending_tool_calls(self) -> frozenset[str]:
        return frozenset(self._pending_tool_calls)

    @property
    def error_message(self) -> str | None:
        return self._error_message

    def clone(self) -> AgentState:
        return AgentState(
            system_prompt=self.system_prompt,
            model=copy.deepcopy(self.model),
            thinking_level=self.thinking_level,
            tools=copy.deepcopy(self._tools),
            messages=copy.deepcopy(self._messages),
            is_streaming=False,
            streaming_message=None,
            pending_tool_calls=set(),
            error_message=self._error_message,
        )


@dataclass(slots=True)
class AgentOptions:
    """Construction options mirroring the pinned TypeScript ``AgentOptions``.

    ``model``/``tools``/``messages`` are Python convenience fields. When an
    ``initial_state`` is supplied, explicitly provided convenience fields win.
    """

    stream_fn: StreamFunction
    initial_state: AgentState | None = None
    model: Model | None = None
    system_prompt: str | None = None
    thinking_level: AgentThinkingLevel | None = None
    tools: list[AgentTool] | None = None
    messages: list[AgentMessage] | None = None
    convert_to_llm: ConvertToLlm = default_convert_to_llm
    transform_context: TransformContext | None = None
    get_api_key: GetApiKey | None = None
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    should_stop_after_turn: ShouldStopAfterTurn | None = None
    prepare_next_turn: PrepareNextTurn | None = None
    steering_mode: QueueMode = "one-at-a-time"
    follow_up_mode: QueueMode = "one-at-a-time"
    tool_execution: ToolExecutionMode = "parallel"
    stream_options: StreamOptions = field(default_factory=StreamOptions)


class Agent:
    """Reusable stateful wrapper over the low-level Agent Loop.

    Listeners are awaited in subscription order. State reduction happens before
    listener dispatch, and ``is_streaming`` remains true until ``agent_end``
    listeners settle. A fresh cancellation token is created for every run.
    """

    @overload
    def __init__(self, options: AgentOptions) -> None: ...

    @overload
    def __init__(
        self,
        options: AgentLoopConfig,
        *,
        stream_fn: StreamFunction,
        system_prompt: str = "",
        tools: list[AgentTool] | None = None,
        messages: list[AgentMessage] | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
    ) -> None: ...

    def __init__(
        self,
        options: AgentOptions | AgentLoopConfig,
        *,
        stream_fn: StreamFunction | None = None,
        system_prompt: str = "",
        tools: list[AgentTool] | None = None,
        messages: list[AgentMessage] | None = None,
        steering_mode: QueueMode = "one-at-a-time",
        follow_up_mode: QueueMode = "one-at-a-time",
    ) -> None:
        if isinstance(options, AgentOptions):
            state = (options.initial_state or AgentState()).clone()
            if options.model is not None:
                state.model = copy.deepcopy(options.model)
            if options.system_prompt is not None:
                state.system_prompt = options.system_prompt
            if options.thinking_level is not None:
                state.thinking_level = options.thinking_level
            if options.tools is not None:
                state.tools = copy.deepcopy(options.tools)
            if options.messages is not None:
                state.messages = copy.deepcopy(options.messages)
            self._config = AgentLoopConfig(
                model=state.model,
                convert_to_llm=options.convert_to_llm,
                transform_context=options.transform_context,
                get_api_key=options.get_api_key,
                should_stop_after_turn=options.should_stop_after_turn,
                prepare_next_turn=options.prepare_next_turn,
                before_tool_call=options.before_tool_call,
                after_tool_call=options.after_tool_call,
                tool_execution=options.tool_execution,
                thinking_level=state.thinking_level,
                stream_options=copy.deepcopy(options.stream_options),
            )
            self._stream_fn = options.stream_fn
            self._state = state
            steering_mode = options.steering_mode
            follow_up_mode = options.follow_up_mode
        else:
            if stream_fn is None:
                raise TypeError("stream_fn is required with AgentLoopConfig")
            self._config = copy.deepcopy(options)
            self._stream_fn = stream_fn
            self._state = AgentState(
                system_prompt=system_prompt,
                model=copy.deepcopy(options.model),
                thinking_level=options.thinking_level,
                tools=copy.deepcopy(tools or []),
                messages=copy.deepcopy(messages or []),
            )

        if not self._state.model.id:
            raise ValueError("Agent requires a model")
        self._steering = MessageQueue(steering_mode)
        self._follow_up = MessageQueue(follow_up_mode)
        self._listeners: list[AgentListener] = []
        self._listener_errors: list[Exception] = []
        self._active_signal: CancellationToken | None = None
        self._active_task: asyncio.Task[list[AgentMessage]] | None = None
        self._external_steering = self._config.get_steering_messages
        self._external_follow_up = self._config.get_follow_up_messages

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def system_prompt(self) -> str:
        return self._state.system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._ensure_idle("change the system prompt")
        self._state.system_prompt = value

    @property
    def model(self) -> Model:
        return self._state.model

    @model.setter
    def model(self, value: Model) -> None:
        self._ensure_idle("change the model")
        self._state.model = value

    @property
    def thinking_level(self) -> AgentThinkingLevel:
        return self._state.thinking_level

    @thinking_level.setter
    def thinking_level(self, value: AgentThinkingLevel) -> None:
        self._ensure_idle("change the thinking level")
        self._state.thinking_level = value

    @property
    def tools(self) -> list[AgentTool]:
        return list(self._state.tools)

    @tools.setter
    def tools(self, value: list[AgentTool]) -> None:
        self._ensure_idle("change tools")
        self._state.tools = value

    @property
    def messages(self) -> list[AgentMessage]:
        return copy.deepcopy(self._state.messages)

    @messages.setter
    def messages(self, value: list[AgentMessage]) -> None:
        self._ensure_idle("replace messages")
        self._state.messages = copy.deepcopy(value)

    @property
    def is_streaming(self) -> bool:
        return self._state.is_streaming

    @property
    def streaming_message(self) -> AgentMessage | None:
        return copy.deepcopy(self._state.streaming_message)

    @property
    def pending_tool_calls(self) -> frozenset[str]:
        return self._state.pending_tool_calls

    @property
    def error_message(self) -> str | None:
        return self._state.error_message

    @property
    def listener_errors(self) -> tuple[Exception, ...]:
        return tuple(self._listener_errors)

    @property
    def signal(self) -> CancellationToken | None:
        return self._active_signal

    @property
    def steering_mode(self) -> QueueMode:
        return self._steering.mode

    @steering_mode.setter
    def steering_mode(self, value: QueueMode) -> None:
        self._steering.mode = value

    @property
    def follow_up_mode(self) -> QueueMode:
        return self._follow_up.mode

    @follow_up_mode.setter
    def follow_up_mode(self, value: QueueMode) -> None:
        self._follow_up.mode = value

    def subscribe(self, listener: AgentListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    async def prompt(
        self,
        prompt: str | AgentMessage | list[AgentMessage],
        images: list[ImageContent] | None = None,
    ) -> list[AgentMessage]:
        if isinstance(prompt, str) and images:
            prompts: list[AgentMessage] = [UserMessage([*images, TextContent(prompt)])]
        else:
            prompts = self._normalize_messages(prompt)
        if not prompts:
            raise ValueError("prompt requires at least one message")
        return await self._start_run(prompts=prompts, is_continue=False)

    async def continue_run(self) -> list[AgentMessage]:
        if not self._state.messages:
            queued = await self._drain_for_continue()
            if not queued:
                raise ValueError("Cannot continue an empty transcript")
            return await self._start_run(prompts=queued, is_continue=False)
        if isinstance(self._state.messages[-1], AssistantMessage):
            queued = await self._drain_for_continue()
            if not queued:
                raise ValueError("Cannot continue when the last message is an assistant message")
            return await self._start_run(prompts=queued, is_continue=False)
        return await self._start_run(prompts=[], is_continue=True)

    async def continue_(self) -> list[AgentMessage]:
        return await self.continue_run()

    def steer(self, message: str | AgentMessage) -> None:
        self._steering.push(self._normalize_message(message))

    def follow_up(self, message: str | AgentMessage) -> None:
        self._follow_up.push(self._normalize_message(message))

    def clear_steering_queue(self) -> None:
        self._steering.clear()

    def clear_follow_up_queue(self) -> None:
        self._follow_up.clear()

    def clear_all_queues(self) -> None:
        self.clear_steering_queue()
        self.clear_follow_up_queue()

    def has_queued_messages(self) -> bool:
        return bool(self._steering) or bool(self._follow_up)

    def set_steering_mode(self, mode: QueueMode) -> None:
        self._steering.set_mode(mode)

    def set_follow_up_mode(self, mode: QueueMode) -> None:
        self._follow_up.set_mode(mode)

    def abort(self, reason: str = "Operation aborted") -> None:
        if self._active_signal is not None:
            self._active_signal.cancel(reason)

    async def wait_for_idle(self) -> None:
        task = self._active_task
        if task is not None and task is not asyncio.current_task():
            await asyncio.shield(task)

    def reset(self) -> None:
        self._ensure_idle("reset the agent")
        self._state.messages.clear()
        self.clear_all_queues()
        self._state._streaming_message = None
        self._state._pending_tool_calls.clear()
        self._state._error_message = None
        self._listener_errors.clear()

    async def _start_run(
        self,
        *,
        prompts: list[AgentMessage],
        is_continue: bool,
    ) -> list[AgentMessage]:
        if self._state.is_streaming:
            raise RuntimeError("Agent is already streaming")
        signal = CancellationToken()
        self._active_signal = signal
        self._state._is_streaming = True
        self._state._error_message = None
        self._listener_errors.clear()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Agent runs require an asyncio task")
        self._active_task = task

        config = replace(
            self._config,
            model=self._state.model,
            thinking_level=self._state.thinking_level,
            get_steering_messages=self._drain_steering,
            get_follow_up_messages=self._drain_follow_up,
        )
        context = AgentContext(
            system_prompt=self._state.system_prompt,
            messages=list(self._state.messages),
            tools=list(self._state.tools),
        )
        low_level = (
            agent_loop_continue(
                context,
                config,
                stream_fn=self._stream_fn,
                signal=signal,
                event_sink=self._process_event,
            )
            if is_continue
            else agent_loop(
                prompts,
                context,
                config,
                stream_fn=self._stream_fn,
                signal=signal,
                event_sink=self._process_event,
            )
        )
        try:
            return copy.deepcopy(await low_level.result())
        finally:
            self._state._is_streaming = False
            self._active_signal = None
            self._active_task = None
            self._state._streaming_message = None
            self._state._pending_tool_calls.clear()

    async def _drain_steering(self) -> list[AgentMessage]:
        local = self._steering.drain()
        external: list[AgentMessage] = []
        if self._external_steering is not None:
            external = await self._external_steering()
        return [*local, *external]

    async def _drain_follow_up(self) -> list[AgentMessage]:
        local = self._follow_up.drain()
        external: list[AgentMessage] = []
        if self._external_follow_up is not None:
            external = await self._external_follow_up()
        return [*local, *external]

    async def _drain_for_continue(self) -> list[AgentMessage]:
        steering = await self._drain_steering()
        if steering:
            return steering
        return await self._drain_follow_up()

    def _apply_event(self, event: AgentEvent) -> None:
        if (
            isinstance(event, MessageStartEvent) and isinstance(event.message, AssistantMessage)
        ) or isinstance(event, MessageUpdateEvent):
            self._state._streaming_message = event.message
        elif isinstance(event, MessageEndEvent):
            self._state.messages.append(event.message)
            if isinstance(event.message, AssistantMessage):
                self._state._streaming_message = None
                if event.message.stop_reason in {"error", "aborted"}:
                    self._state._error_message = event.message.error_message
        elif isinstance(event, ToolExecutionStartEvent):
            self._state._pending_tool_calls.add(event.tool_call_id)
        elif isinstance(event, ToolExecutionEndEvent):
            self._state._pending_tool_calls.discard(event.tool_call_id)

    async def _process_event(self, event: AgentEvent) -> None:
        signal = self._active_signal
        if signal is None:
            raise RuntimeError("Agent event received outside an active run")
        self._apply_event(event)
        await self._dispatch(event, signal)

    async def _dispatch(self, event: AgentEvent, signal: CancellationToken) -> None:
        for listener in tuple(self._listeners):
            try:
                value = self._call_listener(listener, event, signal)
                if inspect.isawaitable(value):
                    await value
            except Exception as exc:
                self._listener_errors.append(exc)

    @staticmethod
    def _call_listener(
        listener: AgentListener,
        event: AgentEvent,
        signal: CancellationToken,
    ) -> Awaitable[None] | None:
        try:
            inspect.signature(listener).bind(event, signal)
        except (TypeError, ValueError):
            return listener(event)
        return listener(event, signal)

    def _ensure_idle(self, action: str) -> None:
        if self._state.is_streaming:
            raise RuntimeError(f"Cannot {action} while the agent is running")

    @staticmethod
    def _normalize_message(message: str | AgentMessage) -> AgentMessage:
        return UserMessage(message) if isinstance(message, str) else copy.deepcopy(message)

    @classmethod
    def _normalize_messages(
        cls,
        value: str | AgentMessage | list[AgentMessage],
    ) -> list[AgentMessage]:
        if isinstance(value, list):
            return copy.deepcopy(value)
        return [cls._normalize_message(value)]
