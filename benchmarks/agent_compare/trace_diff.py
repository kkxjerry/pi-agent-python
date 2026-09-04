from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ToolStep:
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    is_error: bool | None = None
    result_preview: str = ""


@dataclass(slots=True)
class TurnStep:
    index: int
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    text: str = ""
    requested_tools: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    executed_tools: list[ToolStep] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedTrace:
    path: Path
    event_counts: dict[str, int]
    turns: list[TurnStep]
    final_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render two pi JSONL traces as aligned Agent turns"
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--left-name", default="official TypeScript")
    parser.add_argument("--right-name", default="Python")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preview", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    left = normalize_trace(args.left, preview=args.preview)
    right = normalize_trace(args.right, preview=args.preview)
    report = render_trace_diff(left, right, args.left_name, args.right_name)
    if args.output is None:
        print(report)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output.resolve())
    return 0


def normalize_trace(path: Path, *, preview: int = 500) -> NormalizedTrace:
    events = load_jsonl(path)
    counts = Counter(str(event.get("type", "unknown")) for event in events)
    turns: list[TurnStep] = []
    current: TurnStep | None = None
    active_tools: dict[str, ToolStep] = {}
    final_text = ""

    for event in events:
        event_type = event.get("type")
        if event_type == "turn_start":
            current = TurnStep(len(turns) + 1)
            turns.append(current)
            continue
        if current is None:
            continue
        if event_type == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            current.stop_reason = _optional_string(
                message.get("stopReason", message.get("stop_reason"))
            )
            usage = message.get("usage")
            if isinstance(usage, dict):
                current.input_tokens = _integer(usage.get("input"))
                current.output_tokens = _integer(usage.get("output"))
            current.text = _clip(_message_text(message), preview)
            current.requested_tools = _message_tool_calls(message)
            if current.stop_reason not in {"toolUse", "tool_use"}:
                final_text = _message_text(message)
            continue
        if event_type == "tool_execution_start":
            call_id = str(event.get("toolCallId", event.get("tool_call_id", "")))
            tool = ToolStep(
                call_id=call_id,
                name=str(event.get("toolName", event.get("tool_name", ""))),
                arguments=_dict(event.get("args")),
            )
            current.executed_tools.append(tool)
            active_tools[call_id] = tool
            continue
        if event_type == "tool_execution_end":
            call_id = str(event.get("toolCallId", event.get("tool_call_id", "")))
            existing = active_tools.get(call_id)
            if existing is None:
                existing = ToolStep(
                    call_id=call_id,
                    name=str(event.get("toolName", event.get("tool_name", ""))),
                )
                current.executed_tools.append(existing)
            existing.is_error = bool(event.get("isError", event.get("is_error", False)))
            existing.result_preview = _clip(_tool_result_text(event.get("result")), preview)
            active_tools.pop(call_id, None)

    return NormalizedTrace(path, dict(sorted(counts.items())), turns, final_text)


def render_trace_diff(
    left: NormalizedTrace,
    right: NormalizedTrace,
    left_name: str,
    right_name: str,
) -> str:
    lines = [
        "# Normalized Agent trace comparison",
        "",
        f"- Left: `{left_name}` — `{left.path}`",
        f"- Right: `{right_name}` — `{right.path}`",
        "",
        "## Turn alignment",
        "",
        "| Turn | System | Stop | Input | Output | Requested Tool batch | "
        "Executed Tools | Assistant text preview |",
        "|---:|---|---|---:|---:|---|---|---|",
    ]
    maximum = max(len(left.turns), len(right.turns))
    for index in range(maximum):
        for name, trace in ((left_name, left), (right_name, right)):
            if index >= len(trace.turns):
                lines.append(f"| {index + 1} | {name} | — | — | — | — | — | — |")
                continue
            turn = trace.turns[index]
            requested = ", ".join(tool for tool, _arguments in turn.requested_tools) or "—"
            executed = (
                ", ".join(
                    f"{tool.name}{' ✗' if tool.is_error else ''}" for tool in turn.executed_tools
                )
                or "—"
            )
            text = _markdown_cell(turn.text or "—")
            lines.append(
                f"| {turn.index} | {name} | {turn.stop_reason or '—'} | "
                f"{turn.input_tokens} | {turn.output_tokens} | {requested} | "
                f"{executed} | {text} |"
            )

    lines.extend(["", "## Tool details", ""])
    for name, trace in ((left_name, left), (right_name, right)):
        lines.extend([f"### {name}", ""])
        for turn in trace.turns:
            if not turn.executed_tools:
                continue
            lines.append(f"#### Turn {turn.index}")
            lines.append("")
            for tool in turn.executed_tools:
                state = "error" if tool.is_error else "ok"
                lines.append(f"- `{tool.name}` (`{state}`)")
                lines.append(
                    "  - arguments: `"
                    + _markdown_cell(json.dumps(tool.arguments, ensure_ascii=False, sort_keys=True))
                    + "`"
                )
                if tool.result_preview:
                    lines.append(f"  - result: {_markdown_cell(tool.result_preview)}")
            lines.append("")

    lines.extend(
        [
            "## Event count differences",
            "",
            "| Event | Left | Right | Delta (right-left) |",
            "|---|---:|---:|---:|",
        ]
    )
    for event_type in sorted(set(left.event_counts) | set(right.event_counts)):
        left_count = left.event_counts.get(event_type, 0)
        right_count = right.event_counts.get(event_type, 0)
        lines.append(
            f"| `{event_type}` | {left_count} | {right_count} | {right_count - left_count:+d} |"
        )
    lines.extend(
        [
            "",
            "## Final answers",
            "",
            f"### {left_name}",
            "",
            left.final_text or "_No final assistant text._",
            "",
            f"### {right_name}",
            "",
            right.final_text or "_No final assistant text._",
            "",
        ]
    )
    return "\n".join(lines)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: JSONL record must be an object")
        records.append(value)
    return records


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _message_tool_calls(message: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"toolCall", "tool_call", "toolcall"}:
            continue
        result.append((str(block.get("name", "")), _dict(block.get("arguments"))))
    return result


def _tool_result_text(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _integer(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _clip(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: max(0, limit - 1)] + "…"


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
