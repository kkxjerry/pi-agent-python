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
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
        payloads = [
            {
                "id": "response-1",
                "model": "fixture-model",
                "choices": [{"delta": {"content": "cli result"}, "finish_reason": "stop"}],
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
        encoded = "".join(
            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n" for payload in payloads
        )
        encoded += "data: [DONE]\n\n"
        raw = encoded.encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    _SseHandler.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/v1"


def _arguments(tmp_path: Path, base_url: str) -> list[str]:
    return [
        "--model",
        "fixture-model",
        "--provider",
        "local",
        "--base-url",
        base_url,
        "--context-window",
        "32768",
        "--cwd",
        str(tmp_path),
        "--no-session",
        "--no-tools",
    ]


def test_cli_print_shorthand_runs_real_openai_compatible_stream(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    server, thread, base_url = _serve()
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        status = main(["-p", *_arguments(tmp_path, base_url), "hello"])
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


def test_cli_json_mode_keeps_stdout_as_jsonl(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    server, thread, base_url = _serve()
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        status = main(
            [
                "--mode",
                "json",
                *_arguments(tmp_path, base_url),
                "hello",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert status == 0
    assert captured.err == ""
    assert lines[0]["type"] == "session"
    assert any(line["type"] == "message_update" for line in lines)
    assert lines[-1]["type"] == "agent_settled"
