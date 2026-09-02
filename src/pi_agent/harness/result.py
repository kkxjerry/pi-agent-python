from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeAlias, TypeVar, cast

from pi_agent.agent import AgentMessage
from pi_agent.ai import AssistantMessage, ToolResultMessage, Usage, UsageCost

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    value: T
    ok: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True, slots=True)
class Err(Generic[E]):
    error: E
    ok: Literal[False] = field(default=False, init=False)


Result: TypeAlias = Ok[T] | Err[E]


def ok(value: T) -> Result[T, E]:
    return cast(Result[T, E], Ok(value))


def err(error: E) -> Result[T, E]:
    return cast(Result[T, E], Err(error))


def get_or_raise(result: Result[T, E]) -> T:
    if result.ok:
        return result.value
    error = result.error
    if isinstance(error, BaseException):
        raise error
    raise RuntimeError(str(error))


def get_or_throw(result: Result[T, E]) -> T:
    return get_or_raise(result)


def get_or_undefined(result: Result[T, E]) -> T | None:
    return result.value if result.ok else None


def to_error(value: object) -> Exception:
    return value if isinstance(value, Exception) else RuntimeError(str(value))


@dataclass(frozen=True, slots=True)
class HarnessResult:
    new_messages: tuple[AgentMessage, ...]
    transcript: tuple[AgentMessage, ...]
    final_assistant: AssistantMessage | None
    usage: Usage
    aborted: bool
    error_message: str | None

    @property
    def succeeded(self) -> bool:
        return self.final_assistant is not None and self.final_assistant.stop_reason not in {
            "error",
            "aborted",
        }


def build_harness_result(
    new_messages: list[AgentMessage],
    transcript: list[AgentMessage],
) -> HarnessResult:
    assistants = [message for message in new_messages if isinstance(message, AssistantMessage)]
    final = assistants[-1] if assistants else None
    usage = Usage.zero()
    for message in new_messages:
        value: Usage | None = None
        if isinstance(message, (AssistantMessage, ToolResultMessage)):
            value = message.usage
        if value is not None:
            _add_usage(usage, value)
    return HarnessResult(
        new_messages=tuple(copy.deepcopy(new_messages)),
        transcript=tuple(copy.deepcopy(transcript)),
        final_assistant=copy.deepcopy(final),
        usage=usage,
        aborted=final is not None and final.stop_reason == "aborted",
        error_message=final.error_message if final is not None else None,
    )


def _add_usage(target: Usage, value: Usage) -> None:
    target.input += value.input
    target.output += value.output
    target.cache_read += value.cache_read
    target.cache_write += value.cache_write
    target.total_tokens += value.total_tokens
    if value.cache_write_1h is not None:
        target.cache_write_1h = (target.cache_write_1h or 0) + value.cache_write_1h
    if value.reasoning is not None:
        target.reasoning = (target.reasoning or 0) + value.reasoning
    target.cost = UsageCost(
        input=target.cost.input + value.cost.input,
        output=target.cost.output + value.cost.output,
        cache_read=target.cost.cache_read + value.cost.cache_read,
        cache_write=target.cost.cache_write + value.cost.cache_write,
        total=target.cost.total + value.cost.total,
    )
