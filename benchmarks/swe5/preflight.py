#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--namespace", default="swe5")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    instance_ids = [item["instance_id"] for item in manifest["instances"]]
    args.workdir.mkdir(parents=True, exist_ok=True)
    predictions = args.workdir / "empty.predictions.jsonl"
    predictions.write_text(
        "".join(
            json.dumps(
                {
                    "instance_id": instance_id,
                    "model_name_or_path": "swe5-empty",
                    "model_patch": "",
                }
            )
            + "\n"
            for instance_id in instance_ids
        )
    )
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    gold_run = f"{args.namespace}-gold-{timestamp}"
    empty_run = f"{args.namespace}-empty-{timestamp}"
    common = [
        str(args.python),
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        manifest["dataset"],
        "--instance_ids",
        *instance_ids,
        "--max_workers",
        str(args.max_workers),
    ]
    run(
        [
            *common,
            "--predictions_path",
            "gold",
            "--run_id",
            gold_run,
        ],
        cwd=args.workdir,
        log=args.workdir / "gold.log",
    )
    run(
        [
            *common,
            "--predictions_path",
            str(predictions),
            "--run_id",
            empty_run,
        ],
        cwd=args.workdir,
        log=args.workdir / "empty.log",
    )

    gold = collect_results(args.workdir, gold_run, instance_ids)
    empty = collect_results(args.workdir, empty_run, instance_ids)
    failed_gold = [instance_id for instance_id in instance_ids if gold.get(instance_id) is not True]
    passed_empty = [
        instance_id for instance_id in instance_ids if empty.get(instance_id) is not False
    ]
    result = {
        "schemaVersion": 1,
        "dataset": manifest["dataset"],
        "datasetRevision": manifest.get("datasetRevision"),
        "goldRunId": gold_run,
        "emptyRunId": empty_run,
        "instanceIds": instance_ids,
        "goldResolved": gold,
        "emptyResolved": empty,
        "valid": not failed_gold and not passed_empty,
        "failedGold": failed_gold,
        "invalidEmpty": passed_empty,
    }
    output = args.workdir / "preflight.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if failed_gold or passed_empty:
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def run(command: list[str], *, cwd: Path, log: Path) -> None:
    environment = os.environ.copy()
    environment.setdefault("DOCKER_BUILDKIT", "1")
    with log.open("wb") as output:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        tail = tail_text(log, 80)
        joined_command = " ".join(command)
        raise SystemExit(
            f"SWE-bench preflight command exited {completed.returncode}: {joined_command}\n{tail}"
        )


def collect_results(root: Path, run_id: str, instance_ids: Iterable[str]) -> dict[str, bool | None]:
    wanted = set(instance_ids)
    result: dict[str, bool | None] = {instance_id: None for instance_id in wanted}
    candidates = sorted(root.rglob("*.json"), key=lambda path: path.stat().st_mtime)
    for path in candidates:
        if run_id not in str(path) and run_id not in path.read_text(errors="ignore")[:4096]:
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        find_results(value, wanted, result)
    # Per-instance report files do not always repeat run_id in their body. Scan
    # unresolved IDs by path name as a second, bounded pass.
    unresolved = {key for key, value in result.items() if value is None}
    for instance_id in unresolved:
        normalized = instance_id.replace("/", "__")
        for path in candidates:
            path_text = str(path)
            if normalized not in path_text and instance_id not in path_text:
                continue
            if run_id not in path_text:
                continue
            try:
                value = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            resolved = extract_resolved(value, instance_id)
            if resolved is not None:
                result[instance_id] = resolved
                break
    return result


def find_results(value: Any, wanted: set[str], result: dict[str, bool | None]) -> None:
    if isinstance(value, dict):
        for instance_id in wanted:
            if instance_id in value:
                resolved = extract_resolved(value[instance_id], instance_id)
                if resolved is not None:
                    result[instance_id] = resolved
        instance_id = value.get("instance_id") or value.get("instanceId")
        if isinstance(instance_id, str) and instance_id in wanted:
            resolved = extract_resolved(value, instance_id)
            if resolved is not None:
                result[instance_id] = resolved
        for child in value.values():
            find_results(child, wanted, result)
    elif isinstance(value, list):
        for child in value:
            find_results(child, wanted, result)


def extract_resolved(value: Any, instance_id: str) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("resolved", "is_resolved", "isResolved"):
        item = value.get(key)
        if isinstance(item, bool):
            return item
    nested = value.get(instance_id)
    if nested is not None and nested is not value:
        return extract_resolved(nested, instance_id)
    tests = value.get("tests_status") or value.get("testsStatus")
    if isinstance(tests, dict):
        fail_to_pass = tests.get("FAIL_TO_PASS") or tests.get("fail_to_pass")
        pass_to_pass = tests.get("PASS_TO_PASS") or tests.get("pass_to_pass")
        if isinstance(fail_to_pass, dict) and isinstance(pass_to_pass, dict):
            failed = fail_to_pass.get("failure", [])
            regressed = pass_to_pass.get("failure", [])
            if isinstance(failed, list) and isinstance(regressed, list):
                return not failed and not regressed
    return None


def tail_text(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
