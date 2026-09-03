from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import replace
from typing import Any, TextIO

from pi_agent.agent import AgentEvent
from pi_agent.ai import Model

from ..agent_session import AgentSession
from ..events import agent_event_to_dict

RpcWriter = TextIO | Callable[[str], None | Awaitable[None]]


class RpcProtocolError(ValueError):
    pass


class RpcServer:
    """LF-delimited JSON RPC controller for one shared AgentSession."""

    def __init__(self, session: AgentSession, writer: RpcWriter) -> None:
        self.session = session
        self.writer = writer
        self.closed = False
        self._write_lock = asyncio.Lock()
        self._active_run: asyncio.Task[None] | None = None
        self._unsubscribe = session.subscribe(self._on_agent_event)

    async def handle_line(self, line: str) -> bool:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            await self._write(
                {
                    "type": "response",
                    "id": None,
                    "success": False,
                    "error": {"code": "invalid_json", "message": str(exc)},
                }
            )
            return not self.closed
        if not isinstance(request, dict):
            await self._error(None, "invalid_request", "RPC request must be a JSON object")
            return not self.closed
        request_id = request.get("id")
        command = request.get("command", request.get("type"))
        if not isinstance(command, str):
            await self._error(request_id, "invalid_request", "RPC request requires command")
            return not self.closed
        try:
            await self._dispatch(request_id, command, request)
        except RpcProtocolError as exc:
            await self._error(request_id, "invalid_params", str(exc))
        except Exception as exc:
            await self._error(request_id, "command_failed", str(exc))
        return not self.closed

    async def serve(self, lines: AsyncIterable[str]) -> None:
        try:
            async for line in lines:
                if not line.endswith("\n"):
                    await self._error(
                        None, "invalid_framing", "RPC input must be LF-delimited JSON"
                    )
                    continue
                if not await self.handle_line(line[:-1]):
                    break
        finally:
            if self._active_run is not None and not self._active_run.done():
                self.session.abort("RPC input closed")
                await asyncio.gather(self._active_run, return_exceptions=True)
            await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._unsubscribe()
        await self.session.close()

    async def _dispatch(
        self,
        request_id: Any,
        command: str,
        request: dict[str, Any],
    ) -> None:
        if command == "prompt":
            prompt = request.get("message", request.get("prompt"))
            if not isinstance(prompt, str) or not prompt:
                raise RpcProtocolError("prompt requires a non-empty message")
            await self._start_run(request_id, lambda: self.session.prompt(prompt))
            return
        if command in {"continue", "continue_run"}:
            await self._start_run(request_id, self.session.continue_run)
            return
        if command == "steer":
            message = _required_string(request, "message")
            self.session.steer(message)
            await self._success(request_id, {"queued": True})
            return
        if command in {"follow_up", "followUp"}:
            message = _required_string(request, "message")
            self.session.follow_up(message)
            await self._success(request_id, {"queued": True})
            return
        if command == "abort":
            self.session.abort(str(request.get("reason") or "RPC abort"))
            await self._success(request_id, {"aborted": self.session.is_running})
            return
        if command in {"get_state", "state"}:
            await self._success(request_id, self.session.get_state())
            return
        if command in {"get_models", "models"}:
            model = self.session.model
            await self._success(
                request_id,
                {
                    "models": [
                        {
                            "provider": model.provider,
                            "id": model.id,
                            "api": model.api,
                            "baseUrl": model.base_url,
                            "contextWindow": model.context_window,
                            "maxTokens": model.max_tokens,
                        }
                    ]
                },
            )
            return
        if command in {"set_model", "setModel"}:
            if self.session.is_running:
                raise RpcProtocolError("cannot set model while a run is active")
            current = self.session.model
            raw = request.get("model")
            if isinstance(raw, str):
                model = replace(current, id=raw, name=raw)
            elif isinstance(raw, dict):
                model = _model_patch(current, raw)
            else:
                raise RpcProtocolError("set_model requires model string or object")
            await self.session.switch_model(model)
            await self._success(request_id, self.session.get_state()["model"])
            return
        if command in {"set_thinking", "set_thinking_level", "setThinkingLevel"}:
            level = _required_string(request, "level")
            await self.session.set_thinking_level(level)
            await self._success(request_id, {"thinkingLevel": level})
            return
        if command == "compact":
            result = await self.session.compact(force=bool(request.get("force", True)))
            await self._success(request_id, {"compaction": result})
            return
        if command in {"get_tree", "tree"}:
            await self._success(
                request_id,
                {
                    "currentId": self.session.session_manager.current_id,
                    "tree": self.session.get_tree(),
                },
            )
            return
        if command in {"navigate_tree", "navigate"}:
            entry_id = request.get("entryId")
            if entry_id is not None and not isinstance(entry_id, str):
                raise RpcProtocolError("entryId must be a string or null")
            await self.session.navigate_tree(
                entry_id,
                summarize_abandoned_branch=bool(request.get("summarize", False)),
            )
            await self._success(request_id, {"currentId": self.session.session_manager.current_id})
            return
        if command in {"reload_resources", "reload"}:
            resources = await self.session.reload_resources()
            await self._success(
                request_id,
                {
                    "skills": sorted(resources.skills),
                    "prompts": sorted(resources.prompts),
                    "themes": sorted(resources.themes),
                    "extensions": sorted(resources.extensions),
                    "warnings": [warning.message for warning in resources.warnings],
                },
            )
            return
        if command == "new_session":
            path = request.get("path")
            if path is not None and not isinstance(path, str):
                raise RpcProtocolError("path must be a string")
            name = request.get("name")
            if name is not None and not isinstance(name, str):
                raise RpcProtocolError("name must be a string")
            await self.session.new_session(
                path=path,
                name=name,
                no_session=bool(request.get("noSession", False)),
            )
            await self._success(request_id, self.session.get_state()["session"])
            return
        if command == "resume_session":
            path = _required_string(request, "path")
            await self.session.resume_session(
                path, repair_tail=bool(request.get("repairTail", False))
            )
            await self._success(request_id, self.session.get_state()["session"])
            return
        if command == "shutdown":
            await self._success(request_id, {"shutdown": True})
            if self._active_run is not None and not self._active_run.done():
                self.session.abort("RPC shutdown")
                await asyncio.gather(self._active_run, return_exceptions=True)
            self.closed = True
            return
        raise RpcProtocolError(f"unknown command: {command}")

    async def _start_run(
        self,
        request_id: Any,
        operation: Callable[[], Awaitable[Any]],
    ) -> None:
        if self._active_run is not None and not self._active_run.done():
            raise RpcProtocolError("an agent run is already active")
        await self._success(request_id, {"accepted": True})

        async def run() -> None:
            try:
                result = await operation()
                await self._write(
                    {
                        "type": "notification",
                        "event": "run_complete",
                        "requestId": request_id,
                        "result": {
                            "succeeded": result.succeeded,
                            "aborted": result.aborted,
                            "errorMessage": result.error_message,
                            "newMessageCount": len(result.new_messages),
                        },
                    }
                )
            except Exception as exc:
                await self._write(
                    {
                        "type": "notification",
                        "event": "run_failed",
                        "requestId": request_id,
                        "error": {"code": "run_failed", "message": str(exc)},
                    }
                )

        self._active_run = asyncio.create_task(run())

    async def _on_agent_event(self, event: AgentEvent) -> None:
        await self._write({"type": "event", "event": agent_event_to_dict(event)})

    async def _success(self, request_id: Any, result: Any) -> None:
        await self._write({"type": "response", "id": request_id, "success": True, "result": result})

    async def _error(self, request_id: Any, code: str, message: str) -> None:
        await self._write(
            {
                "type": "response",
                "id": request_id,
                "success": False,
                "error": {"code": code, "message": message},
            }
        )

    async def _write(self, value: object) -> None:
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            if callable(self.writer):
                result = self.writer(line)
                if inspect.isawaitable(result):
                    await result
            else:
                self.writer.write(line)
                flush = getattr(self.writer, "flush", None)
                if flush is not None:
                    result = flush()
                    if inspect.isawaitable(result):
                        await result


