from __future__ import annotations

import shlex
from pathlib import Path

from pi_agent.coding_agent import ContainerSandboxSpec, build_container_command

spec = ContainerSandboxSpec(
    image="pi-agent-python:local",
    workspace=Path.cwd(),
    network="none",
    environment_allowlist=("OPENAI_API_KEY",),
)
command = build_container_command(spec, ("--mode", "interactive"))
print(shlex.join(command))
