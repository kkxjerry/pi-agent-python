from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pi_agent.agent import (
    AgentEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from pi_agent.coding_agent import AgentSession, AgentSessionEvent, PromptOptions
from pi_agent.coding_agent.attachments import AttachmentError, AttachmentResolver

from .commands import CommandResult, CommandRouter, default_commands
from .completion import (
    CommandCompleter,
    CompletionRequest,
    CompositeCompleter,
    FileCompleter,
    ModelCompleter,
    apply_completion,
)
from .editor import TextEditor
from .screen import AnsiRenderer
from .terminal import StdioTerminal, Terminal
from .types import EndOfInputEvent, Frame, KeyEvent, ResizeEvent, TerminalEvent, pad_cells
from .view import CompletionView, StatusView, TranscriptView


@dataclass(slots=True)
class TuiOptions:
    alternate_screen: bool = True
    title: str = "pi Python"
    prompt: str = "> "
    initial_prompt: str | None = None
    max_completion_height: int = 8
    show_thinking: bool = True
    cwd: Path | None = None
    attachment_resolver: AttachmentResolver | None = None


@dataclass(slots=True)
class InteractiveTui:
    session: AgentSession
    terminal: Terminal = field(default_factory=StdioTerminal)
    options: TuiOptions = field(default_factory=TuiOptions)
    command_router: CommandRouter = field(default_factory=lambda: CommandRouter(default_commands()))
    editor: TextEditor = field(default_factory=TextEditor)
    transcript: TranscriptView = field(default_factory=TranscriptView)
    completions: CompletionView = field(default_factory=CompletionView)
    renderer: AnsiRenderer = field(default_factory=AnsiRenderer)
    _running: bool = field(default=False, init=False)
    _exit_requested: bool = field(default=False, init=False)
    _run_task: asyncio.Task[Any] | None = field(default=None, init=False)
    _unsubscribe: Any = field(default=None, init=False)
    _dirty: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _input_closed: bool = field(default=False, init=False)
    _completion_engine: CompositeCompleter = field(init=False)
    _attachment_resolver: AttachmentResolver = field(init=False)

    def __post_init__(self) -> None:
        cwd = self.options.cwd or Path(self.session.session_manager.header.cwd)
        commands = [
            (command.name, command.description) for command in self.command_router.command_list()
        ]
        extension_host = getattr(self.session, "extension_host", None)
        extension_commands = getattr(extension_host, "commands", {})
        commands.extend(
            (command.name, command.description) for command in extension_commands.values()
        )
        self._completion_engine = CompositeCompleter(
            (
                CommandCompleter(commands),
                ModelCompleter(self.session.get_available_models()),
                FileCompleter(),
            )
        )
        self._attachment_resolver = self.options.attachment_resolver or AttachmentResolver(cwd)
        self.transcript.show_thinking = self.options.show_thinking
        self.transcript.set_messages(self.session.get_messages())

    async def run(self) -> int:
        if not self.terminal.is_tty:
            raise RuntimeError("interactive mode requires a TTY")
        if self._running:
            raise RuntimeError("TUI is already running")
        self._running = True
        self._unsubscribe = self.session.subscribe(self._on_session_event)
        await self.terminal.write(
            self.renderer.enter(alternate_screen=self.options.alternate_screen)
        )
        try:
            with self.terminal.raw_mode():
                await self._render()
                if self.options.initial_prompt:
                    self.editor.set_text(self.options.initial_prompt)
                    await self._submit_editor()
                await self._event_loop()
        finally:
            if self._run_task is not None and not self._run_task.done():
                self.session.abort("interactive mode closed")
                await asyncio.gather(self._run_task, return_exceptions=True)
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
            await self.terminal.write(
                self.renderer.exit(alternate_screen=self.options.alternate_screen)
            )
            self._running = False
        return 0

    async def _event_loop(self) -> None:
        input_task = asyncio.create_task(self.terminal.read_event())
        try:
            while not self._exit_requested:
                waiters: set[asyncio.Task[Any]] = {input_task}
                if self._run_task is not None:
                    waiters.add(self._run_task)
                dirty_task = asyncio.create_task(self._dirty.wait())
                waiters.add(dirty_task)
                done, pending = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if dirty_task in done:
                    self._dirty.clear()
                    await self._render()
                else:
                    dirty_task.cancel()
                if input_task in done:
                    event = input_task.result()
                    if isinstance(event, EndOfInputEvent):
                        self._input_closed = True
                        if self._run_task is None or self._run_task.done():
                            self._exit_requested = True
                    else:
                        await self._handle_event(event)
                    if not self._exit_requested:
                        input_task = asyncio.create_task(self.terminal.read_event())
                if self._run_task is not None and self._run_task in done:
                    try:
                        self._run_task.result()
                    except Exception as exc:
                        self.transcript.add_notice(f"Error: {exc}")
                    self._run_task = None
                    self.transcript.set_messages(self.session.get_messages())
                    if self._input_closed:
                        self._exit_requested = True
                    await self._render()
                for task in pending:
                    if task is not input_task and task is not self._run_task:
                        task.cancel()
        finally:
            input_task.cancel()
            await asyncio.gather(input_task, return_exceptions=True)

    async def _handle_event(self, event: TerminalEvent) -> None:
        if isinstance(event, ResizeEvent):
            self.renderer.invalidate()
            await self._render()
            return
        if isinstance(event, KeyEvent) and event.key == "tab":
            self._complete(cycle=bool(self.completions.items))
            await self._render()
            return
        if self.completions.items and isinstance(event, KeyEvent):
            if event.key == "up":
                self.completions.selected = (self.completions.selected - 1) % len(
                    self.completions.items
                )
                await self._render()
                return
            if event.key == "down":
                self.completions.selected = (self.completions.selected + 1) % len(
                    self.completions.items
                )
                await self._render()
                return
            if event.key == "enter":
                item = self.completions.items[self.completions.selected]
                text, cursor = apply_completion(self.editor.text, self.editor.cursor, item)
                self.editor.set_text(text, cursor=cursor)
                self.completions.items.clear()
                await self._render()
                return
            if event.key == "escape":
                self.completions.items.clear()
                await self._render()
                return

        if isinstance(event, KeyEvent) and event.key == "ctrl_c":
            if self.session.is_streaming:
                self.session.abort("Interrupted by user")
                self.transcript.add_notice("Abort requested")
            elif self.editor.text:
                self.editor.set_text("")
            else:
                self._exit_requested = True
            await self._render()
            return
        action = self.editor.handle(event)
        if action == "submit":
            await self._submit_editor()
        elif action == "exit":
            self._exit_requested = True
        elif action == "clear_screen":
            self.renderer.invalidate()
        elif action == "cancel":
            self.editor.set_text("")
            self.completions.items.clear()
        if action != "none":
            await self._render()

    async def _submit_editor(self) -> None:
        value = self.editor.submit()
        self.completions.items.clear()
        if not value:
            return
        if value.startswith("/"):
            result = await self.command_router.execute(self.session, value)
            await self._apply_command_result(result)
            return
        try:
            resolved = self._attachment_resolver.resolve_prompt(value)
        except AttachmentError as exc:
            self.transcript.add_notice(f"Attachment error: {exc}")
            return
        if resolved.images and "image" not in self.session.agent.model.input:
            self.transcript.add_notice(
                f"Model {self.session.agent.model.provider}/{self.session.agent.model.id} "
                "does not accept image input"
            )
            return
        prompt = resolved.text or "Use the attached image(s) to answer the request."
        options = PromptOptions(
            images=resolved.images,
            streaming_behavior="followUp" if self.session.is_streaming else None,
        )
        if self.session.is_streaming:
            await self.session.prompt(prompt, options)
            self.transcript.add_notice("Follow-up queued")
            return
        self._run_task = asyncio.create_task(self.session.prompt(prompt, options))

    async def _apply_command_result(self, result: CommandResult) -> None:
        if result.status == "exit":
            self._exit_requested = True
            return
        if isinstance(result.data, dict) and result.data.get("clear"):
            self.transcript.clear_notices()
            self.transcript.set_messages([])
            return
        if result.message:
            prefix = "Error: " if result.status == "error" else ""
            self.transcript.add_notice(prefix + result.message)

    def _complete(self, *, cycle: bool) -> None:
        if cycle and self.completions.items:
            self.completions.selected = (self.completions.selected + 1) % len(
                self.completions.items
            )
            return
        request = CompletionRequest(
            self.editor.text,
            self.editor.cursor,
            self._attachment_resolver.cwd,
        )
        self.completions.items = list(self._completion_engine.complete(request))
        self.completions.selected = 0

    async def _on_session_event(self, event: AgentSessionEvent) -> None:
        if isinstance(event, ToolExecutionStartEvent):
            self.transcript.add_notice(f"Running {event.tool_name}…")
        elif isinstance(event, ToolExecutionEndEvent):
            state = "failed" if event.is_error else "finished"
            self.transcript.add_notice(f"{event.tool_name} {state}")
        elif isinstance(event, AgentEndEvent):
            self.transcript.set_messages(self.session.get_messages())
        elif isinstance(event, MessageUpdateEvent):
            # Partial content lives in Agent.state until message_end persists it.
            messages = self.session.get_messages()
            if event.message is not None:
                messages.append(event.message)
            self.transcript.set_messages(messages)
        self._dirty.set()

    async def _render(self) -> None:
        width, height = self.terminal.size()
        width = max(20, width)
        height = max(6, height)
        editor = self.editor.render(width, prompt=self.options.prompt)
        completion_height = min(
            self.options.max_completion_height,
            len(self.completions.items),
            max(0, height // 3),
        )
        status_height = 1
        transcript_height = max(
            1,
            height - len(editor.lines) - completion_height - status_height,
        )
        transcript_lines = self.transcript.render(width, transcript_height)
        completion_lines = self.completions.render(width, completion_height)
        model = self.session.agent.model
        status = StatusView(
            f"{model.provider}/{model.id}",
            (
                "running · Ctrl-C abort"
                if self.session.is_streaming
                else f"thinking {self.session.agent.thinking_level}"
            ),
        ).render(width)
        lines = [*transcript_lines, *completion_lines, status, *editor.lines]
        lines = [pad_cells(line, width) for line in lines[-height:]]
        cursor_y = len(lines) - len(editor.lines) + editor.cursor_y
        frame = Frame(
            width,
            height,
            tuple(lines),
            cursor_x=editor.cursor_x,
            cursor_y=max(0, min(height - 1, cursor_y)),
            cursor_visible=True,
            title=self.options.title,
        )
        await self.terminal.write(self.renderer.render(frame))
        self.terminal.flush()


async def run_interactive_mode(
    session: AgentSession,
    *,
    terminal: Terminal | None = None,
    options: TuiOptions | None = None,
) -> int:
    return await InteractiveTui(
        session,
        terminal=terminal or StdioTerminal(),
        options=options or TuiOptions(),
    ).run()


InteractiveApp = InteractiveTui
InteractiveAppOptions = TuiOptions


__all__ = [
    "InteractiveApp",
    "InteractiveAppOptions",
    "InteractiveTui",
    "TuiOptions",
    "run_interactive_mode",
]
