from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_agent.coding_agent.runtime import CodingAgentRuntime, CodingAgentRuntimeOptions


class BaseTool:
    name = "base"


class FakeAgent:
    def __init__(self) -> None:
        self.tools: list[Any] = [BaseTool()]
        self.system_prompt = "base prompt"
        self.is_streaming = False


class FakeSession:
    def __init__(self, root: Path) -> None:
        self.agent = FakeAgent()
        self.env = SimpleNamespace(cwd=root)
        self.settings = {"resources.user_root": str(root / "user")}
        self.resources = SimpleNamespace(extensions=())
        self.resource_loader = SimpleNamespace(extra_roots=())
        self.listeners: list[Any] = []
        self.reload_count = 0

    def subscribe(self, listener: Any) -> Any:
        self.listeners.append(listener)

        def unsubscribe() -> None:
            self.listeners.remove(listener)

        return unsubscribe

    async def reload_resources(self) -> None:
        self.reload_count += 1

    async def wait_for_idle(self) -> None:
        return None

    async def emit(self, event: Any) -> None:
        for listener in tuple(self.listeners):
            await listener(event)


def write_extension(path: Path, *, value: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
class Tool:
    name = "extension_tool"

def activate(api):
    api.register_tool(Tool())
    api.register_command("extension.echo", lambda args, context: {value!r} + ":" + ",".join(args))
    api.append_system_prompt("extension prompt")
""",
        encoding="utf-8",
    )


def make_package(root: Path, *, version: str, extension_body: str) -> Path:
    (root / "extensions").mkdir(parents=True, exist_ok=True)
    (root / "extensions" / "main.py").write_text(extension_body, encoding="utf-8")
    (root / "pi-package.json").write_text(
        json.dumps(
            {
                "name": "runtime-package",
                "version": version,
                "extensions": ["extensions/main.py"],
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_runtime_applies_extensions_to_one_session_and_restores_on_close(
    tmp_path: Path,
) -> None:
    extension = tmp_path / "extension.py"
    write_extension(extension)
    session = FakeSession(tmp_path)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "user",
            auth_path=tmp_path / "auth.json",
            extension_paths=(extension,),
            allow_all_extension_capabilities=True,
        ),
    )

    state = await runtime.start()

    assert state.extensions == ("extension",)
    assert state.commands == ("extension.echo",)
    assert [tool.name for tool in session.agent.tools] == ["base", "extension_tool"]
    assert session.agent.system_prompt == "base prompt\n\nextension prompt"
    assert await runtime.invoke_command("extension.echo", ["a", "b"]) == "ok:a,b"
    await session.emit({"type": "example"})

    await runtime.close()
    assert [tool.name for tool in session.agent.tools] == ["base"]
    assert session.agent.system_prompt == "base prompt"
    assert session.listeners == []


@pytest.mark.asyncio
async def test_package_activation_failure_restores_previous_files_lock_and_extension(
    tmp_path: Path,
) -> None:
    first = make_package(
        tmp_path / "first",
        version="1.0.0",
        extension_body=(
            "def activate(api):\n"
            "    api.register_command('package.value', lambda args, context: 'v1')\n"
        ),
    )
    broken = make_package(
        tmp_path / "broken",
        version="2.0.0",
        extension_body="def activate(api):\n    raise RuntimeError('broken update')\n",
    )
    session = FakeSession(tmp_path)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "user",
            allow_all_extension_capabilities=True,
        ),
    )
    await runtime.start()
    installed = await runtime.install_package(first)
    assert installed.version == "1.0.0"
    assert runtime.packages.package_path("runtime-package") in session.resource_loader.extra_roots
    assert await runtime.invoke_command("package.value") == "v1"

    with pytest.raises(RuntimeError, match="broken update"):
        await runtime.update_package("runtime-package", broken)

    restored = await runtime.packages.get("runtime-package")
    assert restored is not None and restored.version == "1.0.0"
    assert (await runtime.packages.verify("runtime-package")).valid is True
    assert await runtime.invoke_command("package.value") == "v1"
    await runtime.close()
    assert session.resource_loader.extra_roots == ()


@pytest.mark.asyncio
async def test_runtime_reload_failure_preserves_previous_extension(tmp_path: Path) -> None:
    extension = tmp_path / "reloadable.py"
    extension.write_text(
        "def activate(api):\n"
        "    api.register_command('runtime.value', lambda args, context: 'v1')\n",
        encoding="utf-8",
    )
    session = FakeSession(tmp_path)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "reload-user",
            extension_paths=(extension,),
            allow_all_extension_capabilities=True,
        ),
    )
    await runtime.start()
    assert await runtime.invoke_command("runtime.value") == "v1"

    extension.write_text(
        "def activate(api):\n    raise RuntimeError('bad candidate')\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="bad candidate"):
        await runtime.reload_extensions()

    assert await runtime.invoke_command("runtime.value") == "v1"
    await runtime.close()


@pytest.mark.asyncio
async def test_runtime_rejects_tool_collision_without_mutating_session(tmp_path: Path) -> None:
    extension = tmp_path / "collision.py"
    extension.write_text(
        "class Tool:\n    name = 'base'\ndef activate(api):\n    api.register_tool(Tool())\n",
        encoding="utf-8",
    )
    session = FakeSession(tmp_path)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "user",
            extension_paths=(extension,),
            allow_all_extension_capabilities=True,
        ),
    )

    with pytest.raises(ValueError, match="conflict with session tools: base"):
        await runtime.start()

    assert [tool.name for tool in session.agent.tools] == ["base"]
    assert session.agent.system_prompt == "base prompt"


@pytest.mark.asyncio
async def test_runtime_wires_package_resources_into_real_agent_session(tmp_path: Path) -> None:
    from pi_agent.ai import FauxProvider, Model
    from pi_agent.coding_agent import AgentSessionOptions, create_agent_session
    from pi_agent.harness.resource_loader import ResourceLoader, ResourceLoaderConfig

    package = tmp_path / "resource-package"
    (package / "skills" / "phase24").mkdir(parents=True)
    (package / "skills" / "phase24" / "SKILL.md").write_text(
        "---\nname: phase24\ndescription: package resource integration\n---\nbody",
        encoding="utf-8",
    )
    (package / "pi-package.json").write_text(
        json.dumps(
            {
                "name": "resource-package",
                "version": "1.0.0",
                "skills": ["skills"],
            }
        ),
        encoding="utf-8",
    )
    model = Model(
        api="fixture",
        provider="fixture",
        id="fixture-model",
        name="Fixture",
        context_window=8192,
        max_tokens=1024,
    )
    created = await create_agent_session(
        AgentSessionOptions(
            cwd=tmp_path,
            model=model,
            stream_fn=FauxProvider([]).stream,
            no_session=True,
            include_coding_tools=False,
            resource_loader=ResourceLoader(
                ResourceLoaderConfig(
                    cwd=tmp_path,
                    user_root=tmp_path / "missing-user-resources",
                )
            ),
        )
    )
    session = getattr(created, "session", created)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "runtime-user",
            allow_all_extension_capabilities=True,
        ),
    )
    await runtime.packages.install(package)

    await runtime.start()

    skill_names = {item.name for item in session.resources.skills}
    assert "phase24" in skill_names

    await runtime.close(close_session=True)
    assert "phase24" not in {item.name for item in session.resources.skills}


@pytest.mark.asyncio
async def test_remove_package_rolls_back_when_candidate_extension_set_fails(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit-reload.py"
    explicit.write_text(
        "def activate(api):\n"
        "    api.register_command('explicit.value', lambda args, context: 'explicit-v1')\n",
        encoding="utf-8",
    )
    package = make_package(
        tmp_path / "removable-package",
        version="1.0.0",
        extension_body=(
            "def activate(api):\n"
            "    api.register_command('removable.value', lambda args, context: 'package-v1')\n"
        ),
    )
    session = FakeSession(tmp_path)
    runtime = CodingAgentRuntime(
        session,
        options=CodingAgentRuntimeOptions(
            package_root=tmp_path / "remove-runtime-user",
            extension_paths=(explicit,),
            allow_all_extension_capabilities=True,
        ),
    )
    await runtime.start()
    await runtime.install_package(package)
    assert await runtime.invoke_command("removable.value") == "package-v1"

    explicit.write_text(
        "def activate(api):\n    raise RuntimeError('broken remaining extension')\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="broken remaining extension"):
        await runtime.remove_package("runtime-package")

    restored = await runtime.packages.get("runtime-package")
    assert restored is not None and restored.version == "1.0.0"
    assert (await runtime.packages.verify("runtime-package")).valid is True
    assert await runtime.invoke_command("removable.value") == "package-v1"
    await runtime.close()
