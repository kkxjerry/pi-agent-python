#!/usr/bin/env python3
# ruff: noqa: E501
# Generated Markdown sentences intentionally remain intact.
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

SYSTEMS = ("upstream-ts", "python")
DISPLAY = {"upstream-ts": "Official TypeScript pi", "python": "Python pi"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.results.resolve()), encoding="utf-8")
    print(args.output)
    return 0


def render(root: Path) -> str:
    manifest = load(root / "swe5.json")
    metadata = load(root / "metadata.json")
    runs = load(root / "agent-runs.json")
    grades = load(root / "grades.json")
    analysis = load(root / "analysis.json")
    preflight = load(root / "preflight.json")
    selected = manifest["instances"]
    run_by_key = {(item["instance_id"], item["system"]): item for item in runs}
    resolved = {system: grades["systems"][system]["resolved"] for system in SYSTEMS}

    lines = [
        "# SWE-5 Harness diagnostic result",
        "",
        "> Five paired SWE-bench Verified tasks, one run per Harness. This is a low-cost diagnostic, not a leaderboard estimate or a statistical significance claim.",
        "",
        "## Reproducibility",
        "",
        f"- Run ID: `{metadata['runId']}`",
        f"- Dataset: `{manifest['dataset']}`",
        f"- Dataset revision: `{manifest.get('datasetRevision')}`",
        f"- Model: `{metadata['model']['provider']}/{metadata['model']['id']}`; thinking off",
        f"- Gold/Empty preflight valid: `{bool(preflight.get('valid'))}`",
        "- Repetitions: `1` per Harness and task",
        "- Gold Patch, Test Patch, FAIL_TO_PASS, and PASS_TO_PASS were not exposed to either Agent",
        "",
        "## Paired outcome",
        "",
        "| Bucket | Instance | TS | Python | TS input/output | Python input/output | TS model/tool calls | Python model/tool calls | TS time | Python time |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in selected:
        instance_id = item["instance_id"]
        ts = run_by_key[(instance_id, "upstream-ts")]
        py = run_by_key[(instance_id, "python")]
        lines.append(
            "| {bucket} | `{instance}` | {ts_resolved} | {py_resolved} | {tsi:,}/{tso:,} | {pyi:,}/{pyo:,} | {tsm}/{tstool} | {pym}/{pytool} | {ts_time:.2f}s | {py_time:.2f}s |".format(
                bucket=item["bucket"],
                instance=instance_id,
                ts_resolved=yes_no(resolved["upstream-ts"][instance_id]),
                py_resolved=yes_no(resolved["python"][instance_id]),
                tsi=int(ts["input_tokens"]),
                tso=int(ts["output_tokens"]),
                pyi=int(py["input_tokens"]),
                pyo=int(py["output_tokens"]),
                tsm=int(ts["model_calls"]),
                tstool=int(ts["tool_calls"]),
                pym=int(py["model_calls"]),
                pytool=int(py["tool_calls"]),
                ts_time=float(ts["wall_seconds"]),
                py_time=float(py["wall_seconds"]),
            )
        )

    counts = {
        system: sum(value is True for value in resolved[system].values()) for system in SYSTEMS
    }
    outcomes = {"ts": 0, "tie": 0, "python": 0}
    for item in selected:
        instance_id = item["instance_id"]
        left = resolved["upstream-ts"][instance_id]
        right = resolved["python"][instance_id]
        if left and not right:
            outcomes["ts"] += 1
        elif right and not left:
            outcomes["python"] += 1
        else:
            outcomes["tie"] += 1

    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- Resolved: TS **{counts['upstream-ts']}/5**, Python **{counts['python']}/5**.",
            f"- Paired outcome: TS wins **{outcomes['ts']}**, ties **{outcomes['tie']}**, Python wins **{outcomes['python']}**.",
        ]
    )
    for system in SYSTEMS:
        system_runs = [run_by_key[(item["instance_id"], system)] for item in selected]
        lines.append(
            "- {name}: median input **{input_tokens:,.0f}**, output **{output_tokens:,.0f}**, model calls **{model_calls:.1f}**, Tool calls **{tool_calls:.1f}**, wall time **{wall:.2f}s**, Trace **{trace:,.0f} bytes**.".format(
                name=DISPLAY[system],
                input_tokens=median(system_runs, "input_tokens"),
                output_tokens=median(system_runs, "output_tokens"),
                model_calls=median(system_runs, "model_calls"),
                tool_calls=median(system_runs, "tool_calls"),
                wall=median(system_runs, "wall_seconds"),
                trace=median(system_runs, "trace_bytes"),
            )
        )

    lines.extend(["", "## First trace divergence", ""])
    for item in selected:
        instance_id = item["instance_id"]
        divergence = analysis["tasks"][instance_id]["firstDivergence"]
        lines.append(
            "- `{instance}` ({bucket}): decision `{index}` — TS `{left}`, Python `{right}`.".format(
                instance=instance_id,
                bucket=item["bucket"],
                index=divergence.get("index"),
                left=divergence.get("upstream-ts"),
                right=divergence.get("python"),
            )
        )

    behavior = analysis["aggregate"]["behavior"]
    lines.extend(
        [
            "",
            "## Validation behavior",
            "",
            f"- Baseline test before first edit: TS **{behavior['upstream-ts']['baselineBeforeEdit']}/5**, Python **{behavior['python']['baselineBeforeEdit']}/5**.",
            f"- Test after edit: TS **{behavior['upstream-ts']['testAfterEdit']}/5**, Python **{behavior['python']['testAfterEdit']}/5**.",
            f"- Target production file read: TS **{behavior['upstream-ts']['targetFileRead']}/5**, Python **{behavior['python']['targetFileRead']}/5**.",
            f"- Test-modification runs: TS **{behavior['upstream-ts']['testModificationRuns']}**, Python **{behavior['python']['testModificationRuns']}**.",
            f"- Budget/timeout terminations: TS **{behavior['upstream-ts']['budgetOrTimeoutRuns']}**, Python **{behavior['python']['budgetOrTimeoutRuns']}**.",
            "",
            "## Interpretation rule",
            "",
            interpretation(counts, outcomes, run_by_key, selected, behavior),
            "",
            "## Trace locations",
            "",
            "The full local result directory is ignored by Git and contains:",
            "",
            "```text",
            "benchmarks/swe5/results/<run-id>/runs/<instance>/<system>/raw.jsonl",
            "benchmarks/swe5/results/<run-id>/runs/<instance>/<system>/timed.jsonl",
            "benchmarks/swe5/results/<run-id>/runs/<instance>/<system>/normalized.jsonl",
            "benchmarks/swe5/results/<run-id>/runs/<instance>/<system>/patch.diff",
            "benchmarks/swe5/results/<run-id>/timelines/<instance>.md",
            "```",
            "",
            "`raw.jsonl` preserves each native Harness protocol; compare `normalized.jsonl` or the paired timeline for behavior-level analysis.",
            "",
        ]
    )
    return "\n".join(lines)


