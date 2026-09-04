from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent


def load_runner() -> ModuleType:
    path = HERE / "run.py"
    spec = importlib.util.spec_from_file_location("pi_agent_compare_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load benchmark runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild summary.json and report.md from saved raw trace artifacts"
    )
    parser.add_argument("result", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = args.result.expanduser().resolve()
    runner = load_runner()
    summary_path = result / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    old_runs = summary.get("runs")
    if not isinstance(old_runs, list):
        raise ValueError("summary.json does not contain a runs array")

    rebuilt: list[Any] = []
    for old in old_runs:
        if not isinstance(old, dict):
            raise ValueError("summary run must be an object")
        system = str(old["system"])
        task = str(old["task"])
        repetition = int(old["repetition"])
        run_id = f"{task}-r{repetition}"
        trace_dir = result / "traces" / system
        raw_path = trace_dir / f"{run_id}.jsonl"
        timed_path = trace_dir / f"{run_id}.timed.jsonl"
        stderr_path = trace_dir / f"{run_id}.stderr.txt"
        capture = runner.ProcessCapture(
            command=[],
            returncode=int(old["returncode"]),
            duration_ms=float(old["duration_ms"]),
            stdout_lines=raw_path.read_text(encoding="utf-8").splitlines(keepends=True),
            stderr=stderr_path.read_text(encoding="utf-8"),
            timed_events=[
                json.loads(line)
                for line in timed_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ],
            invalid_stdout_lines=list(old.get("invalid_stdout_lines", ())),
            timed_out=bool(old["timed_out"]),
        )
        metrics = runner.analyze_capture(
            system,
            task,
            repetition,
            capture,
            bool(old["validator_ok"]),
            str(old["validation_message"]),
            list(old["changed_files"]),
        )
        rebuilt.append(metrics)
        runner.write_json(trace_dir / f"{run_id}.metrics.json", metrics.to_dict())

    summary["runs"] = [run.to_dict() for run in rebuilt]
    summary["aggregates"] = runner.aggregate_runs(rebuilt)
    runner.write_json(summary_path, summary)
    (result / "report.md").write_text(runner.render_report(summary), encoding="utf-8")
    print(result / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
