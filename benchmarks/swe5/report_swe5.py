#!/usr/bin/env python3
# ruff: noqa: E501
# Generated Markdown sentences intentionally remain intact.
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SYSTEMS = ("upstream-ts", "python")


@dataclass(frozen=True, slots=True)
class Behavior:
    tool_sequence: tuple[str, ...]
    decision_sequence: tuple[str, ...]
    unique_reads: int
    repeated_reads: int
    bash_calls: int
    test_calls: int
    failed_bash_calls: int
    edit_calls: int
    baseline_test_before_edit: bool
    test_after_edit: bool
    target_file_read: bool
    first_target_read_ms: float | None
    first_tool_ms: float | None
    first_edit_ms: float | None
    persistence_events: int
    message_update_events: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_sequence": list(self.tool_sequence),
            "decision_sequence": list(self.decision_sequence),
            "unique_reads": self.unique_reads,
            "repeated_reads": self.repeated_reads,
            "bash_calls": self.bash_calls,
            "test_calls": self.test_calls,
            "failed_bash_calls": self.failed_bash_calls,
            "edit_calls": self.edit_calls,
            "baseline_test_before_edit": self.baseline_test_before_edit,
            "test_after_edit": self.test_after_edit,
            "target_file_read": self.target_file_read,
            "first_target_read_ms": self.first_target_read_ms,
            "first_tool_ms": self.first_tool_ms,
            "first_edit_ms": self.first_edit_ms,
            "persistence_events": self.persistence_events,
            "message_update_events": self.message_update_events,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    root = args.results.resolve()
    manifest = json.loads((root / "swe5.json").read_text())
    runs = json.loads((root / "agent-runs.json").read_text())
    grades = json.loads((root / "grades.json").read_text())
    run_by_key = {(item["instance_id"], item["system"]): item for item in runs}
    grade_by_system = {system: grades["systems"][system]["resolved"] for system in SYSTEMS}
    analysis: dict[str, Any] = {
        "schemaVersion": 1,
        "results": str(root),
        "tasks": {},
        "aggregate": {},
    }
    report: list[str] = [
        "# SWE-5 paired Harness diagnostic",
        "",
        "> Five paired, single-run SWE-bench Verified instances. This is a diagnostic sample, not a leaderboard estimate or a statistical significance claim.",
        "",
        "## Configuration",
        "",
        f"- Dataset: `{manifest['dataset']}`",
        f"- Dataset revision: `{manifest.get('datasetRevision')}`",
        "- Model: `dashscope/qwen-plus`, thinking off",
        "- Repetitions: `1`",
        "- Systems: official TypeScript pi and local Python pi",
        "- Gold Patch and Test Patch were unavailable to both agents",
        "",
        "## Outcome",
        "",
        "| Bucket | Instance | TS resolved | Python resolved | TS tokens in/out | Python tokens in/out | TS time | Python time |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    task_rows: list[dict[str, Any]] = []
    for selected in manifest["instances"]:
        instance_id = selected["instance_id"]
        task_analysis: dict[str, Any] = {
            "bucket": selected["bucket"],
            "sourceFiles": selected.get("source_files", []),
            "systems": {},
        }
        behaviors: dict[str, Behavior] = {}
        for system in SYSTEMS:
            run = run_by_key[(instance_id, system)]
            trace_path = root / "runs" / safe_name(instance_id) / system / "normalized.jsonl"
            records = read_jsonl(trace_path)
            behavior = analyze_behavior(records, selected.get("source_files", []))
            behaviors[system] = behavior
            resolved = grade_by_system[system].get(instance_id)
            task_analysis["systems"][system] = {
                "run": run,
                "resolved": resolved,
                "behavior": behavior.to_dict(),
            }
        divergence = first_divergence(
            behaviors["upstream-ts"].decision_sequence,
            behaviors["python"].decision_sequence,
        )
        task_analysis["firstDivergence"] = divergence
        analysis["tasks"][instance_id] = task_analysis
        ts = run_by_key[(instance_id, "upstream-ts")]
        py = run_by_key[(instance_id, "python")]
        ts_resolved = grade_by_system["upstream-ts"].get(instance_id)
        py_resolved = grade_by_system["python"].get(instance_id)
        report.append(
            "| {bucket} | `{instance}` | {ts_result} | {py_result} | {tsi}/{tso} | {pyi}/{pyo} | {tst:.1f}s | {pyt:.1f}s |".format(
                bucket=selected["bucket"],
                instance=instance_id,
                ts_result=display_bool(ts_resolved),
                py_result=display_bool(py_resolved),
                tsi=ts["input_tokens"],
                tso=ts["output_tokens"],
                pyi=py["input_tokens"],
                pyo=py["output_tokens"],
                tst=ts["wall_seconds"],
                pyt=py["wall_seconds"],
            )
        )
        task_rows.append(
            {
                "instance_id": instance_id,
                "bucket": selected["bucket"],
                "ts_resolved": ts_resolved,
                "python_resolved": py_resolved,
                "ts": ts,
                "python": py,
                "behaviors": behaviors,
                "divergence": divergence,
            }
        )
        write_timeline(root, selected, behaviors, records_by_system(root, instance_id))

    aggregate = aggregate_results(task_rows)
    analysis["aggregate"] = aggregate
    report.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Official TypeScript pi resolved: **{aggregate['resolved']['upstream-ts']}/5**",
            f"- Python pi resolved: **{aggregate['resolved']['python']}/5**",
            f"- Paired outcomes (TS win / tie / Python win): **{aggregate['paired']['tsWins']} / {aggregate['paired']['ties']} / {aggregate['paired']['pythonWins']}**",
            f"- Median input tokens — TS: **{format_number(aggregate['median']['upstream-ts']['input_tokens'])}**, Python: **{format_number(aggregate['median']['python']['input_tokens'])}**",
            f"- Median wall time — TS: **{aggregate['median']['upstream-ts']['wall_seconds']:.2f}s**, Python: **{aggregate['median']['python']['wall_seconds']:.2f}s**",
            f"- Median Tool calls — TS: **{aggregate['median']['upstream-ts']['tool_calls']:.1f}**, Python: **{aggregate['median']['python']['tool_calls']:.1f}**",
            "",
            "## Per-task trace diagnosis",
            "",
        ]
    )
    for row in task_rows:
        report.extend(task_section(row))
    report.extend(
        [
            "## Interpretation",
            "",
            interpret(aggregate, task_rows),
            "",
            "## Artifacts",
            "",
            "- `runs/<instance>/<system>/raw.jsonl`: original Harness event stream",
            "- `runs/<instance>/<system>/timed.jsonl`: original events with local elapsed time",
            "- `runs/<instance>/<system>/normalized.jsonl`: common event schema",
            "- `runs/<instance>/<system>/patch.diff`: submitted Patch",
            "- `timelines/<instance>.md`: paired Tool timeline and first divergence",
            "- `predictions.*.jsonl`: official SWE-bench prediction files",
            "- `grades.json`: official Grader output summary",
            "- `analysis.json`: machine-readable diagnostic analysis",
            "",
        ]
    )
    (root / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True, default=json_default) + "\n"
    )
    (root / "report.md").write_text("\n".join(report) + "\n")
    print(root / "report.md")
    return 0


