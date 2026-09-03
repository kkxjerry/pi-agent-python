#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent import __version__  # noqa: E402
from pi_agent.coding_agent.auth import AuthStorage, Credential  # noqa: E402
from pi_agent.coding_agent.extensions import (  # noqa: E402
    ExtensionHost,
    ExtensionPolicy,
    descriptor_from_path,
)
from pi_agent.coding_agent.packages import PackageManager  # noqa: E402
from pi_agent.coding_agent.runtime import (  # noqa: E402
    CodingAgentRuntime,
    CodingAgentRuntimeOptions,
)

REQUIRED = (
    "src/pi_agent/coding_agent/auth.py",
    "src/pi_agent/coding_agent/model_access.py",
    "src/pi_agent/coding_agent/extensions/types.py",
    "src/pi_agent/coding_agent/extensions/host.py",
    "src/pi_agent/coding_agent/packages/types.py",
    "src/pi_agent/coding_agent/packages/manager.py",
    "src/pi_agent/coding_agent/runtime.py",
    "src/pi_agent/coding_agent/package_cli.py",
    "docs/audit/phase-21-24-evidence.md",
)


class BaseTool:
    name = "base"


class FakeSession:
    def __init__(self, root: Path) -> None:
        self.agent = SimpleNamespace(
            tools=[BaseTool()],
            system_prompt="base",
            is_streaming=False,
        )
        self.env = SimpleNamespace(cwd=root)
        self.settings = {"resources.user_root": str(root / "user")}
        self.resources = SimpleNamespace(extensions=())
        self._listeners: list[Any] = []

    def subscribe(self, listener: Any) -> Any:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            self._listeners.remove(listener)

        return unsubscribe

    async def reload_resources(self) -> None:
        return None

    async def wait_for_idle(self) -> None:
        return None


async def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"phase 21-24 gate failed: missing {', '.join(missing)}")
    if __version__ != "0.5.0.dev0":
        raise SystemExit(f"phase 21-24 gate failed: unexpected version {__version__}")

    with tempfile.TemporaryDirectory(prefix="pi-phase-21-24-") as directory:
        root = Path(directory)

        auth = AuthStorage(root / "auth.json")
        await auth.set(
            Credential(
                "fixture",
                "oauth",
                access_token="expired",
                refresh_token="refresh",
                expires_at=time.time() - 1,
            )
        )
        refreshed = await auth.resolve(
            "fixture",
            refresh=lambda _: Credential(
                "fixture",
                "oauth",
                access_token="fresh",
                refresh_token="refresh-2",
                expires_at=time.time() + 3600,
            ),
        )
        if refreshed is None or refreshed.secret() != "fresh":
            raise SystemExit("phase 21-24 gate failed: credential refresh failed")

        extension = root / "explicit.py"
        extension.write_text(
            "class Tool:\n"
            "    name = 'extension_tool'\n"
            "def activate(api):\n"
            "    api.register_tool(Tool())\n"
            "    api.register_command('gate.echo', lambda args, context: ','.join(args))\n"
            "    api.append_system_prompt('extension prompt')\n",
            encoding="utf-8",
        )
        host = ExtensionHost(
            cwd=root,
            policy=ExtensionPolicy(
                allowed_roots=(root,),
                allow_all_capabilities=True,
            ),
        )
        await host.activate(descriptor_from_path(extension))
        if await host.invoke_command("gate.echo", ("a", "b")) != "a,b":
            raise SystemExit("phase 21-24 gate failed: extension command failed")
        await host.close()

        package = root / "package"
        (package / "extensions").mkdir(parents=True)
        (package / "extensions" / "main.py").write_text(
            "def activate(api):\n"
            "    api.register_command('package.gate', lambda args, context: 'ok')\n",
            encoding="utf-8",
        )
        (package / "pi-package.json").write_text(
            json.dumps(
                {
                    "name": "gate-package",
                    "version": "1.0.0",
                    "extensions": ["extensions/main.py"],
                }
            ),
            encoding="utf-8",
        )
        packages = PackageManager(root / "user")
        await packages.install(package)
        verification = await packages.verify("gate-package")
        if not verification.valid:
            raise SystemExit("phase 21-24 gate failed: package integrity failed")

        session = FakeSession(root)
        runtime = CodingAgentRuntime(
            session,
            options=CodingAgentRuntimeOptions(
                package_root=root / "user",
                extension_paths=(extension,),
                allow_all_extension_capabilities=True,
            ),
        )
        state = await runtime.start()
        if state.extensions != ("gate-package:extensions:main", "explicit"):
            raise SystemExit(f"phase 21-24 gate failed: unexpected extensions {state.extensions!r}")
        if [tool.name for tool in session.agent.tools] != ["base", "extension_tool"]:
            raise SystemExit("phase 21-24 gate failed: extension tools were not attached")
        if await runtime.invoke_command("package.gate") != "ok":
            raise SystemExit("phase 21-24 gate failed: package extension was not active")
        await runtime.close()
        if [tool.name for tool in session.agent.tools] != ["base"]:
            raise SystemExit("phase 21-24 gate failed: runtime cleanup did not restore tools")

    print(
        "phase 21-24 gate OK: auth=atomic-refresh, extensions=transactional, "
        "packages=integrity-locked, runtime=session-integrated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
