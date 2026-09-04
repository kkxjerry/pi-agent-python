from __future__ import annotations

from pi_agent.coding_agent.agent_session import AgentSession

from .commands import CommandDefinition, CommandRouter
from .widgets import SelectOption


def model_options(session: AgentSession) -> list[SelectOption]:
    current = session.agent.model
    result: list[SelectOption] = []
    for model in session.get_available_models():
        description = f"{model.context_window:,} ctx"
        if model.reasoning:
            description += " | reasoning"
        if "image" in model.input:
            description += " | image"
        if model.provider == current.provider and model.id == current.id:
            description += " | current"
        result.append(
            SelectOption(
                id=f"{model.provider}/{model.id}",
                label=f"{model.provider}/{model.id}",
                description=description,
                data=model,
            )
        )
    return result


def thinking_options(session: AgentSession) -> list[SelectOption]:
    current = session.agent.thinking_level
    return [
        SelectOption(
            id=level,
            label=level,
            description="current" if level == current else "",
            data=level,
        )
        for level in session.get_available_thinking_levels()
    ]


def session_options(session: AgentSession) -> list[SelectOption]:
    repository = session.session_repository
    if repository is None:
        return []
    return [
        SelectOption(
            id=info.id,
            label=info.name or info.id,
            description=f"{info.entry_count} entries | {info.leaf_count} branches",
            disabled=bool(info.warnings),
            data=info,
        )
        for info in repository.list()
    ]


def command_options(router: CommandRouter) -> list[SelectOption]:
    return [
        SelectOption(
            id=definition.name,
            label=f"/{definition.name}",
            description=definition.description,
            data=definition,
        )
        for definition in router.definitions
    ]


def command_text(definition: CommandDefinition) -> str:
    return f"/{definition.name} "


__all__ = [
    "command_options",
    "command_text",
    "model_options",
    "session_options",
    "thinking_options",
]
