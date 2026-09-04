from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pi_agent.agent import Agent, AgentOptions, AgentThinkingLevel, AgentTool
from pi_agent.agent.types import BeforeToolCall
from pi_agent.ai import (
    Context,
    Model,
    ProviderRegistry,
    StreamFunction,
    StreamOptions,
    TextContent,
    UserMessage,
    message_text,
)
from pi_agent.harness import (
    ExecutionEnv,
    ResourceLoader,
    SystemPromptBuilder,
    create_coding_tools,
    create_local_execution_env,
)
from pi_agent.harness.compaction import (
    CompactionController,
    CompactionSettings,
    Summarizer,
    SummaryResponse,
)
from pi_agent.harness.session import SessionManager, SessionRepository, reconstruct_context

from .agent_session import AgentSession
from .approval import ApprovalGate, compose_before_tool_hooks
from .configuration import build_resource_loader_config, session_directory
from .settings import SettingsResolver, SettingsSnapshot


@dataclass(slots=True)
class CreateAgentSessionOptions:
    cwd: str | Path | None = None
    model: Model | None = None
    provider_registry: ProviderRegistry | None = None
    stream_fn: StreamFunction | None = None
    env: ExecutionEnv | None = None
    settings: SettingsSnapshot | None = None
    settings_resolver: SettingsResolver | None = None
    global_settings_path: str | Path | None = None
    project_settings_path: str | Path | None = None
    setting_overrides: dict[str, Any] = field(default_factory=dict)
    resource_loader: ResourceLoader | None = None
    session_manager: SessionManager | None = None
    session_repository: SessionRepository | None = None
    session_id: str | None = None
    session_name: str | None = None
    no_session: bool = False
    tools: list[AgentTool] = field(default_factory=list)
    include_coding_tools: bool = True
    system_prompt: str = "You are a coding agent."
    summarizer: Summarizer | None = None
    branch_summarizer: Summarizer | None = None
    auto_compaction_enabled: bool | None = None
    before_tool_call: BeforeToolCall | None = None
    approval_gate: ApprovalGate | None = None
    approval_manager: Any | None = None
    api_key: str | None = None


@dataclass(slots=True, frozen=True)
class CreateAgentSessionResult:
    session: AgentSession
    warnings: tuple[str, ...] = ()
    model_fallback_message: str | None = None


