from __future__ import annotations

import inspect
import shlex
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from pi_agent.coding_agent import AgentSession

CommandStatus = Literal["ok", "error", "exit"]
CommandHandler: TypeAlias = Callable[[AgentSession, tuple[str, ...]], Any | Awaitable[Any]]


@dataclass(slots=True, frozen=True)
class CommandResult:
    status: CommandStatus
    message: str = ""
    data: Any = None


@dataclass(slots=True, frozen=True)
class Command:
    name: str
    handler: CommandHandler
    description: str = ""
    usage: str = ""
    aliases: tuple[str, ...] = ()


CommandDefinition = Command


class CommandRouter:
    def __init__(self, commands: Iterable[Command] = ()) -> None:
        self._commands: dict[str, Command] = {}
        for command in commands:
            self.register(command)

    @property
    def definitions(self) -> tuple[Command, ...]:
        return self.command_list()

    def register(self, command: Command) -> None:
        names = (command.name, *command.aliases)
        for name in names:
            if not name or name in self._commands:
                raise ValueError(f"duplicate or invalid command name: {name!r}")
        for name in names:
            self._commands[name] = command

    def command_list(self) -> tuple[Command, ...]:
        unique = {command.name: command for command in self._commands.values()}
        return tuple(unique[name] for name in sorted(unique))

    async def execute(self, session: AgentSession, text: str) -> CommandResult:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return CommandResult("error", f"invalid command syntax: {exc}")
        if not parts or not parts[0].startswith("/"):
            return CommandResult("error", "commands must start with '/'")
        name = parts[0][1:]
        command = self._commands.get(name)
        if command is None:
            extension_host = getattr(session, "extension_host", None)
            extension_commands = getattr(extension_host, "commands", {})
            if extension_host is not None and name in extension_commands:
                try:
                    value = await extension_host.invoke_command(name, session, *parts[1:])
                except Exception as exc:
                    return CommandResult("error", str(exc))
                return _normalize_result(value)
            return CommandResult("error", f"unknown command: /{name}")
        try:
            value = command.handler(session, tuple(parts[1:]))
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            return CommandResult("error", str(exc))
        return _normalize_result(value)


def default_commands() -> tuple[Command, ...]:
    return (
        Command("help", _help, "Show available commands", "/help"),
        Command("quit", _quit, "Exit the interactive mode", "/quit", aliases=("exit",)),
        Command("model", _model, "Show or select a model", "/model [provider/model]"),
        Command(
            "thinking",
            _thinking,
            "Show or set reasoning level",
            "/thinking [off|minimal|low|medium|high|xhigh|max]",
        ),
        Command("new", _new_session, "Start a new session", "/new [name]"),
        Command("session", _session, "Show current session information", "/session"),
        Command("compact", _compact, "Compact the active context", "/compact"),
        Command("reload", _reload, "Reload resources and extensions", "/reload"),
        Command("clear", _clear, "Clear the visible transcript", "/clear"),
        Command("packages", _packages, "List installed packages", "/packages"),
    )


async def _help(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    del session
    lines = [
        "/help              show this help",
        "/model [id]        show or select provider/model",
        "/thinking [level]  show or select reasoning level",
        "/new [name]        create a new session",
        "/session           show current session",
        "/compact           compact active context",
        "/reload            reload resources/extensions",
        "/packages          list installed packages",
        "/clear             clear visible output",
        "/quit              exit",
    ]
    return CommandResult("ok", "\n".join(lines))


async def _quit(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    del session
    return CommandResult("exit", "")


async def _model(session: AgentSession, args: tuple[str, ...]) -> CommandResult:
    if not args:
        model = session.agent.model
        return CommandResult("ok", f"{model.provider}/{model.id}")
    if len(args) != 1 or "/" not in args[0]:
        raise ValueError("usage: /model provider/model")
    provider, model_id = args[0].split("/", 1)
    selected_model = next(
        (
            candidate
            for candidate in session.get_available_models()
            if candidate.provider == provider and candidate.id == model_id
        ),
        None,
    )
    if selected_model is None:
        raise ValueError(f"model is unavailable: {provider}/{model_id}")
    await session.set_model(selected_model)
    return CommandResult(
        "ok",
        f"model: {selected_model.provider}/{selected_model.id}",
    )


async def _thinking(session: AgentSession, args: tuple[str, ...]) -> CommandResult:
    if not args:
        return CommandResult("ok", f"thinking: {session.agent.thinking_level}")
    if len(args) != 1:
        raise ValueError("usage: /thinking level")
    level = args[0]
    if level not in session.get_available_thinking_levels():
        raise ValueError(
            f"thinking level {level!r} is unavailable; allowed: "
            + ", ".join(session.get_available_thinking_levels())
        )
    await session.set_thinking_level(level)
    return CommandResult("ok", f"thinking: {level}")


async def _new_session(session: AgentSession, args: tuple[str, ...]) -> CommandResult:
    name = " ".join(args) or None
    manager = await session.new_session(name=name)
    return CommandResult("ok", f"new session: {manager.header.id}")


async def _session(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    state = session.get_state()["session"]
    return CommandResult(
        "ok",
        f"session {state['id']} · entries {state['entryCount']} · {state['file'] or 'memory'}",
        state,
    )


async def _compact(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    result = await session.compact("manual")
    if result is None:
        return CommandResult("ok", "compaction is disabled")
    return CommandResult(
        "ok",
        f"compacted {result.tokens_before} → {result.tokens_after} tokens",
        dict(result),
    )


async def _reload(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    resources = await session.reload_resources()
    runtime = getattr(session, "coding_runtime", None)
    if runtime is not None:
        await runtime.reload_extensions()
    return CommandResult("ok", f"reloaded generation {resources.generation}")


async def _clear(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    del session
    return CommandResult("ok", "", {"clear": True})


async def _packages(session: AgentSession, _args: tuple[str, ...]) -> CommandResult:
    runtime = getattr(session, "coding_runtime", None)
    package_manager = getattr(runtime, "package_manager", None)
    if package_manager is None:
        return CommandResult("ok", "package runtime is not attached to this session")
    packages = await package_manager.list()
    if not packages:
        return CommandResult("ok", "no installed packages")
    return CommandResult(
        "ok",
        "\n".join(f"{item.name} {item.version}" for item in packages),
    )


def _normalize_result(value: Any) -> CommandResult:
    if isinstance(value, CommandResult):
        return value
    if value is None:
        return CommandResult("ok")
    return CommandResult("ok", str(value), value)
