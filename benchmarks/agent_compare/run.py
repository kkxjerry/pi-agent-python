from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
UPSTREAM_CLI = (
    ROOT
    / "tools"
    / "upstream-fixtures"
    / "node_modules"
    / "@earendil-works"
    / "pi-coding-agent"
    / "dist"
    / "bundle"
    / "cli.js"
)
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_CONTEXT_WINDOW = 131_072
DEFAULT_MAX_TOKENS = 8_192

SystemName = Literal["upstream-ts", "python"]


@dataclass(slots=True, frozen=True)
class Validator:
    kind: str
    expected: str | None = None
    required_substrings: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    additional_commands: tuple[tuple[str, ...], ...] = ()
    require_clean_workspace: bool = False
    protected_files: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ValidationResult:
    overall_ok: bool
    primary_ok: bool
    regression_ok: bool
    semantic_ok: bool
    protected_files_ok: bool
    message: str


@dataclass(slots=True, frozen=True)
class Task:
    id: str
    fixture: Path
    prompt: str
    validator: Validator


@dataclass(slots=True)
class ProcessCapture:
    command: list[str]
    returncode: int
    duration_ms: float
    stdout_lines: list[str]
    stderr: str
    timed_events: list[dict[str, Any]]
    invalid_stdout_lines: list[str]
    timed_out: bool = False


