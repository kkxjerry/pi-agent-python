from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

from pi_agent.cli import main


class _SseHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).requests.append(payload)
        events = [
            {
                "id": "response-1",
                "model": "fixture-model",
                "choices": [
                    {
                        "delta": {"content": "cli result"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ]
        body = "".join(f"data: {json.dumps(event, separators=(',', ':'))}\n\n" for event in events)
        body += "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _server() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    _SseHandler.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/v1"


def _runtime_args(tmp_path: Path, base_url: str) -> list[str]:
    return [
        "--cwd",
        str(tmp_path),
        "--no-session",
        "--provider",
        "local",
        "--model",
        "fixture-model",
        "--api",
        "openai-completions",
        "--base-url",
        base_url,
    ]


def test_cli_print_mode_runs_real_openai_compatible_stream(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    server, thread, base_url = _server()
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        status = main([*_runtime_args(tmp_path, base_url), "-p", "hello"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == "cli result\n"
    assert captured.err == ""
    assert _SseHandler.requests[-1]["messages"][-1] == {
        "role": "user",
        "content": "hello",
    }


def test_cli_json_mode_keeps_stdout_jsonl(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    server, thread, base_url = _server()
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        status = main(
            [
                *_runtime_args(tmp_path, base_url),
                "--mode",
                "json",
                "-p",
                "hello",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]
    assert status == 0
    assert captured.err == ""
    assert records[0]["type"] == "session"
    assert records[-1]["type"] == "agent_settled"
    assert any(record["type"] == "message_update" for record in records)
