#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pi_agent.parity import load_jsonl, validate_fixture

ROOT = Path(__file__).resolve().parents[1]


def _canonical(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ignore no behavior fields; only normalize JSON object ordering via values."""

    return json.loads(json.dumps(records, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a fresh official TypeScript capture with checked-in goldens"
    )
    parser.add_argument("capture_dir", type=Path)
    args = parser.parse_args()

    expected_dir = ROOT / "fixtures" / "upstream"
    expected_names = {path.name for path in expected_dir.glob("*.jsonl")}
    actual_names = {path.name for path in args.capture_dir.glob("*.jsonl")}
    if expected_names != actual_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise SystemExit(f"capture file set differs: missing={missing}, extra={extra}")

    for name in sorted(expected_names):
        expected = load_jsonl(expected_dir / name)
        actual = load_jsonl(args.capture_dir / name)
        validate_fixture(expected, path=expected_dir / name)
        validate_fixture(actual, path=args.capture_dir / name)
        if _canonical(expected) != _canonical(actual):
            raise SystemExit(f"capture differs: {name}")
        print(f"MATCH {name}")
    print(f"upstream capture OK: {len(expected_names)} fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
