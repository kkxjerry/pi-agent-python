# pi-agent-python

A Python reimplementation of the official TypeScript pi behavior pinned in [`UPSTREAM.md`](UPSTREAM.md).

The project does not use PaiCLI, Java ports, LangChain, LangGraph, or another Agent framework as an architectural reference. The current baseline is `earendil-works/pi@v0.84.4` (`b79e4cc`).

## Current milestone

Implemented through Phase 20:

- provider-neutral AI messages, models, usage, and event streams;
- deterministic Faux and OpenAI-compatible streaming providers;
- low-level Agent Loop and stateful Agent;
- sequential/parallel tools, hooks, steering, follow-up, and cancellation;
- local/injectable execution environment and read/write/edit/bash tools;
- append-only JSONL session trees and context compaction;
- resource discovery and layered settings;
- one `AgentSession` used by Print, JSON, and RPC modes.

Interactive TUI, executable extension activation, package management, OAuth breadth, and telemetry exporters are later phases.

## Install for development

```bash
uv sync --all-groups
```

## CLI

Print mode:

```bash
export OPENAI_API_KEY=...
uv run pi-py -p "Read the failing test, fix it, and run the test"
```

JSON event stream:

```bash
uv run pi-py --mode json -p "Inspect this repository"
```

RPC mode:

```bash
uv run pi-py --mode rpc
```

RPC accepts one JSON object per LF-delimited input line:

```json
{"id":"1","command":"prompt","message":"Inspect the repository"}
{"id":"2","command":"get_state"}
{"id":"3","command":"shutdown"}
```

Use an in-memory no-session run:

```bash
uv run pi-py --no-session -p "Summarize README.md"
```

Resume a named JSONL session:

```bash
uv run pi-py --session ~/.pi/sessions/example.jsonl -p "Continue the work"
```

Baseline and parity metadata:

```bash
uv run pi-py upstream --json
uv run pi-py parity --json
```

## Python API

```python
import asyncio

from pi_agent.coding_agent import AgentSessionOptions, create_agent_session


async def main() -> None:
    session = await create_agent_session(
        AgentSessionOptions(cwd=".", no_session=True)
    )
    try:
        result = await session.prompt("Read README.md and summarize the project")
        print(result.final_assistant)
    finally:
        await session.close()


asyncio.run(main())
```

A deterministic provider can be injected for tests:

```python
from pi_agent.ai import AssistantMessage, FauxProvider, TextContent
from pi_agent.coding_agent import AgentSessionOptions, create_agent_session

provider = FauxProvider([
    AssistantMessage(content=[TextContent("done")], stop_reason="stop")
])
session = await create_agent_session(
    AgentSessionOptions(cwd=".", no_session=True, stream_fn=provider.stream)
)
```

## Session model

Session files are append-only JSONL trees. Normal entries carry `id` and `parentId`; navigation appends a cursor instead of deleting another branch. Compaction adds a summary entry and changes the active provider context without erasing the original message records.

A valid last JSON record does not require a trailing newline. A malformed final fragment can be repaired only when explicitly requested; corruption in the middle of the file is rejected.

## Configuration precedence

```text
default < global < project < environment < CLI < runtime
```

Global settings default to `~/.pi/settings.json`; project settings use `.pi/settings.json` or `.pi/settings.toml`. Every resolved value keeps a source record.

## Verification

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest -q
python3.11 scripts/check_phase_0_3.py
python3.11 scripts/check_phase_8_12.py
python3.11 scripts/check_phase_17_20.py
git diff --check
```

The default local execution environment is not a sandbox. Tools run with the permissions of the Python process.
