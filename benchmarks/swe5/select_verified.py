#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PREFERRED_REPOS = (
    "pytest-dev/pytest",
    "sphinx-doc/sphinx",
    "pallets/flask",
    "psf/requests",
    "pydata/xarray",
    "pylint-dev/pylint",
    "django/django",
    "sympy/sympy",
    "astropy/astropy",
    "matplotlib/matplotlib",
    "scikit-learn/scikit-learn",
)
BUCKETS = (
    "localization",
    "surgical_fix",
    "failure_recovery",
    "multi_file_contract",
    "regression_boundary",
)
TEST_MARKERS = ("/test", "/tests", "test_", "_test.py", ".golden", ".snap")
SOURCE_SUFFIXES = (
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".c",
    ".cc",
    ".cpp",
    ".h",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    instance_id: str
    repo: str
    base_commit: str
    bucket: str
    source_files: tuple[str, ...]
    changed_lines: int
    fail_to_pass_count: int
    pass_to_pass_count: int
    problem_chars: int
    statement_mentions_target: bool
    estimated_cost: float

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_files"] = list(self.source_files)
        return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        default="princeton-nlp/SWE-bench_Verified",
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidates-output", type=Path)
    parser.add_argument("--repos", nargs="*")
    parser.add_argument("--max-candidates-per-bucket", type=int, default=8)
    args = parser.parse_args()

    rows, revision = load_rows(args.dataset, args.split)
    candidates = collect_candidates(rows)
    selected_repos = tuple(args.repos or choose_repo_pair(candidates))
    selected, alternatives = choose_five(
        candidates,
        selected_repos,
        max_candidates=args.max_candidates_per_bucket,
    )
    payload = {
        "schemaVersion": 1,
        "dataset": args.dataset,
        "datasetRevision": revision,
        "split": args.split,
        "selectionPolicy": {
            "purpose": "paired low-cost harness diagnostic, not leaderboard scoring",
            "repetitions": 1,
            "buckets": list(BUCKETS),
            "repositories": list(selected_repos),
            "goldPatchVisibleToAgent": False,
            "testPatchVisibleToAgent": False,
        },
        "instances": [candidate.public() for candidate in selected],
        "alternatives": {
            bucket: [candidate.public() for candidate in values]
            for bucket, values in alternatives.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    candidates_output = args.candidates_output
    if candidates_output is not None:
        ranked = sorted(candidates, key=lambda item: (item.estimated_cost, item.instance_id))
        candidates_output.write_text(
            json.dumps(
                {
                    "dataset": args.dataset,
                    "datasetRevision": revision,
                    "countsByRepo": dict(Counter(item.repo for item in candidates)),
                    "countsByBucket": dict(Counter(item.bucket for item in candidates)),
                    "candidates": [item.public() for item in ranked],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    print(
        json.dumps({"selected": [item.instance_id for item in selected], "repos": selected_repos})
    )
    return 0


def load_rows(dataset_name: str, split: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        from datasets import load_dataset
    except ImportError as exc:  # pragma: no cover - exercised by server bootstrap
        raise SystemExit("install the 'datasets' package before selecting SWE instances") from exc
    dataset = load_dataset(dataset_name, split=split)
    rows = [dict(row) for row in dataset]
    revision: str | None = None
    try:
        from huggingface_hub import HfApi

        revision = HfApi().dataset_info(dataset_name).sha
    except Exception:
        revision = getattr(dataset, "_fingerprint", None)
    return rows, revision


def collect_candidates(rows: Iterable[dict[str, Any]]) -> list[Candidate]:
    result: list[Candidate] = []
    for row in rows:
        patch = str(row.get("patch") or "")
        files, changed_lines = patch_metadata(patch)
        source_files = tuple(
            path for path in files if path.endswith(SOURCE_SUFFIXES) and not is_test_path(path)
        )
        if not source_files or len(source_files) > 3:
            continue
        if changed_lines <= 0 or changed_lines > 140:
            continue
        fail_to_pass = parse_tests(row.get("FAIL_TO_PASS"))
        pass_to_pass = parse_tests(row.get("PASS_TO_PASS"))
        if not fail_to_pass:
            continue
        statement = str(row.get("problem_statement") or "")
        if len(statement) < 80 or len(statement) > 18_000:
            continue
        mentions = any(Path(path).name in statement for path in source_files)
        bucket = classify_bucket(
            source_files=source_files,
            changed_lines=changed_lines,
            fail_count=len(fail_to_pass),
            pass_count=len(pass_to_pass),
            statement_mentions_target=mentions,
        )
        if bucket is None:
            continue
        repo = str(row.get("repo") or "")
        instance_id = str(row.get("instance_id") or "")
        base_commit = str(row.get("base_commit") or "")
        if not repo or not instance_id or not base_commit:
            continue
        estimated_cost = (
            changed_lines
            + len(source_files) * 18
            + len(fail_to_pass) * 4
            + min(len(pass_to_pass), 100) * 0.35
            + len(statement) / 500
            + (8 if mentions else 0)
        )
        result.append(
            Candidate(
                instance_id=instance_id,
                repo=repo,
                base_commit=base_commit,
                bucket=bucket,
                source_files=source_files,
                changed_lines=changed_lines,
                fail_to_pass_count=len(fail_to_pass),
                pass_to_pass_count=len(pass_to_pass),
                problem_chars=len(statement),
                statement_mentions_target=mentions,
                estimated_cost=round(estimated_cost, 3),
            )
        )
    return result


def choose_repo_pair(candidates: list[Candidate]) -> tuple[str, str]:
    by_repo_bucket: dict[str, set[str]] = defaultdict(set)
    by_repo_cost: dict[str, list[float]] = defaultdict(list)
    for candidate in candidates:
        by_repo_bucket[candidate.repo].add(candidate.bucket)
        by_repo_cost[candidate.repo].append(candidate.estimated_cost)
    repos = [repo for repo in PREFERRED_REPOS if repo in by_repo_bucket]
    repos.extend(sorted(set(by_repo_bucket) - set(repos)))
    best: tuple[float, tuple[str, str]] | None = None
    priority = {repo: index for index, repo in enumerate(PREFERRED_REPOS)}
    for left, right in itertools.combinations(repos, 2):
        union = by_repo_bucket[left] | by_repo_bucket[right]
        if not set(BUCKETS) <= union:
            continue
        coverage = sum(
            1
            for bucket in BUCKETS
            if bucket in by_repo_bucket[left] and bucket in by_repo_bucket[right]
        )
        average_cost = sum(sorted(by_repo_cost[left] + by_repo_cost[right])[:10]) / 10
        preference = priority.get(left, 100) + priority.get(right, 100)
        score = average_cost + preference * 1.5 - coverage * 8
        if best is None or score < best[0]:
            best = score, (left, right)
    if best is not None:
        return best[1]
    # Fall back to the pair with the widest bucket union. choose_five() may add
    # one low-cost candidate from a third repository for a missing bucket.
    fallback: tuple[tuple[int, int, float], tuple[str, str]] | None = None
    for left, right in itertools.combinations(repos, 2):
        union = by_repo_bucket[left] | by_repo_bucket[right]
        overlap = by_repo_bucket[left] & by_repo_bucket[right]
        costs = sorted(by_repo_cost[left] + by_repo_cost[right])
        average_cost = sum(costs[: min(10, len(costs))]) / max(1, min(10, len(costs)))
        score = (-len(union), -len(overlap), average_cost)
        if fallback is None or score < fallback[0]:
            fallback = score, (left, right)
    if fallback is None:
        raise SystemExit("could not find two repositories with selectable SWE instances")
    return fallback[1]


def choose_five(
    candidates: list[Candidate],
    repos: tuple[str, ...],
    *,
    max_candidates: int,
) -> tuple[list[Candidate], dict[str, list[Candidate]]]:
    by_bucket: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.repo in repos:
            by_bucket[candidate.bucket].append(candidate)
    for values in by_bucket.values():
        values.sort(key=lambda item: (item.estimated_cost, item.instance_id))
    missing = [bucket for bucket in BUCKETS if not by_bucket[bucket]]
    if missing:
        for bucket in missing:
            fallback = sorted(
                (item for item in candidates if item.bucket == bucket),
                key=lambda item: (
                    item.repo not in PREFERRED_REPOS,
                    PREFERRED_REPOS.index(item.repo) if item.repo in PREFERRED_REPOS else 10_000,
                    item.estimated_cost,
                    item.instance_id,
                ),
            )
            if not fallback:
                raise SystemExit(f"no candidate exists for diagnostic bucket {bucket!r}")
            by_bucket[bucket] = fallback

    selected: list[Candidate] = []
    used: set[str] = set()
    repo_counts: Counter[str] = Counter()
    for bucket in BUCKETS:
        values = by_bucket[bucket]
        choice = min(
            (item for item in values if item.instance_id not in used),
            key=lambda item: (
                repo_counts[item.repo] * 12 + item.estimated_cost,
                item.instance_id,
            ),
        )
        selected.append(choice)
        used.add(choice.instance_id)
        repo_counts[choice.repo] += 1
    if len(repo_counts) < min(2, len(repos)):
        target_repo = min(repos, key=repo_counts.get)
        replace_index = max(
            range(len(selected)),
            key=lambda index: repo_counts[selected[index].repo],
        )
        bucket = selected[replace_index].bucket
        replacement = next(
            (
                item
                for item in by_bucket[bucket]
                if item.repo == target_repo and item.instance_id not in used
            ),
            None,
        )
        if replacement is not None:
            old = selected[replace_index]
            used.remove(old.instance_id)
            selected[replace_index] = replacement
            used.add(replacement.instance_id)
    alternatives = {
        bucket: [item for item in by_bucket[bucket] if item.instance_id not in used][
            :max_candidates
        ]
        for bucket in BUCKETS
    }
    return selected, alternatives


def classify_bucket(
    *,
    source_files: tuple[str, ...],
    changed_lines: int,
    fail_count: int,
    pass_count: int,
    statement_mentions_target: bool,
) -> str | None:
    if len(source_files) >= 2:
        return "multi_file_contract"
    if pass_count >= 15 and changed_lines <= 100:
        return "regression_boundary"
    if fail_count >= 2 and 15 <= changed_lines <= 100:
        return "failure_recovery"
    if changed_lines <= 28 and fail_count <= 4:
        return "surgical_fix"
    if not statement_mentions_target and changed_lines <= 90:
        return "localization"
    return None


def patch_metadata(patch: str) -> tuple[tuple[str, ...], int]:
    files: list[str] = []
    changed = 0
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("diff --git a/"):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if match:
                current = match.group(2)
                files.append(current)
            continue
        if current is None or line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
    return tuple(dict.fromkeys(files)), changed


def parse_tests(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in value.splitlines() if item.strip()]
        value = parsed
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def is_test_path(path: str) -> bool:
    lowered = "/" + path.lower()
    return any(marker in lowered for marker in TEST_MARKERS)


if __name__ == "__main__":
    raise SystemExit(main())
