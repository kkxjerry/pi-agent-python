#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SYSTEMS = ("upstream-ts", "python")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TEST_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:python\d*(?:\.\d+)?\s+-m\s+)?"
    r"(?:pytest|unittest|tox|nox|make\s+test|runtests?|test)(?:\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Budget:
    wall_seconds: int = 900
    model_calls: int = 15
    tool_calls: int = 60
    input_tokens: int = 250_000
    output_tokens: int = 20_000


@dataclass(slots=True)
class LiveCounters:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_usage_by_turn: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunResult:
    instance_id: str
    system: str
    order_index: int
    exit_code: int
    termination: str
    wall_seconds: float
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    trace_bytes: int
    patch_bytes: int
    patch_files: tuple[str, ...]
    patch_added: int
    patch_deleted: int
    tests_modified: bool
    container_image: str
    container_image_id: str
    final_text: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["patch_files"] = list(self.patch_files)
        return value


@dataclass(frozen=True, slots=True)
class Task:
    instance_id: str
    repo: str
    base_commit: str
    bucket: str
    problem_statement: str


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path
    container: str
    image: str
    image_id: str
    shims: Path


class Swe5Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(args.repo_root).resolve()
        self.manifest_path = Path(args.manifest).resolve()
        self.manifest = json.loads(self.manifest_path.read_text())
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self.run_id = args.run_id or f"swe5-{timestamp}"
        self.results_root = Path(args.output).resolve() / self.run_id
        self.results_root.mkdir(parents=True, exist_ok=False)
        self.work_root = self.results_root / "work"
        self.work_root.mkdir()
        self.budget = Budget(
            wall_seconds=args.timeout,
            model_calls=args.max_model_calls,
            tool_calls=args.max_tool_calls,
            input_tokens=args.max_input_tokens,
            output_tokens=args.max_output_tokens,
        )
        self.upstream_binary = discover_upstream_binary(self.repo_root)
        self.python_binary = self.repo_root / ".venv" / "bin" / "pi-py"
        if not self.python_binary.is_file():
            raise SystemExit(f"Python CLI is missing: {self.python_binary}")
        self.tasks = load_tasks(self.manifest)
        self.results: list[RunResult] = []

    def run(self) -> int:
        self.write_metadata()
        validate_api_key()
        self.validate_upstream_smoke()
        order = paired_order(self.tasks)
        for order_index, (task, system) in enumerate(order, start=1):
            result = self.run_one(task, system, order_index)
            self.results.append(result)
            self.write_progress()
        self.write_predictions()
        (self.results_root / "agent-runs.done").write_text("ok\n")
        print(self.results_root)
        return 0

    def write_metadata(self) -> None:
        bootstrap = Path.home() / ".cache" / "pi-agent-swe5" / "bootstrap.json"
        metadata = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dataset": self.manifest["dataset"],
            "datasetRevision": self.manifest.get("datasetRevision"),
            "instances": [
                {
                    "instance_id": task.instance_id,
                    "repo": task.repo,
                    "base_commit": task.base_commit,
                    "bucket": task.bucket,
                }
                for task in self.tasks
            ],
            "systems": list(SYSTEMS),
            "model": {
                "provider": "dashscope",
                "id": self.args.model,
                "baseUrl": DASHSCOPE_BASE_URL,
                "thinking": "off",
            },
            "budgetPerRun": asdict(self.budget),
            "pythonGitSha": source_revision(self.repo_root),
            "pythonGitStatus": source_status(self.repo_root),
            "upstreamBinary": str(self.upstream_binary),
            "host": {
                "architecture": os.uname().machine,
                "kernel": os.uname().release,
                "docker": command_output(["docker", "version", "--format", "{{.Server.Version}}"]),
            },
            "bootstrap": json.loads(bootstrap.read_text()) if bootstrap.is_file() else None,
            "secretsRecorded": False,
        }
        (self.results_root / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )
        shutil.copy2(self.manifest_path, self.results_root / "swe5.json")

    def validate_upstream_smoke(self) -> None:
        smoke = self.results_root / "upstream-smoke"
        smoke.mkdir()
        home = smoke / "home"
        write_upstream_home(home, self.args.model)
        workspace = smoke / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        shims = smoke / "shims"
        shims.mkdir()
        environment = base_environment(home, shims)
        command = upstream_command(
            self.upstream_binary,
            self.args.model,
            "Reply with exactly SWE5_SMOKE_OK and do not call tools.",
            no_tools=True,
        )
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            stdout=smoke.joinpath("raw.jsonl").open("wb"),
            stderr=smoke.joinpath("stderr.log").open("wb"),
            timeout=120,
            check=False,
        )
        combined = smoke.joinpath("raw.jsonl").read_text(errors="replace") + smoke.joinpath(
            "stderr.log"
        ).read_text(errors="replace")
        if completed.returncode != 0 or "SWE5_SMOKE_OK" not in combined:
            raise SystemExit(f"official TypeScript pi smoke failed; see {smoke / 'stderr.log'}")

    def run_one(self, task: Task, system: str, order_index: int) -> RunResult:
        slug = safe_name(task.instance_id)
        run_dir = self.results_root / "runs" / slug / system
        run_dir.mkdir(parents=True)
        workspace = prepare_workspace(task, run_dir, order_index)
        home = run_dir / "home"
        home.mkdir(mode=0o700)
        if system == "upstream-ts":
            write_upstream_home(home, self.args.model)
        prompt = task_prompt(task)
        (run_dir / "prompt.txt").write_text(prompt)
        environment = base_environment(home, workspace.shims)
        environment["SWE5_INSTANCE_ID"] = task.instance_id
        environment["SWE5_SYSTEM"] = system
        if system == "upstream-ts":
            command = upstream_command(
                self.upstream_binary,
                self.args.model,
                prompt,
            )
        else:
            command = python_command(
                self.python_binary,
                self.args.model,
                prompt,
            )
        (run_dir / "command.json").write_text(json.dumps(redact_command(command), indent=2) + "\n")
        try:
            process_result = capture_process(
                command,
                cwd=workspace.root,
                environment=environment,
                run_dir=run_dir,
                budget=self.budget,
            )
            patch = collect_patch(workspace.root)
            (run_dir / "patch.diff").write_text(patch)
            patch_files, added, deleted = patch_stats(patch)
            final_text = extract_final_text(run_dir / "timed.jsonl")
            result = RunResult(
                instance_id=task.instance_id,
                system=system,
                order_index=order_index,
                exit_code=process_result["exit_code"],
                termination=process_result["termination"],
                wall_seconds=process_result["wall_seconds"],
                model_calls=process_result["counters"].model_calls,
                tool_calls=process_result["counters"].tool_calls,
                input_tokens=process_result["counters"].input_tokens,
                output_tokens=process_result["counters"].output_tokens,
                trace_bytes=run_dir.joinpath("raw.jsonl").stat().st_size,
                patch_bytes=len(patch.encode()),
                patch_files=patch_files,
                patch_added=added,
                patch_deleted=deleted,
                tests_modified=any(is_test_path(path) for path in patch_files),
                container_image=workspace.image,
                container_image_id=workspace.image_id,
                final_text=final_text,
            )
            (run_dir / "metrics.json").write_text(
                json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
            )
            normalize_trace(run_dir / "timed.jsonl", run_dir / "normalized.jsonl")
            return result
        finally:
            subprocess.run(
                ["docker", "rm", "-f", workspace.container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if not self.args.keep_workspaces:
                shutil.rmtree(workspace.root, ignore_errors=True)

    def write_progress(self) -> None:
        (self.results_root / "agent-runs.json").write_text(
            json.dumps([result.to_dict() for result in self.results], indent=2, sort_keys=True)
            + "\n"
        )

    def write_predictions(self) -> None:
        for system in SYSTEMS:
            rows = []
            for result in self.results:
                if result.system != system:
                    continue
                patch_path = (
                    self.results_root
                    / "runs"
                    / safe_name(result.instance_id)
                    / system
                    / "patch.diff"
                )
                rows.append(
                    {
                        "instance_id": result.instance_id,
                        "model_name_or_path": f"{system}-{self.args.model}",
                        "model_patch": patch_path.read_text(),
                    }
                )
            (self.results_root / f"predictions.{system}.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--run-id")
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-model-calls", type=int, default=15)
    parser.add_argument("--max-tool-calls", type=int, default=60)
    parser.add_argument("--max-input-tokens", type=int, default=250_000)
    parser.add_argument("--max-output-tokens", type=int, default=20_000)
    parser.add_argument("--keep-workspaces", action="store_true")
    args = parser.parse_args()
    return Swe5Runner(args).run()


def load_tasks(manifest: dict[str, Any]) -> list[Task]:
    rows = dataset_rows(manifest["dataset"], manifest.get("split", "test"))
    by_id = {str(row["instance_id"]): row for row in rows}
    tasks: list[Task] = []
    for selected in manifest["instances"]:
        instance_id = selected["instance_id"]
        row = by_id.get(instance_id)
        if row is None:
            raise SystemExit(f"selected instance disappeared from dataset: {instance_id}")
        tasks.append(
            Task(
                instance_id=instance_id,
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                bucket=str(selected["bucket"]),
                problem_statement=str(row["problem_statement"]),
            )
        )
    return tasks


def dataset_rows(name: str, split: str) -> list[dict[str, Any]]:
    from datasets import load_dataset

    return [dict(row) for row in load_dataset(name, split=split)]


def paired_order(tasks: Sequence[Task]) -> list[tuple[Task, str]]:
    result: list[tuple[Task, str]] = []
    for index, task in enumerate(tasks):
        systems = SYSTEMS if index % 2 == 0 else tuple(reversed(SYSTEMS))
        result.extend((task, system) for system in systems)
    return result


def task_prompt(task: Task) -> str:
    return (
        "You are working on a SWE-bench repository at its exact base commit.\n"
        "Resolve the issue below in the repository. Inspect the relevant code before editing, "
        "make the smallest correct production-code change, and run focused tests when possible. "
        "Do not modify tests, benchmark files, or dependency lock files merely to hide a failure. "
        "Do not stop after explaining a solution: edit the repository. When done, summarize the "
        "change and validation briefly.\n\n"
        f"Issue:\n{task.problem_statement.strip()}\n"
    )


def prepare_workspace(task: Task, run_dir: Path, order_index: int) -> Workspace:
    image = find_instance_image(task.instance_id)
    image_id = command_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    seed = f"swe5-seed-{order_index}-{safe_name(task.instance_id)[-36:]}"
    subprocess.run(
        ["docker", "rm", "-f", seed], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["docker", "create", "--name", seed, "--entrypoint", "/bin/bash", image, "-lc", "true"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    workspace = run_dir / "workspace"
    workspace.mkdir()
    try:
        subprocess.run(
            ["docker", "cp", f"{seed}:/testbed/.", str(workspace)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", seed],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if not (workspace / ".git").exists():
        raise SystemExit(f"instance image {image} did not contain /testbed/.git")
    subprocess.run(
        ["git", "reset", "--hard", task.base_commit],
        cwd=workspace,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["git", "clean", "-fdx"], cwd=workspace, check=True, stdout=subprocess.DEVNULL)
    container = f"swe5-run-{order_index}-{safe_name(task.instance_id)[-34:]}"
    subprocess.run(
        ["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--entrypoint",
            "/bin/bash",
            "-v",
            f"{workspace}:/testbed",
            image,
            "-lc",
            "trap : TERM INT; sleep infinity & wait",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    shims = run_dir / "shims"
    write_shims(shims, container)
    return Workspace(workspace, container, image, image_id, shims)


def find_instance_image(instance_id: str) -> str:
    safe = instance_id.replace("/", "__").lower()
    images = command_output(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
    candidates = [
        line.strip()
        for line in images.splitlines()
        if safe in line.lower() and "eval" in line.lower()
    ]
    if not candidates:
        expected = f"swebench/sweb.eval.x86_64.{safe}:latest"
        if (
            subprocess.run(
                ["docker", "image", "inspect", expected],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ):
            return expected
        raise SystemExit(f"could not locate a prepared SWE-bench image for {instance_id}")
    return sorted(candidates, key=lambda item: ("latest" not in item, len(item)))[0]


def write_shims(directory: Path, container: str) -> None:
    directory.mkdir()
    commands = (
        "python",
        "python3",
        "pytest",
        "tox",
        "nox",
        "make",
        "pip",
        "pip3",
        "coverage",
        "django-admin",
        "sphinx-build",
        "pylint",
        "mypy",
    )
    script = f"""#!/bin/sh
set -eu
command_name=$(basename "$0")
exec docker exec -i -w /testbed {shell_quote(container)} /bin/bash -lc '
  source /root/.bashrc >/dev/null 2>&1 || true
  source /etc/profile >/dev/null 2>&1 || true
  exec "$@"
' swe5-shim "$command_name" "$@"
"""
    for name in commands:
        path = directory / name
        path.write_text(script)
        path.chmod(0o755)


def discover_upstream_binary(repo_root: Path) -> Path:
    candidates = sorted(repo_root.glob("benchmarks/agent_compare/**/node_modules/.bin/pi"))
    if not candidates:
        raise SystemExit("official TypeScript pi binary is not installed under agent_compare")
    return candidates[0].resolve()


def upstream_command(binary: Path, model: str, prompt: str, *, no_tools: bool = False) -> list[str]:
    command = [
        str(binary),
        "--mode",
        "json",
        "--provider",
        "dashscope",
        "--model",
        model,
        "--thinking",
        "off",
        "--no-session",
    ]
    if no_tools:
        command.append("--no-tools")
    command.extend(["-p", prompt])
    return command


def python_command(binary: Path, model: str, prompt: str) -> list[str]:
    return [
        str(binary),
        "--mode",
        "json",
        "--provider",
        "dashscope",
        "--model",
        model,
        "--base-url",
        DASHSCOPE_BASE_URL,
        "--thinking",
        "off",
        "--no-session",
        "--approval",
        "off",
        "-p",
        prompt,
    ]


def write_upstream_home(home: Path, model: str) -> None:
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True, mode=0o700)
    config = {
        "providers": {
            "dashscope": {
                "baseUrl": DASHSCOPE_BASE_URL,
                "apiKey": "DASHSCOPE_API_KEY",
                "api": "openai-completions",
                "models": [
                    {
                        "id": model,
                        "name": model,
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "contextWindow": 96_000,
                        "maxTokens": 4_096,
                    }
                ],
            }
        }
    }
    (agent / "models.json").write_text(json.dumps(config, indent=2) + "\n")


def base_environment(home: Path, shims: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["PATH"] = str(shims) + os.pathsep + environment.get("PATH", "")
    environment["PI_CODING_AGENT_DIR"] = str(home / ".pi" / "agent")
    environment["NO_COLOR"] = "1"
    environment["TERM"] = "dumb"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def capture_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    run_dir: Path,
    budget: Budget,
) -> dict[str, Any]:
    raw_path = run_dir / "raw.jsonl"
    timed_path = run_dir / "timed.jsonl"
    stderr_path = run_dir / "stderr.log"
    counters = LiveCounters()
    started = time.monotonic()
    termination = "completed"
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    with (
        raw_path.open("wb") as raw,
        timed_path.open("w", encoding="utf-8") as timed,
        stderr_path.open("wb") as stderr,
    ):
        while selector.get_map():
            elapsed = time.monotonic() - started
            reason = budget_reason(counters, budget, elapsed)
            if reason is not None and process.poll() is None:
                termination = reason
                terminate_process_group(process)
            events = selector.select(timeout=0.25)
            if not events and process.poll() is not None:
                for fileobj, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    chunk = fileobj.read()
                    if chunk:
                        buffers[kind].extend(chunk)
                    selector.unregister(fileobj)
                continue
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                kind = key.data
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffers[kind].extend(chunk)
                lines = drain_lines(buffers[kind])
                for line in lines:
                    if kind == "stderr":
                        stderr.write(line + b"\n")
                        continue
                    raw.write(line + b"\n")
                    record = parse_json(line)
                    update_counters(counters, record)
                    timed.write(
                        json.dumps(
                            {
                                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                                "event": record
                                if record is not None
                                else {"raw": line.decode(errors="replace")},
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            if process.poll() is not None and not selector.get_map():
                break
        for kind, buffer in buffers.items():
            if buffer:
                if kind == "stderr":
                    stderr.write(buffer)
                else:
                    raw.write(buffer)
                    line = bytes(buffer)
                    record = parse_json(line)
                    update_counters(counters, record)
                    timed.write(
                        json.dumps(
                            {
                                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                                "event": record
                                if record is not None
                                else {"raw": line.decode(errors="replace")},
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    try:
        exit_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        terminate_process_group(process, force=True)
        exit_code = process.wait(timeout=10)
    if termination == "completed" and exit_code != 0:
        termination = "process_error"
    return {
        "exit_code": exit_code,
        "termination": termination,
        "wall_seconds": round(time.monotonic() - started, 3),
        "counters": counters,
    }


def drain_lines(buffer: bytearray) -> list[bytes]:
    lines: list[bytes] = []
    while True:
        index = buffer.find(b"\n")
        if index < 0:
            break
        lines.append(bytes(buffer[:index]))
        del buffer[: index + 1]
    return lines


def parse_json(line: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def update_counters(counters: LiveCounters, event: dict[str, Any] | None) -> None:
    if event is None:
        return
    event_type = str(event.get("type") or event.get("event") or "")
    if event_type in {"turn_start", "assistant_message_start", "model_start"}:
        counters.model_calls += 1
    if event_type == "tool_execution_start":
        counters.tool_calls += 1
    if event_type in {"turn_end", "message_end", "agent_end"}:
        usage = find_usage(event)
        if usage is not None:
            input_tokens, output_tokens, key = usage
            previous = counters.last_usage_by_turn.get(key)
            if previous is None:
                counters.input_tokens += input_tokens
                counters.output_tokens += output_tokens
                counters.last_usage_by_turn[key] = (input_tokens, output_tokens)


def find_usage(value: Any) -> tuple[int, int, str] | None:
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            input_tokens = integer_value(usage, "input", "input_tokens", "inputTokens")
            output_tokens = integer_value(usage, "output", "output_tokens", "outputTokens")
            if input_tokens is not None or output_tokens is not None:
                identity = (
                    value.get("id")
                    or value.get("messageId")
                    or hashlib.sha1(json.dumps(usage, sort_keys=True).encode()).hexdigest()
                )
                return input_tokens or 0, output_tokens or 0, str(identity)
        for child in value.values():
            found = find_usage(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_usage(child)
            if found is not None:
                return found
    return None


def integer_value(value: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, int) and not isinstance(item, bool):
            return item
    return None


def budget_reason(counters: LiveCounters, budget: Budget, elapsed: float) -> str | None:
    if elapsed > budget.wall_seconds:
        return "timeout"
    if counters.model_calls > budget.model_calls:
        return "budget_model_calls"
    if counters.tool_calls > budget.tool_calls:
        return "budget_tool_calls"
    if counters.input_tokens > budget.input_tokens:
        return "budget_input_tokens"
    if counters.output_tokens > budget.output_tokens:
        return "budget_output_tokens"
    return None


def terminate_process_group(process: subprocess.Popen[bytes], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    with suppress_process_error():
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    if not force:
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            with suppress_process_error():
                os.killpg(process.pid, signal.SIGKILL)


class suppress_process_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> bool:
        return isinstance(args[1], (ProcessLookupError, PermissionError))


def collect_patch(workspace: Path) -> str:
    subprocess.run(
        ["git", "add", "-N", "--", "."], cwd=workspace, check=False, stdout=subprocess.DEVNULL
    )
    completed = subprocess.run(
        ["git", "diff", "--binary", "HEAD", "--", "."],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def patch_stats(patch: str) -> tuple[tuple[str, ...], int, int]:
    files: list[str] = []
    added = 0
    deleted = 0
    for line in patch.splitlines():
        match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if match:
            files.append(match.group(2))
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted += 1
    return tuple(dict.fromkeys(files)), added, deleted


def is_test_path(path: str) -> bool:
    lowered = "/" + path.lower()
    return any(marker in lowered for marker in ("/test", "/tests", "test_", "_test.py"))


def normalize_trace(source: Path, destination: Path) -> None:
    records: list[dict[str, Any]] = []
    for line in source.read_text(errors="replace").splitlines():
        try:
            wrapped = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = wrapped.get("event")
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "unknown")
        normalized: dict[str, Any] = {
            "elapsed_ms": wrapped.get("elapsed_ms"),
            "type": event_type,
        }
        if event_type.startswith("tool_execution"):
            normalized.update(tool_fields(event))
        elif event_type in {"message_update", "message_end", "turn_end"}:
            normalized["text_delta"] = find_text_delta(event)
            usage = find_usage(event)
            if usage is not None:
                normalized["input_tokens"] = usage[0]
                normalized["output_tokens"] = usage[1]
        records.append(normalized)
    destination.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    )


def tool_fields(event: dict[str, Any]) -> dict[str, Any]:
    tool = event.get("toolName") or event.get("tool_name") or event.get("name")
    call_id = event.get("toolCallId") or event.get("tool_call_id") or event.get("id")
    args = event.get("args") or event.get("arguments")
    result = event.get("result")
    is_error = event.get("isError") if "isError" in event else event.get("is_error")
    exit_code = find_key(result, {"exitCode", "exit_code"})
    fields: dict[str, Any] = {
        "tool": tool,
        "tool_call_id": call_id,
        "is_error": is_error,
        "exit_code": exit_code,
    }
    if isinstance(args, dict):
        fields["target"] = args.get("path") or args.get("file_path")
        command = args.get("command")
        if isinstance(command, str):
            fields["command_kind"] = "test" if TEST_COMMAND.search(command) else "shell"
            fields["command"] = command[:500]
    return fields


def find_key(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                return child
            found = find_key(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_key(child, keys)
            if found is not None:
                return found
    return None


def find_text_delta(event: dict[str, Any]) -> str | None:
    nested = event.get("assistantMessageEvent") or event.get("assistant_message_event")
    if isinstance(nested, dict):
        delta = nested.get("delta")
        if isinstance(delta, str):
            return delta
    delta = event.get("delta")
    return delta if isinstance(delta, str) else None


def extract_final_text(path: Path) -> str:
    chunks: list[str] = []
    final = ""
    for line in path.read_text(errors="replace").splitlines():
        try:
            wrapped = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = wrapped.get("event")
        if not isinstance(event, dict):
            continue
        delta = find_text_delta(event)
        if delta:
            chunks.append(delta)
        if event.get("type") in {"message_end", "turn_end"}:
            text = find_text(event)
            if text:
                final = text
    return (final or "".join(chunks))[-8000:]


def find_text(value: Any) -> str | None:
    if isinstance(value, dict):
        role = value.get("role")
        content = value.get("content")
        if role == "assistant":
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [
                    item.get("text")
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                ]
                if texts:
                    return "".join(texts)
        for child in value.values():
            found = find_text(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_text(child)
            if found:
                return found
    return None


def validate_api_key() -> None:
    value = os.environ.get("DASHSCOPE_API_KEY")
    if not value:
        raise SystemExit("DASHSCOPE_API_KEY is not set in the server process")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def redact_command(command: Sequence[str]) -> list[str]:
    return ["[REDACTED]" if "key=" in item.lower() else item for item in command]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def source_revision(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout.strip()
    revision_file = root / "benchmarks" / "swe5" / "PYTHON_GIT_SHA"
    return revision_file.read_text().strip() if revision_file.is_file() else "unknown"


def source_status(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "archive-sync"


def command_output(command: list[str]) -> str:
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
