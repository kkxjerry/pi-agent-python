from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

from pi_agent.agent import (
    Agent,
    AgentContext,
    AgentEvent,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentThinkingLevel,
    CompletedTurnContext,
    MessageEndEvent,
    QueueMode,
)
from pi_agent.ai import (
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    ModelRegistry,
    ToolResultMessage,
    Usage,
    UserMessage,
    message_text,
)
from pi_agent.harness import ExecutionEnv, HarnessResult, ResourceLoader, ResourceSnapshot
from pi_agent.harness.compaction import (
    CompactionController,
    CompactionResult,
    Summarizer,
    compact_session,
    should_compact,
    summarize_and_navigate_branch,
)
from pi_agent.harness.result import build_harness_result, get_or_raise
from pi_agent.harness.session import (
    CompactionEntry,
    SessionManager,
    SessionRepository,
    TreeEntry,
    reconstruct_context,
    record_to_dict,
)
from pi_agent.harness.system_prompt import SystemPromptBuilder
from pi_agent.harness.types import ShellExecOptions

StreamingBehavior: TypeAlias = Literal["steer", "followUp"]
QueueName: TypeAlias = Literal["steering", "follow_up", "all"]
CompactionReason: TypeAlias = Literal["auto", "manual", "branch"]
SessionListener: TypeAlias = Callable[["AgentSessionEvent"], Awaitable[None] | None]


@dataclass(slots=True, frozen=True)
class PromptOptions:
    images: tuple[ImageContent, ...] = ()
    streaming_behavior: StreamingBehavior | None = None


@dataclass(slots=True, frozen=True)
class AgentSettledEvent:
    type: Literal["agent_settled"] = field(default="agent_settled", init=False)


@dataclass(slots=True, frozen=True)
class QueueUpdateEvent:
    queue: Literal["steering", "follow_up"]
    action: Literal["push", "clear"]
    message: AgentMessage | None = None
    type: Literal["queue_update"] = field(default="queue_update", init=False)


@dataclass(slots=True, frozen=True)
class CompactionStartEvent:
    reason: CompactionReason
    type: Literal["compaction_start"] = field(default="compaction_start", init=False)


@dataclass(slots=True, frozen=True)
class CompactionEndEvent:
    reason: CompactionReason
    result: CompactionResult | None
    aborted: bool = False
    will_retry: bool = False
    error_message: str | None = None
    type: Literal["compaction_end"] = field(default="compaction_end", init=False)


@dataclass(slots=True, frozen=True)
class EntryAppendedEvent:
    entry: TreeEntry
    type: Literal["entry_appended"] = field(default="entry_appended", init=False)


@dataclass(slots=True, frozen=True)
class SessionInfoChangedEvent:
    session_id: str
    name: str | None
    type: Literal["session_info_changed"] = field(default="session_info_changed", init=False)


@dataclass(slots=True, frozen=True)
class ThinkingLevelChangedEvent:
    thinking_level: AgentThinkingLevel
    type: Literal["thinking_level_changed"] = field(default="thinking_level_changed", init=False)


AgentSessionEvent: TypeAlias = (
    AgentEvent
    | AgentSettledEvent
    | QueueUpdateEvent
    | CompactionStartEvent
    | CompactionEndEvent
    | EntryAppendedEvent
    | SessionInfoChangedEvent
    | ThinkingLevelChangedEvent
)


@dataclass(slots=True, frozen=True)
class AgentSessionState:
    model: Model
    thinking_level: AgentThinkingLevel
    messages: tuple[AgentMessage, ...]
    is_streaming: bool
    is_compacting: bool
    session_id: str
    session_name: str | None
    auto_compaction_enabled: bool
    pending_tool_calls: frozenset[str]
    error_message: str | None


@dataclass(slots=True, frozen=True)
class SessionStats:
    session_id: str
    entry_count: int
    message_count: int
    assistant_count: int
    tool_result_count: int
    leaf_count: int
    usage: Usage

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "entryCount": self.entry_count,
            "messageCount": self.message_count,
            "assistantCount": self.assistant_count,
            "toolResultCount": self.tool_result_count,
            "leafCount": self.leaf_count,
            "usage": {
                "input": self.usage.input,
                "output": self.usage.output,
                "cacheRead": self.usage.cache_read,
                "cacheWrite": self.usage.cache_write,
                "totalTokens": self.usage.total_tokens,
                "cost": {
                    "input": self.usage.cost.input,
                    "output": self.usage.cost.output,
                    "cacheRead": self.usage.cost.cache_read,
                    "cacheWrite": self.usage.cost.cache_write,
                    "total": self.usage.cost.total,
                },
            },
        }


