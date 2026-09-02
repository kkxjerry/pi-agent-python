from __future__ import annotations

import json
from pathlib import Path


def test_source_map_is_pinned_unique_and_relative() -> None:
    value = json.loads(Path("docs/upstream/source-map.json").read_text(encoding="utf-8"))
    assert value["upstream"] == {
        "repository": "earendil-works/pi",
        "tag": "v0.84.4",
        "commit": "b79e4cc",
    }
    upstream = [entry["upstream"] for entry in value["entries"]]
    python = [entry["python"] for entry in value["entries"]]
    assert len(upstream) == len(set(upstream))
    assert len(python) == len(set(python))
    assert all(not path.startswith(("/", "~")) for path in python)
