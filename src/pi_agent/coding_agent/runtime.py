from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import AuthStorage, CredentialResolver, RefreshCredential
from .extensions import (
    ExtensionDescriptor,
    ExtensionHost,
    ExtensionPolicy,
    descriptor_from_path,
)
from .packages import (
    PackageInstallReceipt,
    PackageLockEntry,
    PackageManager,
    PackageRemoveReceipt,
    PackageVerification,
)


@dataclass(slots=True)
class CodingAgentRuntimeOptions:
    package_root: str | Path | None = None
    auth_path: str | Path | None = None
    extension_paths: tuple[Path, ...] = ()
    extension_capabilities: frozenset[str] = frozenset()
    allow_all_extension_capabilities: bool = False
    strict_extensions: bool = True
    environment: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeState:
    started: bool
    extensions: tuple[str, ...]
    commands: tuple[str, ...]
    tools: tuple[str, ...]
    packages: tuple[str, ...]


class CodingAgentRuntime:
    """Compose credentials, packages, and extensions above one AgentSession.

    Agent Core remains unaware of package discovery and Python module loading.
    Package mutations are committed only after a complete candidate extension
    host and the session resource snapshot have both been validated.
    """

    def __init__(
        self,
        session: Any,
        *,
        options: CodingAgentRuntimeOptions | None = None,
    ) -> None:
        self.session = session
        self.options = options or CodingAgentRuntimeOptions()
        configured_root = self.options.package_root or _setting(
            session, "resources.user_root", "~/.pi/agent"
        )
        package_root = Path(configured_root).expanduser()
        auth_path = Path(self.options.auth_path or package_root / "auth.json").expanduser()
        self.packages = PackageManager(package_root)
        self.auth = AuthStorage(auth_path)
        if self.options.environment is None:
            self.credentials = CredentialResolver(storage=self.auth)
        else:
            self.credentials = CredentialResolver(
                storage=self.auth,
                environment=self.options.environment,
            )

        allowed_roots = {
            path.expanduser().resolve().parent if path.suffix else path.expanduser().resolve()
            for path in self.options.extension_paths
        }
        allowed_roots.add(self.packages.packages_dir.resolve())
        for descriptor in _resource_descriptors(getattr(session, "resources", None)):
            allowed_roots.add(descriptor.path.parent)
        self._policy = ExtensionPolicy(
            allowed_roots=tuple(sorted(allowed_roots, key=str)),
            allowed_capabilities=self.options.extension_capabilities,
            allow_all_capabilities=self.options.allow_all_extension_capabilities,
        )
        self.extensions = self._new_host()
        self._started = False
        self._base_tools: list[Any] = []
        self._original_system_prompt = ""
        self._base_system_prompt = ""
        self._base_resource_roots: tuple[Path, ...] = ()
        self._unsubscribe: Any = None

    async def start(self) -> RuntimeState:
        if self._started:
            return await self.state()
        agent = _agent(self.session)
        if bool(getattr(agent, "is_streaming", False)):
            raise RuntimeError("cannot start extension runtime while the agent is running")
        self._base_tools = list(getattr(agent, "tools", ()))
        self._original_system_prompt = str(getattr(agent, "system_prompt", ""))
        self._base_system_prompt = self._original_system_prompt
        self._base_resource_roots = _resource_roots(self.session)
        try:
            await self._sync_package_resources()
            descriptors = await self._discover_descriptors()
            candidate = await self._candidate_host(descriptors)
            self._validate_tool_contributions(candidate)
            old = self.extensions
            self.extensions = candidate
            self._apply_contributions()
            await old.close()
            subscribe = getattr(self.session, "subscribe", None)
            if callable(subscribe):
                self._unsubscribe = subscribe(self._dispatch_event)
            self._started = True
            return await self.state()
        except Exception:
            await self.extensions.close()
            await self._restore_resources()
            self._restore_agent()
            raise

    async def close(self, *, close_session: bool = False) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        wait = getattr(self.session, "wait_for_idle", None)
        if callable(wait):
            await _maybe_await(wait())
        await self.extensions.close()
        await self._restore_resources()
        self._restore_agent()
        self._started = False
        if close_session:
            close = getattr(self.session, "close", None)
            if callable(close):
                await _maybe_await(close())

    async def state(self) -> RuntimeState:
        packages = await self.packages.list()
        return RuntimeState(
            started=self._started,
            extensions=tuple(handle.descriptor.name for handle in self.extensions.handles),
            commands=tuple(sorted(self.extensions.commands)),
            tools=tuple(sorted(self.extensions.tools)),
            packages=tuple(entry.name for entry in packages),
        )

    async def api_key(
        self,
        provider: str,
        *,
        explicit: str | None = None,
        refresh: RefreshCredential | None = None,
    ) -> str | None:
        return await self.credentials.api_key(provider, explicit=explicit, refresh=refresh)

    async def invoke_command(self, name: str, arguments: Iterable[str] = ()) -> Any:
        self._ensure_started()
        return await self.extensions.invoke_command(name, arguments, session=self.session)

    async def reload_extensions(self) -> RuntimeState:
        self._ensure_idle()
        await self._sync_package_resources()
        descriptors = await self._discover_descriptors()
        try:
            candidate = await self._candidate_host(descriptors)
            self._validate_tool_contributions(candidate)
        except Exception:
            self._apply_contributions()
            raise
        old = self.extensions
        self.extensions = candidate
        try:
            self._apply_contributions()
        except Exception:
            self.extensions = old
            await candidate.close()
            self._apply_contributions()
            raise
        await old.close()
        self._started = True
        return await self.state()

    async def install_package(self, source: str | Path) -> PackageLockEntry:
        self._ensure_idle()
        receipt = await self.packages.install(source, finalize=False)
        try:
            await self._validate_receipt_extensions(receipt)
            await self.reload_extensions()
            await receipt.finalize()
            return receipt.entry
        except Exception as exc:
            await self._rollback_install(receipt, exc)
            raise

    async def update_package(
        self,
        name: str,
        source: str | Path | None = None,
    ) -> PackageLockEntry:
        self._ensure_idle()
        receipt = await self.packages.update(name, source, finalize=False)
        try:
            await self._validate_receipt_extensions(receipt)
            await self.reload_extensions()
            await receipt.finalize()
            return receipt.entry
        except Exception as exc:
            await self._rollback_install(receipt, exc)
            raise

    async def remove_package(self, name: str) -> bool:
        self._ensure_idle()
        receipt = await self.packages.stage_remove(name)
        if receipt is None:
            return False
        try:
            await self.reload_extensions()
            await receipt.finalize()
            return True
        except Exception as exc:
            await self._rollback_remove(receipt, exc)
            raise

    async def verify_packages(self) -> tuple[PackageVerification, ...]:
        return await self.packages.verify_all()

    async def _rollback_install(
        self,
        receipt: PackageInstallReceipt,
        original: Exception,
    ) -> None:
        try:
            await receipt.rollback()
            await self.reload_extensions()
        except Exception as rollback_error:
            original.add_note(f"package rollback also failed: {rollback_error}")

    async def _rollback_remove(
        self,
        receipt: PackageRemoveReceipt,
        original: Exception,
    ) -> None:
        try:
            await receipt.rollback()
            await self.reload_extensions()
        except Exception as rollback_error:
            original.add_note(f"package removal rollback also failed: {rollback_error}")

    async def _validate_receipt_extensions(self, receipt: PackageInstallReceipt) -> None:
        descriptors = tuple(
            descriptor
            for descriptor in await self.packages.extension_descriptors()
            if descriptor.metadata.get("package") == receipt.entry.name
        )
        validator = self._new_host()
        try:
            await validator.activate_many(descriptors, strict=True)
        finally:
            await validator.close()

    async def _candidate_host(
        self,
        descriptors: Iterable[ExtensionDescriptor],
    ) -> ExtensionHost:
        candidate = self._new_host()
        try:
            await candidate.activate_many(
                descriptors,
                strict=self.options.strict_extensions,
            )
            return candidate
        except Exception:
            await candidate.close()
            raise

    def _new_host(self) -> ExtensionHost:
        return ExtensionHost(
            cwd=_session_cwd(self.session),
            settings=_settings_mapping(self.session),
            services={
                "session": self.session,
                "package_manager": self.packages,
                "auth_storage": self.auth,
                "runtime": self,
            },
            session=self.session,
            policy=self._policy,
        )

    async def _discover_descriptors(self) -> tuple[ExtensionDescriptor, ...]:
        values: list[ExtensionDescriptor] = []
        for index, path in enumerate(self.options.extension_paths):
            values.append(
                descriptor_from_path(
                    path,
                    source="explicit",
                    priority=30_000 + index,
                )
            )
        package_directory = self.packages.packages_dir.resolve()
        values.extend(
            descriptor
            for descriptor in _resource_descriptors(getattr(self.session, "resources", None))
            if not descriptor.path.is_relative_to(package_directory)
        )
        values.extend(await self.packages.extension_descriptors())
        selected: dict[str, ExtensionDescriptor] = {}
        for descriptor in sorted(
            values,
            key=lambda item: (item.priority, str(item.path), item.name),
        ):
            previous = selected.get(descriptor.name)
            if previous is not None and previous.path != descriptor.path:
                raise ValueError(
                    f"extension name {descriptor.name!r} is provided by both "
                    f"{previous.path} and {descriptor.path}"
                )
            selected[descriptor.name] = descriptor
        return tuple(selected.values())

    def _validate_tool_contributions(self, host: ExtensionHost) -> None:
        base_names = {
            name
            for tool in self._base_tools
            if isinstance((name := getattr(tool, "name", None)), str)
        }
        overlap = base_names & set(host.tools)
        if overlap:
            raise ValueError(
                "extension tools conflict with session tools: " + ", ".join(sorted(overlap))
            )

    def _apply_contributions(self) -> None:
        self._validate_tool_contributions(self.extensions)
        agent = _agent(self.session)
        agent.tools = [*self._base_tools, *self.extensions.tools.values()]
        fragments = self.extensions.system_prompts
        prompt = self._base_system_prompt
        if fragments:
            prompt = "\n\n".join((prompt, *fragments)).strip()
        agent.system_prompt = prompt

    def _restore_agent(self) -> None:
        agent = _agent(self.session)
        if bool(getattr(agent, "is_streaming", False)):
            return
        agent.tools = list(self._base_tools)
        agent.system_prompt = self._original_system_prompt

    async def _sync_package_resources(self) -> None:
        package_roots = await self.packages.resource_roots()
        _set_resource_roots(
            self.session,
            tuple(dict.fromkeys((*self._base_resource_roots, *package_roots))),
        )
        reload_resources = getattr(self.session, "reload_resources", None)
        if callable(reload_resources):
            await _maybe_await(reload_resources())
            self._base_system_prompt = str(getattr(_agent(self.session), "system_prompt", ""))

    async def _restore_resources(self) -> None:
        _set_resource_roots(self.session, self._base_resource_roots)
        reload_resources = getattr(self.session, "reload_resources", None)
        if callable(reload_resources):
            await _maybe_await(reload_resources())

    async def _dispatch_event(self, event: Any) -> None:
        await self.extensions.dispatch_event(event)

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("CodingAgentRuntime has not been started")

    def _ensure_idle(self) -> None:
        self._ensure_started()
        if bool(getattr(_agent(self.session), "is_streaming", False)):
            raise RuntimeError("package and extension mutations require an idle agent")