def analyze_behavior(records: list[dict[str, Any]], target_files: Iterable[str]) -> Behavior:
    tools: list[str] = []
    decisions: list[str] = []
    reads: list[str] = []
    bash_calls = 0
    test_calls = 0
    failed_bash = 0
    edits = 0
    first_tool: float | None = None
    first_edit: float | None = None
    first_target: float | None = None
    first_edit_index: int | None = None
    test_indices: list[int] = []
    targets = set(target_files)
    persistence = 0
    updates = 0
    for index, record in enumerate(records):
        event_type = record.get("type")
        if event_type == "entry_appended":
            persistence += 1
        if event_type == "message_update":
            updates += 1
        if event_type != "tool_execution_start":
            if (
                event_type == "tool_execution_end"
                and record.get("tool") == "bash"
                and (
                    record.get("is_error") is True
                    or (isinstance(record.get("exit_code"), int) and record["exit_code"] != 0)
                )
            ):
                failed_bash += 1
            continue
        tool = str(record.get("tool") or "unknown")
        elapsed = number(record.get("elapsed_ms"))
        first_tool = elapsed if first_tool is None else first_tool
        tools.append(tool)
        target = record.get("target")
        command_kind = record.get("command_kind")
        if tool == "read" and isinstance(target, str):
            reads.append(target)
            decisions.append(f"read:{Path(target).name}")
            if first_target is None and any(
                target.endswith(path) or path.endswith(target) for path in targets
            ):
                first_target = elapsed
        elif tool in {"edit", "write", "patch", "apply_patch"}:
            edits += 1
            first_edit = elapsed if first_edit is None else first_edit
            first_edit_index = index if first_edit_index is None else first_edit_index
            decisions.append(f"edit:{Path(str(target or '')).name}")
        elif tool == "bash":
            bash_calls += 1
            if command_kind == "test":
                test_calls += 1
                test_indices.append(index)
                decisions.append("bash:test")
            else:
                decisions.append("bash:shell")
        else:
            decisions.append(tool)
    repeated_reads = len(reads) - len(set(reads))
    baseline = first_edit_index is not None and any(
        index < first_edit_index for index in test_indices
    )
    test_after = first_edit_index is not None and any(
        index > first_edit_index for index in test_indices
    )
    return Behavior(
        tool_sequence=tuple(tools),
        decision_sequence=tuple(decisions),
        unique_reads=len(set(reads)),
        repeated_reads=repeated_reads,
        bash_calls=bash_calls,
        test_calls=test_calls,
        failed_bash_calls=failed_bash,
        edit_calls=edits,
        baseline_test_before_edit=baseline,
        test_after_edit=test_after,
        target_file_read=first_target is not None,
        first_target_read_ms=first_target,
        first_tool_ms=first_tool,
        first_edit_ms=first_edit,
        persistence_events=persistence,
        message_update_events=updates,
    )


