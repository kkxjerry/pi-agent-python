from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Instances called out in public SWE-bench issues as having broken gold or
# malformed FAIL_TO_PASS/PASS_TO_PASS metadata. Gold preflight remains the
# authoritative filter; this list merely avoids wasting image/build time.
KNOWN_PROBLEMATIC = frozenset(
    {
        "astropy__astropy-7606",
        "astropy__astropy-8707",
        "astropy__astropy-8872",
        "django__django-10097",
        "django__django-14792",
        "django__django-15525",
        "django__django-16950",
        "matplotlib__matplotlib-18869",
        "matplotlib__matplotlib-20488",
        "psf__requests-1766",
        "pydata__xarray-2905",
        "pydata__xarray-3305",
        "pydata__xarray-3993",
        "pydata__xarray-4094",
        "pydata__xarray-4695",
        "pydata__xarray-6721",
        "pydata__xarray-6744",
        "pydata__xarray-6938",
        "pydata__xarray-6992",
        "pytest-dev__pytest-5262",
        "pytest-dev__pytest-7521",
        "sphinx-doc__sphinx-10323",
        "sphinx-doc__sphinx-10435",
        "sphinx-doc__sphinx-8595",
        "sphinx-doc__sphinx-9711",
        "sympy__sympy-13852",
    }
)


@dataclass(slots=True, frozen=True)
class PatchStats:
    files: tuple[str, ...]
    source_files: tuple[str, ...]
    test_files: tuple[str, ...]
    additions: int
    deletions: int

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(slots=True, frozen=True)
class Candidate:
    instance_id: str
    repo: str
    difficulty: str
    image: str
    base_commit: str
    fail_to_pass: int
    pass_to_pass: int
    issue_chars: int
    patch: PatchStats
    test_patch: PatchStats
    problem_statement: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["patch"]["changed_lines"] = self.patch.changed_lines
        value["test_patch"]["changed_lines"] = self.test_patch.changed_lines
        return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select SWE-bench Verified diagnostic candidates")
    parser.add_argument("parquet", type=Path)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--difficulty", action="append", default=[])
    parser.add_argument("--min-source-files", type=int, default=0)
    parser.add_argument("--max-source-files", type=int, default=3)
    parser.add_argument("--min-changed-lines", type=int, default=0)
    parser.add_argument("--max-changed-lines", type=int, default=120)
    parser.add_argument("--max-test-patch-lines", type=int, default=250)
    parser.add_argument("--min-fail-to-pass", type=int, default=1)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--include-known-problematic", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("json", "tsv"), default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.parquet.is_file():
        raise SystemExit(f"Parquet file does not exist: {args.parquet}")
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment setup guard
        raise SystemExit("duckdb is required to read the candidate Parquet file") from exc

    rows = (
        duckdb.connect()
        .execute(
            "SELECT instance_id, repo, difficulty, image, base_commit, patch, test_patch, "
            "problem_statement, FAIL_TO_PASS, PASS_TO_PASS "
            "FROM read_parquet(?)",
            [str(args.parquet)],
        )
        .fetchall()
    )
    candidates: list[Candidate] = []
    selected_repos = set(args.repo)
    selected_difficulties = set(args.difficulty)
    for row in rows:
        (
            instance_id,
            repo,
            difficulty,
            image,
            base_commit,
            patch,
            test_patch,
            problem_statement,
            fail_to_pass,
            pass_to_pass,
        ) = row
        if not args.include_known_problematic and instance_id in KNOWN_PROBLEMATIC:
            continue
        if selected_repos and repo not in selected_repos:
            continue
        if selected_difficulties and difficulty not in selected_difficulties:
            continue
        patch_stats = parse_patch(str(patch or ""))
        test_stats = parse_patch(str(test_patch or ""))
        if not args.min_source_files <= len(patch_stats.source_files) <= args.max_source_files:
            continue
        if not args.min_changed_lines <= patch_stats.changed_lines <= args.max_changed_lines:
            continue
        if test_stats.changed_lines > args.max_test_patch_lines:
            continue
        if len(fail_to_pass or ()) < args.min_fail_to_pass:
            continue
        candidates.append(
            Candidate(
                instance_id=str(instance_id),
                repo=str(repo),
                difficulty=str(difficulty),
                image=str(image),
                base_commit=str(base_commit),
                fail_to_pass=len(fail_to_pass or ()),
                pass_to_pass=len(pass_to_pass or ()),
                issue_chars=len(str(problem_statement or "")),
                patch=patch_stats,
                test_patch=test_stats,
                problem_statement=normalize_problem(str(problem_statement or "")),
            )
        )

    candidates.sort(key=candidate_key)
    selected = candidates[: args.limit]
    if args.format == "tsv":
        print(
            "instance_id\trepo\tdifficulty\tsource_files\tchanged_lines\t"
            "fail_to_pass\tpass_to_pass\tfiles\tproblem"
        )
        for candidate in selected:
            problem = re.sub(r"[\r\n]+", " ", candidate.problem_statement)[:180]
            print(
                "\t".join(
                    (
                        candidate.instance_id,
                        candidate.repo,
                        candidate.difficulty,
                        str(len(candidate.patch.source_files)),
                        str(candidate.patch.changed_lines),
                        str(candidate.fail_to_pass),
                        str(candidate.pass_to_pass),
                        ",".join(candidate.patch.source_files),
                        problem,
                    )
                )
            )
    else:
        values = [candidate.to_dict() for candidate in selected]
        if args.compact:
            for value in values:
                value["problem_statement"] = value["problem_statement"][:500]
        print(
            json.dumps(
                {"count": len(candidates), "candidates": values},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def parse_patch(patch: str) -> PatchStats:
    files: list[str] = []
    additions = 0
    deletions = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if match:
                files.append(match.group(2))
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    unique = tuple(dict.fromkeys(files))
    test_files = tuple(path for path in unique if is_test_path(path))
    source_files = tuple(path for path in unique if path not in test_files)
    return PatchStats(unique, source_files, test_files, additions, deletions)


def is_test_path(path: str) -> bool:
    lowered = path.casefold()
    name = Path(path).name.casefold()
    return (
        "/test" in f"/{lowered}"
        or lowered.startswith("test")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "/tests/" in f"/{lowered}/"
    )


def normalize_problem(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def candidate_key(candidate: Candidate) -> tuple[Any, ...]:
    difficulty_order = {
        "<15 min fix": 0,
        "15 min - 1 hour": 1,
        "1-4 hours": 2,
        ">4 hours": 3,
    }
    return (
        difficulty_order.get(candidate.difficulty, 9),
        len(candidate.patch.source_files),
        candidate.patch.changed_lines,
        candidate.test_patch.changed_lines,
        -candidate.pass_to_pass,
        candidate.instance_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
