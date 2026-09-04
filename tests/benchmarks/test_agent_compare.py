from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "benchmarks" / "agent_compare" / "run.py"
TRACE_DIFF_PATH = ROOT / "benchmarks" / "agent_compare" / "trace_diff.py"


def load_path(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_module() -> ModuleType:
    return load_path(MODULE_PATH, "agent_compare_run")


def assistant_message(
    *,
    text: str,
    usage_input: int,
    usage_output: int,
    stop_reason: str = "stop",
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "usage": {
            "input": usage_input,
            "output": usage_output,
            "totalTokens": usage_input + usage_output,
        },
        "stopReason": stop_reason,
    }


def test_analyze_capture_normalizes_upstream_and_python_trace_shapes() -> None:
    module = load_module()
    events = [
        {"elapsedMs": 1.0, "event": {"type": "session", "version": 3}},
        {"elapsedMs": 2.0, "event": {"type": "agent_start"}},
        {
            "elapsedMs": 10.0,
            "event": {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "ok"},
            },
        },
        {
            "elapsedMs": 20.0,
            "event": {
                "type": "tool_execution_start",
                "toolCallId": "call-1",
                "toolName": "read",
                "args": {"path": "a.py"},
            },
        },
        {
            "elapsedMs": 25.0,
            "event": {
                "type": "tool_execution_end",
                "toolCallId": "call-1",
                "toolName": "read",
                "isError": False,
                "result": {"content": []},
            },
        },
        {
            "elapsedMs": 30.0,
            "event": {
                "type": "message_end",
                "message": assistant_message(
                    text="done",
                    usage_input=12,
                    usage_output=3,
                ),
            },
        },
    ]
    capture = module.ProcessCapture(
        command=["fixture"],
        returncode=0,
        duration_ms=31.0,
        stdout_lines=[json.dumps(item["event"]) + "\n" for item in events],
        stderr="",
        timed_events=events,
        invalid_stdout_lines=[],
    )

    metrics = module.analyze_capture(
        "upstream-ts",
        "fixture",
        1,
        capture,
        True,
        "passed",
        [],
    )

    assert metrics.success is True
    assert metrics.model_calls == 1
    assert metrics.input_tokens == 12
    assert metrics.output_tokens == 3
    assert metrics.total_tokens == 15
    assert metrics.tool_calls == 1
    assert metrics.tool_errors == 0
    assert metrics.tool_sequence == ["read"]
    assert metrics.time_to_first_text_delta_ms == 10.0
    assert metrics.session_schema_version == 3


@pytest.mark.asyncio
async def test_exact_output_validator_separates_format_from_artifact_changes(
    tmp_path: Path,
) -> None:
    module = load_module()
    (tmp_path / "value.txt").write_text("one", encoding="utf-8")
    before = module.snapshot_tree(tmp_path)
    task = module.Task(
        id="exact",
        fixture=tmp_path,
        prompt="fixture",
        validator=module.Validator(
            kind="exact_output",
            expected="ANSWER",
            require_clean_workspace=True,
        ),
    )

    ok, message = await module.validate_task(
        task,
        tmp_path,
        before,
        module.snapshot_tree(tmp_path),
        "explanation\nANSWER",
    )

    assert ok is False
    assert "differs" in message


def test_snapshot_tree_and_diff_ignore_runtime_caches(tmp_path: Path) -> None:
    module = load_module()
    (tmp_path / "source.py").write_text("one", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "source.pyc").write_bytes(b"cache")
    before = module.snapshot_tree(tmp_path)

    (tmp_path / "source.py").write_text("two", encoding="utf-8")
    (tmp_path / "created.txt").write_text("new", encoding="utf-8")
    after = module.snapshot_tree(tmp_path)

    assert set(before) == {"source.py"}
    assert module.tree_diff(before, after) == ["A created.txt", "M source.py"]


def test_generated_upstream_config_references_environment_without_secret(
    tmp_path: Path,
) -> None:
    module = load_module()
    path = tmp_path / "models.json"

    module.write_upstream_model_config(
        path,
        provider="dashscope",
        model="qwen-plus",
        base_url="https://example.invalid/v1",
        api_key_env="DASHSCOPE_API_KEY",
        context_window=131_072,
        max_tokens=8_192,
    )

    text = path.read_text(encoding="utf-8")
    value = json.loads(text)
    provider = value["providers"]["dashscope"]
    assert provider["apiKey"] == "$DASHSCOPE_API_KEY"
    assert provider["models"][0]["contextWindow"] == 131_072
    assert "secret" not in text.casefold()


def test_trace_diff_aligns_parallel_and_serial_tool_turns(tmp_path: Path) -> None:
    module = load_path(TRACE_DIFF_PATH, "agent_compare_trace_diff")
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    left.write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {"type": "turn_start"},
                {
                    "type": "message_end",
                    "message": {
                        **assistant_message(
                            text="read both",
                            usage_input=10,
                            usage_output=2,
                            stop_reason="toolUse",
                        ),
                        "content": [
                            {"type": "toolCall", "name": "read", "arguments": {"path": "a"}},
                            {"type": "toolCall", "name": "read", "arguments": {"path": "b"}},
                        ],
                    },
                },
                {"type": "tool_execution_start", "toolCallId": "a", "toolName": "read"},
                {"type": "tool_execution_end", "toolCallId": "a", "toolName": "read"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    right.write_text(left.read_text(encoding="utf-8"), encoding="utf-8")

    report = module.render_trace_diff(
        module.normalize_trace(left),
        module.normalize_trace(right),
        "left",
        "right",
    )

    assert "read, read" in report
    assert "Event count differences" in report
