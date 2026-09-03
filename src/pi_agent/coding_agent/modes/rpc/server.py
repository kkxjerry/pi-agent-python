from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeAlias, cast

from pi_agent.ai import ImageContent, Model, message_to_dict

from ...agent_session import AgentSession, AgentSessionEvent, PromptOptions
from ..json_event import parse_json_line, serialize_json_line, to_json_event

RpcObjectWriter: TypeAlias = Callable[[dict[str, Any]], Awaitable[None] | None]
RpcLineWriter: TypeAlias = Callable[[str], Awaitable[None] | None]


@dataclass(slots=True)
class RpcServer:
    session: AgentSession
    writer: RpcObjectWriter
    shutdown_requested: bool = False
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, init=False)
    _unsubscribe: Callable[[], None] | None = field(default=None, init=False)

    def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self.session.subscribe(self._on_session_event)

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    async def handle_line(self, line: str) -> None:
        try:
            request = parse_json_line(line)
        except Exception as exc:
            await self._write_response(None, "unknown", False, error=str(exc))
            return
        await self.handle_request(request)

    async def handle_request(self, request: dict[str, Any]) -> None:
        request_id = request.get("id")
        command = request.get("type")
        if not isinstance(command, str) or not command:
            await self._write_response(
                request_id,
                "unknown",
                False,
                error="RPC request requires a non-empty string type",
            )
            return
        try:
            if command == "prompt":
                await self._accept_prompt(request_id, request)
                return
            data = await self._dispatch(command, request)
            await self._write_response(request_id, command, True, data=data)
        except Exception as exc:
            await self._write_response(request_id, command, False, error=str(exc))

    async def _accept_prompt(self, request_id: Any, request: dict[str, Any]) -> None:
        message = _required_string(request, "message")
        behavior = request.get("streamingBehavior")
        if behavior not in {None, "steer", "followUp"}:
            raise ValueError("streamingBehavior must be 'steer' or 'followUp'")
        images = _parse_images(request.get("images"))
        if self.session.is_streaming:
            if behavior is None:
                raise RuntimeError("prompt while streaming requires streamingBehavior")
            await self.session.prompt(
                message,
                PromptOptions(
                    images=images,
                    streaming_behavior=cast(Any, behavior),
                ),
            )
            await self._write_response(
                request_id,
                "prompt",
                True,
                data={"queued": behavior},
            )
            return

        await self._write_response(request_id, "prompt", True, data={"accepted": True})

        async def run_prompt() -> None:
            try:
                await self.session.prompt(message, PromptOptions(images=images))
            except Exception as exc:
                await self._write(
                    {
                        "type": "rpc_error",
                        "command": "prompt",
                        "error": str(exc),
                    }
                )

        self._track(asyncio.create_task(run_prompt()))

    async def _dispatch(self, command: str, request: dict[str, Any]) -> Any:
        if command == "steer":
            await self.session.steer_async(_required_string(request, "message"))
            return {"queued": "steering"}
        if command == "follow_up":
            await self.session.follow_up_async(_required_string(request, "message"))
            return {"queued": "follow_up"}
        if command == "abort":
            self.session.abort(str(request.get("reason") or "Operation aborted"))
            return {"aborted": True}
        if command == "clear_queue":
            queue = request.get("queue", "all")
            if queue not in {"steering", "follow_up", "all"}:
                raise ValueError("queue must be steering, follow_up, or all")
            await self.session.clear_queue(cast(Any, queue))
            return {"queue": queue, "cleared": True}
        if command == "new_session":
            manager = await self.session.new_session(name=_optional_string(request, "name"))
            return {"sessionId": manager.header.id, "sessionFile": _session_file(self.session)}
        if command == "switch_session":
            manager = await self.session.switch_session(_required_string(request, "sessionId"))
            return {"sessionId": manager.header.id, "sessionFile": _session_file(self.session)}
        if command == "fork":
            entry_id = _optional_string(request, "entryId")
            await self.session.fork(entry_id)
            return {
                "sessionId": self.session.session_id,
                "leafId": self.session.get_tree()["leafId"],
            }
        if command == "get_state":
            return self.state_dict()
        if command == "set_model":
            model = self._resolve_model(request)
            await self.session.set_model(model)
            return {"model": _model_to_dict(model)}
        if command == "cycle_model":
            model = await self.session.cycle_model(_direction(request))
            return {"model": _model_to_dict(model)}
        if command == "get_available_models":
            return {
                "models": [_model_to_dict(model) for model in self.session.get_available_models()]
            }
        if command == "set_thinking_level":
            level = _required_string(request, "level")
            await self.session.set_thinking_level(cast(Any, level))
            return {"thinkingLevel": level}
        if command == "cycle_thinking_level":
            level = await self.session.cycle_thinking_level(_direction(request))
            return {"thinkingLevel": level}
        if command == "get_available_thinking_levels":
            return {"levels": self.session.get_available_thinking_levels()}
        if command == "set_steering_mode":
            mode = _queue_mode(request)
            self.session.set_steering_mode(mode)
            return {"steeringMode": mode}
        if command == "set_follow_up_mode":
            mode = _queue_mode(request)
            self.session.set_follow_up_mode(mode)
            return {"followUpMode": mode}
        if command == "compact":
            result = await self.session.compact("manual")
            if result is None:
                return {"compacted": False}
            return {
                "compacted": True,
                "summary": result.summary,
                "tokensBefore": result.tokens_before,
                "tokensAfter": result.tokens_after,
            }
        if command == "set_auto_compaction":
            enabled = _required_boolean(request, "enabled")
            self.session.set_auto_compaction(enabled)
            return {"autoCompactionEnabled": enabled}
        if command == "get_session_stats":
            return self.session.get_session_stats().to_dict()
        if command == "get_entries":
            return {"entries": self.session.get_entries()}
        if command == "get_tree":
            return self.session.get_tree()
        if command == "get_messages":
            return {
                "messages": [
                    message_to_dict(message) if not isinstance(message, dict) else message
                    for message in self.session.get_messages()
                ]
            }
        if command == "get_last_assistant_text":
            return {"text": self.session.get_last_assistant_text()}
        if command == "set_session_name":
            name = _optional_string(request, "name")
            await self.session.set_session_name(name)
            return {"name": name}
        if command == "reload_resources":
            resources = await self.session.reload_resources()
            return {
                "generation": resources.generation,
                "skills": [skill.name for skill in resources.skills],
                "prompts": [template.name for template in resources.prompt_templates],
                "warnings": [warning.message for warning in resources.warnings],
            }
        if command == "get_commands":
            return {
                "commands": [
                    {
                        "name": template.name,
                        "description": template.description,
                        "source": "prompt",
                    }
                    for template in self.session.resources.prompt_templates
                ]
                + [
                    {
                        "name": f"skill:{skill.name}",
                        "description": skill.description,
                        "source": "skill",
                    }
                    for skill in self.session.resources.skills
                ]
            }
        if command == "bash":
            return await self.session.execute_bash(
                _required_string(request, "command"),
                timeout=_optional_number(request, "timeout"),
            )
        if command == "shutdown":
            self.shutdown_requested = True
            self.session.abort("RPC shutdown")
            return {"shutdown": True}
        raise ValueError(f"Unknown RPC command: {command}")

    def state_dict(self) -> dict[str, Any]:
        state = self.session.state
        return {
            "model": _model_to_dict(state.model),
            "thinkingLevel": state.thinking_level,
            "isStreaming": state.is_streaming,
            "isCompacting": state.is_compacting,
            "isIdle": self.session.is_idle,
            "sessionId": state.session_id,
            "sessionName": state.session_name,
            "sessionFile": _session_file(self.session),
            "autoCompactionEnabled": state.auto_compaction_enabled,
            "messageCount": len(state.messages),
            "pendingToolCalls": sorted(state.pending_tool_calls),
            "errorMessage": state.error_message,
        }

    def _resolve_model(self, request: dict[str, Any]) -> Model:
        provider = _required_string(request, "provider")
        model_id = _required_string(request, "modelId")
        registry = self.session.model_registry
        if registry is None:
            current = self.session.agent.model
            if current.provider == provider and current.id == model_id:
                return current
            raise RuntimeError("model switching requires a model registry")
        return registry.get(provider, model_id)

    async def _on_session_event(self, event: AgentSessionEvent) -> None:
        await self._write(to_json_event(event))

    async def _write_response(
        self,
        request_id: Any,
        command: str,
        success: bool,
        *,
        data: Any = None,
        error: str | None = None,
    ) -> None:
        response: dict[str, Any] = {
            "type": "response",
            "command": command,
            "success": success,
        }
        if request_id is not None:
            response["id"] = request_id
        if success:
            if data is not None:
                response["data"] = data
        else:
            response["error"] = error or "Unknown RPC error"
        await self._write(response)

    async def _write(self, value: dict[str, Any]) -> None:
        async with self._write_lock:
            result = self.writer(value)
            if inspect.isawaitable(result):
                await result

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def run_rpc_mode(
    session: AgentSession,
    reader: AsyncIterable[str],
    writer: RpcLineWriter,
) -> None:
    async def write_object(value: dict[str, Any]) -> None:
        result = writer(serialize_json_line(value))
        if inspect.isawaitable(result):
            await result

    server = RpcServer(session, write_object)
    server.start()
    try:
        async for line in reader:
            # Prompt requests acknowledge immediately and run the agent in a
            # background task, so processing control commands sequentially does
            # not block steer/follow_up/abort. Sequential dispatch also makes
            # response ordering deterministic and lets shutdown stop the reader
            # before it consumes another line.
            await server.handle_line(line)
            if server.shutdown_requested:
                break
        await session.wait_for_idle()
    finally:
        await server.close()