@dataclass(slots=True)
class RunMetrics:
    system: SystemName
    task: str
    repetition: int
    process_ok: bool
    validator_ok: bool
    success: bool
    returncode: int
    timed_out: bool
    duration_ms: float
    time_to_first_event_ms: float | None
    time_to_first_text_delta_ms: float | None
    time_to_first_tool_start_ms: float | None
    time_to_last_tool_end_ms: float | None
    event_count: int
    trace_bytes: int
    event_counts: dict[str, int]
    event_sequence: list[str]
    model_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tool_calls: int
    tool_errors: int
    tool_sequence: list[str]
    tool_batches: list[list[str]]
    max_tool_batch: int
    turn_count: int
    final_stop_reason: str | None
    final_text: str
    session_schema_version: int | None
    entry_appended_events: int
    changed_files: list[str]
    validation_message: str
    stderr: str
    invalid_stdout_lines: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the pinned TypeScript pi coding agent with the Python port "
            "using the same model, tasks, and isolated workspaces."
        )
    )
    parser.add_argument("--tasks", type=Path, default=HERE / "tasks.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument(
        "--system",
        action="append",
        choices=("upstream-ts", "python"),
        default=[],
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--provider", default="dashscope")
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--context-window", type=int, default=DEFAULT_CONTEXT_WINDOW)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--core-parity", action="store_true")
    parser.add_argument("--keep-workspaces", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if args.repetitions <= 0:
        raise SystemExit("--repetitions must be positive")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.context_window <= 0 or args.max_tokens <= 0:
        raise SystemExit("--context-window and --max-tokens must be positive")
    if not os.environ.get(args.api_key_env):
        raise SystemExit(
            f"{args.api_key_env} is not present in this process. "
            "Run this command from a shell that exports it."
        )
    if not UPSTREAM_CLI.is_file():
        raise SystemExit(
            "The pinned TypeScript coding-agent package is not installed. "
            "Run `npm install` in tools/upstream-fixtures."
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or HERE / "results" / timestamp).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = load_tasks(args.tasks)
    if args.task:
        selected_ids = set(args.task)
        tasks = [task for task in tasks if task.id in selected_ids]
        missing = selected_ids - {task.id for task in tasks}
        if missing:
            raise SystemExit("unknown tasks: " + ", ".join(sorted(missing)))
    systems: list[SystemName] = args.system or ["upstream-ts", "python"]

    metadata = {
        "schemaVersion": 1,
        "createdAt": datetime.now(UTC).isoformat(),
        "upstream": {
            "package": "@earendil-works/pi-coding-agent",
            "version": "0.84.4",
            "repository": "earendil-works/pi",
            "tag": "v0.84.4",
            "commit": "b79e4cc",
        },
        "python": {"command": [sys.executable, "-m", "pi_agent"]},
        "model": {
            "provider": args.provider,
            "id": args.model,
            "baseUrl": args.base_url,
            "apiKeyEnvironment": args.api_key_env,
            "contextWindow": args.context_window,
            "maxTokens": args.max_tokens,
        },
        "repetitions": args.repetitions,
        "tasks": [task.id for task in tasks],
    }
    write_json(output / "metadata.json", metadata)

    core_parity: dict[str, Any] | None = None
    if args.core_parity:
        core_parity = await run_core_parity(output, timeout=args.timeout)
        write_json(output / "core-parity.json", core_parity)

    runs: list[RunMetrics] = []
    for task in tasks:
        for repetition in range(1, args.repetitions + 1):
            for system in systems:
                print(f"RUN {system} {task.id} repetition={repetition}", flush=True)
                result = await run_one(
                    system,
                    task,
                    repetition,
                    output=output,
                    provider=args.provider,
                    model=args.model,
                    base_url=args.base_url,
                    api_key_env=args.api_key_env,
                    context_window=args.context_window,
                    max_tokens=args.max_tokens,
                    timeout=args.timeout,
                    keep_workspace=args.keep_workspaces,
                )
                runs.append(result)
                print(
                    f"  success={result.success} duration={result.duration_ms:.0f}ms "
                    f"modelCalls={result.model_calls} tools={result.tool_calls} "
                    f"tokens={result.input_tokens}+{result.output_tokens}",
                    flush=True,
                )

    summary = {
        "metadata": metadata,
        "coreParity": core_parity,
        "runs": [run.to_dict() for run in runs],
        "aggregates": aggregate_runs(runs),
    }
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(render_report(summary), encoding="utf-8")
    print(f"REPORT {output / 'report.md'}")
    print(f"SUMMARY {output / 'summary.json'}")
    return 0 if all(run.success for run in runs) else 2


def load_tasks(path: Path) -> list[Task]:
    value = json.loads(path.read_text(encoding="utf-8"))
    raw_tasks = value.get("tasks") if isinstance(value, dict) else None
    if not isinstance(raw_tasks, list):
        raise ValueError("task file requires a tasks array")
    tasks: list[Task] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ValueError("each task must be an object")
        validator_value = raw.get("validator", {})
        if not isinstance(validator_value, dict):
            raise ValueError("task validator must be an object")
        fixture = (path.parent / str(raw["fixture"])).resolve()
        if not fixture.is_dir():
            raise ValueError(f"fixture directory does not exist: {fixture}")
        tasks.append(
            Task(
                id=str(raw["id"]),
                fixture=fixture,
                prompt=str(raw["prompt"]),
                validator=Validator(
                    kind=str(validator_value.get("kind", "none")),
                    expected=(
                        str(validator_value["expected"]) if "expected" in validator_value else None
                    ),
                    required_substrings=tuple(
                        str(item) for item in validator_value.get("requiredSubstrings", ())
                    ),
                    command=tuple(str(item) for item in validator_value.get("command", ())),
                    additional_commands=tuple(
                        tuple(str(part) for part in command)
                        for command in validator_value.get("additionalCommands", ())
                        if isinstance(command, list)
                    ),
                    require_clean_workspace=bool(
                        validator_value.get("require_clean_workspace", False)
                    ),
                    protected_files=tuple(
                        str(item) for item in validator_value.get("protected_files", ())
                    ),
                ),
            )
        )
    return tasks


async def run_one(
    system: SystemName,
    task: Task,
    repetition: int,
    *,
    output: Path,
    provider: str,
    model: str,
    base_url: str,
    api_key_env: str,
    context_window: int,
    max_tokens: int,
    timeout: float,
    keep_workspace: bool,
) -> RunMetrics:
    run_id = f"{task.id}-r{repetition}"
    with tempfile.TemporaryDirectory(prefix=f"pi-agent-compare-{system}-{run_id}-") as temporary:
        temporary_root = Path(temporary)
        workspace = temporary_root / "workspace"
        shutil.copytree(task.fixture, workspace)
        before = snapshot_tree(workspace)

        isolated_home = temporary_root / "home"
        isolated_home.mkdir()
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(isolated_home),
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
                "PI_SKIP_VERSION_CHECK": "1",
                "NO_COLOR": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        agent_dir: Path | None = None
        if system == "upstream-ts":
            agent_dir = temporary_root / "agent-dir"
            agent_dir.mkdir()
            write_upstream_model_config(
                agent_dir / "models.json",
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                context_window=context_window,
                max_tokens=max_tokens,
            )
            env["PI_CODING_AGENT_DIR"] = str(agent_dir)
            command = [
                "node",
                str(UPSTREAM_CLI),
                "--mode",
                "json",
                "--provider",
                provider,
                "--model",
                model,
                "--no-session",
                "--tools",
                "read,bash,edit,write",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-themes",
                "--no-context-files",
                "--no-approve",
                "-p",
                task.prompt,
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "pi_agent",
                "--mode",
                "json",
                "--provider",
                provider,
                "--model",
                model,
                "--base-url",
                base_url,
                "--context-window",
                str(context_window),
                "--max-tokens",
                str(max_tokens),
                "--no-session",
                "--no-extensions",
                "--approval",
                "off",
                "-p",
                task.prompt,
            ]

        capture = await capture_process(command, cwd=workspace, env=env, timeout=timeout)
        after = snapshot_tree(workspace)
        changed_files = tree_diff(before, after)
        final_text = extract_final_text(capture.timed_events)
        validator_ok, validation_message = await validate_task(
            task,
            workspace,
            before,
            after,
            final_text,
        )
        metrics = analyze_capture(
            system,
            task.id,
            repetition,
            capture,
            validator_ok,
            validation_message,
            changed_files,
        )

        if keep_workspace:
            retained = output / "workspaces" / system / run_id
            retained.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(workspace, retained)
            if agent_dir is not None:
                retained_agent_dir = output / "agent-dirs" / run_id
                retained_agent_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(agent_dir, retained_agent_dir)

    trace_dir = output / "traces" / system
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{run_id}.jsonl").write_text("".join(capture.stdout_lines), encoding="utf-8")
    (trace_dir / f"{run_id}.timed.jsonl").write_text(
        "".join(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            for event in capture.timed_events
        ),
        encoding="utf-8",
    )
    (trace_dir / f"{run_id}.stderr.txt").write_text(capture.stderr, encoding="utf-8")
    write_json(trace_dir / f"{run_id}.metrics.json", metrics.to_dict())
    return metrics


async def capture_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> ProcessCapture:
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout = process.stdout
    stderr = process.stderr
    assert stdout is not None
    assert stderr is not None
    stdout_lines: list[str] = []
    timed_events: list[dict[str, Any]] = []
    invalid: list[str] = []
    stderr_parts: list[str] = []

    async def read_stdout() -> None:
        while True:
            raw = await stdout.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace")
            stdout_lines.append(line)
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                invalid.append(stripped)
                continue
            if not isinstance(event, dict):
                invalid.append(stripped)
                continue
            timed_events.append(
                {
                    "elapsedMs": (time.perf_counter() - started) * 1000,
                    "event": event,
                }
            )

    async def read_stderr() -> None:
        while True:
            raw = await stderr.read(8192)
            if not raw:
                return
            stderr_parts.append(raw.decode("utf-8", errors="replace"))

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
    await asyncio.gather(stdout_task, stderr_task)
    duration_ms = (time.perf_counter() - started) * 1000
    return ProcessCapture(
        command=command,
        returncode=process.returncode or 0,
        duration_ms=duration_ms,
        stdout_lines=stdout_lines,
        stderr="".join(stderr_parts),
        timed_events=timed_events,
        invalid_stdout_lines=invalid,
        timed_out=timed_out,
    )


def analyze_capture(
    system: SystemName,
    task: str,
    repetition: int,
    capture: ProcessCapture,
    validator_ok: bool,
    validation_message: str,
    changed_files: list[str],
) -> RunMetrics:
    events = [item["event"] for item in capture.timed_events]
    event_counts = Counter(str(event.get("type", "unknown")) for event in events)
    assistant_messages = [
        event.get("message")
        for event in events
        if event.get("type") == "message_end"
        and isinstance(event.get("message"), dict)
        and event["message"].get("role") == "assistant"
    ]
    usages = [message.get("usage", {}) for message in assistant_messages]
    tool_starts = [event for event in events if event.get("type") == "tool_execution_start"]
    tool_ends = [event for event in events if event.get("type") == "tool_execution_end"]
    timed_tool_starts = [
        item for item in capture.timed_events if item["event"].get("type") == "tool_execution_start"
    ]
    timed_tool_ends = [
        item for item in capture.timed_events if item["event"].get("type") == "tool_execution_end"
    ]
    tool_batches = [
        names for message in assistant_messages if (names := extract_message_tool_names(message))
    ]
    first_event_ms = capture.timed_events[0]["elapsedMs"] if capture.timed_events else None
    first_text_ms = next(
        (
            item["elapsedMs"]
            for item in capture.timed_events
            if _assistant_update_type(item["event"]) == "text_delta"
        ),
        None,
    )
    final_message = assistant_messages[-1] if assistant_messages else {}
    session: dict[str, Any] = next(
        (event for event in events if event.get("type") == "session"),
        {},
    )
    sequence = [event_signature(event) for event in events]
    return RunMetrics(
        system=system,
        task=task,
        repetition=repetition,
        process_ok=capture.returncode == 0 and not capture.timed_out,
        validator_ok=validator_ok,
        success=(
            capture.returncode == 0
            and not capture.timed_out
            and validator_ok
            and bool(assistant_messages)
        ),
        returncode=capture.returncode,
        timed_out=capture.timed_out,
        duration_ms=capture.duration_ms,
        time_to_first_event_ms=first_event_ms,
        time_to_first_text_delta_ms=first_text_ms,
        time_to_first_tool_start_ms=(
            timed_tool_starts[0]["elapsedMs"] if timed_tool_starts else None
        ),
        time_to_last_tool_end_ms=(timed_tool_ends[-1]["elapsedMs"] if timed_tool_ends else None),
        event_count=len(events),
        trace_bytes=sum(len(line.encode("utf-8")) for line in capture.stdout_lines),
        event_counts=dict(sorted(event_counts.items())),
        event_sequence=sequence,
        model_calls=len(assistant_messages),
        input_tokens=sum(_integer(usage.get("input")) for usage in usages),
        output_tokens=sum(_integer(usage.get("output")) for usage in usages),
        total_tokens=sum(
            _integer(usage.get("totalTokens", usage.get("total_tokens"))) for usage in usages
        ),
        tool_calls=len(tool_starts),
        tool_errors=sum(
            bool(event.get("isError", event.get("is_error", False))) for event in tool_ends
        ),
        tool_sequence=[
            str(event.get("toolName", event.get("tool_name", ""))) for event in tool_starts
        ],
        tool_batches=tool_batches,
        max_tool_batch=max((len(batch) for batch in tool_batches), default=0),
        turn_count=event_counts.get("turn_start", 0),
        final_stop_reason=(
            str(final_message.get("stopReason", final_message.get("stop_reason")))
            if final_message.get("stopReason", final_message.get("stop_reason")) is not None
            else None
        ),
        final_text=extract_message_text(final_message),
        session_schema_version=(
            _integer(session.get("version")) if session.get("version") is not None else None
        ),
        entry_appended_events=event_counts.get("entry_appended", 0),
        changed_files=changed_files,
        validation_message=validation_message,
        stderr=capture.stderr,
        invalid_stdout_lines=capture.invalid_stdout_lines,
    )


async def validate_task(
    task: Task,
    workspace: Path,
    before: dict[str, str],
    after: dict[str, str],
    final_text: str,
) -> tuple[bool, str]:
    validator = task.validator
    messages: list[str] = []
    ok = True
    if validator.kind == "exact_output":
        expected = validator.expected or ""
        if final_text.strip() != expected:
            ok = False
            messages.append(f"final output differs from expected {expected!r}")
    elif validator.kind == "command":
        commands = (
            *((validator.command,) if validator.command else ()),
            *validator.additional_commands,
        )
        if not commands:
            return False, "command validator has no command"
        for command in commands:
            expanded = tuple(
                part.replace("{benchmark}", str(HERE)).replace("{workspace}", str(workspace))
                for part in command
            )
            process = await asyncio.create_subprocess_exec(
                *expanded,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                ok = False
                messages.append(
                    "validator command failed: "
                    + (stdout + stderr).decode("utf-8", errors="replace").strip()
                )
    elif validator.kind != "none":
        return False, f"unknown validator kind: {validator.kind}"

    if validator.require_clean_workspace and before != after:
        ok = False
        messages.append("workspace changed during a read-only task")
    for relative in validator.protected_files:
        if before.get(relative) != after.get(relative):
            ok = False
            messages.append(f"protected file changed: {relative}")
    return ok, "; ".join(messages) if messages else "passed"


def snapshot_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if should_ignore(relative):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def should_ignore(relative: Path) -> bool:
    ignored = {".git", ".pi", ".pytest_cache", "__pycache__", ".mypy_cache"}
    return any(part in ignored for part in relative.parts) or relative.suffix in {".pyc", ".pyo"}


def tree_diff(before: dict[str, str], after: dict[str, str]) -> list[str]:
    names = sorted(set(before) | set(after))
    result: list[str] = []
    for name in names:
        if name not in before:
            result.append(f"A {name}")
        elif name not in after:
            result.append(f"D {name}")
        elif before[name] != after[name]:
            result.append(f"M {name}")
    return result


def extract_final_text(timed_events: list[dict[str, Any]]) -> str:
    messages = [
        item["event"].get("message")
        for item in timed_events
        if item["event"].get("type") == "message_end"
        and isinstance(item["event"].get("message"), dict)
        and item["event"]["message"].get("role") == "assistant"
    ]
    return extract_message_text(messages[-1]) if messages else ""


def extract_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
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


def extract_message_tool_names(message: Any) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        str(block.get("name", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in {"toolCall", "tool_call", "toolcall"}
    ]


def event_signature(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", "unknown"))
    if event_type == "message_update":
        subtype = _assistant_update_type(event)
        return f"message_update:{subtype}" if subtype else event_type
    if event_type.startswith("tool_execution"):
        tool = event.get("toolName", event.get("tool_name"))
        return f"{event_type}:{tool}" if tool else event_type
    if event_type in {"message_start", "message_end"}:
        message = event.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        return f"{event_type}:{role}" if role else event_type
    return event_type


def _assistant_update_type(event: dict[str, Any]) -> str | None:
    value = event.get("assistantMessageEvent", event.get("assistant_message_event"))
    if not isinstance(value, dict):
        return None
    subtype = value.get("type")
    return str(subtype) if subtype is not None else None


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value)
    return 0


async def run_core_parity(output: Path, *, timeout: float) -> dict[str, Any]:
    capture_dir = output / "core-upstream"
    capture = await capture_process(
        [
            "npm",
            "run",
            "capture",
            "--",
            "--out",
            str(capture_dir),
        ],
        cwd=ROOT / "tools" / "upstream-fixtures",
        env=dict(os.environ),
        timeout=max(timeout, 300),
    )
    compare = await capture_process(
        [sys.executable, str(ROOT / "scripts" / "compare_upstream_capture.py"), str(capture_dir)],
        cwd=ROOT,
        env=dict(os.environ),
        timeout=max(timeout, 300),
    )
    parity_tests = await capture_process(
        [sys.executable, "-m", "pytest", "-q", "tests/parity"],
        cwd=ROOT,
        env=dict(os.environ),
        timeout=max(timeout, 300),
    )
    return {
        "captureReturnCode": capture.returncode,
        "compareReturnCode": compare.returncode,
        "pythonParityReturnCode": parity_tests.returncode,
        "matched": (
            capture.returncode == 0 and compare.returncode == 0 and parity_tests.returncode == 0
        ),
        "captureStdout": "".join(capture.stdout_lines),
        "captureStderr": capture.stderr,
        "compareStdout": "".join(compare.stdout_lines),
        "compareStderr": compare.stderr,
        "pythonParityStdout": "".join(parity_tests.stdout_lines),
        "pythonParityStderr": parity_tests.stderr,
    }


def aggregate_runs(runs: list[RunMetrics]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[RunMetrics]] = {}
    for run in runs:
        grouped.setdefault((run.system, run.task), []).append(run)
    result: dict[str, Any] = {}
    for (system, task), values in sorted(grouped.items()):
        result[f"{system}:{task}"] = {
            "runs": len(values),
            "successRate": sum(value.success for value in values) / len(values),
            "medianDurationMs": statistics.median(value.duration_ms for value in values),
            "medianTimeToFirstTextDeltaMs": median_optional(
                value.time_to_first_text_delta_ms for value in values
            ),
            "medianTimeToFirstToolStartMs": median_optional(
                value.time_to_first_tool_start_ms for value in values
            ),
            "medianTimeToLastToolEndMs": median_optional(
                value.time_to_last_tool_end_ms for value in values
            ),
            "medianModelCalls": statistics.median(value.model_calls for value in values),
            "medianToolCalls": statistics.median(value.tool_calls for value in values),
            "medianMaxToolBatch": statistics.median(value.max_tool_batch for value in values),
            "medianTurns": statistics.median(value.turn_count for value in values),
            "medianInputTokens": statistics.median(value.input_tokens for value in values),
            "medianOutputTokens": statistics.median(value.output_tokens for value in values),
            "medianTraceBytes": statistics.median(value.trace_bytes for value in values),
        }
    return result


def median_optional(values: Any) -> float | None:
    selected = [float(value) for value in values if value is not None]
    return statistics.median(selected) if selected else None


def render_report(summary: dict[str, Any]) -> str:
    metadata = summary["metadata"]
    runs = summary["runs"]
    aggregates = summary["aggregates"]
    lines = [
        "# pi Agent trace comparison",
        "",
        f"Generated: `{metadata['createdAt']}`",
        "",
        "Both systems used the same model endpoint, task prompt, isolated fixture, and "
        "read/bash/edit/write tool budget. Live model runs are observational rather than "
        "deterministic; use at least three repetitions before treating latency or tool-choice "
        "differences as stable.",
        "",
        "## Configuration",
        "",
        f"- Upstream: `@earendil-works/pi-coding-agent@{metadata['upstream']['version']}`",
        f"- Python: `{metadata['python']['command']}`",
        f"- Model: `{metadata['model']['provider']}/{metadata['model']['id']}`",
        f"- Context window: `{metadata['model']['contextWindow']}`",
        f"- Maximum output: `{metadata['model']['maxTokens']}`",
        f"- Repetitions: `{metadata['repetitions']}`",
        "",
    ]
    core = summary.get("coreParity")
    if core is not None:
        lines.extend(
            [
                "## Deterministic Agent Core parity",
                "",
                f"- Fresh upstream capture matched checked-in goldens: `{core['matched']}`",
                f"- Capture exit: `{core['captureReturnCode']}`",
                f"- Golden comparison exit: `{core['compareReturnCode']}`",
                f"- Python parity tests exit: `{core['pythonParityReturnCode']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Live task results",
            "",
            "| System | Task | Run | Pass | Wall ms | First text ms | First Tool ms | "
            "Model calls | Tools | Max batch | Tool errors | Input tok | Output tok | "
            "Trace bytes | Changed files |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for run in runs:
        first_text = run["time_to_first_text_delta_ms"]
        first_tool = run["time_to_first_tool_start_ms"]
        changed = ", ".join(run["changed_files"]) or "—"
        lines.append(
            "| {system} | {task} | {repetition} | {success} | {duration:.0f} | "
            "{first_text} | {first_tool} | {model_calls} | {tool_calls} | "
            "{max_tool_batch} | {tool_errors} | {input_tokens} | {output_tokens} | "
            "{trace_bytes} | {changed} |".format(
                system=run["system"],
                task=run["task"],
                repetition=run["repetition"],
                success="yes" if run["success"] else "no",
                duration=run["duration_ms"],
                first_text=(f"{first_text:.0f}" if first_text is not None else "—"),
                first_tool=(f"{first_tool:.0f}" if first_tool is not None else "—"),
                model_calls=run["model_calls"],
                tool_calls=run["tool_calls"],
                max_tool_batch=run["max_tool_batch"],
                tool_errors=run["tool_errors"],
                input_tokens=run["input_tokens"],
                output_tokens=run["output_tokens"],
                trace_bytes=run["trace_bytes"],
                changed=changed.replace("|", "\\|"),
            )
        )
    lines.extend(["", "## Aggregates", ""])
    lines.extend(
        [
            "| System:task | Success | Wall ms | First text ms | First Tool ms | "
            "Model calls | Tools | Max batch | Input tok | Output tok | Trace bytes |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, value in aggregates.items():
        first = value["medianTimeToFirstTextDeltaMs"]
        first_tool = value["medianTimeToFirstToolStartMs"]
        first_text = f"{first:.0f}" if first is not None else "—"
        first_tool_text = f"{first_tool:.0f}" if first_tool is not None else "—"
        lines.append(
            f"| {name} | {value['successRate']:.0%} | {value['medianDurationMs']:.0f} | "
            f"{first_text} | {first_tool_text} | {value['medianModelCalls']:.1f} | "
            f"{value['medianToolCalls']:.1f} | {value['medianMaxToolBatch']:.1f} | "
            f"{value['medianInputTokens']:.0f} | "
            f"{value['medianOutputTokens']:.0f} | {value['medianTraceBytes']:.0f} |"
        )
    lines.extend(["", "## Trace-shape differences", ""])
    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        if run["repetition"] == 1:
            by_task.setdefault(run["task"], {})[run["system"]] = run
    for task, systems in sorted(by_task.items()):
        upstream = systems.get("upstream-ts")
        python = systems.get("python")
        if upstream is None or python is None:
            continue
        lines.extend([f"### `{task}`", ""])
        lines.append(
            f"- Event count: upstream `{upstream['event_count']}`, "
            f"Python `{python['event_count']}`."
        )
        lines.append(
            f"- Trace payload: upstream `{upstream['trace_bytes']}` bytes, "
            f"Python `{python['trace_bytes']}` bytes."
        )
        lines.append(
            f"- Input tokens: upstream `{upstream['input_tokens']}`, "
            f"Python `{python['input_tokens']}`."
        )
        lines.append(
            f"- Tool path: upstream `{upstream['tool_sequence']}`, "
            f"Python `{python['tool_sequence']}`."
        )
        lines.append(
            f"- Tool batches: upstream `{upstream['tool_batches']}`, "
            f"Python `{python['tool_batches']}`."
        )
        only_upstream = Counter(upstream["event_sequence"]) - Counter(python["event_sequence"])
        only_python = Counter(python["event_sequence"]) - Counter(upstream["event_sequence"])
        lines.append(f"- Extra upstream event signatures: `{dict(only_upstream)}`.")
        lines.append(f"- Extra Python event signatures: `{dict(only_python)}`.")
        lines.append("")
    lines.extend(
        [
            "## Artifacts",
            "",
            "- Raw JSONL traces: `traces/<system>/<task>-rN.jsonl`",
            "- Per-line timings: `traces/<system>/<task>-rN.timed.jsonl`",
            "- Per-run metrics: `traces/<system>/<task>-rN.metrics.json`",
            "- Machine-readable summary: `summary.json`",
            "",
        ]
    )
    return "\n".join(lines)


def write_upstream_model_config(
    path: Path,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key_env: str,
    context_window: int,
    max_tokens: int,
) -> None:
    write_json(
        path,
        {
            "providers": {
                provider: {
                    "baseUrl": base_url,
                    "api": "openai-completions",
                    "apiKey": f"${api_key_env}",
                    "authHeader": True,
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": False,
                    },
                    "models": [
                        {
                            "id": model,
                            "name": model,
                            "reasoning": False,
                            "input": ["text"],
                            "contextWindow": context_window,
                            "maxTokens": max_tokens,
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                        }
                    ],
                }
            }
        },
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
