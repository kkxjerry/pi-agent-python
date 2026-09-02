from __future__ import annotations

from pi_agent.ai import parse_streaming_json


def test_parse_streaming_json_returns_useful_partial_objects() -> None:
    assert parse_streaming_json("") == {}
    assert parse_streaming_json('{"path":') == {}
    assert parse_streaming_json('{"path":"REA') == {"path": "REA"}
    assert parse_streaming_json('{"path":"README.md","offset":1') == {
        "path": "README.md",
        "offset": 1,
    }
    assert parse_streaming_json('{"nested":{"ok":true') == {"nested": {"ok": True}}
    assert parse_streaming_json('{"items":[1,2,"thr') == {"items": [1, 2, "thr"]}


def test_parse_streaming_json_keeps_complete_json_semantics() -> None:
    assert parse_streaming_json('{"escaped":"line\\nnext","none":null}') == {
        "escaped": "line\nnext",
        "none": None,
    }
    assert parse_streaming_json("[true,false,3.5]") == [True, False, 3.5]
