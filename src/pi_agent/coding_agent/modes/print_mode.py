from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TextIO

from pi_agent.agent import AgentMessage
from pi_agent.ai import AssistantMessage, ImageContent, TextContent
from pi_agent.harness.session import record_to_dict

from ..agent_session import AgentSession, AgentSessionEvent, PromptOptions
from .json_event import serialize_json_line, to_json_event


@dataclass(slots=True)
class PrintModeOptions:
    output_mode: Literal["text", "json"] = "text"
    initial_message: str | AgentMessage | None = None
    messages: Sequence[str | AgentMessage] = field(default_factory=tuple)
    initial_images: tuple[ImageContent, ...] = ()
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)


async def run_print_mode(session: AgentSession, options: PrintModeOptions) -> int:
    """Run one headless session using either final-text or JSONL output."""

    prompts: list[str | AgentMessage] = []
    if options.initial_message is not None:
        prompts.append(options.initial_message)
    prompts.extend(options.messages)
    if not prompts:
        options.stderr.write("No prompt was provided.\n")
        options.stderr.flush()
        return 1

    unsubscribe = None
    if options.output_mode == "json":
        options.stdout.write(serialize_json_line(record_to_dict(session.session_manager.header)))

        async def on_event(event: AgentSessionEvent) -> None:
            options.stdout.write(serialize_json_line(to_json_event(event)))
            options.stdout.flush()

        unsubscribe = session.subscribe(on_event)

    last_result: list[AgentMessage] = []
    try:
        for index, prompt in enumerate(prompts):
            selected = PromptOptions(
                images=options.initial_images if index == 0 else (),
            )
            last_result = await session.prompt(prompt, selected)
    except Exception as exc:
        options.stderr.write(f"{exc}\n")
        options.stderr.flush()
        return 1
    finally:
        if unsubscribe is not None:
            unsubscribe()

    final = next(
        (message for message in reversed(last_result) if isinstance(message, AssistantMessage)),
        None,
    )
    if final is None:
        options.stderr.write("The agent completed without an assistant response.\n")
        options.stderr.flush()
        return 1
    if final.stop_reason in {"error", "aborted"}:
        options.stderr.write((final.error_message or final.stop_reason) + "\n")
        options.stderr.flush()
        return 1
    if options.output_mode == "text":
        text = "".join(block.text for block in final.content if isinstance(block, TextContent))
        if text:
            options.stdout.write(text)
            if not text.endswith("\n"):
                options.stdout.write("\n")
            options.stdout.flush()
    return 0
