from __future__ import annotations

import asyncio
import copy
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pi_agent.agent import (
    Agent,
    AgentEvent,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentOptions,
    AgentState,
    AgentTool,
    CompletedTurnContext,
    MessageEndEvent,
)
from pi_agent.ai import (
    AssistantMessage,
    Context,
    Message,
    Model,
    OpenAICompatibleProvider,
    StreamFunction,
    StreamOptions,
    ToolResultMessage,
    UserMessage,
    message_text,
)
from pi_agent.ai.types import usage_to_dict
from pi_agent.harness import (
    ExecutionEnv,
    HarnessResult,
    build_harness_result,
    build_system_prompt,
    create_coding_tools,
    create_local_execution_env,
)
from pi_agent.harness.compaction import (
    CompactionController,
    CompactionSettings,
    SummaryResult,
    Summarizer,
    generate_branch_summary,
    serialize_conversation,
)
from pi_agent.harness.resource_loader import ResourceLoader
from pi_agent.harness.resource_types import LoadedResources
from pi_agent.harness.session import SessionEntry, SessionManager, SessionTreeNode

from .configuration import (
    compaction_settings_from_settings,
    load_settings,
    model_from_settings,
    resource_loader_from_settings,
    stream_options_from_settings,
)
from .settings import Settings

SessionListener = Callable[[AgentEvent], Awaitable[None] | None]


@dataclass(slots=True)
class AgentSessionOptions:
    cwd: str | Path = field(default_factory=Path.cwd)
    model: Model | None = None
    stream_fn: StreamFunction | None = None
    stream_options: StreamOptions | None = None
    api_key: str | None = None
    env: ExecutionEnv | None = None
    settings: Settings | None = None
    session_manager: SessionManager | None = None
    session_path: str | Path | None = None
    no_session: bool = False
    repair_session_tail: bool = False
    resource_loader: ResourceLoader | None = None
    resources: LoadedResources | None = None
    system_prompt: str = ""
    tools: list[AgentTool] = field(default_factory=list)
    include_coding_tools: bool = True
    summarizer: Summarizer | None = None
    compaction_settings: CompactionSettings | None = None


