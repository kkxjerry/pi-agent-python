from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from .types import (
    DuplicateContributionError,
    ExtensionActivationError,
    ExtensionCommand,
    ExtensionCommandContext,
    ExtensionContribution,
    ExtensionDescriptor,
    ExtensionDiagnostic,
    ExtensionDisposer,
    ExtensionEventHandler,
    ExtensionHandle,
    ExtensionPolicy,
)


class ExtensionAPI:
    """Registration API exposed to one extension during staged activation."""

    def __init__(
        self,
        *,
        descriptor: ExtensionDescriptor,
        cwd: Path,
        settings: Mapping[str, Any],
        services: Mapping[str, Any],
        session: Any,
        contribution: ExtensionContribution,
        reserved_tools: frozenset[str],
        reserved_commands: frozenset[str],
        reserved_services: frozenset[str],
    ) -> None:
        self.descriptor = descriptor
        self.cwd = cwd
        self.settings = settings
        self.services = services
        self.session = session
        self._contribution = contribution
        self._reserved_tools = reserved_tools
        self._reserved_commands = reserved_commands
        self._reserved_services = reserved_services

    def register_tool(self, tool: Any, *, name: str | None = None) -> None:
        selected = name or getattr(tool, "name", None)
        if not isinstance(selected, str) or not selected:
            raise ValueError("registered tools require a non-empty name")
        if selected in self._reserved_tools or selected in self._contribution.tools:
            raise DuplicateContributionError(
                self.descriptor, f"duplicate tool contribution {selected!r}"
            )
        self._contribution.tools[selected] = tool

    def register_command(
        self,
        name: str,
        handler: Any,
        *,
        description: str = "",
        argument_hint: str | None = None,
    ) -> None:
        if name in self._reserved_commands or name in self._contribution.commands:
            raise DuplicateContributionError(
                self.descriptor, f"duplicate command contribution {name!r}"
            )
        if not callable(handler):
            raise TypeError("extension command handler must be callable")
        self._contribution.commands[name] = ExtensionCommand(
            name=name,
            handler=handler,
            description=description,
            argument_hint=argument_hint,
        )

    def register_service(self, name: str, service: Any) -> None:
        if not name:
            raise ValueError("service name must not be empty")
        if name in self._reserved_services or name in self._contribution.services:
            raise DuplicateContributionError(
                self.descriptor, f"duplicate service contribution {name!r}"
            )
        self._contribution.services[name] = service

    def append_system_prompt(self, text: str) -> None:
        if text.strip():
            self._contribution.system_prompts.append(text.strip())

    def on_event(self, handler: ExtensionEventHandler) -> None:
        if not callable(handler):
            raise TypeError("extension event handler must be callable")
        self._contribution.event_handlers.append(handler)

    def on_dispose(self, disposer: ExtensionDisposer) -> None:
        if not callable(disposer):
            raise TypeError("extension disposer must be callable")
        self._contribution.disposers.append(disposer)