async def create_coding_agent_runtime(
    session_options: Any = None,
    *,
    runtime_options: CodingAgentRuntimeOptions | None = None,
) -> CodingAgentRuntime:
    from .sdk import create_agent_session

    created = await create_agent_session(session_options)
    runtime = CodingAgentRuntime(created.session, options=runtime_options)
    await runtime.start()
    return runtime


def _resource_descriptors(resources: Any) -> list[ExtensionDescriptor]:
    extensions = getattr(resources, "extensions", ()) if resources is not None else ()
    result: list[ExtensionDescriptor] = []
    for index, item in enumerate(extensions):
        path = getattr(item, "path", None)
        source = getattr(item, "source", None)
        if path is None and source is not None:
            path = getattr(source, "path", None)
        if not isinstance(path, (str, Path)):
            continue
        name = getattr(item, "name", None)
        result.append(
            descriptor_from_path(
                path,
                name=name if isinstance(name, str) and name else None,
                source="resource",
                priority=10_000 + index,
            )
        )
    return result


def _agent(session: Any) -> Any:
    agent = getattr(session, "agent", None)
    if agent is None:
        raise TypeError("CodingAgentRuntime requires a session with an agent")
    return agent


def _session_cwd(session: Any) -> Path:
    env = getattr(session, "env", None)
    value = getattr(env, "cwd", None)
    if value is None:
        value = getattr(session, "cwd", None)
    if value is None:
        return Path.cwd().resolve()
    if not isinstance(value, (str, Path)):
        raise TypeError("session cwd must be a string or Path")
    return Path(value).expanduser().resolve()