async def create_agent_session(
    options: CreateAgentSessionOptions | None = None,
) -> CreateAgentSessionResult:
    selected = options or CreateAgentSessionOptions()
    cwd = Path(selected.cwd or Path.cwd()).expanduser().resolve()
    settings = selected.settings or _resolve_settings(selected, cwd)
    warnings = [warning.message for warning in settings.warnings]

    resource_loader = selected.resource_loader or ResourceLoader(
        build_resource_loader_config(settings, cwd=cwd)
    )
    resources = resource_loader.load()
    warnings.extend(warning.message for warning in resources.warnings)

    repository = selected.session_repository
    manager = selected.session_manager
    session_enabled = bool(settings.require("session.enabled")) and not selected.no_session
    if manager is None:
        if not session_enabled:
            manager = SessionManager.in_memory(cwd=str(cwd), name=selected.session_name)
        else:
            repository = repository or SessionRepository(session_directory(settings))
            manager = (
                repository.open(selected.session_id)
                if selected.session_id is not None
                else repository.create(cwd=str(cwd), name=selected.session_name)
            )

    reconstructed = reconstruct_context(manager.tree.active_path())
    registry = selected.provider_registry
    model, fallback = _resolve_model(selected, settings, reconstructed, registry)
    if fallback:
        warnings.append(fallback)
    stream_fn = selected.stream_fn or (registry.stream if registry is not None else None)
    if stream_fn is None:
        raise ValueError("create_agent_session requires stream_fn or provider_registry")

    env = selected.env or create_local_execution_env(cwd)
    tools = _select_tools(selected, settings, env)
    if selected.approval_manager is not None:
        selected.approval_manager.session_id = manager.header.id
        tools = selected.approval_manager.guard_tools(tools)
    system_prompt = SystemPromptBuilder(selected.system_prompt).build(resources)
    thinking_level = _resolve_thinking_level(settings, reconstructed.thinking_level, model)
    stream_options = _stream_options(settings, manager.header.id, selected.api_key)

    summarizer = selected.summarizer or create_model_summarizer(stream_fn, model, stream_options)
    controller: CompactionController | None = None
    auto_compaction = (
        bool(settings.require("compaction.enabled"))
        if selected.auto_compaction_enabled is None
        else selected.auto_compaction_enabled
    )
    if auto_compaction:
        controller_settings = _compaction_settings(settings, model)
        if controller_settings is None:
            warnings.append(
                "automatic compaction was disabled because the model context window is smaller "
                "than the configured reserve"
            )
            auto_compaction = False
        else:
            controller = CompactionController(controller_settings, summarizer)

    if selected.approval_gate is not None:
        selected.approval_gate.session_id = manager.header.id
    before_tool_call = compose_before_tool_hooks(
        selected.before_tool_call,
        selected.approval_gate,
    )
    agent = Agent(
        AgentOptions(
            model=model,
            stream_fn=stream_fn,
            system_prompt=system_prompt,
            thinking_level=thinking_level,
            tools=tools,
            messages=list(copy.deepcopy(reconstructed.messages)),
            stream_options=stream_options,
            before_tool_call=before_tool_call,
        )
    )
    session = AgentSession(
        agent=agent,
        session_manager=manager,
        env=env,
        resource_loader=resource_loader,
        resources=resources,
        model_registry=registry.models if registry is not None else None,
        session_repository=repository,
        compaction_controller=controller,
        summarizer=summarizer,
        branch_summarizer=selected.branch_summarizer or summarizer,
        auto_compaction_enabled=auto_compaction,
    )
    session.install_prepare_next_turn()
    _seed_new_session(manager, model, thinking_level)
    return CreateAgentSessionResult(session, tuple(warnings), fallback)


async def create_agent_session_only(
    options: CreateAgentSessionOptions | None = None,
) -> AgentSession:
    return (await create_agent_session(options)).session


def create_model_summarizer(
    stream_fn: StreamFunction,
    model: Model,
    base_options: StreamOptions | None = None,
) -> Summarizer:
    async def summarize(conversation: str, previous_summary: str | None = None) -> SummaryResponse:
        previous = (
            f"\n\nPrevious summary to refine:\n{previous_summary}" if previous_summary else ""
        )
        prompt = (
            "Summarize this coding-agent conversation for continuation. Preserve current goals, "
            "decisions, constraints, files read or changed, tool outcomes, failures, and "
            "next steps. Do not invent facts.\n\nConversation:\n"
            f"{conversation}{previous}"
        )
        options = copy.deepcopy(base_options or StreamOptions())
        options.max_tokens = min(model.max_tokens or 4096, 4096)
        options.cache_retention = "none"
        response_or_awaitable = stream_fn(
            model,
            Context(messages=[UserMessage([TextContent(prompt)])]),
            options,
        )
        response = (
            await response_or_awaitable
            if inspect.isawaitable(response_or_awaitable)
            else response_or_awaitable
        )
        result = await response.result()
        if result.stop_reason in {"error", "aborted"}:
            raise RuntimeError(result.error_message or "summary generation failed")
        text = message_text(result).strip()
        if not text:
            raise RuntimeError("summary generation returned no text")
        return SummaryResponse(text, copy.deepcopy(result.usage))

    return summarize