class ExtensionHost:
    """Transactional Python extension loader and contribution registry.

    Extensions execute in-process and are therefore trusted code. ``ExtensionPolicy``
    gates paths and declared capabilities, but it is not a security sandbox.
    """

    def __init__(
        self,
        *,
        cwd: str | Path,
        settings: Mapping[str, Any] | None = None,
        services: Mapping[str, Any] | None = None,
        session: Any = None,
        policy: ExtensionPolicy | None = None,
    ) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.settings = dict(settings or {})
        self.base_services = dict(services or {})
        self.session = session
        self.policy = policy or ExtensionPolicy()
        self._handles: dict[str, ExtensionHandle] = {}
        self._diagnostics: list[ExtensionDiagnostic] = []
        self._lock = asyncio.Lock()

    @property
    def handles(self) -> tuple[ExtensionHandle, ...]:
        return tuple(
            sorted(
                self._handles.values(),
                key=lambda item: (
                    item.descriptor.priority,
                    str(item.descriptor.path),
                    item.descriptor.name,
                ),
            )
        )

    @property
    def diagnostics(self) -> tuple[ExtensionDiagnostic, ...]:
        return tuple(self._diagnostics)

    @property
    def tools(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for handle in self.handles:
            result.update(handle.contribution.tools)
        return result

    @property
    def commands(self) -> dict[str, ExtensionCommand]:
        result: dict[str, ExtensionCommand] = {}
        for handle in self.handles:
            result.update(handle.contribution.commands)
        return result

    @property
    def services(self) -> dict[str, Any]:
        result = dict(self.base_services)
        for handle in self.handles:
            result.update(handle.contribution.services)
        return result

    @property
    def system_prompts(self) -> tuple[str, ...]:
        return tuple(text for handle in self.handles for text in handle.contribution.system_prompts)

    async def activate(self, descriptor: ExtensionDescriptor) -> ExtensionHandle:
        async with self._lock:
            if descriptor.name in self._handles:
                raise ExtensionActivationError(descriptor, "an extension with this name is active")
            handle = await self._stage(descriptor)
            self._handles[descriptor.name] = handle
            return handle

    async def activate_many(
        self,
        descriptors: Iterable[ExtensionDescriptor],
        *,
        strict: bool = True,
    ) -> tuple[ExtensionHandle, ...]:
        ordered = sorted(
            descriptors,
            key=lambda item: (item.priority, str(item.path), item.name),
        )
        activated: list[ExtensionHandle] = []
        for descriptor in ordered:
            try:
                activated.append(await self.activate(descriptor))
            except Exception as exc:
                self._diagnostics.append(
                    ExtensionDiagnostic(
                        "error",
                        "activation_failed",
                        str(exc),
                        descriptor.name,
                        descriptor.path,
                    )
                )
                if not strict:
                    continue
                for handle in reversed(activated):
                    await self.deactivate(handle.descriptor.name, suppress_errors=True)
                raise
        return tuple(activated)

    async def reload(self, name: str) -> ExtensionHandle:
        async with self._lock:
            old = self._handles.get(name)
            if old is None:
                raise KeyError(f"unknown extension: {name}")
            candidate = await self._stage(old.descriptor, ignore_extension=name)
            try:
                await self._dispose_handle(old)
            except Exception as exc:
                self._diagnostics.append(
                    ExtensionDiagnostic(
                        "warning",
                        "dispose_failed_during_reload",
                        str(exc),
                        name,
                        old.descriptor.path,
                    )
                )
            self._handles[name] = candidate
            return candidate

    async def deactivate(self, name: str, *, suppress_errors: bool = False) -> bool:
        async with self._lock:
            handle = self._handles.pop(name, None)
            if handle is None:
                return False
            try:
                await self._dispose_handle(handle)
            except Exception as exc:
                self._diagnostics.append(
                    ExtensionDiagnostic(
                        "error",
                        "dispose_failed",
                        str(exc),
                        name,
                        handle.descriptor.path,
                    )
                )
                if not suppress_errors:
                    raise
            return True

    async def close(self) -> None:
        for handle in reversed(self.handles):
            await self.deactivate(handle.descriptor.name, suppress_errors=True)

    async def invoke_command(
        self,
        name: str,
        arguments: Iterable[str] = (),
        *,
        session: Any = None,
    ) -> Any:
        command = self.commands.get(name)
        if command is None:
            raise KeyError(f"unknown extension command: {name}")
        owner = next(
            handle.descriptor.name
            for handle in self.handles
            if name in handle.contribution.commands
        )
        context = ExtensionCommandContext(
            extension_name=owner,
            cwd=self.cwd,
            services=self.services,
            session=self.session if session is None else session,
        )
        value = command.handler(tuple(arguments), context)
        return await _maybe_await(value)

    async def dispatch_event(self, event: Any) -> tuple[ExtensionDiagnostic, ...]:
        diagnostics: list[ExtensionDiagnostic] = []
        for handle in self.handles:
            for handler in tuple(handle.contribution.event_handlers):
                try:
                    await _maybe_await(handler(event))
                except Exception as exc:
                    diagnostic = ExtensionDiagnostic(
                        "error",
                        "event_handler_failed",
                        str(exc),
                        handle.descriptor.name,
                        handle.descriptor.path,
                    )
                    diagnostics.append(diagnostic)
                    self._diagnostics.append(diagnostic)
        return tuple(diagnostics)

    async def _stage(
        self,
        descriptor: ExtensionDescriptor,
        *,
        ignore_extension: str | None = None,
    ) -> ExtensionHandle:
        entry = self._validate_descriptor(descriptor)
        reserved_tools, reserved_commands, reserved_services = self._reserved_names(
            ignore_extension
        )
        contribution = ExtensionContribution()
        module_name = _module_name(descriptor, entry)
        module: ModuleType | None = None
        try:
            module = _load_module(entry, module_name)
            api = ExtensionAPI(
                descriptor=descriptor,
                cwd=self.cwd,
                settings=self.settings,
                services=self.services,
                session=self.session,
                contribution=contribution,
                reserved_tools=frozenset(reserved_tools),
                reserved_commands=frozenset(reserved_commands),
                reserved_services=frozenset(reserved_services),
            )
            activate = getattr(module, "activate", None)
            if activate is None or not callable(activate):
                raise ExtensionActivationError(
                    descriptor, "module must export a callable activate(api)"
                )
            result = await _maybe_await(activate(api))
            disposer = _result_disposer(result)
            if disposer is not None:
                contribution.disposers.append(disposer)
            return ExtensionHandle(descriptor, module_name, contribution, module)
        except Exception as exc:
            cleanup_error: Exception | None = None
            try:
                await _dispose_all(contribution.disposers)
            except Exception as dispose_exc:
                cleanup_error = dispose_exc
            finally:
                sys.modules.pop(module_name, None)
            error = (
                exc
                if isinstance(exc, ExtensionActivationError)
                else ExtensionActivationError(descriptor, str(exc))
            )
            if cleanup_error is not None:
                error.add_note(f"staged extension cleanup also failed: {cleanup_error}")
            if error is exc:
                raise
            raise error from exc

    def _validate_descriptor(self, descriptor: ExtensionDescriptor) -> Path:
        path = descriptor.path
        if path.is_symlink():
            raise ExtensionActivationError(descriptor, "symbolic-link entry points are not allowed")
        entry = path / "__init__.py" if path.is_dir() else path
        if not entry.is_file() or entry.suffix != ".py":
            raise ExtensionActivationError(descriptor, f"entry point is not a Python file: {entry}")
        if self.policy.allowed_roots and not any(
            entry.is_relative_to(root) for root in self.policy.allowed_roots
        ):
            raise ExtensionActivationError(descriptor, "entry point is outside allowed roots")
        requested = set(descriptor.capabilities)
        if not self.policy.allow_all_capabilities:
            denied = requested - set(self.policy.allowed_capabilities)
            if denied:
                rendered = ", ".join(sorted(denied))
                raise ExtensionActivationError(
                    descriptor, f"extension capabilities are not allowed: {rendered}"
                )
        return entry

    def _reserved_names(self, ignore_extension: str | None) -> tuple[set[str], set[str], set[str]]:
        tools: set[str] = set()
        commands: set[str] = set()
        services = set(self.base_services)
        for name, handle in self._handles.items():
            if name == ignore_extension:
                continue
            tools.update(handle.contribution.tools)
            commands.update(handle.contribution.commands)
            services.update(handle.contribution.services)
        return tools, commands, services

    async def _dispose_handle(self, handle: ExtensionHandle) -> None:
        try:
            await _dispose_all(handle.contribution.disposers)
        finally:
            sys.modules.pop(handle.module_name, None)


def descriptor_from_path(
    path: str | Path,
    *,
    name: str | None = None,
    source: str = "explicit",
    priority: int = 0,
    version: str | None = None,
    capabilities: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> ExtensionDescriptor:
    actual = Path(path).expanduser().resolve()
    selected_name = name or (actual.name if actual.is_dir() else actual.stem)
    return ExtensionDescriptor(
        name=selected_name,
        path=actual,
        source=source,
        priority=priority,
        version=version,
        capabilities=tuple(capabilities),
        metadata=dict(metadata or {}),
    )


def _module_name(descriptor: ExtensionDescriptor, entry: Path) -> str:
    try:
        modified = entry.stat().st_mtime_ns
    except OSError:
        modified = time.time_ns()
    digest = hashlib.sha256(f"{entry}:{modified}:{time.time_ns()}".encode()).hexdigest()[:16]
    safe = "".join(character if character.isalnum() else "_" for character in descriptor.name)
    return f"_pi_extension_{safe}_{digest}"


def _load_module(entry: Path, module_name: str) -> ModuleType:
    package_locations = [str(entry.parent)] if entry.name == "__init__.py" else None
    spec = importlib.util.spec_from_file_location(
        module_name,
        entry,
        submodule_search_locations=package_locations,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not create an import specification for {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _result_disposer(value: Any) -> ExtensionDisposer | None:
    if value is None:
        return None
    if callable(value):
        return cast(ExtensionDisposer, value)
    dispose = getattr(value, "dispose", None)
    if callable(dispose):
        return cast(ExtensionDisposer, dispose)
    raise TypeError("activate(api) must return None, a disposer, or an object with dispose()")


async def _dispose_all(disposers: Iterable[ExtensionDisposer]) -> None:
    errors: list[Exception] = []
    for disposer in reversed(tuple(disposers)):
        try:
            await _maybe_await(disposer())
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise ExceptionGroup("one or more extension disposers failed", errors)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["ExtensionAPI", "ExtensionHost", "descriptor_from_path"]
