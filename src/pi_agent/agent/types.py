from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from pi_agent.ai import (
    AssistantMessage,
    CancellationToken,
    ImageContent,
    Message,
    Model,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
    Usage,
)

AgentMessage: TypeAlias = Message | dict[str, Any]
ToolExecutionMode: TypeAlias = Literal["parallel", "sequential"]
QueueMode: TypeAlias = Literal["all", "one-at-a-time"]
AgentThinkingLevel: TypeAlias = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
ToolContent: TypeAlias = TextContent | ImageContent


@dataclass(slots=True)
class AgentToolResult:
    content: list[ToolContent]
    details: Any = field(default_factory=dict)
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    terminate: bool = False


ToolUpdateCallback: TypeAlias = Callable[[AgentToolResult], None]
ToolExecute: TypeAlias = Callable[
    [str, dict[str, Any], CancellationToken | None, ToolUpdateCallback],
    Awaitable[AgentToolResult],
]
PrepareArguments: TypeAlias = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class AgentTool:
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecute
    prepare_arguments: PrepareArguments | None = None
    execution_mode: ToolExecutionMode = "parallel"
    timeout: float | None = None
    constrained_sampling: bool | dict[str, Any] = False

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            constrained_sampling=self.constrained_sampling,
        )


@dataclass(slots=True)
class AgentContext:
    system_prompt: str = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)


@dataclass(slots=True)
class BeforeToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    context: AgentContext


@dataclass(slots=True)
class BeforeToolCallResult:
    block: bool = False
    reason: str | None = None
    terminate: bool = False


@dataclass(slots=True)
class AfterToolCallContext:
    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: dict[str, Any]
    result: AgentToolResult
    is_error: bool
    context: AgentContext


@dataclass(slots=True)
class AfterToolCallResult:
    content: list[ToolContent] | None = None
    details: Any = None
    replace_details: bool = False
    is_error: bool | None = None
    usage: Usage | None = None
    replace_usage: bool = False
    terminate: bool | None = None

    @classmethod
    def replacing_details(cls, details: Any, **kwargs: Any) -> AfterToolCallResult:
        return cls(details=details, replace_details=True, **kwargs)

    @classmethod
    def replacing_usage(cls, usage: Usage | None, **kwargs: Any) -> AfterToolCallResult:
        return cls(usage=usage, replace_usage=True, **kwargs)


@dataclass(slots=True)
class CompletedTurnContext:
    message: AssistantMessage
    tool_results: list[ToolResultMessage]
    context: AgentContext
    new_messages: list[AgentMessage]


@dataclass(slots=True)
class AgentLoopTurnUpdate:
    context: AgentContext | None = None
    model: Model | None = None
    thinking_level: AgentThinkingLevel | None = None


ConvertToLlm: TypeAlias = Callable[[list[AgentMessage]], list[Message] | Awaitable[list[Message]]]
TransformContext: TypeAlias = Callable[
    [list[AgentMessage], CancellationToken | None],
    list[AgentMessage] | Awaitable[list[AgentMessage]],
]
GetApiKey: TypeAlias = Callable[[str], str | Awaitable[str | None] | None]
GetQueuedMessages: TypeAlias = Callable[[], Awaitable[list[AgentMessage]]]
BeforeToolCall: TypeAlias = Callable[
    [BeforeToolCallContext, CancellationToken | None],
    BeforeToolCallResult | Awaitable[BeforeToolCallResult | None] | None,
]
AfterToolCall: TypeAlias = Callable[
    [AfterToolCallContext, CancellationToken | None],
    AfterToolCallResult | Awaitable[AfterToolCallResult | None] | None,
]
ShouldStopAfterTurn: TypeAlias = Callable[[CompletedTurnContext], bool | Awaitable[bool]]
PrepareNextTurn: TypeAlias = Callable[
    [CompletedTurnContext], AgentLoopTurnUpdate | Awaitable[AgentLoopTurnUpdate | None] | None
]


@dataclass(slots=True)
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlm
    transform_context: TransformContext | None = None
    get_api_key: GetApiKey | None = None
    get_steering_messages: GetQueuedMessages | None = None
    get_follow_up_messages: GetQueuedMessages | None = None
    should_stop_after_turn: ShouldStopAfterTurn | None = None
    prepare_next_turn: PrepareNextTurn | None = None
    before_tool_call: BeforeToolCall | None = None
    after_tool_call: AfterToolCall | None = None
    tool_execution: ToolExecutionMode = "parallel"
    thinking_level: AgentThinkingLevel = "off"
    stream_options: StreamOptions = field(default_factory=StreamOptions)
