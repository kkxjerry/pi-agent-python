"""Regression tests for the maintained SWE runner; no API or container is used."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "maintained_swe_run", ROOT / "benchmarks/agent_compare/swe_run.py"
)
assert SPEC is not None and SPEC.loader is not None
swe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = swe
SPEC.loader.exec_module(swe)


def record():
    return swe.SweRecord(
        instance_id="example__repo-1",
        repo="example/repo",
        base_commit="abc123",
        difficulty="<15 min fix",
        image="swebench/example:latest",
        eval_type="pass_and_fail",
        log_parser="pytest",
        problem_statement="Repair the bug.",
        hints_text="PRIVATE HINT",
        patch="PRIVATE GOLD",
        test_patch="PRIVATE TEST",
        eval_script="echo grading",
        fail_to_pass=("test_new",),
        pass_to_pass=("test_old",),
    )


def grade():
    return swe.GradeReport(
        instance_id="example__repo-1",
        valid=True,
        resolved=True,
        f2p_success=("test_new",),
        f2p_failure=(),
        p2p_success=("test_old",),
        p2p_failure=(),
        status_map={"test_new": "PASSED", "test_old": "PASSED"},
        test_output_found=True,
        timed_out=False,
        returncode=0,
        duration_ms=1,
        message="resolved",
    )


def capture(stop_reason="stop"):
    event = {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
            "stopReason": stop_reason,
            "usage": {"input": 3, "output": 1, "totalTokens": 4},
        },
    }
    return swe.Capture(
        command=["test-agent"],
        returncode=0,
        duration_ms=1,
        stdout_lines=[json.dumps(event) + "\n"],
        stderr="",
        timed_events=[{"elapsedMs": 1, "event": event}],
        invalid_stdout_lines=[],
    )


@pytest.mark.asyncio
async def test_capture_preserves_json_event_larger_than_64k(monkeypatch):
    stopped = AsyncMock()
    monkeypatch.setattr(swe, "stop_container", stopped)
    code = (
        "import json; "
        "print(json.dumps({'type':'tool_execution_end','payload':'x'*100000})); "
        "print(json.dumps({'type':'agent_end'}))"
    )
    result = await swe.capture_agent(
        argparse.Namespace(container_engine="unused"),
        "unused",
        [sys.executable, "-c", code],
        timeout=5,
        budgets=swe.BudgetConfig(15, 60, 250000, 20000),
    )
    assert result.returncode == 0
    assert not result.timed_out
    assert [item["event"]["type"] for item in result.timed_events] == [
        "tool_execution_end",
        "agent_end",
    ]
    stopped.assert_not_called()


@pytest.mark.asyncio
async def test_reader_failure_is_not_silently_swallowed(monkeypatch):
    monkeypatch.setattr(swe, "stop_container", AsyncMock())

    def broken_observer(self, event):
        raise RuntimeError("injected trace reader failure")

    monkeypatch.setattr(swe.BudgetTracker, "observe", broken_observer)
    result = await swe.capture_agent(
        argparse.Namespace(container_engine="unused"),
        "unused",
        [sys.executable, "-c", "import json; print(json.dumps({'type':'agent_end'}))"],
        timeout=5,
        budgets=swe.BudgetConfig(15, 60, 250000, 20000),
    )
    assert "injected trace reader failure" in (result.capture_error or "")


@pytest.mark.asyncio
async def test_timeout_preserves_events_and_reaps_process(monkeypatch):
    stopped = AsyncMock()
    monkeypatch.setattr(swe, "stop_container", stopped)
    code = 'import time; print(\'{"type":"agent_start"}\', flush=True); time.sleep(30)'
    result = await swe.capture_agent(
        argparse.Namespace(container_engine="unused"),
        "unused",
        [sys.executable, "-c", code],
        timeout=0.5,
        budgets=swe.BudgetConfig(15, 60, 250000, 20000),
    )
    assert result.timed_out
    assert result.returncode != 0
    assert result.timed_events[0]["event"]["type"] == "agent_start"
    stopped.assert_awaited_once()


def test_cached_prompt_tokens_count_against_input_budget():
    tracker = swe.BudgetTracker(swe.BudgetConfig(15, 60, 100, 20000))
    result = tracker.observe(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "usage": {"input": 1, "cacheRead": 200, "output": 1},
            },
        }
    )
    assert result == "BUDGET_EXCEEDED_INPUT_TOKENS"
    assert tracker.input_tokens == 201


def test_ambiguous_skipped_and_failed_test_ids_are_not_accepted():
    assert (
        swe.test_status(
            "test[param",
            {"test[param1]": "SKIPPED", "test[param2]": "FAILED"},
        )
        is None
    )


@pytest.mark.parametrize(
    "left,right",
    [
        (
            ("bash", {"command": "pytest tests/test_a.py"}),
            ("bash", {"command": "pytest tests/test_b.py"}),
        ),
        (("read", {"path": "pkg.py", "offset": 1}), ("read", {"path": "pkg.py", "offset": 100})),
        (
            ("edit", {"path": "pkg.py", "oldText": "a", "newText": "b"}),
            ("edit", {"path": "pkg.py", "oldText": "a", "newText": "c"}),
        ),
    ],
)
def test_tool_actions_preserve_decision_arguments(left, right):
    def sequence(value):
        return swe.action_sequence(
            [
                {
                    "elapsedMs": 1,
                    "event": {
                        "type": "tool_execution_start",
                        "toolName": value[0],
                        "args": value[1],
                    },
                }
            ]
        )

    assert sequence(left) != sequence(right)


def test_provider_error_is_a_process_failure_even_with_exit_zero():
    result = swe.analyze_run(
        "python",
        swe.SuiteTask("example__repo-1", "test", 5, 5),
        record(),
        capture("error"),
        "",
        [],
        [],
        grade(),
    )
    assert result.process_ok is False


def mock_run(monkeypatch):
    args = argparse.Namespace(container_engine="unused", keep_containers=False)
    suite = swe.load_suite(ROOT / "benchmarks/agent_compare/swe5.json")
    task = swe.SuiteTask("example__repo-1", "test", 5, 5)
    monkeypatch.setattr(swe, "create_container", AsyncMock())
    monkeypatch.setattr(swe, "assert_container_runtime", AsyncMock())
    monkeypatch.setattr(swe, "agent_command", lambda *args: ["test-agent"])
    monkeypatch.setattr(swe, "capture_agent", AsyncMock(return_value=capture()))
    monkeypatch.setattr(swe, "collect_patch", AsyncMock(return_value=("candidate-patch", [], [])))
    monkeypatch.setattr(swe, "remove_container", AsyncMock())
    return args, suite, task


@pytest.mark.asyncio
async def test_trace_is_saved_before_grading_can_fail(monkeypatch, tmp_path):
    args, suite, task = mock_run(monkeypatch)
    monkeypatch.setattr(
        swe, "evaluate_container", AsyncMock(side_effect=RuntimeError("grader failed"))
    )
    monkeypatch.setattr(
        swe, "grade_fresh_container", AsyncMock(side_effect=RuntimeError("grader failed"))
    )
    with pytest.raises(RuntimeError, match="grader failed"):
        await swe.run_agent(args, suite, task, record(), "python", tmp_path)
    assert (tmp_path / "traces/python/example__repo-1.capture.json").is_file()
    assert (tmp_path / "traces/python/example__repo-1.jsonl").is_file()


@pytest.mark.asyncio
async def test_candidate_patch_is_graded_in_a_fresh_container(monkeypatch, tmp_path):
    args, suite, task = mock_run(monkeypatch)
    dirty_grader = AsyncMock(return_value=(grade(), "grade log"))
    fresh_grader = AsyncMock(return_value=grade())
    monkeypatch.setattr(swe, "evaluate_container", dirty_grader)
    monkeypatch.setattr(swe, "grade_fresh_container", fresh_grader)
    await swe.run_agent(args, suite, task, record(), "python", tmp_path)
    fresh_grader.assert_awaited_once()
    assert fresh_grader.call_args.kwargs["patch"] == "candidate-patch"
    dirty_grader.assert_not_called()


@pytest.mark.asyncio
async def test_patch_is_diffed_against_dataset_base_not_mutable_head(monkeypatch):
    command = AsyncMock(return_value=swe.CommandResult(returncode=0))
    monkeypatch.setattr(swe, "command_capture", command)
    await swe.collect_patch("unused", "unused", base_commit="abc123")
    diff_commands = [call.args[0] for call in command.call_args_list if "diff" in call.args[0]]
    assert len(diff_commands) == 2
    assert all("abc123" in args and "HEAD" not in args for args in diff_commands)


@pytest.mark.asyncio
async def test_export_includes_an_agent_committed_fix(monkeypatch, tmp_path):
    original = swe.command_capture

    async def git(*args):
        return await original(["git", "-C", str(tmp_path), *args])

    await git("init", "-q")
    await git("config", "user.name", "Offline Fixture")
    await git("config", "user.email", "fixture@example.invalid")
    source = tmp_path / "answer.py"
    source.write_text("value = 1\n")
    await git("add", "answer.py")
    await git("commit", "-qm", "base")
    base = (await git("rev-parse", "HEAD")).stdout.strip()
    source.write_text("value = 2\n")
    await git("commit", "-qam", "agent fix")
    assert not (await git("diff", "HEAD")).stdout

    async def local_git(command, **kwargs):
        assert command[5] == "git"
        return await original(["git", "-C", str(tmp_path), *command[6:]], **kwargs)

    monkeypatch.setattr(swe, "command_capture", local_git)
    patch, changed, _ = await swe.collect_patch("unused", "unused", base_commit=base)
    assert "+value = 2" in patch
    assert changed == ["answer.py"]


def test_prompt_does_not_expose_private_evaluation_fields():
    prompt = swe.task_prompt(record())
    assert "Repair the bug." in prompt
    assert not any(value in prompt for value in ("PRIVATE GOLD", "PRIVATE TEST", "PRIVATE HINT"))


def test_both_harnesses_use_identical_testbed_path():
    suite = swe.load_suite(ROOT / "benchmarks/agent_compare/swe5.json")
    paths = []
    for system in ("upstream-ts", "python"):
        command = swe.agent_command(
            argparse.Namespace(container_engine="podman"), suite, system, "unused", "issue"
        )
        paths.append(next(arg for arg in command if arg.startswith("PATH=")))
    assert paths[0] == paths[1]
    assert paths[0].index("envs/testbed/bin") < paths[0].index("/opt/miniconda3/bin")


def test_thinking_off_is_an_explicit_request_override_for_both(tmp_path):
    from pi_agent.cli import build_parser

    suite = swe.load_suite(ROOT / "benchmarks/agent_compare/swe5.json")
    config = tmp_path / "models.json"
    swe.write_upstream_config(config, suite.model, "OFFLINE_FIXTURE_TOKEN")
    model = json.loads(config.read_text())["providers"]["dashscope"]["models"][0]
    assert model["samplingParams"] == {"enable_thinking": False}
    command = swe.agent_command(
        argparse.Namespace(container_engine="podman"), suite, "python", "unused", "issue"
    )
    value = command[command.index("--sampling-params") + 1]
    args = build_parser().parse_args(["--sampling-params", value])
    assert args.sampling_params == {"enable_thinking": False}


@pytest.mark.parametrize("value", ["[]", "not-json", "true"])
def test_sampling_parameters_reject_non_objects(value):
    from pi_agent.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--sampling-params", value])