class AgentSession:
    """Product-level session shared by print, JSON, RPC, and future TUI modes."""

    def __init__(
        self,
        *,
        options: AgentSessionOptions,
        settings: Settings,
        model: Model,
        stream_fn: StreamFunction,
        stream_options: StreamOptions,
        env: ExecutionEnv,
        resources: LoadedResources,
        resource_loader: ResourceLoader,
        session_manager: SessionManager,
        compaction: CompactionController | None,
    ) -> None:
        self.options = options
        self.settings = settings
        self.env = env
        self.resources = resources
        self.resource_loader = resource_loader
        self.session_manager = session_manager
        self.compaction = compaction
        self._closed = False
        self._model = model
        self._stream_fn = stream_fn
        self._stream_options = stream_options
        self._listeners: list[SessionListener] = []
        self._persistence_lock = asyncio.Lock()
        self._compaction_lock = asyncio.Lock()
        self._auto_compaction_entries: set[int] = set()

        selected_tools: list[AgentTool] = []
        if options.include_coding_tools:
            selected_tools.extend(create_coding_tools(env))
        selected_tools.extend(options.tools)
        enabled = set(cast(list[str], settings.require("tools.enabled")))
        if enabled:
            selected_tools = [
                tool for tool in selected_tools if tool.name in enabled or tool in options.tools
            ]
        names = [tool.name for tool in selected_tools]
        if len(names) != len(set(names)):
            raise ValueError("AgentSession tools must have unique names")
        default_timeout = float(settings.require("tools.timeout"))
        for tool in selected_tools:
            if tool.timeout is None:
                tool.timeout = default_timeout

        prompt = build_system_prompt(
            options.system_prompt,
            cwd=env.cwd,
            resources=resources,
        )
        state = AgentState(
            system_prompt=prompt,
            model=model,
            thinking_level=cast(Any, settings.require("thinking.level")),
            tools=selected_tools,
            messages=session_manager.model_context(),
        )
        self.agent = Agent(
            AgentOptions(
                model=model,
                stream_fn=stream_fn,
                initial_state=state,
                stream_options=stream_options,
                prepare_next_turn_with_context=self._prepare_next_turn,
            )
        )
        self.agent.subscribe(self._on_agent_event)

    @classmethod
    async def create(cls, options: AgentSessionOptions | None = None) -> AgentSession:
        opts = options or AgentSessionOptions()
        cwd = Path(opts.cwd).expanduser().resolve()
        settings = opts.settings or load_settings(cwd=cwd)
        model = opts.model or model_from_settings(settings)
        stream_options = opts.stream_options or stream_options_from_settings(settings)
        if opts.api_key is not None:
            stream_options.api_key = opts.api_key
        if opts.stream_fn is None:
            provider = OpenAICompatibleProvider(
                api_key=opts.api_key or _environment_api_key(model.provider)
            )
            stream_fn = provider.stream
        else:
            stream_fn = opts.stream_fn
        env = opts.env or create_local_execution_env(cwd)
        loader = opts.resource_loader or resource_loader_from_settings(
            settings,
            cwd=cwd,
        )
        resources = opts.resources or loader.load()
        manager = opts.session_manager or await _create_session_manager(opts, settings, cwd)
        compaction_settings = opts.compaction_settings or compaction_settings_from_settings(
            settings
        )
        summarizer = opts.summarizer or _model_summarizer(model, stream_fn, stream_options)
        controller = (
            CompactionController(compaction_settings, summarizer)
            if compaction_settings.enabled
            else None
        )
        previous = _latest_summary(manager.active_entries())
        if controller is not None:
            controller.previous_summary = previous
        return cls(
            options=opts,
            settings=settings,
            model=model,
            stream_fn=stream_fn,
            stream_options=stream_options,
            env=env,
            resources=resources,
            resource_loader=loader,
            session_manager=manager,
            compaction=controller,
        )

    @property
    def model(self) -> Model:
        return self.agent.model

    @property
    def thinking_level(self) -> str:
        return self.agent.thinking_level

    @property
    def is_running(self) -> bool:
        return self.agent.is_streaming

    @property
    def session_path(self) -> Path | None:
        return self.session_manager.path

    @property
    def transcript(self) -> list[Message]:
        return [
            message
            for entry in self.session_manager.active_entries()
            if (message := entry.message) is not None
        ]

    def subscribe(self, listener: SessionListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    async def prompt(self, prompt: str | AgentMessage | list[AgentMessage]) -> HarnessResult:
        self._ensure_open()
        self._sync_agent_context()
        new_messages = await self.agent.prompt(prompt)
        return build_harness_result(new_messages, self.transcript)

    async def continue_run(self) -> HarnessResult:
        self._ensure_open()
        self._sync_agent_context()
        new_messages = await self.agent.continue_run()
        return build_harness_result(new_messages, self.transcript)

    def steer(self, message: str | AgentMessage) -> None:
        self._ensure_open()
        self.agent.steer(message)

    def follow_up(self, message: str | AgentMessage) -> None:
        self._ensure_open()
        self.agent.follow_up(message)

    def abort(self, reason: str = "Operation aborted") -> None:
        self.agent.abort(reason)

    async def wait_for_idle(self) -> None:
        await self.agent.wait_for_idle()

    async def compact(self, *, force: bool = True) -> dict[str, Any] | None:
        self._ensure_open()
        if self.agent.is_streaming:
            raise RuntimeError("Cannot compact while the AgentSession is running")
        if self.compaction is None:
            return None
        async with self._compaction_lock:
            messages = self.session_manager.model_context()
            if not force and not self.compaction.needed(messages, self.model):
                return None
            compacted = await self.compaction.prepare_next_provider_context(
                messages,
                self.model,
                force=True,
            )
            result = self.compaction.last_result
            if result is None:
                return None
            first_kept_id = self._first_kept_message_id(result.retained_count)
            entry = await self.session_manager.record_compaction(
                summary=result.summary,
                first_kept_id=first_kept_id,
                usage=usage_to_dict(result.usage),
                files=result.files.to_dict(),
            )
            self.agent.messages = compacted
            return {
                "entryId": entry.id,
                "summary": result.summary,
                "summarizedCount": result.summarized_count,
                "retainedCount": result.retained_count,
                "contextTokensBefore": result.context_tokens_before,
                "estimatedTokensAfter": result.estimated_tokens_after,
            }

    async def switch_model(self, model: Model) -> None:
        self._ensure_open()
        if self.agent.is_streaming:
            raise RuntimeError("Cannot switch model while running")
        self.agent.model = model
        self._model = model
        await self.session_manager.record_model(model)

    async def set_thinking_level(self, level: str) -> None:
        if level not in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(f"Unsupported thinking level: {level}")
        if self.agent.is_streaming:
            raise RuntimeError("Cannot change thinking level while running")
        self.agent.thinking_level = cast(Any, level)
        await self.session_manager.record_thinking_level(level)

    async def reload_resources(self) -> LoadedResources:
        if self.agent.is_streaming:
            raise RuntimeError("Cannot reload resources while running")
        self.resources = self.resource_loader.reload()
        self.agent.system_prompt = build_system_prompt(
            self.options.system_prompt,
            cwd=self.env.cwd,
            resources=self.resources,
        )
        return self.resources

    async def new_session(
        self,
        *,
        path: str | Path | None = None,
        name: str | None = None,
        no_session: bool = False,
    ) -> None:
        if self.agent.is_streaming:
            raise RuntimeError("Cannot replace session while running")
        if no_session:
            manager = await SessionManager.memory(cwd=self.env.cwd, name=name)
        else:
            target = (
                Path(path).expanduser().resolve() if path is not None else self._next_session_path()
            )
            manager = await SessionManager.create(
                target,
                cwd=self.env.cwd,
                name=name,
                fsync=bool(self.settings.require("session.fsync")),
            )
        self.session_manager = manager
        if self.compaction is not None:
            self.compaction.previous_summary = None
            self.compaction.last_result = None
        self.agent.reset()
        self.agent.messages = []

    async def resume_session(
        self,
        path: str | Path,
        *,
        repair_tail: bool = False,
    ) -> None:
        if self.agent.is_streaming:
            raise RuntimeError("Cannot resume a session while running")
        manager = await SessionManager.open(
            path,
            fsync=bool(self.settings.require("session.fsync")),
            repair_tail=repair_tail,
        )
        self.session_manager = manager
        if self.compaction is not None:
            self.compaction.previous_summary = _latest_summary(manager.active_entries())
            self.compaction.last_result = None
        self.agent.messages = manager.model_context()

    async def navigate_tree(
        self,
        entry_id: str | None,
        *,
        summarize_abandoned_branch: bool = False,
    ) -> None:
        if self.agent.is_streaming:
            raise RuntimeError("Cannot navigate the session tree while running")
        previous = self.session_manager.current_id
        summary: str | None = None
        if summarize_abandoned_branch and previous != entry_id and self.compaction is not None:
            ancestor = self.session_manager.common_ancestor(previous, entry_id)
            previous_branch = self.session_manager.active_entries()
            unique_messages = _messages_after(previous_branch, ancestor)
            if unique_messages:
                branch = await generate_branch_summary(
                    unique_messages,
                    summarizer=self.compaction.summarizer,
                    previous_summary=None,
                    from_id=previous,
                    to_id=entry_id,
                )
                summary = branch.summary
        await self.session_manager.navigate(entry_id)
        if summary is not None:
            await self.session_manager.record_branch_summary(
                summary=summary,
                from_id=previous,
                to_id=entry_id,
            )
        self.agent.messages = self.session_manager.model_context()

    def get_tree(self) -> list[dict[str, Any]]:
        return [
            _tree_to_dict(node, self.session_manager.current_id)
            for node in self.session_manager.tree()
        ]

    def get_state(self) -> dict[str, Any]:
        state = self.agent.state
        return {
            "session": {
                "id": self.session_manager.info.id,
                "path": str(self.session_path) if self.session_path is not None else None,
                "name": self.session_manager.info.name,
                "currentId": self.session_manager.current_id,
                "entryCount": self.session_manager.info.entry_count,
            },
            "model": {
                "provider": state.model.provider,
                "id": state.model.id,
                "api": state.model.api,
                "baseUrl": state.model.base_url,
            },
            "thinkingLevel": state.thinking_level,
            "isStreaming": state.is_streaming,
            "pendingToolCalls": sorted(state.pending_tool_calls),
            "errorMessage": state.error_message,
            "toolNames": [tool.name for tool in state.tools],
            "resourceWarnings": [warning.message for warning in self.resources.warnings],
        }

    async def close(self) -> None:
        if self._closed:
            return
        if self.agent.is_streaming:
            self.agent.abort("AgentSession closing")
            await self.agent.wait_for_idle()
        await self.env.cleanup()
        self._closed = True

    async def _on_agent_event(self, event: AgentEvent, *_args: Any) -> None:
        if isinstance(event, MessageEndEvent):
            async with self._persistence_lock:
                message = event.message
                if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
                    await self.session_manager.append_message(message)
                elif isinstance(message, dict):
                    await self.session_manager.record_custom(
                        str(message.get("role", "custom")),
                        copy.deepcopy(message),
                    )
        for listener in tuple(self._listeners):
            value = listener(event)
            if asyncio.iscoroutine(value):
                await value

    async def _prepare_next_turn(
        self,
        completed: CompletedTurnContext,
        signal: Any = None,
    ) -> AgentLoopTurnUpdate | None:
        if self.compaction is None:
            return None
        messages = [
            message
            for message in completed.context.messages
            if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage))
        ]
        if not self.compaction.needed(messages, self.model):
            return None
        async with self._compaction_lock:
            compacted = await self.compaction.prepare_next_provider_context(
                messages,
                self.model,
                signal,
            )
            result = self.compaction.last_result
            if result is None:
                return None
            marker = id(result)
            if marker not in self._auto_compaction_entries:
                self._auto_compaction_entries.add(marker)
                await self.session_manager.record_compaction(
                    summary=result.summary,
                    first_kept_id=self._first_kept_message_id(result.retained_count),
                    usage=usage_to_dict(result.usage),
                    files=result.files.to_dict(),
                )
            return AgentLoopTurnUpdate(
                context=type(completed.context)(
                    system_prompt=completed.context.system_prompt,
                    messages=list(compacted),
                    tools=list(completed.context.tools),
                )
            )

    def _first_kept_message_id(self, retained_count: int) -> str | None:
        message_entries = [
            entry for entry in self.session_manager.active_entries() if entry.type == "message"
        ]
        if retained_count <= 0 or retained_count > len(message_entries):
            return None
        return message_entries[-retained_count].id

    def _sync_agent_context(self) -> None:
        if self.agent.is_streaming:
            raise RuntimeError("AgentSession is already running")
        self.agent.messages = self.session_manager.model_context()

    def _next_session_path(self) -> Path:
        directory = Path(str(self.settings.require("session.directory"))).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return directory / f"{stamp}-{uuid.uuid4().hex[:10]}.jsonl"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AgentSession is closed")