@dataclass(slots=True, frozen=True)
class CompactionRunResult(Mapping[str, Any]):
    entry: CompactionEntry
    result: CompactionResult

    @property
    def summary(self) -> str:
        return self.result.summary

    @property
    def tokens_before(self) -> int:
        return self.result.tokens_before

    @property
    def tokens_after(self) -> int:
        return self.result.tokens_after

    def _mapping(self) -> dict[str, Any]:
        return {
            "entryId": self.entry.id,
            "summary": self.result.summary,
            "cutIndex": self.result.cut_index,
            "tokensBefore": self.result.tokens_before,
            "tokensAfter": self.result.tokens_after,
            "splitTurn": self.result.split_turn,
            "attempts": self.result.attempts,
        }

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "tokens_before": "tokensBefore",
            "tokens_after": "tokensAfter",
            "entry_id": "entryId",
        }
        return self._mapping()[aliases.get(key, key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._mapping())

    def __len__(self) -> int:
        return len(self._mapping())


class AgentRunMessages(list[AgentMessage]):
    """List-compatible result with the useful HarnessResult status surface."""

    def __init__(self, messages: list[AgentMessage], harness_result: HarnessResult) -> None:
        super().__init__(messages)
        self.harness_result = harness_result

    @property
    def succeeded(self) -> bool:
        return self.harness_result.succeeded

    @property
    def usage(self) -> Usage:
        return self.harness_result.usage

    @property
    def final_assistant(self) -> AssistantMessage | None:
        return self.harness_result.final_assistant

    @property
    def error_message(self) -> str | None:
        return self.harness_result.error_message


class AgentSession:
    """Shared product runtime used by print, JSON, RPC, and extension hosts.

    The low-level :class:`~pi_agent.agent.Agent` remains session-independent.
    This class owns persistence, resource snapshots, model/thinking metadata,
    compaction barriers, and product lifecycle events.
    """

    def __init__(
        self,
        *,
        agent: Agent,
        session_manager: SessionManager,
        env: ExecutionEnv,
        resource_loader: ResourceLoader,
        resources: ResourceSnapshot,
        model_registry: ModelRegistry | None = None,
        session_repository: SessionRepository | None = None,
        compaction_controller: CompactionController | None = None,
        summarizer: Summarizer | None = None,
        branch_summarizer: Summarizer | None = None,
        auto_compaction_enabled: bool = False,
        default_system_prompt: str = "You are a coding agent.",
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.env = env
        self.resource_loader = resource_loader
        self.resources = resources
        self.model_registry = model_registry
        self.session_repository = session_repository
        self.compaction_controller = compaction_controller
        self.summarizer = summarizer
        self.branch_summarizer = branch_summarizer or summarizer
        self.auto_compaction_enabled = auto_compaction_enabled
        self.default_system_prompt = default_system_prompt
        self._listeners: list[SessionListener] = []
        self._closed = False
        self._is_compacting = False
        self._persistence_error: Exception | None = None
        self._external_prepare_next_turn = agent.prepare_next_turn
        self._unsubscribe_agent = agent.subscribe(self._on_agent_event)

    @property
    def state(self) -> AgentSessionState:
        return AgentSessionState(
            model=copy.deepcopy(self.agent.model),
            thinking_level=self.agent.thinking_level,
            messages=tuple(self.agent.messages),
            is_streaming=self.agent.is_streaming,
            is_compacting=self._is_compacting,
            session_id=self.session_id,
            session_name=self.session_name,
            auto_compaction_enabled=self.auto_compaction_enabled,
            pending_tool_calls=self.agent.pending_tool_calls,
            error_message=self.agent.error_message,
        )

    @property
    def session_id(self) -> str:
        return self.session_manager.header.id

    @property
    def session_name(self) -> str | None:
        return self.session_manager.tree.labels().get(
            self.session_manager.header.id,
            self.session_manager.header.name,
        )

    @property
    def session_file(self) -> Path | None:
        store = self.session_manager.store
        return store.path if hasattr(store, "path") else None

    @property
    def session_path(self) -> Path | None:
        return self.session_file

    @property
    def is_streaming(self) -> bool:
        return self.agent.is_streaming

    @property
    def is_idle(self) -> bool:
        return not self.agent.is_streaming and not self._is_compacting

    @property
    def transcript(self) -> list[Message]:
        return copy.deepcopy(self.session_manager.active_messages())

    def subscribe(self, listener: SessionListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    def install_prepare_next_turn(self) -> None:
        self.agent.prepare_next_turn = self._prepare_next_turn

    async def prompt(
        self,
        prompt: str | AgentMessage | list[AgentMessage],
        options: PromptOptions | None = None,
    ) -> AgentRunMessages:
        self._ensure_open()
        selected = options or PromptOptions()
        if self.agent.is_streaming:
            if selected.streaming_behavior == "steer":
                await self.steer_async(cast(str | AgentMessage, prompt))
                return self._empty_run()
            if selected.streaming_behavior == "followUp":
                await self.follow_up_async(cast(str | AgentMessage, prompt))
                return self._empty_run()
            raise RuntimeError("prompt while streaming requires streaming_behavior")
        self._sync_agent_context()
        self._persistence_error = None
        messages = await self.agent.prompt(prompt, images=list(selected.images) or None)
        self._raise_persistence_error()
        await self._emit(AgentSettledEvent())
        transcript: list[AgentMessage] = list(self.session_manager.active_messages())
        harness_result = build_harness_result(messages, transcript)
        return AgentRunMessages(messages, harness_result)

    async def continue_run(self) -> AgentRunMessages:
        self._ensure_open()
        self._sync_agent_context()
        self._persistence_error = None
        messages = await self.agent.continue_run()
        self._raise_persistence_error()
        await self._emit(AgentSettledEvent())
        transcript: list[AgentMessage] = list(self.session_manager.active_messages())
        harness_result = build_harness_result(messages, transcript)
        return AgentRunMessages(messages, harness_result)

    def steer(self, message: str | AgentMessage) -> None:
        self.agent.steer(message)

    async def steer_async(self, message: str | AgentMessage) -> None:
        self.steer(message)
        await self._emit(QueueUpdateEvent("steering", "push", _as_message(message)))

    def follow_up(self, message: str | AgentMessage) -> None:
        self.agent.follow_up(message)

    async def follow_up_async(self, message: str | AgentMessage) -> None:
        self.follow_up(message)
        await self._emit(QueueUpdateEvent("follow_up", "push", _as_message(message)))

    async def clear_queue(self, queue: QueueName = "all") -> None:
        if queue in {"steering", "all"}:
            self.agent.clear_steering_queue()
            await self._emit(QueueUpdateEvent("steering", "clear"))
        if queue in {"follow_up", "all"}:
            self.agent.clear_follow_up_queue()
            await self._emit(QueueUpdateEvent("follow_up", "clear"))

    def set_steering_mode(self, mode: QueueMode) -> None:
        self.agent.set_steering_mode(mode)

    def set_follow_up_mode(self, mode: QueueMode) -> None:
        self.agent.set_follow_up_mode(mode)

    def abort(self, reason: str = "Operation aborted") -> None:
        self.agent.abort(reason)

    async def wait_for_idle(self) -> None:
        await self.agent.wait_for_idle()

    async def compact(
        self,
        reason: CompactionReason = "manual",
        *,
        force: bool = True,
    ) -> CompactionRunResult | None:
        self._ensure_open()
        if self.agent.is_streaming:
            raise RuntimeError("cannot compact while the agent is streaming")
        controller = self.compaction_controller
        if controller is None:
            return None
        current = reconstruct_context(
            self.session_manager.tree.active_path(),
            compaction_prefix=controller.settings.summary_prefix,
        ).messages
        if not force and not should_compact(current, controller.settings):
            return None
        await self._emit(CompactionStartEvent(reason))
        self._is_compacting = True
        try:
            entry, result = await compact_session(
                self.session_manager,
                controller.settings,
                controller.summarizer,
                force=force,
                before=controller.before,
                after=controller.after,
            )
            self._sync_agent_context()
            await self._emit(EntryAppendedEvent(entry))
            await self._emit(CompactionEndEvent(reason, result))
            return CompactionRunResult(entry, result)
        except asyncio.CancelledError:
            await self._emit(CompactionEndEvent(reason, None, aborted=True))
            raise
        except Exception as exc:
            await self._emit(CompactionEndEvent(reason, None, error_message=str(exc)))
            raise
        finally:
            self._is_compacting = False

    def set_auto_compaction(self, enabled: bool) -> None:
        self.auto_compaction_enabled = enabled

    async def set_model(self, model: Model) -> None:
        self._ensure_idle("set model")
        self.agent.model = copy.deepcopy(model)
        entry = self.session_manager.append_model_change(model.provider, model.id)
        await self._emit(EntryAppendedEvent(entry))

    async def switch_model(self, model: Model) -> None:
        await self.set_model(model)

    async def cycle_model(self, direction: int = 1) -> Model:
        models = self.get_available_models()
        if not models:
            raise RuntimeError("no models are available")
        try:
            index = next(
                index
                for index, model in enumerate(models)
                if model.provider == self.agent.model.provider and model.id == self.agent.model.id
            )
        except StopIteration:
            index = -1 if direction > 0 else 0
        selected = models[(index + (1 if direction >= 0 else -1)) % len(models)]
        await self.set_model(selected)
        return selected

    def get_available_models(self) -> list[Model]:
        if self.model_registry is None:
            return [copy.deepcopy(self.agent.model)]
        return copy.deepcopy(self.model_registry.list())

    async def set_thinking_level(self, level: AgentThinkingLevel) -> None:
        self._ensure_idle("set thinking level")
        if level not in self.get_available_thinking_levels():
            raise ValueError(
                f"thinking level {level!r} is unavailable for model {self.agent.model.id!r}"
            )
        self.agent.thinking_level = level
        entry = self.session_manager.append_thinking_level(level)
        await self._emit(EntryAppendedEvent(entry))
        await self._emit(ThinkingLevelChangedEvent(level))

    async def cycle_thinking_level(self, direction: int = 1) -> AgentThinkingLevel:
        levels = self.get_available_thinking_levels()
        try:
            index = levels.index(self.agent.thinking_level)
        except ValueError:
            index = 0
        selected = levels[(index + (1 if direction >= 0 else -1)) % len(levels)]
        await self.set_thinking_level(selected)
        return selected

    def get_available_thinking_levels(self) -> list[AgentThinkingLevel]:
        if not self.agent.model.reasoning:
            return ["off"]
        return ["off", "minimal", "low", "medium", "high", "xhigh", "max"]

    async def new_session(self, *, name: str | None = None) -> SessionManager:
        self._ensure_idle("create session")
        if self.session_repository is None:
            manager = SessionManager.in_memory(cwd=self.session_manager.header.cwd, name=name)
        else:
            manager = self.session_repository.create(
                cwd=self.session_manager.header.cwd,
                name=name,
            )
        self.session_manager = manager
        self.agent.reset()
        self.agent.messages = []
        model_entry = manager.append_model_change(self.agent.model.provider, self.agent.model.id)
        thinking_entry = manager.append_thinking_level(self.agent.thinking_level)
        await self._emit(EntryAppendedEvent(model_entry))
        await self._emit(EntryAppendedEvent(thinking_entry))
        await self._emit(SessionInfoChangedEvent(manager.header.id, name))
        return manager

    async def switch_session(self, session_id: str) -> SessionManager:
        self._ensure_idle("switch session")
        if self.session_repository is None:
            raise RuntimeError("session switching requires a SessionRepository")
        manager = self.session_repository.open(session_id)
        self.session_manager = manager
        self._restore_branch_runtime()
        await self._emit(SessionInfoChangedEvent(manager.header.id, self.session_name))
        return manager

    async def resume_session(self, session_id: str) -> SessionManager:
        return await self.switch_session(session_id)

    async def fork(self, entry_id: str | None) -> None:
        self._ensure_idle("fork session")
        self.session_manager.fork(entry_id)
        self._restore_branch_runtime()

    async def navigate_tree(
        self,
        entry_id: str | None,
        *,
        summarize_abandoned: bool = False,
    ) -> None:
        self._ensure_idle("navigate session tree")
        if summarize_abandoned and self.branch_summarizer is not None:
            if entry_id is None:
                self.session_manager.navigate(None)
            else:
                summary_entry, _result = await summarize_and_navigate_branch(
                    self.session_manager,
                    to_entry_id=entry_id,
                    summarizer=self.branch_summarizer,
                )
                if summary_entry is not None:
                    await self._emit(EntryAppendedEvent(summary_entry))
        else:
            self.session_manager.navigate(entry_id)
        self._restore_branch_runtime()

    async def set_session_name(self, name: str | None) -> None:
        self._ensure_idle("rename session")
        entry = self.session_manager.set_label(self.session_id, name)
        await self._emit(EntryAppendedEvent(entry))
        await self._emit(SessionInfoChangedEvent(self.session_id, name))

    async def reload_resources(self) -> ResourceSnapshot:
        self._ensure_idle("reload resources")
        resources = self.resource_loader.reload()
        self.resources = resources
        self.agent.system_prompt = SystemPromptBuilder(self.default_system_prompt).build(resources)
        return resources

    def get_entries(self) -> list[dict[str, Any]]:
        return [
            record_to_dict(self.session_manager.tree.entries[entry_id])
            for entry_id in self.session_manager.tree.order
        ]

    def get_tree(self) -> dict[str, Any]:
        def node(entry_id: str) -> dict[str, Any]:
            entry = self.session_manager.tree.entries[entry_id]
            return {
                "entry": record_to_dict(entry),
                "children": [
                    node(child) for child in self.session_manager.tree.child_ids(entry_id)
                ],
            }

        roots = [node(entry_id) for entry_id in self.session_manager.tree.child_ids(None)]
        return {
            "sessionId": self.session_id,
            "leafId": self.session_manager.leaf_id,
            "root": {
                "id": self.session_id,
                "name": self.session_name,
                "cwd": self.session_manager.header.cwd,
                "children": roots,
            },
        }

    def get_messages(self) -> list[Message]:
        return copy.deepcopy(self.session_manager.active_messages())

    def get_last_assistant_text(self) -> str | None:
        for message in reversed(self.session_manager.active_messages()):
            if isinstance(message, AssistantMessage):
                return message_text(message)
        return None

    def get_session_stats(self) -> SessionStats:
        messages = self.session_manager.active_messages()
        usage = build_harness_result(list(messages), list(messages)).usage
        return SessionStats(
            session_id=self.session_id,
            entry_count=len(self.session_manager.tree.entries),
            message_count=len(messages),
            assistant_count=sum(isinstance(message, AssistantMessage) for message in messages),
            tool_result_count=sum(isinstance(message, ToolResultMessage) for message in messages),
            leaf_count=len(self.session_manager.tree.leaf_ids()),
            usage=usage,
        )

    def get_state(self) -> dict[str, Any]:
        state = self.state
        return {
            "model": {
                "provider": state.model.provider,
                "id": state.model.id,
                "name": state.model.name,
                "api": state.model.api,
            },
            "thinkingLevel": state.thinking_level,
            "isStreaming": state.is_streaming,
            "isCompacting": state.is_compacting,
            "session": {
                "id": state.session_id,
                "name": state.session_name,
                "file": str(self.session_file) if self.session_file is not None else None,
                "entryCount": len(self.session_manager.tree.entries),
                "leafId": self.session_manager.leaf_id,
            },
            "autoCompactionEnabled": state.auto_compaction_enabled,
            "pendingToolCalls": sorted(state.pending_tool_calls),
            "errorMessage": state.error_message,
        }

    async def execute_bash(self, command: str, *, timeout: float | None = None) -> dict[str, Any]:
        result = get_or_raise(
            await self.env.exec(
                command,
                ShellExecOptions(cwd=self.session_manager.header.cwd, timeout=timeout),
            )
        )
        return {
            "command": result.command or command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exitCode": result.exit_code,
            "durationSeconds": result.duration_seconds,
        }

    async def close(self) -> None:
        if self._closed:
            return
        self.abort("AgentSession closed")
        await self.wait_for_idle()
        self._unsubscribe_agent()
        await self.env.cleanup()
        self._closed = True

    async def _on_agent_event(self, event: AgentEvent, *_args: Any) -> None:
        if isinstance(event, MessageEndEvent):
            try:
                entry: TreeEntry
                if isinstance(event.message, (UserMessage, AssistantMessage, ToolResultMessage)):
                    entry = self.session_manager.append_message(event.message)
                else:
                    entry = self.session_manager.append_custom(
                        str(event.message.get("role", "custom")),
                        copy.deepcopy(event.message),
                    )
                await self._emit(EntryAppendedEvent(entry))
            except Exception as exc:
                self._persistence_error = exc
                raise
        await self._emit(event)

    async def _emit(self, event: AgentSessionEvent) -> None:
        for listener in tuple(self._listeners):
            result = listener(event)
            if inspect.isawaitable(result):
                await result

    async def _prepare_next_turn(
        self,
        completed: CompletedTurnContext,
    ) -> AgentLoopTurnUpdate | None:
        external_update: AgentLoopTurnUpdate | None = None
        if self._external_prepare_next_turn is not None:
            value = self._external_prepare_next_turn(completed)
            external_update = await value if inspect.isawaitable(value) else value
        controller = self.compaction_controller
        if not self.auto_compaction_enabled or controller is None:
            return external_update
        current = reconstruct_context(
            self.session_manager.tree.active_path(),
            compaction_prefix=controller.settings.summary_prefix,
        ).messages
        if not should_compact(current, controller.settings):
            return external_update
        await self._emit(CompactionStartEvent("auto"))
        self._is_compacting = True
        try:
            decision = await controller.prepare_next_provider_context(self.session_manager)
            if not decision.compacted:
                return external_update
            if decision.entry is not None:
                await self._emit(EntryAppendedEvent(decision.entry))
            await self._emit(CompactionEndEvent("auto", decision.result))
            context = AgentContext(
                system_prompt=completed.context.system_prompt,
                messages=list(decision.messages),
                tools=list(completed.context.tools),
            )
            if external_update is not None and external_update.context is not None:
                context.system_prompt = external_update.context.system_prompt
                context.tools = list(external_update.context.tools)
            return AgentLoopTurnUpdate(
                context=context,
                model=external_update.model if external_update is not None else None,
                thinking_level=(
                    external_update.thinking_level if external_update is not None else None
                ),
            )
        except asyncio.CancelledError:
            await self._emit(CompactionEndEvent("auto", None, aborted=True))
            raise
        except Exception as exc:
            await self._emit(CompactionEndEvent("auto", None, error_message=str(exc)))
            raise
        finally:
            self._is_compacting = False

    def _sync_agent_context(self) -> None:
        reconstructed = reconstruct_context(self.session_manager.tree.active_path())
        self.agent.messages = list(copy.deepcopy(reconstructed.messages))

    def _restore_branch_runtime(self) -> None:
        reconstructed = reconstruct_context(self.session_manager.tree.active_path())
        if (
            reconstructed.model_provider
            and reconstructed.model_id
            and self.model_registry is not None
        ):
            with suppress(KeyError):
                self.agent.model = self.model_registry.get(
                    reconstructed.model_provider,
                    reconstructed.model_id,
                )
        if reconstructed.thinking_level in self.get_available_thinking_levels():
            self.agent.thinking_level = cast(AgentThinkingLevel, reconstructed.thinking_level)
        else:
            self.agent.thinking_level = "off"
        self.agent.messages = list(copy.deepcopy(reconstructed.messages))

    def _raise_persistence_error(self) -> None:
        if self._persistence_error is None:
            return
        error = self._persistence_error
        self._persistence_error = None
        raise RuntimeError(f"session persistence failed: {error}") from error

    def _ensure_idle(self, action: str) -> None:
        self._ensure_open()
        if self.agent.is_streaming or self._is_compacting:
            raise RuntimeError(f"cannot {action} while AgentSession is busy")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AgentSession is closed")

    def _empty_run(self) -> AgentRunMessages:
        transcript: list[AgentMessage] = list(self.session_manager.active_messages())
        harness_result = build_harness_result([], transcript)
        return AgentRunMessages([], harness_result)


def _as_message(message: str | AgentMessage) -> AgentMessage:
    return UserMessage(message) if isinstance(message, str) else copy.deepcopy(message)
