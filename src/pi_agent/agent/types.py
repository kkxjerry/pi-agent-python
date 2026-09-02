from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from pi_agent.ai import (
    CancellationToken,
    Context,
    ImageContent,
    Message,
    Model,
    StreamOptions,
    TextContent,
    ToolDefinition,
    Usage,
)
from pi_agent.ai.types import JsonValue

AgentMessage: TypeAlias = Message | dict[str, Any]
ToolExecutionMode: TypeAlias = Literal["sequential", "parallel"]


@dataclass(slots=True)
class AgentToolResult:
    content: list[TextContent | ImageContent]
    details: Any = field(default_factory=dict)
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    terminate: bool = False


ToolUpdateCallback: TypeAlias = Callable[[AgentToolResult], None]
ToolExecute: TypeAlias = Callable[
    [str, dict[str, JsonValue], CancellationToken | None, ToolUpdateCallback],
    Awaitable[AgentToolResult],
]
ToolPrepareArguments: TypeAlias = Callable[
    [dict[str, JsonValue]],
    Awaitable[dict[str, JsonValue]] | dict[str, JsonValue],
]


@dataclass(slots=True)
class AgentTool:
    name: str
    label: str
    description: str
    parameters: dict[str, JsonValue]
    execute: ToolExecute
    constrained_sampling: bool | dict[str, JsonValue] = False
    execution_mode: ToolExecutionMode = "sequential"
    prepare_arguments: ToolPrepareArguments | None = None

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


ConvertToLlm: TypeAlias = Callable[[list[AgentMessage]], Awaitable[list[Message]] | list[Message]]
TransformContext: TypeAlias = Callable[
    [list[AgentMessage], CancellationToken | None],
    Awaitable[list[AgentMessage]] | list[AgentMessage],
]
GetApiKey: TypeAlias = Callable[[str], Awaitable[str | None] | str | None]


@dataclass(slots=True)
class AgentLoopConfig:
    model: Model
    convert_to_llm: ConvertToLlm
    transform_context: TransformContext | None = None
    get_api_key: GetApiKey | None = None
    stream_options: StreamOptions = field(default_factory=StreamOptions)

    def provider_context(self, messages: list[Message], tools: list[AgentTool]) -> Context:
        return Context(
            system_prompt=None,
            messages=messages,
            tools=[tool.definition() for tool in tools] or None,
        )