async def create_agent_session(options: AgentSessionOptions | None = None) -> AgentSession:
    return await AgentSession.create(options)


async def _create_session_manager(
    options: AgentSessionOptions,
    settings: Settings,
    cwd: Path,
) -> SessionManager:
    if options.no_session or not bool(settings.require("session.enabled")):
        return await SessionManager.memory(cwd=cwd)
    fsync = bool(settings.require("session.fsync"))
    if options.session_path is not None:
        path = Path(options.session_path).expanduser().resolve()
        if path.exists():
            return await SessionManager.open(
                path,
                fsync=fsync,
                repair_tail=options.repair_session_tail,
            )
        return await SessionManager.create(path, cwd=cwd, fsync=fsync)
    directory = Path(str(settings.require("session.directory"))).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}-{uuid.uuid4().hex[:10]}.jsonl"
    return await SessionManager.create(path, cwd=cwd, fsync=fsync)


def _model_summarizer(
    model: Model,
    stream_fn: StreamFunction,
    stream_options: StreamOptions,
) -> Summarizer:
    async def summarize(
        messages: list[Message],
        previous_summary: str | None,
        signal: Any,
    ) -> SummaryResult:
        prior = f"\n\nPrevious summary:\n{previous_summary}" if previous_summary else ""
        prompt = (
            "Summarize the following coding-agent conversation for continuation. "
            "Preserve goals, constraints, decisions, file paths, edits, test results, "
            "failures, and unresolved work. Do not invent facts."
            f"{prior}\n\nConversation:\n{serialize_conversation(messages)}"
        )
        options = copy.deepcopy(stream_options)
        options.signal = signal
        context = Context(messages=[UserMessage(prompt)], system_prompt=None, tools=None)
        response = stream_fn(model, context, options)
        if asyncio.iscoroutine(response):
            response = await response
        async for _event in response:
            pass
        final = await response.result()
        if final.stop_reason in {"error", "aborted"}:
            raise RuntimeError(final.error_message or "Summary request failed")
        text = message_text(final)
        if not text.strip():
            raise RuntimeError("Summary model returned no text")
        return SummaryResult(text.strip(), final.usage)

    return summarize


