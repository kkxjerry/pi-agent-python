from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SWE5 = ROOT / "benchmarks" / "swe5"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SWE5 / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_selection_patch_metadata_and_bucket_rules() -> None:
    module = load("select_verified")
    patch = """diff --git a/pkg/core.py b/pkg/core.py
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_core.py b/tests/test_core.py
--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -1 +1 @@
-old test
+new test
"""
    files, changed = module.patch_metadata(patch)
    assert files == ("pkg/core.py", "tests/test_core.py")
    assert changed == 4
    assert module.is_test_path("tests/test_core.py") is True
    assert (
        module.classify_bucket(
            source_files=("pkg/core.py", "pkg/api.py"),
            changed_lines=40,
            fail_count=2,
            pass_count=5,
            statement_mentions_target=False,
        )
        == "multi_file_contract"
    )


def test_trace_normalization_extracts_tool_and_usage(tmp_path: Path) -> None:
    module = load("run_swe5")
    source = tmp_path / "timed.jsonl"
    destination = tmp_path / "normalized.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "elapsed_ms": 10,
                        "event": {
                            "type": "tool_execution_start",
                            "toolName": "bash",
                            "toolCallId": "call-1",
                            "args": {"command": "python -m pytest -q"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "elapsed_ms": 20,
                        "event": {
                            "type": "tool_execution_end",
                            "toolName": "bash",
                            "toolCallId": "call-1",
                            "isError": True,
                            "result": {"details": {"exitCode": 1}},
                        },
                    }
                ),
                json.dumps(
                    {
                        "elapsed_ms": 30,
                        "event": {
                            "type": "turn_end",
                            "message": {"usage": {"input": 12, "output": 3}},
                        },
                    }
                ),
            ]
        )
        + "\n"
    )

    module.normalize_trace(source, destination)

    records = [json.loads(line) for line in destination.read_text().splitlines()]
    assert records[0]["command_kind"] == "test"
    assert records[1]["exit_code"] == 1
    assert records[1]["is_error"] is True
    assert records[2]["input_tokens"] == 12
    assert records[2]["output_tokens"] == 3


def test_report_behavior_and_first_divergence() -> None:
    module = load("report_swe5")
    records = [
        {
            "elapsed_ms": 10,
            "type": "tool_execution_start",
            "tool": "read",
            "target": "pkg/core.py",
        },
        {
            "elapsed_ms": 20,
            "type": "tool_execution_start",
            "tool": "bash",
            "command_kind": "test",
        },
        {
            "elapsed_ms": 30,
            "type": "tool_execution_start",
            "tool": "edit",
            "target": "pkg/core.py",
        },
        {
            "elapsed_ms": 40,
            "type": "tool_execution_start",
            "tool": "bash",
            "command_kind": "test",
        },
    ]
    behavior = module.analyze_behavior(records, ["pkg/core.py"])

    assert behavior.target_file_read is True
    assert behavior.baseline_test_before_edit is True
    assert behavior.test_after_edit is True
    assert behavior.edit_calls == 1
    assert module.first_divergence(
        ("read:core.py", "bash:test"),
        ("read:api.py", "bash:test"),
    ) == {"index": 0, "upstream-ts": "read:core.py", "python": "read:api.py"}