async def run_rpc_mode(
    session: AgentSession,
    *,
    lines: AsyncIterable[str] | None = None,
    output: RpcWriter | None = None,
) -> None:
    server = RpcServer(session, output or sys.stdout)
    await server.serve(lines or _stdin_lines())


async def _stdin_lines() -> AsyncIterable[str]:
    while True:
        raw = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not raw:
            return
        yield raw.decode("utf-8", errors="replace")


def _required_string(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value:
        raise RpcProtocolError(f"{key} must be a non-empty string")
    return value


def _model_patch(current: Model, value: dict[str, Any]) -> Model:
    model_id = value.get("id", current.id)
    provider = value.get("provider", current.provider)
    api = value.get("api", current.api)
    base_url = value.get("baseUrl", value.get("base_url", current.base_url))
    if not all(isinstance(item, str) and item for item in (model_id, provider, api, base_url)):
        raise RpcProtocolError("model id/provider/api/baseUrl must be non-empty strings")
    context_window = value.get("contextWindow", current.context_window)
    max_tokens = value.get("maxTokens", current.max_tokens)
    if not isinstance(context_window, int) or context_window <= 0:
        raise RpcProtocolError("contextWindow must be a positive integer")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise RpcProtocolError("maxTokens must be a positive integer")
    return replace(
        current,
        id=model_id,
        name=str(value.get("name", model_id)),
        provider=provider,
        api=api,
        base_url=base_url,
        context_window=context_window,
        max_tokens=max_tokens,
    )