async def run_rpc_stdio(session: AgentSession) -> None:
    async def lines() -> AsyncIterable[str]:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                return
            yield line

    async def write(line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()

    await run_rpc_mode(session, lines(), write)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string or null")
    return item


def _required_boolean(value: dict[str, Any], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _optional_number(value: dict[str, Any], key: str) -> float | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int | float):
        raise ValueError(f"{key} must be a number")
    return float(item)


def _queue_mode(value: dict[str, Any]) -> Any:
    mode = value.get("mode")
    if mode not in {"all", "one-at-a-time"}:
        raise ValueError("mode must be all or one-at-a-time")
    return mode


def _direction(value: dict[str, Any]) -> int:
    direction = value.get("direction", 1)
    if direction in {-1, "previous", "backward"}:
        return -1
    if direction in {1, "next", "forward"}:
        return 1
    raise ValueError("direction must be next/forward/1 or previous/backward/-1")


def _parse_images(value: Any) -> tuple[ImageContent, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("images must be an array")
    images: list[ImageContent] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"images[{index}] must be an object")
        data = item.get("data")
        mime_type = item.get("mimeType")
        if not isinstance(data, str) or not isinstance(mime_type, str):
            raise ValueError(f"images[{index}] requires data and mimeType strings")
        images.append(ImageContent(data, mime_type))
    return tuple(images)


def _model_to_dict(model: Model) -> dict[str, Any]:
    return {
        "provider": model.provider,
        "id": model.id,
        "name": model.name,
        "api": model.api,
        "reasoning": model.reasoning,
        "input": list(model.input),
        "contextWindow": model.context_window,
        "maxTokens": model.max_tokens,
    }


def _session_file(session: AgentSession) -> str | None:
    path = session.session_file
    return str(path) if path is not None else None
