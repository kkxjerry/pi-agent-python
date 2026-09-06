#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from preflight import collect_results

SYSTEMS = ("upstream-ts", "python")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()

    results = args.results.resolve()
    manifest = json.loads((results / "swe5.json").read_text())
    instance_ids = [item["instance_id"] for item in manifest["instances"]]
    args.workdir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    grades: dict[str, Any] = {
        "schemaVersion": 1,
        "dataset": manifest["dataset"],
        "datasetRevision": manifest.get("datasetRevision"),
        "systems": {},
    }
    for system in SYSTEMS:
        run_id = f"{results.name}-{system}-{timestamp}"
        log = args.workdir / f"{system}.grader.log"
        command = [
            str(args.python),
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            manifest["dataset"],
            "--predictions_path",
            str(results / f"predictions.{system}.jsonl"),
            "--instance_ids",
            *instance_ids,
            "--max_workers",
            str(args.max_workers),
            "--run_id",
            run_id,
        ]
        environment = os.environ.copy()
        environment.setdefault("DOCKER_BUILDKIT", "1")
        with log.open("wb") as output:
            completed = subprocess.run(
                command,
                cwd=args.workdir,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        resolved = collect_results(args.workdir, run_id, instance_ids)
        details = collect_details(args.workdir, run_id, instance_ids)
        grades["systems"][system] = {
            "runId": run_id,
            "exitCode": completed.returncode,
            "resolved": resolved,
            "details": details,
            "resolvedCount": sum(value is True for value in resolved.values()),
            "unknownCount": sum(value is None for value in resolved.values()),
            "log": str(log),
        }
        if completed.returncode != 0 and all(value is None for value in resolved.values()):
            grades["systems"][system]["fatal"] = tail(log, 100)
    output = results / "grades.json"
    output.write_text(json.dumps(grades, indent=2, sort_keys=True) + "\n")
    valid = all(item["unknownCount"] == 0 for item in grades["systems"].values())
    print(json.dumps(grades, sort_keys=True))
    return 0 if valid else 1


def collect_details(root: Path, run_id: str, instance_ids: list[str]) -> dict[str, Any]:
    details: dict[str, Any] = {instance_id: None for instance_id in instance_ids}
    for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime):
        text_path = str(path)
        if run_id not in text_path:
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for instance_id in instance_ids:
            safe = instance_id.replace("/", "__")
            if instance_id not in text_path and safe not in text_path:
                continue
            extracted = extract_test_status(value, instance_id)
            if extracted is not None:
                details[instance_id] = extracted
    return details


def extract_test_status(value: Any, instance_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        tests = value.get("tests_status") or value.get("testsStatus")
        if isinstance(tests, dict):
            return tests
        if instance_id in value:
            nested = extract_test_status(value[instance_id], instance_id)
            if nested is not None:
                return nested
        for child in value.values():
            nested = extract_test_status(child, instance_id)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = extract_test_status(child, instance_id)
            if nested is not None:
                return nested
    return None


def tail(path: Path, count: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-count:])
    except OSError:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