def _resource_roots(session: Any) -> tuple[Path, ...]:
    loader = getattr(session, "resource_loader", None)
    if loader is None:
        return ()
    config = getattr(loader, "config", None)
    values = getattr(config, "extra_roots", None)
    if values is None:
        values = getattr(loader, "extra_roots", ())
    if values is None:
        return ()
    return tuple(Path(path).expanduser().resolve() for path in values)


def _set_resource_roots(session: Any, roots: tuple[Path, ...]) -> None:
    loader = getattr(session, "resource_loader", None)
    if loader is None:
        return
    normalized = tuple(Path(path).expanduser().resolve() for path in roots)
    config = getattr(loader, "config", None)
    if config is not None and hasattr(config, "extra_roots"):
        config.extra_roots = normalized
    elif hasattr(loader, "extra_roots"):
        loader.extra_roots = normalized


def _setting(session: Any, key: str, default: Any) -> Any:
    settings = getattr(session, "settings", None)
    if settings is None:
        return default
    getter = getattr(settings, "get", None)
    if callable(getter):
        return getter(key, default)
    if isinstance(settings, Mapping):
        return settings.get(key, default)
    return default


def _settings_mapping(session: Any) -> dict[str, Any]:
    settings = getattr(session, "settings", None)
    if settings is None:
        return {}
    flat = getattr(settings, "flat", None)
    if callable(flat):
        value = flat()
        return dict(value) if isinstance(value, Mapping) else {}
    values = getattr(settings, "values", None)
    if isinstance(values, Mapping):
        return dict(values)
    if isinstance(settings, Mapping):
        return dict(settings)
    return {}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "CodingAgentRuntime",
    "CodingAgentRuntimeOptions",
    "RuntimeState",
    "create_coding_agent_runtime",
]
