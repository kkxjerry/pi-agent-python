from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FixtureError(ValueError):
    """A parity fixture violates the repository contract."""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FixtureError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise FixtureError(f"{path}:{line_number}: record must be an object")
            records.append(value)
    return records


def validate_fixture(records: list[dict[str, Any]], *, path: Path | None = None) -> None:
    label = str(path) if path is not None else "fixture"
    if len(records) < 3:
        raise FixtureError(f"{label}: expected meta, event(s), and result records")
    meta = records[0]
    if meta.get("record") != "meta" or meta.get("schemaVersion") != 1:
        raise FixtureError(f"{label}: invalid fixture metadata")
    if meta.get("provenance") not in {
        "source-contract",
        "upstream-execution",
        "python-execution",
    }:
        raise FixtureError(f"{label}: invalid provenance")

    events = [record for record in records if record.get("record") == "event"]
    if not events:
        raise FixtureError(f"{label}: no event records")
    if [record.get("index") for record in events] != list(range(len(events))):
        raise FixtureError(f"{label}: event indices must be contiguous from zero")
    event_types = [record.get("event", {}).get("type") for record in events]
    if event_types[0] != "agent_start" or event_types[-1] != "agent_end":
        raise FixtureError(f"{label}: invalid lifecycle boundaries")
    if records[-1].get("record") != "result":
        raise FixtureError(f"{label}: final record must be result")

    starts: set[str] = set()
    ends: set[str] = set()
    for record in events:
        event = record.get("event")
        if not isinstance(event, dict):
            raise FixtureError(f"{label}: event payload must be an object")
        call_id = event.get("toolCallId")
        if event.get("type") == "tool_execution_start" and isinstance(call_id, str):
            starts.add(call_id)
        if event.get("type") == "tool_execution_end" and isinstance(call_id, str):
            ends.add(call_id)
    if starts != ends:
        raise FixtureError(f"{label}: tool execution start/end IDs differ")