def _latest_summary(entries: list[SessionEntry]) -> str | None:
    for entry in reversed(entries):
        if entry.type == "compaction" and isinstance(entry.data.get("summary"), str):
            return cast(str, entry.data["summary"])
    return None


def _messages_after(entries: list[SessionEntry], ancestor_id: str | None) -> list[Message]:
    collect = ancestor_id is None
    messages: list[Message] = []
    for entry in entries:
        if not collect:
            if entry.id == ancestor_id:
                collect = True
            continue
        message = entry.message
        if message is not None:
            messages.append(message)
    return messages


def _tree_to_dict(node: SessionTreeNode, current_id: str | None) -> dict[str, Any]:
    return {
        "id": node.entry.id,
        "parentId": node.entry.parent_id,
        "type": node.entry.type,
        "timestamp": node.entry.timestamp,
        "current": node.entry.id == current_id,
        "data": copy.deepcopy(node.entry.data),
        "children": [_tree_to_dict(child, current_id) for child in node.children],
    }


def _environment_api_key(provider: str) -> str | None:
    names = [
        f"{provider.upper().replace('-', '_')}_API_KEY",
        "OPENAI_API_KEY" if provider == "openai" else "",
        "ANTHROPIC_API_KEY" if provider == "anthropic" else "",
    ]
    for name in names:
        if name and os.environ.get(name):
            return os.environ[name]
    return None
