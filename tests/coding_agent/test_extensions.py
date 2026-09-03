from __future__ import annotations

from pathlib import Path

import pytest

from pi_agent.coding_agent.extensions import (
    ExtensionActivationError,
    ExtensionDescriptor,
    ExtensionHost,
    ExtensionPolicy,
    descriptor_from_path,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_extension_activation_registers_contributions_and_disposes(tmp_path: Path) -> None:
    extension = tmp_path / "sample.py"
    marker = tmp_path / "marker.txt"
    write(
        extension,
        f"""
from pathlib import Path

class Tool:
    name = "sample_tool"

async def activate(api):
    api.register_tool(Tool())
    api.register_command("echo", lambda args, context: "|".join(args))
    api.register_service("sample.service", {{"ready": True}})
    api.append_system_prompt("sample prompt")
    async def on_event(event):
        Path({str(marker)!r}).write_text(str(event), encoding="utf-8")
    api.on_event(on_event)
    api.on_dispose(lambda: Path({str(marker)!r}).write_text("disposed", encoding="utf-8"))
""",
    )
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,), allow_all_capabilities=True),
    )

    handle = await host.activate(descriptor_from_path(extension))

    assert handle.descriptor.name == "sample"
    assert list(host.tools) == ["sample_tool"]
    assert list(host.commands) == ["echo"]
    assert host.services["sample.service"] == {"ready": True}
    assert host.system_prompts == ("sample prompt",)
    assert await host.invoke_command("echo", ["a", "b"]) == "a|b"
    assert await host.dispatch_event({"type": "test"}) == ()
    assert "test" in marker.read_text(encoding="utf-8")

    await host.close()
    assert marker.read_text(encoding="utf-8") == "disposed"
    assert host.handles == ()


@pytest.mark.asyncio
async def test_failed_activation_rolls_back_staged_contributions(tmp_path: Path) -> None:
    extension = tmp_path / "broken.py"
    marker = tmp_path / "disposed.txt"
    write(
        extension,
        f"""
from pathlib import Path
class Tool:
    name = "temporary"
def activate(api):
    api.register_tool(Tool())
    api.on_dispose(lambda: Path({str(marker)!r}).write_text("yes", encoding="utf-8"))
    raise RuntimeError("boom")
""",
    )
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,), allow_all_capabilities=True),
    )

    with pytest.raises(ExtensionActivationError, match="boom"):
        await host.activate(descriptor_from_path(extension))

    assert host.tools == {}
    assert host.handles == ()
    assert marker.read_text(encoding="utf-8") == "yes"


@pytest.mark.asyncio
async def test_strict_batch_activation_rolls_back_prior_extensions(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    marker = tmp_path / "first-disposed.txt"
    write(
        first,
        f"""
from pathlib import Path
def activate(api):
    api.register_command("first", lambda args, context: "ok")
    api.on_dispose(lambda: Path({str(marker)!r}).write_text("yes", encoding="utf-8"))
""",
    )
    write(second, "def activate(api):\n    raise RuntimeError('bad second')\n")
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,), allow_all_capabilities=True),
    )

    with pytest.raises(ExtensionActivationError, match="bad second"):
        await host.activate_many(
            [descriptor_from_path(first), descriptor_from_path(second)],
            strict=True,
        )

    assert host.handles == ()
    assert marker.read_text(encoding="utf-8") == "yes"


@pytest.mark.asyncio
async def test_reload_failure_preserves_previous_active_extension(tmp_path: Path) -> None:
    extension = tmp_path / "reloadable.py"
    write(
        extension,
        "def activate(api):\n    api.register_command('value', lambda args, context: 'v1')\n",
    )
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,), allow_all_capabilities=True),
    )
    await host.activate(descriptor_from_path(extension))
    assert await host.invoke_command("value") == "v1"

    write(extension, "def activate(api):\n    raise RuntimeError('invalid replacement')\n")
    with pytest.raises(ExtensionActivationError, match="invalid replacement"):
        await host.reload("reloadable")

    assert await host.invoke_command("value") == "v1"
    await host.close()


@pytest.mark.asyncio
async def test_capability_policy_fails_before_import(tmp_path: Path) -> None:
    extension = tmp_path / "network.py"
    write(extension, "def activate(api):\n    pass\n")
    descriptor = ExtensionDescriptor(
        "network",
        extension,
        capabilities=("network",),
    )
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,)),
    )

    with pytest.raises(ExtensionActivationError, match="not allowed: network"):
        await host.activate(descriptor)


@pytest.mark.asyncio
async def test_activation_error_survives_failing_staged_cleanup(tmp_path: Path) -> None:
    extension = tmp_path / "cleanup_failure.py"
    write(
        extension,
        "def activate(api):\n"
        "    def dispose():\n"
        "        raise RuntimeError('dispose failed')\n"
        "    api.on_dispose(dispose)\n"
        "    raise RuntimeError('activation failed')\n",
    )
    host = ExtensionHost(
        cwd=tmp_path,
        policy=ExtensionPolicy(allowed_roots=(tmp_path,), allow_all_capabilities=True),
    )

    with pytest.raises(ExtensionActivationError, match="activation failed") as captured:
        await host.activate(descriptor_from_path(extension))

    assert host.handles == ()
    assert any("cleanup also failed" in note for note in (captured.value.__notes__ or []))