def aggregate_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = {
        system: sum(
            row[f"{'ts' if system == 'upstream-ts' else 'python'}_resolved"] is True for row in rows
        )
        for system in SYSTEMS
    }
    paired = {"tsWins": 0, "ties": 0, "pythonWins": 0}
    for row in rows:
        left = row["ts_resolved"]
        right = row["python_resolved"]
        if left is True and right is not True:
            paired["tsWins"] += 1
        elif right is True and left is not True:
            paired["pythonWins"] += 1
        else:
            paired["ties"] += 1
    median: dict[str, dict[str, float]] = {}
    for system in SYSTEMS:
        values = [row["ts" if system == "upstream-ts" else "python"] for row in rows]
        median[system] = {
            key: statistics.median(float(item[key]) for item in values)
            for key in (
                "input_tokens",
                "output_tokens",
                "model_calls",
                "tool_calls",
                "wall_seconds",
                "trace_bytes",
            )
        }
    consistency: dict[str, Any] = {}
    for system in SYSTEMS:
        behavior_key = system
        behavior = [row["behaviors"][behavior_key] for row in rows]
        consistency[system] = {
            "baselineBeforeEdit": sum(item.baseline_test_before_edit for item in behavior),
            "testAfterEdit": sum(item.test_after_edit for item in behavior),
            "targetFileRead": sum(item.target_file_read for item in behavior),
            "testModificationRuns": sum(
                bool(row["ts" if system == "upstream-ts" else "python"]["tests_modified"])
                for row in rows
            ),
            "budgetOrTimeoutRuns": sum(
                row["ts" if system == "upstream-ts" else "python"]["termination"]
                not in {"completed", "process_error"}
                for row in rows
            ),
        }
    return {"resolved": resolved, "paired": paired, "median": median, "behavior": consistency}


def first_divergence(left: Sequence[str], right: Sequence[str]) -> dict[str, Any]:
    length = max(len(left), len(right))
    for index in range(length):
        left_value = left[index] if index < len(left) else None
        right_value = right[index] if index < len(right) else None
        if left_value != right_value:
            return {"index": index, "upstream-ts": left_value, "python": right_value}
    return {"index": None, "upstream-ts": None, "python": None}


def task_section(row: dict[str, Any]) -> list[str]:
    ts = row["ts"]
    py = row["python"]
    ts_behavior: Behavior = row["behaviors"]["upstream-ts"]
    py_behavior: Behavior = row["behaviors"]["python"]
    divergence = row["divergence"]
    lines = [
        f"### `{row['instance_id']}` — {row['bucket']}",
        "",
        f"- Result: TS `{display_bool(row['ts_resolved'])}`, Python `{display_bool(row['python_resolved'])}`.",
        f"- First divergence at normalized decision {divergence['index']}: TS `{divergence['upstream-ts']}`, Python `{divergence['python']}`.",
        f"- TS Tool path: `{short_sequence(ts_behavior.decision_sequence)}`.",
        f"- Python Tool path: `{short_sequence(py_behavior.decision_sequence)}`.",
        f"- Baseline test before first edit: TS `{ts_behavior.baseline_test_before_edit}`, Python `{py_behavior.baseline_test_before_edit}`.",
        f"- Test after edit: TS `{ts_behavior.test_after_edit}`, Python `{py_behavior.test_after_edit}`.",
        f"- Patch: TS `{len(ts['patch_files'])}` files / `+{ts['patch_added']}/-{ts['patch_deleted']}`, Python `{len(py['patch_files'])}` files / `+{py['patch_added']}/-{py['patch_deleted']}`.",
        "",
    ]
    return lines


