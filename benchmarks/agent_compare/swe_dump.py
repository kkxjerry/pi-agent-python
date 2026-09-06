from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump selected SWE-bench Verified records")
    parser.add_argument("parquet", type=Path)
    parser.add_argument("instance_id", nargs="+")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.parquet.is_file():
        raise SystemExit(f"Parquet file does not exist: {args.parquet}")
    try:
        import duckdb  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment guard
        raise SystemExit("duckdb is required") from exc

    placeholders = ",".join("?" for _ in args.instance_id)
    query = (
        f"SELECT * FROM read_parquet(?) WHERE instance_id IN ({placeholders}) ORDER BY instance_id"
    )
    connection = duckdb.connect()
    cursor = connection.execute(query, [str(args.parquet), *args.instance_id])
    columns = [item[0] for item in cursor.description]
    records = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    if len(records) != len(set(args.instance_id)):
        found = {str(item["instance_id"]) for item in records}
        missing = sorted(set(args.instance_id) - found)
        raise SystemExit("missing instances: " + ", ".join(missing))
    if args.compact:
        for record in records:
            record["patch"] = _summary(str(record.get("patch", "")))
            record["test_patch"] = _summary(str(record.get("test_patch", "")))
            record["eval_script"] = str(record.get("eval_script", ""))[:4000]
            record["problem_statement"] = str(record.get("problem_statement", ""))[:4000]
    print(json.dumps({"instances": records}, ensure_ascii=False, indent=2, default=str))
    return 0


def _summary(value: str) -> dict[str, Any]:
    return {
        "bytes": len(value.encode("utf-8")),
        "lines": value.count("\n") + bool(value),
        "head": "\n".join(value.splitlines()[:40]),
    }


if __name__ == "__main__":
    raise SystemExit(main())
