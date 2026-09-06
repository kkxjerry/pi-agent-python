from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shlex
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve().parent
DEFAULT_PARQUET = Path("/root/pi-swe5/SWE-bench_Verified-rich/data/test-00000-of-00001.parquet")
DEFAULT_UPSTREAM_ROOT = Path("/root/pi-swe5/upstream")
DEFAULT_PYTHON_ROOT = Path("/root/pi-swe5/pi-agent-python")
DEFAULT_NODE_ROOT = Path("/opt/node-v24.15.0-linux-x64")
UPSTREAM_PACKAGE_VERSION = "0.84.4"
AGENT_PATH = (
    "/opt/miniconda3/envs/testbed/bin:/opt/pi-node/bin:/opt/miniconda3/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
UPSTREAM_CLI_RELATIVE = Path("node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js")
START_TEST_OUTPUT = ">>>>> Start Test Output"
END_TEST_OUTPUT = ">>>>> End Test Output"
PASSING = frozenset({"PASSED", "XFAIL"})
MAINTAINED = frozenset({"PASSED", "XFAIL", "SKIPPED"})
TEST_STATUSES = ("PASSED", "FAILED", "SKIPPED", "XFAIL", "XPASS", "ERROR")
SystemName = Literal["upstream-ts", "python"]
PhaseName = Literal["all", "preflight", "run"]


@dataclass(slots=True, frozen=True)
class SuiteTask:
    instance_id: str
    bucket: str
    agent_timeout_seconds: float
    grade_timeout_seconds: float


@dataclass(slots=True, frozen=True)
class ModelConfig:
    provider: str
    model_id: str
    base_url: str
    context_window: int
    max_tokens: int
    thinking: str


@dataclass(slots=True, frozen=True)
class BudgetConfig:
    max_model_calls: int
    max_tool_calls: int
    max_input_tokens: int
    max_output_tokens: int


@dataclass(slots=True, frozen=True)
class Suite:
    name: str
    dataset: str
    dataset_revision: str
    tasks: tuple[SuiteTask, ...]
    model: ModelConfig
    budgets: BudgetConfig


@dataclass(slots=True, frozen=True)
class SweRecord:
    instance_id: str
    repo: str
    base_commit: str
    difficulty: str
    image: str
    eval_type: str
    log_parser: str
    problem_statement: str
    hints_text: str
    patch: str
    test_patch: str
    eval_script: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]

    @property
    def gold_files(self) -> tuple[str, ...]:
        return patch_files(self.patch)

    @property
    def gold_source_files(self) -> tuple[str, ...]:
        return tuple(path for path in self.gold_files if not is_test_path(path))

    def public_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "repo": self.repo,
            "baseCommit": self.base_commit,
            "difficulty": self.difficulty,
            "image": self.image,
            "evalType": self.eval_type,
            "logParser": self.log_parser,
            "problemStatement": self.problem_statement,
            "failToPass": list(self.fail_to_pass),
            "passToPass": list(self.pass_to_pass),
            "goldPatch": {
                "files": list(self.gold_files),
                "sourceFiles": list(self.gold_source_files),
                "changedLines": patch_changed_lines(self.patch),
            },
            "testPatch": {
                "files": list(patch_files(self.test_patch)),
                "changedLines": patch_changed_lines(self.test_patch),
            },
        }


@dataclass(slots=True)
class Capture:
    command: list[str]
    returncode: int
    duration_ms: float
    stdout_lines: list[str]
    stderr: str
    timed_events: list[dict[str, Any]]
    invalid_stdout_lines: list[str]
    timed_out: bool = False
    budget_exceeded: str | None = None
    capture_error: str | None = None


@dataclass(slots=True, frozen=True)
class GradeReport:
    instance_id: str
    valid: bool
    resolved: bool
    f2p_success: tuple[str, ...]
    f2p_failure: tuple[str, ...]
    p2p_success: tuple[str, ...]
    p2p_failure: tuple[str, ...]
    status_map: dict[str, str]
    test_output_found: bool
    timed_out: bool
    returncode: int
    duration_ms: float
    message: str

    @property
    def f2p_rate(self) -> float:
        total = len(self.f2p_success) + len(self.f2p_failure)
        return len(self.f2p_success) / total if total else 1.0

    @property
    def p2p_rate(self) -> float:
        total = len(self.p2p_success) + len(self.p2p_failure)
        return len(self.p2p_success) / total if total else 1.0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["f2pRate"] = self.f2p_rate
        value["p2pRate"] = self.p2p_rate
        return value


@dataclass(slots=True, frozen=True)
class PreflightReport:
    instance_id: str
    empty: GradeReport
    gold: GradeReport
    accepted: bool
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "empty": self.empty.to_dict(),
            "gold": self.gold.to_dict(),
            "accepted": self.accepted,
            "rejectionReason": self.rejection_reason,
        }


@dataclass(slots=True)
class RunReport:
    system: SystemName
    instance_id: str
    bucket: str
    process_ok: bool
    resolved: bool
    returncode: int
    timed_out: bool
    budget_exceeded: str | None
    duration_ms: float
    grade_duration_ms: float
    time_to_first_event_ms: float | None
    time_to_first_text_ms: float | None
    time_to_first_tool_ms: float | None
    time_to_first_mutation_ms: float | None
    time_to_first_test_ms: float | None
    model_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tool_calls: int
    tool_errors: int
    tool_sequence: list[str]
    tool_batches: list[list[str]]
    action_sequence: list[str]
    bash_commands: list[str]
    baseline_test_before_edit: bool
    verification_test_after_edit: bool
    first_correct_file_read_ms: float | None
    unique_read_files: int
    repeated_reads: int
    event_count: int
    trace_bytes: int
    patch_bytes: int
    patch_changed_lines: int
    changed_files: list[str]
    changed_test_files: list[str]
    final_text: str
    final_stop_reason: str | None
    grade: GradeReport
    stderr: str
    invalid_stdout_lines: list[str]
    capture_error: str | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["grade"] = self.grade.to_dict()
        return value