def interpret(aggregate: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    ts_resolved = aggregate["resolved"]["upstream-ts"]
    py_resolved = aggregate["resolved"]["python"]
    statements: list[str] = []
    difference = ts_resolved - py_resolved
    if abs(difference) >= 2:
        winner = "official TypeScript pi" if difference > 0 else "Python pi"
        statements.append(
            f"In this five-task paired diagnostic, {winner} resolved at least two more instances, which is a meaningful correctness signal for these selected capabilities."
        )
    elif difference:
        winner = "official TypeScript pi" if difference > 0 else "Python pi"
        statements.append(
            f"{winner} resolved one additional instance. With only five single runs, treat this as a directional signal rather than a general SWE-bench ranking."
        )
    else:
        statements.append(
            "The systems resolved the same number of instances; the useful differences are therefore in efficiency, validation discipline, and the per-task first-divergence traces."
        )
    ts_tokens = aggregate["median"]["upstream-ts"]["input_tokens"]
    py_tokens = aggregate["median"]["python"]["input_tokens"]
    if ts_tokens and py_tokens:
        ratio = py_tokens / ts_tokens
        if ratio <= 0.8:
            statements.append(
                f"Python used {100 * (1 - ratio):.0f}% fewer median input tokens. Check failed-task traces before treating this as a pure efficiency win."
            )
        elif ratio >= 1.2:
            statements.append(
                f"Python used {100 * (ratio - 1):.0f}% more median input tokens, indicating a measurable context/turn overhead in this sample."
            )
    ts_validation = aggregate["behavior"]["upstream-ts"]["testAfterEdit"]
    py_validation = aggregate["behavior"]["python"]["testAfterEdit"]
    if ts_validation != py_validation:
        better = "TypeScript" if ts_validation > py_validation else "Python"
        statements.append(
            f"{better} performed post-edit validation on more tasks ({max(ts_validation, py_validation)}/5 versus {min(ts_validation, py_validation)}/5)."
        )
    timeouts = {system: aggregate["behavior"][system]["budgetOrTimeoutRuns"] for system in SYSTEMS}
    if any(timeouts.values()):
        statements.append(
            f"Budget/timeout terminations occurred (TS {timeouts['upstream-ts']}, Python {timeouts['python']}); those tasks should be interpreted as Harness control failures, not ordinary wrong patches."
        )
    statements.append(
        "Because each pair ran once, online-model nondeterminism remains a confounder. Re-run only the one or two tasks whose first divergence is most consequential before changing the Harness."
    )
    return " ".join(statements)


def records_by_system(root: Path, instance_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        system: read_jsonl(root / "runs" / safe_name(instance_id) / system / "normalized.jsonl")
        for system in SYSTEMS
    }


def write_timeline(
    root: Path,
    selected: dict[str, Any],
    behaviors: dict[str, Behavior],
    records: dict[str, list[dict[str, Any]]],
) -> None:
    timeline_root = root / "timelines"
    timeline_root.mkdir(exist_ok=True)
    divergence = first_divergence(
        behaviors["upstream-ts"].decision_sequence,
        behaviors["python"].decision_sequence,
    )
    lines = [
        f"# {selected['instance_id']}",
        "",
        f"Bucket: `{selected['bucket']}`",
        "",
        f"First divergence: index `{divergence['index']}`, TS `{divergence['upstream-ts']}`, Python `{divergence['python']}`.",
        "",
    ]
    for system in SYSTEMS:
        lines.extend([f"## {system}", "", "| elapsed | event | detail |", "|---:|---|---|"])
        for record in records[system]:
            event_type = record.get("type")
            if event_type not in {
                "agent_start",
                "turn_start",
                "tool_execution_start",
                "tool_execution_end",
                "turn_end",
                "agent_end",
            }:
                continue
            detail = ""
            if event_type.startswith("tool_execution"):
                detail = str(record.get("tool") or "")
                if record.get("target"):
                    detail += f" {record['target']}"
                if record.get("command_kind"):
                    detail += f" [{record['command_kind']}]"
                if record.get("exit_code") is not None:
                    detail += f" exit={record['exit_code']}"
                if record.get("is_error") is not None:
                    detail += f" error={record['is_error']}"
            lines.append(
                f"| {number(record.get('elapsed_ms')) / 1000:.3f}s | `{event_type}` | {escape_pipe(detail)} |"
            )
        lines.append("")
    (timeline_root / f"{safe_name(selected['instance_id'])}.md").write_text("\n".join(lines) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "_.-" else "_" for character in value
    )


def number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def display_bool(value: Any) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"


def format_number(value: float) -> str:
    return f"{value:,.0f}" if math.isfinite(value) else "unknown"


def short_sequence(values: Sequence[str], limit: int = 14) -> str:
    selected = list(values[:limit])
    if len(values) > limit:
        selected.append(f"…(+{len(values) - limit})")
    return " → ".join(selected) or "no tool decisions"


def escape_pipe(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def json_default(value: Any) -> Any:
    if isinstance(value, Behavior):
        return value.to_dict()
    raise TypeError(type(value).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
