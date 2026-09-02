from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from pi_agent.agent import (
    Agent,
    AgentListener,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    default_convert_to_llm,
)
from pi_agent.ai import Model, StreamFunction

from .result import HarnessResult, build_harness_result
from .tools import create_coding_tools
from .types import ExecutionEnv


class AgentHarness:
    """Session-independent orchestration around a stateful Agent and execution env.

    Persistence, compaction, and session trees intentionally remain outside this
    Phase 12 harness. The harness owns runtime composition, coding tools, usage
    aggregation, cancellation, and a stable result object.
    """

    def __init__(
        self,
        *,
        model: Model,
        stream_fn: StreamFunction,
        env: ExecutionEnv,
        system_prompt: str = "",
        tools: list[AgentTool] | None = None,
        config: AgentLoopConfig | None = None,
        include_coding_tools: bool = True,
    ) -> None:
        selected_tools: list[AgentTool] = []
        if include_coding_tools:
            selected_tools.extend(create_coding_tools(env))
        if tools:
            selected_tools.extend(tools)
        names = [tool.name for tool in selected_tools]
        if len(names) != len(set(names)):
            raise ValueError("Harness tools must have unique names")
        loop_config = config or AgentLoopConfig(
            model=model,
            convert_to_llm=default_convert_to_llm,
        )
        if loop_config.model != model:
            loop_config = replace(loop_config, model=model)
        self.env = env
        self.agent = Agent(
            loop_config,
            stream_fn=stream_fn,
            system_prompt=system_prompt,
            tools=selected_tools,
        )

    async def prompt(
        self,
        prompt: str | AgentMessage | list[AgentMessage],
    ) -> HarnessResult:
        messages = await self.agent.prompt(prompt)
        return build_harness_result(messages, self.agent.messages)

    async def run(
        self,
        prompt: str | AgentMessage | list[AgentMessage],
    ) -> HarnessResult:
        return await self.prompt(prompt)

    async def continue_run(self) -> HarnessResult:
        messages = await self.agent.continue_run()
        return build_harness_result(messages, self.agent.messages)

    def subscribe(self, listener: AgentListener) -> Callable[[], None]:
        return self.agent.subscribe(listener)

    def steer(self, message: str | AgentMessage) -> None:
        self.agent.steer(message)

    def follow_up(self, message: str | AgentMessage) -> None:
        self.agent.follow_up(message)

    def abort(self, reason: str = "Operation aborted") -> None:
        self.agent.abort(reason)