def _resolve_settings(
    options: CreateAgentSessionOptions,
    cwd: Path,
) -> SettingsSnapshot:
    resolver = options.settings_resolver or SettingsResolver()
    global_path = options.global_settings_path or Path.home() / ".pi" / "agent" / "settings.json"
    project_path = options.project_settings_path or cwd / ".pi" / "settings.json"
    return resolver.resolve(
        global_path=global_path,
        project_path=project_path,
        cli=options.setting_overrides,
    )


def _resolve_model(
    options: CreateAgentSessionOptions,
    settings: SettingsSnapshot,
    reconstructed: Any,
    registry: ProviderRegistry | None,
) -> tuple[Model, str | None]:
    if options.model is not None:
        return copy.deepcopy(options.model), None

    fallback: str | None = None
    if reconstructed.model_provider and reconstructed.model_id and registry is not None:
        try:
            return registry.models.get(
                reconstructed.model_provider,
                reconstructed.model_id,
            ), None
        except KeyError:
            fallback = (
                f"session model {reconstructed.model_provider}/{reconstructed.model_id} "
                "is unavailable; using the configured fallback"
            )

    provider = settings.get("model.provider")
    model_id = settings.get("model.id")
    if registry is not None and provider and model_id:
        return registry.models.get(str(provider), str(model_id)), fallback
    if registry is not None and model_id:
        return registry.models.resolve(str(model_id)), fallback
    if registry is not None:
        models = registry.models.list()
        if models:
            return models[0], fallback
    raise ValueError("no model is configured or registered")


def _resolve_thinking_level(
    settings: SettingsSnapshot,
    restored: str | None,
    model: Model,
) -> AgentThinkingLevel:
    raw = restored or str(settings.require("thinking.level"))
    allowed = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
    level = cast(AgentThinkingLevel, raw if raw in allowed else "off")
    return level if model.reasoning else "off"


def _stream_options(
    settings: SettingsSnapshot,
    session_id: str,
    api_key: str | None,
) -> StreamOptions:
    transport = str(settings.require("provider.transport"))
    # Phase 6 currently implements SSE/auto. Other transports remain provider
    # metadata until the corresponding provider adapter is added.
    normalized_transport = "sse" if transport == "sse" else "auto"
    return StreamOptions(
        api_key=api_key,
        session_id=session_id,
        transport=cast(Any, normalized_transport),
        timeout=float(settings.require("provider.timeout_seconds")),
        stream_idle_timeout=cast(
            float | None,
            settings.require("provider.stream_idle_timeout_seconds"),
        ),
        max_retries=int(settings.require("provider.max_retries")),
        max_retry_delay=float(settings.require("provider.max_retry_delay_seconds")),
    )


def _compaction_settings(
    settings: SettingsSnapshot,
    model: Model,
) -> CompactionSettings | None:
    context_window = model.context_window
    reserve = int(settings.require("compaction.reserve_tokens"))
    keep = int(settings.require("compaction.keep_recent_tokens"))
    if context_window <= 0 or reserve >= context_window:
        return None
    return CompactionSettings(
        context_window=context_window,
        reserve_tokens=reserve,
        keep_recent_tokens=keep,
    )


def _select_tools(
    options: CreateAgentSessionOptions,
    settings: SettingsSnapshot,
    env: ExecutionEnv,
) -> list[AgentTool]:
    selected: list[AgentTool] = []
    enabled = set(cast(tuple[str, ...], settings.require("tools.enabled")))
    if options.include_coding_tools:
        selected.extend(tool for tool in create_coding_tools(env) if tool.name in enabled)
    selected.extend(copy.deepcopy(options.tools))
    names = [tool.name for tool in selected]
    if len(names) != len(set(names)):
        raise ValueError("AgentSession tools must have unique names")
    return selected


def _seed_new_session(
    manager: SessionManager,
    model: Model,
    thinking_level: AgentThinkingLevel,
) -> None:
    entries = manager.active_entries()
    if not entries:
        manager.append_model_change(model.provider, model.id)
        manager.append_thinking_level(thinking_level)