def interpretation(
    counts: dict[str, int],
    outcomes: dict[str, int],
    runs: dict[tuple[str, str], dict[str, Any]],
    selected: list[dict[str, Any]],
    behavior: dict[str, Any],
) -> str:
    difference = counts["upstream-ts"] - counts["python"]
    if abs(difference) >= 2:
        winner = "official TypeScript pi" if difference > 0 else "Python pi"
        correctness = f"{winner} resolved at least two more selected capabilities, a meaningful correctness signal for this diagnostic set."
    elif difference:
        winner = "official TypeScript pi" if difference > 0 else "Python pi"
        correctness = f"{winner} resolved one more task; this is directional evidence only because every pair ran once."
    else:
        correctness = "Both Harnesses resolved the same number of tasks; compare efficiency and first-divergence traces rather than declaring a correctness winner."
    ts_input = median(
        [runs[(item["instance_id"], "upstream-ts")] for item in selected], "input_tokens"
    )
    py_input = median([runs[(item["instance_id"], "python")] for item in selected], "input_tokens")
    if ts_input > 0:
        delta = (py_input / ts_input - 1) * 100
        efficiency = f"Python median input Token usage was {abs(delta):.0f}% {'higher' if delta > 0 else 'lower'} than TS."
    else:
        efficiency = "Input Token comparison was unavailable."
    validation_delta = (
        behavior["python"]["testAfterEdit"] - behavior["upstream-ts"]["testAfterEdit"]
    )
    if validation_delta:
        validation = f"Python performed post-edit tests on {abs(validation_delta)} {'more' if validation_delta > 0 else 'fewer'} task(s)."
    else:
        validation = "Both Harnesses performed post-edit tests on the same number of tasks."
    return (
        f"{correctness} {efficiency} {validation} "
        "Online-model nondeterminism remains a confounder; rerun only the one or two most consequential divergent tasks before changing the Harness."
    )


def median(rows: list[dict[str, Any]], key: str) -> float:
    return statistics.median(float(row[key]) for row in rows)


def yes_no(value: Any) -> str:
    return "yes" if value is True else "no" if value is False else "unknown"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
