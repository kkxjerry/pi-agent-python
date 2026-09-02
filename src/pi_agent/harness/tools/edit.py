from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_agent.agent import AgentTool, AgentToolResult
from pi_agent.ai import CancellationToken

from ..mutation_queue import with_file_mutation_queue
from ..path_utils import normalize_tool_path, resolve_tool_path
from ..result import get_or_raise
from ..types import ExecutionEnv
from .common import require_string, text_result


@dataclass(frozen=True, slots=True)
class _Edit:
    old_text: str
    new_text: str


def create_edit_tool(env: ExecutionEnv) -> AgentTool:
    async def execute(
        _call_id: str,
        arguments: dict[str, Any],
        signal: CancellationToken | None,
        _update: Any,
    ) -> AgentToolResult:
        _raise_if_cancelled(signal)
        raw_path = require_string(arguments, "path")
        normalized_path = normalize_tool_path(raw_path)
        edits = _parse_edits(arguments)
        resolved = await resolve_tool_path(env, normalized_path, signal)

        async def mutate() -> AgentToolResult:
            data = get_or_raise(await env.read_binary_file(resolved, signal))
            if b"\x00" in data[:8_192]:
                raise ValueError(f"Binary files are not supported by edit: {raw_path}")
            try:
                original = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"File is not valid UTF-8: {raw_path}") from exc

            newline = _detect_newline(original)
            updated = _normalize_newlines(original)
            replacements: list[dict[str, int]] = []
            for index, edit in enumerate(edits, start=1):
                old_text = _normalize_newlines(edit.old_text)
                new_text = _normalize_newlines(edit.new_text)
                if not old_text:
                    raise ValueError(f"Edit {index} oldText must not be empty")
                occurrences = updated.count(old_text)
                if occurrences == 0:
                    raise ValueError(f"Edit {index} oldText was not found in {raw_path}")
                if occurrences > 1:
                    raise ValueError(
                        f"Edit {index} oldText matched {occurrences} locations in {raw_path}; "
                        "provide more context"
                    )
                position = updated.index(old_text)
                updated = updated[:position] + new_text + updated[position + len(old_text) :]
                replacements.append(
                    {
                        "index": index,
                        "position": position,
                        "removedChars": len(old_text),
                        "addedChars": len(new_text),
                    }
                )

            restored = updated.replace("\n", newline) if newline != "\n" else updated
            if restored == original:
                return text_result(
                    f"No changes needed in {raw_path}",
                    details={
                        "path": resolved,
                        "changed": False,
                        "replacements": len(replacements),
                        "edits": replacements,
                    },
                )
            encoded = restored.encode("utf-8")
            get_or_raise(await env.write_file(resolved, encoded, signal))
            return text_result(
                f"Applied {len(edits)} edit(s) to {raw_path}",
                details={
                    "path": resolved,
                    "changed": True,
                    "beforeBytes": len(data),
                    "afterBytes": len(encoded),
                    "replacements": len(replacements),
                    "edits": replacements,
                },
            )

        return await with_file_mutation_queue(env, resolved, mutate)

    return AgentTool(
        name="edit",
        label="Edit",
        description=(
            "Apply one or more exact, unique text replacements atomically. "
            "Use oldText/newText for one edit or edits for a sequence."
        ),
        parameters={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "oldText": {"type": "string"},
                "newText": {"type": "string"},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["oldText", "newText"],
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


def _parse_edits(arguments: dict[str, Any]) -> list[_Edit]:
    raw_edits = arguments.get("edits")
    has_single = "oldText" in arguments or "newText" in arguments
    if raw_edits is not None and has_single:
        raise ValueError("Use either oldText/newText or edits, not both")
    if raw_edits is not None:
        if not isinstance(raw_edits, list) or not raw_edits:
            raise ValueError("edits must be a non-empty array")
        parsed: list[_Edit] = []
        for index, item in enumerate(raw_edits, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Edit {index} must be an object")
            old_text = item.get("oldText")
            new_text = item.get("newText")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise ValueError(f"Edit {index} oldText and newText must be strings")
            parsed.append(_Edit(old_text=old_text, new_text=new_text))
        return parsed
    old_text = arguments.get("oldText")
    new_text = arguments.get("newText")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise ValueError("oldText and newText are required for a single edit")
    return [_Edit(old_text=old_text, new_text=new_text)]


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _detect_newline(value: str) -> str:
    if "\r\n" in value:
        return "\r\n"
    if "\r" in value:
        return "\r"
    return "\n"


def _raise_if_cancelled(signal: CancellationToken | None) -> None:
    if signal is not None:
        signal.raise_if_cancelled()