@dataclass(slots=True)
class BudgetTracker:
    config: BudgetConfig
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    exceeded: str | None = None

    def observe(self, event: dict[str, Any]) -> str | None:
        event_type = str(event.get("type", ""))
        if event_type == "tool_execution_start":
            self.tool_calls += 1
        if event_type == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                self.model_calls += 1
                usage = message.get("usage")
                if isinstance(usage, dict):
                    self.input_tokens += prompt_token_count(usage)
                    self.output_tokens += integer(usage.get("output"))
        checks = (
            (self.model_calls, self.config.max_model_calls, "MODEL_CALLS"),
            (self.tool_calls, self.config.max_tool_calls, "TOOL_CALLS"),
            (self.input_tokens, self.config.max_input_tokens, "INPUT_TOKENS"),
            (self.output_tokens, self.config.max_output_tokens, "OUTPUT_TOKENS"),
        )
        for value, limit, name in checks:
            if value > limit and self.exceeded is None:
                self.exceeded = f"BUDGET_EXCEEDED_{name}"
        return self.exceeded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a paired SWE-bench Verified diagnostic for two pi harnesses"
    )
    parser.add_argument("--suite", type=Path, default=HERE / "swe5.json")
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--phase", choices=("all", "preflight", "run"), default="all")
    parser.add_argument("--system", action="append", choices=("upstream-ts", "python"))
    parser.add_argument("--instance", action="append", default=[])
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument("--container-engine", default="podman")
    parser.add_argument("--upstream-root", type=Path, default=DEFAULT_UPSTREAM_ROOT)
    parser.add_argument("--python-root", type=Path, default=DEFAULT_PYTHON_ROOT)
    parser.add_argument("--node-root", type=Path, default=DEFAULT_NODE_ROOT)
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--python-revision", default="unknown")
    parser.add_argument("--cpus", type=float, default=8.0)
    parser.add_argument("--memory", default="16g")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    suite = load_suite(args.suite)
    selected_tasks = list(suite.tasks)
    if args.instance:
        requested = set(args.instance)
        selected_tasks = [task for task in selected_tasks if task.instance_id in requested]
        missing = requested - {task.instance_id for task in selected_tasks}
        if missing:
            raise SystemExit("unknown suite instances: " + ", ".join(sorted(missing)))
    records = load_records(args.parquet, [task.instance_id for task in selected_tasks])
    validate_environment(args, suite, records)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    output = (
        (args.output or HERE / "results" / f"swe5-{suite.model.model_id}-{timestamp}")
        .expanduser()
        .resolve()
    )
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("instances", "preflight", "traces", "patches", "grades", "pairs", "runs"):
        (output / directory).mkdir(parents=True, exist_ok=True)

    metadata = await build_metadata(args, suite, selected_tasks, records)
    write_json(output / "metadata.json", metadata)
    for task in selected_tasks:
        write_json(
            output / "instances" / f"{task.instance_id}.json",
            records[task.instance_id].public_dict(),
        )

    preflights: dict[str, PreflightReport] = {}
    if args.phase in {"all", "preflight"} and not args.skip_preflight:
        for task in selected_tasks:
            record = records[task.instance_id]
            print(f"PREFLIGHT {task.instance_id}", flush=True)
            preflight_report = await preflight_instance(args, task, record, output)
            preflights[task.instance_id] = preflight_report
            write_json(
                output / "preflight" / f"{task.instance_id}.json",
                preflight_report.to_dict(),
            )
            print(
                f"  emptyResolved={preflight_report.empty.resolved} "
                f"goldResolved={preflight_report.gold.resolved} "
                f"accepted={preflight_report.accepted}",
                flush=True,
            )
        write_json(
            output / "preflight-summary.json",
            {key: value.to_dict() for key, value in preflights.items()},
        )
    if args.phase == "preflight":
        report_text = render_preflight_report(suite, selected_tasks, preflights)
        (output / "report.md").write_text(report_text, encoding="utf-8")
        print(f"REPORT {output / 'report.md'}")
        return 0 if all(item.accepted for item in preflights.values()) else 2

    if not args.skip_preflight:
        rejected = [
            task.instance_id
            for task in selected_tasks
            if task.instance_id in preflights and not preflights[task.instance_id].accepted
        ]
        if rejected:
            write_json(
                output / "summary.json", {"metadata": metadata, "preflightRejected": rejected}
            )
            raise SystemExit(
                "preflight rejected instances before model calls: " + ", ".join(rejected)
            )

    systems: list[SystemName] = args.system or ["upstream-ts", "python"]
    runs: list[RunReport] = []
    for index, task in enumerate(selected_tasks):
        order = systems
        if len(systems) == 2 and index % 2 == 1:
            order = [systems[1], systems[0]]
        for system in order:
            record = records[task.instance_id]
            print(f"RUN {system} {task.instance_id} bucket={task.bucket}", flush=True)
            run = await run_agent(args, suite, task, record, system, output)
            runs.append(run)
            write_json(
                output / "runs" / f"{system}--{task.instance_id}.json",
                run.to_dict(),
            )
            print(
                f"  resolved={run.resolved} duration={run.duration_ms / 1000:.1f}s "
                f"calls={run.model_calls} tools={run.tool_calls} "
                f"tokens={run.input_tokens}+{run.output_tokens}",
                flush=True,
            )

    summary = build_summary(metadata, suite, selected_tasks, preflights, runs)
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(render_report(summary), encoding="utf-8")
    write_pair_reports(output, selected_tasks, runs)
    print(f"REPORT {output / 'report.md'}")
    print(f"SUMMARY {output / 'summary.json'}")
    return 0


def load_suite(path: Path) -> Suite:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("suite must contain a JSON object")
    model = value.get("model")
    budgets = value.get("budgets")
    raw_instances = value.get("instances")
    if (
        not isinstance(model, dict)
        or not isinstance(budgets, dict)
        or not isinstance(raw_instances, list)
    ):
        raise ValueError("suite requires model, budgets, and instances")
    tasks = tuple(
        SuiteTask(
            instance_id=str(item["instance_id"]),
            bucket=str(item["bucket"]),
            agent_timeout_seconds=float(item.get("agentTimeoutSeconds", 900)),
            grade_timeout_seconds=float(item.get("gradeTimeoutSeconds", 1200)),
        )
        for item in raw_instances
        if isinstance(item, dict)
    )
    return Suite(
        name=str(value.get("name", path.stem)),
        dataset=str(value.get("dataset", "SWE-bench_Verified")),
        dataset_revision=str(value.get("datasetRevision", "unknown")),
        tasks=tasks,
        model=ModelConfig(
            provider=str(model["provider"]),
            model_id=str(model["id"]),
            base_url=str(model["baseUrl"]),
            context_window=int(model["contextWindow"]),
            max_tokens=int(model["maxTokens"]),
            thinking=str(model.get("thinking", "off")),
        ),
        budgets=BudgetConfig(
            max_model_calls=int(budgets["maxModelCalls"]),
            max_tool_calls=int(budgets["maxToolCalls"]),
            max_input_tokens=int(budgets["maxInputTokens"]),
            max_output_tokens=int(budgets["maxOutputTokens"]),
        ),
    )


def load_records(path: Path, instance_ids: Sequence[str]) -> dict[str, SweRecord]:
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit("duckdb is required to read SWE-bench metadata") from exc
    placeholders = ",".join("?" for _ in instance_ids)
    query = (
        "SELECT instance_id, repo, base_commit, difficulty, image, eval_type, log_parser, "
        "problem_statement, hints_text, patch, test_patch, eval_script, "
        "FAIL_TO_PASS, PASS_TO_PASS FROM read_parquet(?) "
        f"WHERE instance_id IN ({placeholders})"
    )
    rows = duckdb.connect().execute(query, [str(path), *instance_ids]).fetchall()
    result: dict[str, SweRecord] = {}
    for row in rows:
        record = SweRecord(
            instance_id=str(row[0]),
            repo=str(row[1]),
            base_commit=str(row[2]),
            difficulty=str(row[3]),
            image=str(row[4]),
            eval_type=str(row[5]),
            log_parser=str(row[6]),
            problem_statement=str(row[7]),
            hints_text=str(row[8] or ""),
            patch=str(row[9]),
            test_patch=str(row[10]),
            eval_script=str(row[11]),
            fail_to_pass=tuple(str(item) for item in (row[12] or ())),
            pass_to_pass=tuple(str(item) for item in (row[13] or ())),
        )
        result[record.instance_id] = record
    missing = sorted(set(instance_ids) - set(result))
    if missing:
        raise ValueError("missing dataset records: " + ", ".join(missing))
    return result


