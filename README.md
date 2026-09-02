# pi-agent-python

A Python reimplementation of the pinned official TypeScript pi behavior.

- Upstream: `earendil-works/pi`
- Baseline: `v0.84.4` / `b79e4cc`
- Python: 3.11+
- Current implementation milestone: Phase 0–16

This is an independent Python package. It does not reuse PaiCLI, Java-agent, LangChain, LangGraph, AutoGen, or CrewAI architecture.

## Development

```bash
uv sync --all-extras
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Baseline and milestone gates:

```bash
uv run python scripts/check_phase_0_3.py
uv run python scripts/check_phase_13_16.py
```

## AI and Agent Loop

```python
from pi_agent.agent import AgentContext, AgentLoopConfig, agent_loop, default_convert_to_llm
from pi_agent.ai import FauxProvider, Model, TextContent, AssistantMessage, UserMessage

model = Model(api="openai-completions", provider="fixture", id="fixture", name="Fixture")
provider = FauxProvider([AssistantMessage(content=[TextContent("done")], stop_reason="stop")])
stream = agent_loop(
    [UserMessage("hello")],
    AgentContext(),
    AgentLoopConfig(model=model, convert_to_llm=default_convert_to_llm),
    stream_fn=provider.stream,
)
```

## Append-only sessions

```python
from pi_agent.ai import UserMessage
from pi_agent.harness.session import SessionManager

session = SessionManager.create(
    ".pi/sessions/example.jsonl",
    cwd=".",
    session_id="example",
)
session.append_message(UserMessage("Inspect this repository"))
```

The JSONL store keeps the full branch tree. Navigation appends a cursor record; compaction appends a summary record instead of rewriting old messages. An invalid unterminated final record can be repaired explicitly, while corruption in the middle of a session fails closed.

## Compaction

```python
from pi_agent.harness.compaction import CompactionController, CompactionSettings

controller = CompactionController(
    CompactionSettings(
        context_window=128_000,
        reserve_tokens=16_384,
        keep_recent_tokens=20_000,
    ),
    summarizer,
)
decision = await controller.prepare_next_provider_context(session)
```

The controller is intended for the boundary after tool results and immediately before the next provider request. Safe cuts never separate a ToolCall from its ToolResult batch.

## Resources

```python
from pathlib import Path
from pi_agent.harness.resource_loader import ResourceLoader, ResourceLoaderConfig
from pi_agent.harness.system_prompt import SystemPromptBuilder

snapshot = ResourceLoader(ResourceLoaderConfig(cwd=Path.cwd())).load()
system_prompt = SystemPromptBuilder().build(snapshot)
```

Discovery covers user and project `SYSTEM.md`, `APPEND_SYSTEM.md`, ancestor `AGENTS.md`/`CLAUDE.md`, skills, prompt templates, themes, extension references, and package manifests. Skill bodies remain on demand rather than being injected wholesale.

## Settings

```python
from pi_agent.coding_agent.settings import SettingsResolver

settings = SettingsResolver().resolve(
    global_path="~/.pi/agent/settings.json",
    project_path=".pi/settings.json",
    cli={"model.id": "example-model"},
    runtime={"thinking.level": "high"},
)
print(settings.get("model.id"))
print(settings.source("model.id"))
```

Precedence is defaults → global → project → environment → CLI → runtime. Every resolved key retains its source layer and location. Runtime overrides are not persisted implicitly.

## Upstream fixture verification

```bash
npm --prefix tools/upstream-fixtures ci
npm --prefix tools/upstream-fixtures run typecheck
npm --prefix tools/upstream-fixtures run capture -- --out /tmp/pi-upstream-capture
uv run python scripts/compare_upstream_capture.py /tmp/pi-upstream-capture
```

See `PARITY.md`, `SCOPE.md`, and `docs/audit/` for exact claims and deliberate boundaries.
