# pi-agent-python

A clean Python behavioral reimplementation of the pinned official TypeScript pi
agent toolkit. Current milestone: **Phase 0–7**.

## Development setup

```bash
uv sync --python 3.11 --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
```

## Metadata commands

```bash
uv run pi-py --version
uv run pi-py upstream --json
uv run pi-py parity --json
```

## Low-level API

```python
from pi_agent.ai import AssistantMessage, FauxProvider, TextContent, UserMessage
from pi_agent.agent import AgentContext, AgentLoopConfig, agent_loop, default_convert_to_llm

provider = FauxProvider([
    AssistantMessage(content=[TextContent("hello")], stop_reason="stop")
])
stream = agent_loop(
    [UserMessage("hello")],
    AgentContext(system_prompt="You are a coding agent."),
    AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm),
    stream_fn=provider.stream,
)

async for event in stream:
    print(event.type)
messages = await stream.result()
```

Real network requests are opt-in. The test suite uses deterministic transports
and does not spend model tokens.

## Upstream behavior evidence

```bash
cd tools/upstream-fixtures
npm ci --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run capture -- --out /tmp/pi-upstream-capture
cd ../..
python scripts/compare_upstream_capture.py /tmp/pi-upstream-capture
```

The checked-in fixture provenance is `upstream-execution`, not a hand-authored
claim. See `PARITY.md`, `docs/audit/`, and `docs/upstream/`.

## Current boundary

Phase 7 is sequential. Parallel tools are Phase 8; stateful Agent,
steering/follow-up, and subscribers are Phase 9. Sessions, compaction, coding
product modes, extensions, and TUI are intentionally later layers.