def validate_environment(
    args: argparse.Namespace, suite: Suite, records: dict[str, SweRecord]
) -> None:
    if not args.parquet.is_file():
        raise SystemExit(f"Parquet file is missing: {args.parquet}")
    if not args.upstream_root.joinpath(UPSTREAM_CLI_RELATIVE).is_file():
        raise SystemExit(f"upstream CLI is missing below {args.upstream_root}")
    if not args.python_root.joinpath("src/pi_agent/__init__.py").is_file():
        raise SystemExit(f"Python source is missing below {args.python_root}")
    if not args.node_root.joinpath("bin/node").is_file():
        raise SystemExit(f"Node runtime is missing below {args.node_root}")
    if not os.environ.get(args.api_key_env) and args.phase in {"all", "run"}:
        raise SystemExit(f"{args.api_key_env} is not set in the benchmark process")
    if suite.model.context_window <= 0 or suite.model.max_tokens <= 0:
        raise ValueError("model token limits must be positive")
    for record in records.values():
        if not record.eval_script.strip() or not record.test_patch.strip():
            raise ValueError(f"instance lacks evaluation data: {record.instance_id}")


async def build_metadata(
    args: argparse.Namespace,
    suite: Suite,
    tasks: Sequence[SuiteTask],
    records: dict[str, SweRecord],
) -> dict[str, Any]:
    engine_version = await command_text([args.container_engine, "--version"])
    uname = await command_text(["uname", "-a"])
    image_info: dict[str, Any] = {}
    for task in tasks:
        record = records[task.instance_id]
        image = normalize_image(record.image)
        inspect = await command_capture(
            [args.container_engine, "image", "inspect", image, "--format", "{{.Id}}"],
            check=False,
        )
        image_info[task.instance_id] = {
            "reference": image,
            "local": inspect.returncode == 0,
            "id": inspect.stdout.strip() if inspect.returncode == 0 else None,
        }
    return {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "suite": suite.name,
        "dataset": suite.dataset,
        "datasetRevision": suite.dataset_revision,
        "instances": [task.instance_id for task in tasks],
        "buckets": {task.instance_id: task.bucket for task in tasks},
        "upstream": {
            "package": "@earendil-works/pi-coding-agent",
            "version": UPSTREAM_PACKAGE_VERSION,
        },
        "python": {
            "revision": args.python_revision,
            "source": str(args.python_root),
            "sourceSha256": source_digest(args.python_root / "src"),
        },
        "runnerSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "suiteSha256": hashlib.sha256(args.suite.read_bytes()).hexdigest(),
        "model": asdict(suite.model),
        "budgets": asdict(suite.budgets),
        "container": {
            "engine": args.container_engine,
            "version": engine_version.strip(),
            "cpus": args.cpus,
            "memory": args.memory,
            "images": image_info,
        },
        "host": {"uname": uname.strip()},
        "runPolicy": {
            "repetitions": 1,
            "concurrency": 1,
            "order": "alternating by task",
            "hintsExposed": False,
            "goldPatchExposed": False,
            "testPatchExposed": False,
            "gradingEnvironment": "fresh container plus exported candidate patch",
            "toolPath": AGENT_PATH,
            "inputTokenAccounting": "uncached input + cache reads + cache writes",
            "budgetEnforcement": "post-event observer; not a strict request-admission gate",
        },
    }


async def preflight_instance(
    args: argparse.Namespace,
    task: SuiteTask,
    record: SweRecord,
    output: Path,
) -> PreflightReport:
    empty = await grade_fresh_container(
        args,
        record,
        output / "preflight" / f"{record.instance_id}--empty",
        patch="",
        timeout=task.grade_timeout_seconds,
        label="empty",
    )
    gold = await grade_fresh_container(
        args,
        record,
        output / "preflight" / f"{record.instance_id}--gold",
        patch=record.patch,
        timeout=task.grade_timeout_seconds,
        label="gold",
    )
    reason: str | None = None
    if not empty.valid:
        reason = "empty-patch evaluation was invalid"
    elif empty.resolved:
        reason = "empty patch already resolves the instance"
    elif not gold.valid:
        reason = "gold-patch evaluation was invalid"
    elif not gold.resolved:
        reason = "gold patch did not fully resolve F2P/P2P"
    return PreflightReport(record.instance_id, empty, gold, reason is None, reason)


async def grade_fresh_container(
    args: argparse.Namespace,
    record: SweRecord,
    prefix: Path,
    *,
    patch: str,
    timeout: float,
    label: str,
) -> GradeReport:
    run_dir = prefix.parent / f".{prefix.name}-state"
    run_dir.mkdir(parents=True, exist_ok=False)
    container = container_name(f"preflight-{label}", record.instance_id)
    try:
        await create_container(args, record, container, run_dir, include_key=False)
        if patch:
            applied = await command_capture(
                [
                    args.container_engine,
                    "exec",
                    "-i",
                    "--workdir",
                    "/testbed",
                    container,
                    "git",
                    "apply",
                    "--whitespace=nowarn",
                    "-",
                ],
                input_text=patch,
                check=False,
            )
            if applied.returncode != 0:
                report = invalid_grade(
                    record,
                    f"{label} patch failed to apply: {applied.stderr.strip()}",
                    returncode=applied.returncode,
                )
                write_json(prefix.with_suffix(".json"), report.to_dict())
                return report
        grade, log = await evaluate_container(args, container, record, timeout)
        prefix.with_suffix(".log").write_text(log, encoding="utf-8")
        write_json(prefix.with_suffix(".json"), grade.to_dict())
        return grade
    finally:
        if not args.keep_containers:
            await remove_container(args.container_engine, container)
        remove_tree(run_dir)


async def run_agent(
    args: argparse.Namespace,
    suite: Suite,
    task: SuiteTask,
    record: SweRecord,
    system: SystemName,
    output: Path,
) -> RunReport:
    safe_id = safe_name(record.instance_id)
    run_dir = output / "runs" / f"{system}--{safe_id}-state"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "home").mkdir()
    container = container_name(system, record.instance_id)
    trace_prefix = output / "traces" / system / safe_id
    trace_prefix.parent.mkdir(parents=True, exist_ok=True)
    try:
        if system == "upstream-ts":
            write_upstream_config(
                run_dir / "agent-dir" / "models.json", suite.model, args.api_key_env
            )
        await create_container(args, record, container, run_dir, include_key=True)
        await assert_container_runtime(args, container, system)
        prompt = task_prompt(record)
        command = agent_command(args, suite, system, container, prompt)
        capture = await capture_agent(
            args,
            container,
            command,
            timeout=task.agent_timeout_seconds,
            budgets=suite.budgets,
        )
        # Persist the expensive run before patch extraction or grading can fail.
        write_capture(trace_prefix, capture)
        if capture.timed_out or capture.budget_exceeded or capture.capture_error:
            await ensure_container_running(args.container_engine, container)
        patch, changed_files, changed_test_files = await collect_patch(
            args.container_engine,
            container,
            base_commit=record.base_commit,
        )
        (output / "patches" / system).mkdir(parents=True, exist_ok=True)
        (output / "patches" / system / f"{safe_id}.diff").write_text(
            patch,
            encoding="utf-8",
        )
        # Grade the exported patch, never the agent's modified environment/index.
        grade = await grade_fresh_container(
            args,
            record,
            output / "grades" / system / safe_id,
            patch=patch,
            timeout=task.grade_timeout_seconds,
            label="candidate",
        )
        report = analyze_run(
            system,
            task,
            record,
            capture,
            patch,
            changed_files,
            changed_test_files,
            grade,
        )
        write_json(trace_prefix.with_suffix(".metrics.json"), report.to_dict())
        return report
    finally:
        if not args.keep_containers:
            await remove_container(args.container_engine, container)
        if not args.keep_containers:
            remove_tree(run_dir)


