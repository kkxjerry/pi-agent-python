"""Execute both real CLIs against scripted localhost SSE, never a paid model.

This validates transport/tool recovery, not model repair skill or SWE pass rate.
Only synthetic sources and fabricated token usage enter this fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import shutil
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from .test_swe_run import ROOT, swe

SOURCE = "def normalize_name(value):\n    return value\n"
FIXED = "def normalize_name(value):\n    return value.strip().lower()\n"
TEST = (
    "import unittest\n"
    "from sample import normalize_name\n"
    "class TestName(unittest.TestCase):\n"
    "    def test_name(self):\n"
    "        self.assertEqual(normalize_name('  ALICE  '), 'alice')\n"
)
CLI = ROOT / "tools/upstream-fixtures" / swe.UPSTREAM_CLI_RELATIVE


class ScriptedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, script):
        super().__init__(("127.0.0.1", 0), Handler)
        self.script = script
        self.requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        server = self.server
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        index = len(server.requests)
        server.requests.append(body)  # Intentionally never record authorization headers.
        if index >= len(server.script):
            self.send_error(400, "Unexpected additional fixture request")
            return
        action = server.script[index]
        if action is None:
            delta = {"content": "OFFLINE_FIXTURE_DONE"}
            reason = "stop"
        else:
            name, arguments = action
            if name == "edit":
                schema = next(
                    tool["function"]["parameters"]
                    for tool in body["tools"]
                    if tool["function"]["name"] == "edit"
                )
                if "edits" in schema.get("properties", {}):
                    arguments = {
                        "path": arguments["path"],
                        "edits": [
                            {key: value for key, value in arguments.items() if key != "path"}
                        ],
                    }
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"fixture-call-{index}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            }
            reason = "tool_calls"
        common = {"id": f"fixture-response-{index}", "model": "qwen-plus", "created": 0}
        chunks = [
            {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]},
            {
                **common,
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    "prompt_tokens_details": {"cached_tokens": 20},
                },
            },
        ]
        payload = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        data = (payload + "data: [DONE]\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.mark.asyncio
async def test_real_cli_pair_transmits_failures_and_recovers(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node or not CLI.is_file():
        pytest.skip("pinned official CLI is not installed; no network installation attempted")
    monkeypatch.setattr(swe, "stop_container", AsyncMock())
    baseline = f"{shlex.quote(sys.executable)} -m unittest -q"
    script = [
        ("read", {"path": "sample.py"}),
        ("bash", {"command": baseline}),
        ("edit", {"path": "sample.py", "oldText": "does not exist", "newText": "bad"}),
        ("read", {"path": "sample.py"}),
        ("edit", {"path": "sample.py", "oldText": SOURCE, "newText": FIXED}),
        ("bash", {"command": baseline}),
        None,
    ]
    reports = []
    for system in ("upstream-ts", "python"):
        run = tmp_path / system
        workspace = run / "workspace"
        home = run / "home"
        workspace.mkdir(parents=True)
        home.mkdir()
        (workspace / "sample.py").write_text(SOURCE)
        (workspace / "test_sample.py").write_text(TEST)
        server = ScriptedServer(script)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        suite = swe.load_suite(ROOT / "benchmarks/agent_compare/swe5.json")
        suite = replace(
            suite,
            model=replace(suite.model, base_url=f"http://127.0.0.1:{server.server_port}/v1"),
        )
        env = {
            "HOME": str(home),
            "PATH": f"{Path(node).parent}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONUNBUFFERED": "1",
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
            "PI_SKIP_VERSION_CHECK": "1",
            "NO_COLOR": "1",
            "OFFLINE_FIXTURE_TOKEN": "offline-fixture-not-a-secret",
        }
        # Reuse actual benchmark CLI arguments; replace container-only executable paths.
        generated = swe.agent_command(
            argparse.Namespace(container_engine="unused"), suite, system, "unused", "repair"
        )
        if system == "upstream-ts":
            start = generated.index("/opt/pi-node/bin/node")
            command = [node, str(CLI), *generated[start + 2 :]]
            agent_dir = run / "agent-dir"
            swe.write_upstream_config(
                agent_dir / "models.json", suite.model, "OFFLINE_FIXTURE_TOKEN"
            )
            env["PI_CODING_AGENT_DIR"] = str(agent_dir)
        else:
            start = generated.index("/opt/miniconda3/bin/python")
            command = [sys.executable, *generated[start + 1 :]]
            command[command.index("--cwd") + 1] = str(workspace)
        try:
            result = await swe.capture_agent(
                argparse.Namespace(container_engine="unused"),
                "unused",
                command,
                timeout=90,
                budgets=suite.budgets,
                cwd=workspace,
                env=env,
            )
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=3)
        swe.write_capture(run / "capture", result)
        assert result.returncode == 0, (system, result.stderr)
        assert not result.timed_out, system
        assert result.capture_error is None, (system, result.capture_error)
        assert not result.invalid_stdout_lines
        assert len(server.requests) == 7, (system, len(server.requests), result.stderr)
        assert (workspace / "sample.py").read_text() == FIXED
        assert (workspace / "test_sample.py").read_text() == TEST
        assert all(body.get("enable_thinking") is False for body in server.requests)
        limits = [
            body.get("max_tokens", body.get("max_completion_tokens")) for body in server.requests
        ]
        assert limits == [4096] * 7, (system, limits)
        for request_index, call_id in ((2, "fixture-call-1"), (3, "fixture-call-2")):
            received = [
                item
                for item in server.requests[request_index]["messages"]
                if item.get("role") == "tool" and item.get("tool_call_id") == call_id
            ]
            assert len(received) == 1, (system, request_index)
            text = received[0]["content"]
            if request_index == 2:
                assert "FAILED" in text, (system, text)
                assert "code 1" in text or "exit code: 1" in text, (system, text)
            else:
                assert "not" in text.lower() or "match" in text.lower(), (system, text)
        ends = [
            item["event"]
            for item in result.timed_events
            if item["event"].get("type") == "tool_execution_end"
        ]
        failed_ids = {
            event.get("toolCallId", event.get("tool_call_id"))
            for event in ends
            if event.get("isError", event.get("is_error"))
        }
        assert failed_ids == {"fixture-call-1", "fixture-call-2"}, (system, failed_ids)
        reports.append(
            {
                "system": system,
                "requests": len(server.requests),
                "tool_calls": len(ends),
                "recoverable_errors": len(failed_ids),
                "events": len(result.timed_events),
                "trace_bytes": sum(len(line.encode()) for line in result.stdout_lines),
                "max_event_bytes": max(len(line.encode()) for line in result.stdout_lines),
                "patch_verified": True,
                "test_unchanged": True,
                "thinking_disabled_on_wire": True,
                "token_usage_is_synthetic": True,
            }
        )
    print("\nOFFLINE_CLI_PAIR " + json.dumps(reports, sort_keys=True))