async def create_container(
    args: argparse.Namespace,
    record: SweRecord,
    name: str,
    run_dir: Path,
    *,
    include_key: bool,
) -> None:
    await remove_container(args.container_engine, name)
    command = [
        args.container_engine,
        "run",
        "--detach",
        "--name",
        name,
        "--hostname",
        name,
        "--network",
        "bridge",
        "--cpus",
        str(args.cpus),
        "--memory",
        args.memory,
        "--pids-limit",
        "4096",
        "--env",
        "HOME=/opt/pi-run/home",
        "--env",
        "PYTHONPATH=/opt/pi-python/src",
        "--env",
        "PI_OFFLINE=1",
        "--env",
        "PI_TELEMETRY=0",
        "--env",
        "PI_SKIP_VERSION_CHECK=1",
        "--env",
        "NO_COLOR=1",
        "--env",
        "PYTHONUNBUFFERED=1",
        "--volume",
        f"{args.node_root.resolve()}:/opt/pi-node:ro",
        "--volume",
        f"{args.upstream_root.resolve()}:/opt/pi-upstream:ro",
        "--volume",
        f"{args.python_root.resolve()}:/opt/pi-python:ro",
        "--volume",
        f"{run_dir.resolve()}:/opt/pi-run:rw",
        "--workdir",
        "/testbed",
        "--entrypoint",
        "/bin/bash",
    ]
    if include_key:
        command.extend(["--env", args.api_key_env])
    command.extend(
        [
            normalize_image(record.image),
            "-lc",
            "mkdir -p /opt/pi-run/home && exec sleep infinity",
        ]
    )
    result = await command_capture(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"container start failed for {record.instance_id}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


async def assert_container_runtime(
    args: argparse.Namespace,
    container: str,
    system: SystemName,
) -> None:
    if system == "upstream-ts":
        command = [
            args.container_engine,
            "exec",
            container,
            "/opt/pi-node/bin/node",
            "--version",
        ]
    else:
        command = [
            args.container_engine,
            "exec",
            "--env",
            "PYTHONPATH=/opt/pi-python/src",
            container,
            "/opt/miniconda3/bin/python",
            "-c",
            "import pi_agent; print(pi_agent.__version__)",
        ]
    result = await command_capture(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"{system} runtime is unusable inside the instance image: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def agent_command(
    args: argparse.Namespace,
    suite: Suite,
    system: SystemName,
    container: str,
    prompt: str,
) -> list[str]:
    model = suite.model
    prefix = [
        args.container_engine,
        "exec",
        "--workdir",
        "/testbed",
        "--env",
        "HOME=/opt/pi-run/home",
        container,
    ]
    if system == "upstream-ts":
        cli = "/opt/pi-upstream/" + UPSTREAM_CLI_RELATIVE.as_posix()
        return [
            *prefix,
            "/usr/bin/env",
            f"PATH={AGENT_PATH}",
            "PI_CODING_AGENT_DIR=/opt/pi-run/agent-dir",
            "/opt/pi-node/bin/node",
            cli,
            "--mode",
            "json",
            "--provider",
            model.provider,
            "--model",
            model.model_id,
            "--thinking",
            model.thinking,
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
            prompt,
        ]
    return [
        *prefix,
        "/usr/bin/env",
        "PYTHONPATH=/opt/pi-python/src",
        f"PATH={AGENT_PATH}",
        "/opt/miniconda3/bin/python",
        "-m",
        "pi_agent",
        "--mode",
        "json",
        "--cwd",
        "/testbed",
        "--provider",
        model.provider,
        "--model",
        model.model_id,
        "--base-url",
        model.base_url,
        "--context-window",
        str(model.context_window),
        "--max-tokens",
        str(model.max_tokens),
        "--thinking",
        model.thinking,
        "--sampling-params",
        json.dumps({"enable_thinking": model.thinking != "off"}),
        "--no-session",
        "--no-extensions",
        "--approval",
        "off",
        "-p",
        prompt,
    ]


async def capture_agent(
    args: argparse.Namespace,
    container: str,
    command: list[str],
    *,
    timeout: float,
    budgets: BudgetConfig,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> Capture:
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
    stderr_parts: list[str] = []
    timed_events: list[dict[str, Any]] = []
    invalid: list[str] = []
    tracker = BudgetTracker(budgets)
    stop_task: asyncio.Task[None] | None = None
    capture_error: str | None = None

    def request_stop() -> None:
        nonlocal stop_task
        if stop_task is None:
            stop_task = asyncio.create_task(
                asyncio.wait_for(stop_container(args.container_engine, container), timeout=15)
            )

    def consume_line(raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace")
        stdout_lines.append(line)
        stripped = line.strip()
        if not stripped:
            return
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            invalid.append(stripped)
            return
        if not isinstance(event, dict):
            invalid.append(stripped)
            return
        elapsed = (time.perf_counter() - started) * 1000
        timed_events.append({"elapsedMs": elapsed, "event": event})
        if tracker.observe(event):
            request_stop()

    async def read_stdout() -> None:
        nonlocal capture_error
        pending = bytearray()
        try:
            # read() avoids asyncio.readline()'s default 64 KiB event limit.
            while raw := await stdout.read(65536):
                pending.extend(raw)
                while (end := pending.find(b"\n")) >= 0:
                    line = bytes(pending[: end + 1])
                    del pending[: end + 1]
                    consume_line(line)
                if len(pending) > 32 * 1024 * 1024:
                    raise ValueError("Trace event exceeds the explicit 32 MiB safety limit")
            if pending:
                consume_line(bytes(pending))
        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {exc}"
            request_stop()
            # Continue draining so a full pipe cannot deadlock process.wait().
            if pending:
                stdout_lines.append(pending.decode("utf-8", errors="replace"))
            while raw := await stdout.read(65536):
                stdout_lines.append(raw.decode("utf-8", errors="replace"))

    async def read_stderr() -> None:
        while True:
            raw = await stderr.read(8192)
            if not raw:
                return
            stderr_parts.append(raw.decode("utf-8", errors="replace"))

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    wait_task = asyncio.create_task(process.wait())
    tasks = asyncio.gather(wait_task, stdout_task, stderr_task)
    timed_out = False

    async def terminate() -> None:
        nonlocal capture_error
        request_stop()
        assert stop_task is not None
        try:
            await stop_task
        except Exception as exc:
            capture_error = capture_error or f"container stop failed: {type(exc).__name__}: {exc}"
        if process.returncode is None:
            with contextlib_suppress(ProcessLookupError):
                process.kill()

    try:
        await asyncio.wait_for(asyncio.shield(tasks), timeout=timeout)
    except TimeoutError:
        timed_out = True
        await terminate()
    except asyncio.CancelledError:
        await terminate()
        await asyncio.gather(wait_task, stdout_task, stderr_task, return_exceptions=True)
        await asyncio.gather(tasks, return_exceptions=True)
        raise
    except Exception as exc:
        capture_error = capture_error or f"{type(exc).__name__}: {exc}"
        await terminate()
    finally:
        if stop_task is not None:
            try:
                await stop_task
            except Exception as exc:
                capture_error = capture_error or f"container stop failed: {exc}"
    outcomes = await asyncio.gather(wait_task, stdout_task, stderr_task, return_exceptions=True)
    await asyncio.gather(tasks, return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            capture_error = capture_error or f"{type(outcome).__name__}: {outcome}"
    return Capture(
        command=command,
        returncode=process.returncode or 0,
        duration_ms=(time.perf_counter() - started) * 1000,
        stdout_lines=stdout_lines,
        stderr="".join(stderr_parts),
        timed_events=timed_events,
        invalid_stdout_lines=invalid,
        timed_out=timed_out,
        budget_exceeded=tracker.exceeded,
        capture_error=capture_error,
    )


async def collect_patch(
    engine: str,
    container: str,
    *,
    base_commit: str,
) -> tuple[str, list[str], list[str]]:
    untracked = await command_capture(
        [
            engine,
            "exec",
            "--workdir",
            "/testbed",
            container,
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=False,
        binary=True,
    )
    if untracked.returncode != 0:
        raise RuntimeError(f"cannot enumerate candidate files: {untracked.stderr}")
    if untracked.stdout_bytes:
        selected = [
            item.decode("utf-8", errors="surrogateescape")
            for item in untracked.stdout_bytes.split(b"\0")
            if item and not ignored_artifact(item.decode("utf-8", errors="surrogateescape"))
        ]
        if selected:
            await command_capture(
                [
                    engine,
                    "exec",
                    "--workdir",
                    "/testbed",
                    container,
                    "git",
                    "add",
                    "-N",
                    "--",
                    *selected,
                ],
                check=True,
            )
    patch_result = await command_capture(
        [
            engine,
            "exec",
            "--workdir",
            "/testbed",
            container,
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            base_commit,
            "--",
        ],
        check=True,
    )
    names_result = await command_capture(
        [
            engine,
            "exec",
            "--workdir",
            "/testbed",
            container,
            "git",
            "diff",
            "--name-only",
            base_commit,
            "--",
        ],
        check=True,
    )
    changed = [line for line in names_result.stdout.splitlines() if line.strip()]
    changed_tests = [path for path in changed if is_test_path(path)]
    return patch_result.stdout, changed, changed_tests


async def evaluate_container(
    args: argparse.Namespace,
    container: str,
    record: SweRecord,
    timeout: float,
) -> tuple[GradeReport, str]:
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        args.container_engine,
        "exec",
        "-i",
        "--workdir",
        "/testbed",
        container,
        "/bin/bash",
        "-s",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    timed_out = False
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(record.eval_script.encode("utf-8")),
            timeout=timeout,
        )
    except TimeoutError:
        timed_out = True
        await stop_container(args.container_engine, container)
        with contextlib_suppress(ProcessLookupError):
            process.kill()
        stdout, _ = await process.communicate()
    log = stdout.decode("utf-8", errors="replace")
    duration_ms = (time.perf_counter() - started) * 1000
    report = grade_log(
        record,
        log,
        returncode=process.returncode or 0,
        timed_out=timed_out,
        duration_ms=duration_ms,
    )
    return report, log


def grade_log(
    record: SweRecord,
    log: str,
    *,
    returncode: int,
    timed_out: bool,
    duration_ms: float,
) -> GradeReport:
    if timed_out:
        return invalid_grade(
            record,
            "evaluation timed out",
            returncode=returncode,
            timed_out=True,
            duration_ms=duration_ms,
        )
    output, found = extract_test_output(log)
    if not found:
        return invalid_grade(
            record,
            "test output markers were not found",
            returncode=returncode,
            duration_ms=duration_ms,
        )
    status_map = parse_test_log(output, record.log_parser)
    if not status_map:
        return invalid_grade(
            record,
            f"{record.log_parser} parser found no test statuses",
            returncode=returncode,
            duration_ms=duration_ms,
            test_output_found=True,
        )
    f2p_success = tuple(
        case for case in record.fail_to_pass if test_status(case, status_map) in PASSING
    )
    f2p_failure = tuple(case for case in record.fail_to_pass if case not in f2p_success)
    p2p_success = tuple(
        case for case in record.pass_to_pass if test_status(case, status_map) in MAINTAINED
    )
    p2p_failure = tuple(case for case in record.pass_to_pass if case not in p2p_success)
    resolved = not f2p_failure and not p2p_failure
    message = (
        "resolved"
        if resolved
        else f"F2P {len(f2p_success)}/{len(record.fail_to_pass)}, "
        f"P2P {len(p2p_success)}/{len(record.pass_to_pass)}"
    )
    return GradeReport(
        instance_id=record.instance_id,
        valid=True,
        resolved=resolved,
        f2p_success=f2p_success,
        f2p_failure=f2p_failure,
        p2p_success=p2p_success,
        p2p_failure=p2p_failure,
        status_map=status_map,
        test_output_found=True,
        timed_out=False,
        returncode=returncode,
        duration_ms=duration_ms,
        message=message,
    )


def invalid_grade(
    record: SweRecord,
    message: str,
    *,
    returncode: int,
    timed_out: bool = False,
    duration_ms: float = 0.0,
    test_output_found: bool = False,
) -> GradeReport:
    return GradeReport(
        instance_id=record.instance_id,
        valid=False,
        resolved=False,
        f2p_success=(),
        f2p_failure=record.fail_to_pass,
        p2p_success=(),
        p2p_failure=record.pass_to_pass,
        status_map={},
        test_output_found=test_output_found,
        timed_out=timed_out,
        returncode=returncode,
        duration_ms=duration_ms,
        message=message,
    )


def parse_test_log(log: str, parser: str) -> dict[str, str]:
    if parser in {"pytest", "parse_log_pytest"}:
        return parse_log_pytest(log)
    if parser in {"sympy", "parse_log_sympy"}:
        return parse_log_sympy(log)
    raise ValueError(f"unsupported log parser in SWE-5 suite: {parser}")


def parse_log_pytest(log: str) -> dict[str, str]:
    status_map: dict[str, str] = {}
    skip_count = re.compile(r"^\[\d+\]$")
    for raw in log.splitlines():
        line = raw.strip("\r")
        if not any(line.startswith(status) for status in TEST_STATUSES):
            continue
        if line.startswith("FAILED"):
            line = line.replace(" - ", " ")
        parts = line.split()
        if len(parts) <= 1:
            continue
        if parts[0] == "SKIPPED" and skip_count.match(parts[1]):
            continue
        status_map[parts[1]] = parts[0]
    return status_map


def parse_log_sympy(log: str) -> dict[str, str]:
    status_map: dict[str, str] = {}
    for match in re.findall(r"(_*) (.*)\.py:(.*) (_*)", log):
        status_map[f"{match[1]}.py:{match[2]}"] = "FAILED"
    for raw in log.splitlines():
        line = raw.strip()
        if not line.startswith("test_"):
            continue
        if line.endswith(" E"):
            status_map[line.split()[0]] = "ERROR"
        elif line.endswith(" F"):
            status_map[line.split()[0]] = "FAILED"
        elif line.endswith(" ok"):
            status_map[line.split()[0]] = "PASSED"
    return status_map


def test_status(case: str, status_map: dict[str, str]) -> str | None:
    if case in status_map:
        return status_map[case]
    if case.count("[") > case.count("]"):
        matches = [key for key in status_map if key.startswith(case)]
        outcomes = {status_map[key] for key in matches}
        if matches and len(outcomes) == 1:
            return status_map[matches[0]]
    return None


def extract_test_output(log: str) -> tuple[str, bool]:
    if START_TEST_OUTPUT not in log or END_TEST_OUTPUT not in log:
        return log, False
    return log.split(START_TEST_OUTPUT, 1)[1].split(END_TEST_OUTPUT, 1)[0], True


def analyze_run(
    system: SystemName,
    task: SuiteTask,
    record: SweRecord,
    capture: Capture,
    patch: str,
    changed_files: list[str],
    changed_test_files: list[str],
    grade: GradeReport,
) -> RunReport:
    events = [item["event"] for item in capture.timed_events]
    assistant_messages = [
        event.get("message")
        for event in events
        if event.get("type") == "message_end"
        and isinstance(event.get("message"), dict)
        and event["message"].get("role") == "assistant"
    ]
    usages = [message.get("usage", {}) for message in assistant_messages]
    timed_tools = [
        item for item in capture.timed_events if item["event"].get("type") == "tool_execution_start"
    ]
    tool_ends = [event for event in events if event.get("type") == "tool_execution_end"]
    tool_sequence = [tool_name(item["event"]) for item in timed_tools]
    tool_batches = [
        names for message in assistant_messages if (names := message_tool_names(message))
    ]
    first_mutation_index = next(
        (
            index
            for index, item in enumerate(timed_tools)
            if tool_name(item["event"]) in {"edit", "write"}
        ),
        None,
    )
    first_mutation_ms = (
        timed_tools[first_mutation_index]["elapsedMs"] if first_mutation_index is not None else None
    )
    test_indices = [
        index
        for index, item in enumerate(timed_tools)
        if tool_name(item["event"]) == "bash"
        and is_test_command(tool_arguments(item["event"]).get("command"))
    ]
    first_test_ms = timed_tools[test_indices[0]]["elapsedMs"] if test_indices else None
    baseline = bool(
        test_indices and (first_mutation_index is None or test_indices[0] < first_mutation_index)
    )
    verification = bool(
        first_mutation_index is not None
        and any(index > first_mutation_index for index in test_indices)
    )
    read_paths = [
        str(tool_arguments(item["event"]).get("path", ""))
        for item in timed_tools
        if tool_name(item["event"]) == "read"
    ]
    nonempty_read_paths = [path for path in read_paths if path]
    correct_read_ms = next(
        (
            item["elapsedMs"]
            for item in timed_tools
            if tool_name(item["event"]) == "read"
            and path_matches_gold(str(tool_arguments(item["event"]).get("path", "")), record)
        ),
        None,
    )
    bash_commands = [
        str(tool_arguments(item["event"]).get("command", ""))
        for item in timed_tools
        if tool_name(item["event"]) == "bash"
    ]
    final_message = assistant_messages[-1] if assistant_messages else {}
    return RunReport(
        system=system,
        instance_id=record.instance_id,
        bucket=task.bucket,
        process_ok=(
            capture.returncode == 0
            and not capture.timed_out
            and capture.budget_exceeded is None
            and capture.capture_error is None
            and not capture.invalid_stdout_lines
            and bool(assistant_messages)
            and final_message.get("stopReason", final_message.get("stop_reason"))
            not in {"error", "aborted"}
        ),
        resolved=grade.resolved,
        returncode=capture.returncode,
        timed_out=capture.timed_out,
        budget_exceeded=capture.budget_exceeded,
        duration_ms=capture.duration_ms,
        grade_duration_ms=grade.duration_ms,
        time_to_first_event_ms=(
            capture.timed_events[0]["elapsedMs"] if capture.timed_events else None
        ),
        time_to_first_text_ms=next(
            (
                item["elapsedMs"]
                for item in capture.timed_events
                if assistant_update_type(item["event"]) == "text_delta"
            ),
            None,
        ),
        time_to_first_tool_ms=(timed_tools[0]["elapsedMs"] if timed_tools else None),
        time_to_first_mutation_ms=first_mutation_ms,
        time_to_first_test_ms=first_test_ms,
        model_calls=len(assistant_messages),
        input_tokens=sum(prompt_token_count(usage) for usage in usages if isinstance(usage, dict)),
        output_tokens=sum(
            integer(usage.get("output")) for usage in usages if isinstance(usage, dict)
        ),
        total_tokens=sum(
            integer(usage.get("totalTokens", usage.get("total_tokens")))
            for usage in usages
            if isinstance(usage, dict)
        ),
        tool_calls=len(timed_tools),
        tool_errors=sum(
            bool(event.get("isError", event.get("is_error", False))) for event in tool_ends
        ),
        tool_sequence=tool_sequence,
        tool_batches=tool_batches,
        action_sequence=action_sequence(timed_tools),
        bash_commands=bash_commands,
        baseline_test_before_edit=baseline,
        verification_test_after_edit=verification,
        first_correct_file_read_ms=correct_read_ms,
        unique_read_files=len(set(nonempty_read_paths)),
        repeated_reads=max(
            0,
            len(nonempty_read_paths) - len(set(nonempty_read_paths)),
        ),
        event_count=len(events),
        trace_bytes=sum(len(line.encode("utf-8")) for line in capture.stdout_lines),
        patch_bytes=len(patch.encode("utf-8")),
        patch_changed_lines=patch_changed_lines(patch),
        changed_files=changed_files,
        changed_test_files=changed_test_files,
        final_text=message_text(final_message),
        final_stop_reason=(
            str(final_message.get("stopReason", final_message.get("stop_reason")))
            if isinstance(final_message, dict)
            and final_message.get("stopReason", final_message.get("stop_reason")) is not None
            else None
        ),
        grade=grade,
        stderr=capture.stderr,
        invalid_stdout_lines=capture.invalid_stdout_lines,
        capture_error=capture.capture_error,
        cache_read_tokens=sum(
            integer(usage.get("cacheRead")) for usage in usages if isinstance(usage, dict)
        ),
        cache_write_tokens=sum(
            integer(usage.get("cacheWrite")) for usage in usages if isinstance(usage, dict)
        ),
    )


def build_summary(
    metadata: dict[str, Any],
    suite: Suite,
    tasks: Sequence[SuiteTask],
    preflights: dict[str, PreflightReport],
    runs: Sequence[RunReport],
) -> dict[str, Any]:
    by_system: dict[str, Any] = {}
    for system in ("upstream-ts", "python"):
        values = [run for run in runs if run.system == system]
        if not values:
            continue
        resolved = [run for run in values if run.resolved]
        by_system[system] = {
            "tasks": len(values),
            "resolved": len(resolved),
            "processFailures": sum(not run.process_ok for run in values),
            "timeouts": sum(run.timed_out for run in values),
            "budgetExceeded": sum(run.budget_exceeded is not None for run in values),
            "inputTokens": sum(run.input_tokens for run in values),
            "outputTokens": sum(run.output_tokens for run in values),
            "modelCalls": sum(run.model_calls for run in values),
            "toolCalls": sum(run.tool_calls for run in values),
            "traceBytes": sum(run.trace_bytes for run in values),
            "medianDurationMs": statistics.median(run.duration_ms for run in values),
            "baselineTestRate": sum(run.baseline_test_before_edit for run in values) / len(values),
            "verificationTestRate": sum(run.verification_test_after_edit for run in values)
            / len(values),
            "tokensPerResolved": (
                sum(run.total_tokens for run in values) / len(resolved) if resolved else None
            ),
        }
    pair_results: list[dict[str, Any]] = []
    for task in tasks:
        pair = {run.system: run for run in runs if run.instance_id == task.instance_id}
        if not pair:
            continue
        upstream = pair.get("upstream-ts")
        python = pair.get("python")
        pair_results.append(
            {
                "instanceId": task.instance_id,
                "bucket": task.bucket,
                "upstreamResolved": upstream.resolved if upstream else None,
                "pythonResolved": python.resolved if python else None,
                "firstDivergence": first_divergence(upstream, python),
            }
        )
    return {
        "metadata": metadata,
        "suite": {
            "name": suite.name,
            "tasks": [asdict(task) for task in tasks],
        },
        "preflight": {key: value.to_dict() for key, value in preflights.items()},
        "runs": [run.to_dict() for run in runs],
        "aggregates": by_system,
        "pairs": pair_results,
    }


def render_report(summary: dict[str, Any]) -> str:
    metadata = summary["metadata"]
    runs = summary["runs"]
    lines = [
        "# SWE-5 pi Harness diagnostic",
        "",
        f"Generated: `{metadata['createdAt']}`",
        "",
        "This is a five-instance paired diagnostic, not a leaderboard estimate. Each "
        "instance was run once per Harness with the same model and resource budget.",
        "",
        "## Configuration",
        "",
        f"- Dataset: `{metadata['dataset']}` @ `{metadata['datasetRevision']}`",
        f"- Model: `{metadata['model']['provider']}/{metadata['model']['model_id']}`",
        f"- Context: `{metadata['model']['context_window']}`; "
        f"max output: `{metadata['model']['max_tokens']}`",
        f"- Upstream: `@earendil-works/pi-coding-agent@{metadata['upstream']['version']}`",
        f"- Python revision: `{metadata['python']['revision']}`",
        "- Repetitions: `1`; concurrency: `1`; task order alternates by Harness.",
        "",
        "## Gold / empty preflight",
        "",
        "| Instance | Empty resolved | Gold resolved | Accepted |",
        "|---|---:|---:|---:|",
    ]
    for instance_id, value in summary.get("preflight", {}).items():
        lines.append(
            f"| `{instance_id}` | {yes_no(value['empty']['resolved'])} | "
            f"{yes_no(value['gold']['resolved'])} | {yes_no(value['accepted'])} |"
        )
    lines.extend(
        [
            "",
            "## Paired results",
            "",
            "| Harness | Instance | Bucket | Resolved | F2P | P2P | Wall s | Calls | Tools | "
            "Input tok | Output tok | Patch lines | Baseline test | "
            "Verify after edit | Trace KiB |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in runs:
        grade = run["grade"]
        lines.append(
            f"| {run['system']} | `{run['instance_id']}` | {run['bucket']} | "
            f"{yes_no(run['resolved'])} | {grade['f2pRate']:.0%} | {grade['p2pRate']:.0%} | "
            f"{run['duration_ms'] / 1000:.1f} | {run['model_calls']} | {run['tool_calls']} | "
            f"{run['input_tokens']} | {run['output_tokens']} | {run['patch_changed_lines']} | "
            f"{yes_no(run['baseline_test_before_edit'])} | "
            f"{yes_no(run['verification_test_after_edit'])} | {run['trace_bytes'] / 1024:.1f} |"
        )
    lines.extend(["", "## Aggregate", ""])
    lines.extend(
        [
            "| Harness | Resolved | Input tok | Output tok | Model calls | Tool calls | "
            "Median wall s | Baseline test | Verify after edit | Trace MiB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for system, value in summary["aggregates"].items():
        lines.append(
            f"| {system} | {value['resolved']}/{value['tasks']} | {value['inputTokens']} | "
            f"{value['outputTokens']} | {value['modelCalls']} | {value['toolCalls']} | "
            f"{value['medianDurationMs'] / 1000:.1f} | {value['baselineTestRate']:.0%} | "
            f"{value['verificationTestRate']:.0%} | {value['traceBytes'] / 1048576:.2f} |"
        )
    lines.extend(["", "## First trace divergence", ""])
    for pair in summary["pairs"]:
        lines.append(f"- `{pair['instanceId']}` ({pair['bucket']}): {pair['firstDivergence']}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- Raw and timed traces: `traces/<system>/`",
            "- Normalized per-run metrics: `runs/` and `traces/<system>/*.metrics.json`",
            "- Model patches: `patches/<system>/`",
            "- Evaluation logs/reports: `grades/<system>/`",
            "- Per-task paired timelines: `pairs/`",
            "- Machine-readable aggregate: `summary.json`",
            "",
        ]
    )
    return "\n".join(lines)


def render_preflight_report(
    suite: Suite,
    tasks: Sequence[SuiteTask],
    reports: dict[str, PreflightReport],
) -> str:
    lines = [
        "# SWE-5 preflight",
        "",
        f"Suite: `{suite.name}`",
        "",
        "| Instance | Bucket | Empty valid | Empty resolved | Gold valid | "
        "Gold resolved | Accepted |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for task in tasks:
        value = reports[task.instance_id]
        lines.append(
            f"| `{task.instance_id}` | {task.bucket} | {yes_no(value.empty.valid)} | "
            f"{yes_no(value.empty.resolved)} | {yes_no(value.gold.valid)} | "
            f"{yes_no(value.gold.resolved)} | {yes_no(value.accepted)} |"
        )
        if value.rejection_reason:
            lines.append(f"|  |  |  |  |  | Reason | {value.rejection_reason} |")
    return "\n".join(lines) + "\n"


def write_pair_reports(
    output: Path,
    tasks: Sequence[SuiteTask],
    runs: Sequence[RunReport],
) -> None:
    for task in tasks:
        pair = {run.system: run for run in runs if run.instance_id == task.instance_id}
        upstream = pair.get("upstream-ts")
        python = pair.get("python")
        if upstream is None or python is None:
            continue
        lines = [
            f"# Trace pair: {task.instance_id}",
            "",
            f"Bucket: `{task.bucket}`",
            "",
            f"First divergence: {first_divergence(upstream, python)}",
            "",
            "## Outcome",
            "",
            "| Harness | Resolved | F2P | P2P | Tokens | Wall s |",
            "|---|---:|---:|---:|---:|---:|",
            f"| upstream-ts | {yes_no(upstream.resolved)} | {upstream.grade.f2p_rate:.0%} | "
            f"{upstream.grade.p2p_rate:.0%} | {upstream.total_tokens} | "
            f"{upstream.duration_ms / 1000:.1f} |",
            f"| python | {yes_no(python.resolved)} | {python.grade.f2p_rate:.0%} | "
            f"{python.grade.p2p_rate:.0%} | {python.total_tokens} | "
            f"{python.duration_ms / 1000:.1f} |",
            "",
            "## Actions",
            "",
            "| Step | Upstream TS | Python |",
            "|---:|---|---|",
        ]
        maximum = max(len(upstream.action_sequence), len(python.action_sequence))
        for index in range(maximum):
            left = upstream.action_sequence[index] if index < len(upstream.action_sequence) else "—"
            right = python.action_sequence[index] if index < len(python.action_sequence) else "—"
            lines.append(f"| {index + 1} | `{escape_table(left)}` | `{escape_table(right)}` |")
        lines.extend(
            [
                "",
                "## Patch",
                "",
                f"- Upstream files: `{upstream.changed_files}`; changed lines: "
                f"`{upstream.patch_changed_lines}`.",
                f"- Python files: `{python.changed_files}`; changed lines: "
                f"`{python.patch_changed_lines}`.",
                "",
            ]
        )
        (output / "pairs" / f"{safe_name(task.instance_id)}.md").write_text(
            "\n".join(lines),
            encoding="utf-8",
        )


def first_divergence(
    upstream: RunReport | None,
    python: RunReport | None,
) -> str:
    if upstream is None or python is None:
        return "one Harness result is missing"
    for index, (left, right) in enumerate(
        zip(upstream.action_sequence, python.action_sequence, strict=False)
    ):
        if left != right:
            return f"step {index + 1}: upstream `{left}` vs Python `{right}`"
    if len(upstream.action_sequence) != len(python.action_sequence):
        shorter = min(len(upstream.action_sequence), len(python.action_sequence))
        left = (
            upstream.action_sequence[shorter]
            if shorter < len(upstream.action_sequence)
            else "<end>"
        )
        right = (
            python.action_sequence[shorter] if shorter < len(python.action_sequence) else "<end>"
        )
        return f"step {shorter + 1}: upstream `{left}` vs Python `{right}`"
    if upstream.resolved != python.resolved:
        return (
            f"same Tool names/arguments, outcome differs ({upstream.resolved} vs {python.resolved})"
        )
    return "no Tool-name/argument divergence; compare Tool results and model events"


def action_sequence(timed_tools: Sequence[dict[str, Any]]) -> list[str]:
    """Keep complete tool arguments; a coarse category cannot locate divergence."""
    return [
        (tool_name(item["event"]) or "unknown-tool")
        + ":"
        + json.dumps(
            tool_arguments(item["event"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in timed_tools
    ]


def task_prompt(record: SweRecord) -> str:
    return (
        "Resolve the following repository issue in the current /testbed checkout.\n\n"
        "Rules:\n"
        "- Inspect the repository before editing.\n"
        "- Do not modify tests, benchmark metadata, or dependency lock files to hide failures.\n"
        "- Run a relevant reproduction or baseline test when practical.\n"
        "- Make the smallest correct source change.\n"
        "- Run targeted tests after the change and a reasonable regression check.\n"
        "- Tests use the preinstalled testbed environment; python on PATH selects it.\n"
        "- Do not use the network or install new dependencies.\n"
        "- Finish with a concise summary only after the patch is verified.\n\n"
        "Issue:\n"
        f"{record.problem_statement.strip()}"
    )


def write_upstream_config(path: Path, model: ModelConfig, api_key_env: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "providers": {
                model.provider: {
                    "baseUrl": model.base_url,
                    "api": "openai-completions",
                    "apiKey": f"${api_key_env}",
                    "authHeader": True,
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": False,
                    },
                    "models": [
                        {
                            "id": model.model_id,
                            "name": model.model_id,
                            "reasoning": False,
                            "input": ["text"],
                            "contextWindow": model.context_window,
                            "maxTokens": model.max_tokens,
                            "samplingParams": {"enable_thinking": model.thinking != "off"},
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


def write_capture(prefix: Path, capture: Capture) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".jsonl").write_text("".join(capture.stdout_lines), encoding="utf-8")
    prefix.with_suffix(".timed.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            for item in capture.timed_events
        ),
        encoding="utf-8",
    )
    prefix.with_suffix(".stderr.txt").write_text(capture.stderr, encoding="utf-8")
    write_json(
        prefix.with_suffix(".capture.json"),
        {
            "command": redact_command(capture.command),
            "returncode": capture.returncode,
            "durationMs": capture.duration_ms,
            "timedOut": capture.timed_out,
            "budgetExceeded": capture.budget_exceeded,
            "captureError": capture.capture_error,
            "invalidStdoutLines": capture.invalid_stdout_lines,
        },
    )


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: bytes = b""


async def command_capture(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    binary: bool = False,
) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(
        input_text.encode("utf-8") if input_text is not None else None
    )
    result = CommandResult(
        returncode=process.returncode or 0,
        stdout="" if binary else stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        stdout_bytes=stdout if binary else b"",
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {shlex.join(command)}\n"
            f"{result.stderr or result.stdout}"
        )
    return result


async def command_text(command: Sequence[str]) -> str:
    return (await command_capture(command)).stdout


async def stop_container(engine: str, name: str) -> None:
    await command_capture([engine, "stop", "--time", "3", name], check=False)


async def ensure_container_running(engine: str, name: str) -> None:
    inspect = await command_capture(
        [engine, "inspect", name, "--format", "{{.State.Running}}"],
        check=False,
    )
    if inspect.stdout.strip() != "true":
        result = await command_capture([engine, "start", name], check=False)
        if result.returncode != 0:
            raise RuntimeError(f"could not restart stopped container {name}: {result.stderr}")


async def remove_container(engine: str, name: str) -> None:
    await command_capture([engine, "rm", "--force", name], check=False)


def normalize_image(value: str) -> str:
    return (
        value
        if "/" in value.split("/", 1)[0] or value.startswith("docker.io/")
        else f"docker.io/{value}"
    )


def container_name(prefix: str, instance_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}:{instance_id}".encode()).hexdigest()[:10]
    return f"pi-swe5-{safe_name(prefix)}-{safe_name(instance_id)[:30]}-{digest}"


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")


def patch_files(patch: str) -> tuple[str, ...]:
    result: list[str] = []
    for line in patch.splitlines():
        match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if match:
            result.append(match.group(2))
    return tuple(dict.fromkeys(result))


def patch_changed_lines(patch: str) -> int:
    total = 0
    for line in patch.splitlines():
        if line.startswith(("+++ ", "--- ")):
            continue
        if line.startswith(("+", "-")):
            total += 1
    return total


def is_test_path(path: str) -> bool:
    normalized = f"/{path.casefold()}/"
    name = Path(path).name.casefold()
    return (
        "/tests/" in normalized
        or "/testing/" in normalized
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def ignored_artifact(path: str) -> bool:
    parts = Path(path).parts
    ignored = {".git", ".pi", ".pytest_cache", "__pycache__", ".mypy_cache"}
    return any(part in ignored for part in parts) or Path(path).suffix in {".pyc", ".pyo"}


def path_matches_gold(path: str, record: SweRecord) -> bool:
    normalized = path.removeprefix("/testbed/").lstrip("./")
    return any(
        normalized == gold or normalized.endswith("/" + gold) for gold in record.gold_source_files
    )


def tool_name(event: dict[str, Any]) -> str:
    return str(event.get("toolName", event.get("tool_name", "")))


def tool_arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("args", event.get("arguments", {}))
    return value if isinstance(value, dict) else {}


def message_tool_names(message: Any) -> list[str]:
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


def message_text(message: Any) -> str:
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


def assistant_update_type(event: dict[str, Any]) -> str | None:
    value = event.get("assistantMessageEvent", event.get("assistant_message_event"))
    if not isinstance(value, dict):
        return None
    subtype = value.get("type")
    return str(subtype) if subtype is not None else None


def is_test_command(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(
        re.search(
            r"(^|\s|/)(pytest|py\.test|bin/test|unittest|tox|nox)(\s|$)|python(?:\d+(?:\.\d+)*)?\s+-m\s+(pytest|unittest)",
            value,
        )
    )


def command_head(command: str) -> str:
    compact = re.sub(r"\s+", " ", command.strip())
    return compact[:80] + ("…" if len(compact) > 80 else "")


def short_path(path: str) -> str:
    normalized = path.removeprefix("/testbed/").lstrip("./")
    return normalized or path


def prompt_token_count(usage: dict[str, Any]) -> int:
    """pi usage.input excludes cache reads/writes; all occupy input budget."""
    return sum(integer(usage.get(key)) for key in ("input", "cacheRead", "cacheWrite"))


def integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    return int(value) if isinstance(value, int | float) else 0


def yes_no(value: Any) -> str:
    return "yes" if value else "no"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "'")


def redact_command(command: Sequence[str]) -> list[str]:
    result: list[str] = []
    for item in command:
        if "api_key" in item.casefold() or "authorization" in item.casefold():
            result.append("[REDACTED_ARGUMENT]")
        else:
            result.append(item)
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class contextlib_suppress:
    def __init__(self, *exceptions: type[BaseException]) -> None:
        self.exceptions = exceptions

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        del traceback
        return exc_type is not None and issubclass(exc_type, self.exceptions)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
