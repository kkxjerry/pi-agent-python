# Upstream package map — pi v0.84.4

This file records ownership observed in the pinned TypeScript source. It is not a
new Python architecture proposal.

## Product dependency chain

```text
pi-telemetry (optional)
       ↓
pi-ai
       ↓
pi-agent-core
       ↓
pi-coding-agent
       ↓
pi-tui (interactive mode)
```

The monorepo also contains protocol, client/server, evaluation, and Node-specific
session backend packages. They are recorded for future parity but do not block
the Python core.

## `packages/ai`

Owns:

- model and provider descriptors;
- user, assistant, and ToolResult messages;
- text, thinking, image, and Tool Call content blocks;
- usage, cost tiers, cache accounting, stop reasons, and diagnostics;
- provider-neutral assistant event streams;
- provider adapters, retry/error normalization, auth helpers, and model catalogues.

It does **not** own the agent loop, durable transcript, coding tools, or UI.

Python implementation through Phase 7:

```text
src/pi_agent/ai/types.py
src/pi_agent/ai/events.py
src/pi_agent/ai/event_stream.py
src/pi_agent/ai/provider.py
src/pi_agent/ai/faux.py
src/pi_agent/ai/openai_compatible.py
src/pi_agent/ai/json_parse.py
src/pi_agent/ai/errors.py
src/pi_agent/ai/retry.py
src/pi_agent/ai/registry.py
```

## `packages/agent`

Owns:

- `agentLoop()` and `agentLoopContinue()`;
- AgentMessage-to-provider-message conversion;
- tool lookup, argument preparation/validation, execution, progress, and errors;
- agent lifecycle events;
- steering/follow-up queues;
- the stateful `Agent` wrapper and subscriber ordering;
- reusable harness contracts.

Provider-specific HTTP and coding-product behavior remain outside this package.

Python implementation through Phase 7:

```text
src/pi_agent/agent/types.py
src/pi_agent/agent/events.py
src/pi_agent/agent/event_stream.py
src/pi_agent/agent/schema.py
src/pi_agent/agent/agent_loop.py
```

Stateful Agent and queues are Phase 9.

## `packages/coding-agent`

Owns the terminal coding product:

- AgentSession and runtime assembly;
- default system prompt and coding tools;
- JSONL sessions, branch navigation, resume, and export;
- compaction and branch summaries;
- resources, settings, auth, model selection, and package loading;
- extensions, skills, prompt templates, and themes;
- interactive, print, JSON, RPC, and SDK entry points.

None of these responsibilities are pulled into Agent Core merely for convenience.

## `packages/tui`

Owns terminal input and rendering:

- terminal capability abstraction;
- main/alternate screen modes;
- synchronized differential rendering;
- editor, completion, selection, overlays, Markdown, code rendering, and images;
- application-owned viewport and resize handling.

It observes AgentSession events; it does not call providers or tools directly.

## `packages/telemetry`

Owns optional typed spans and exporters. Telemetry failures may not change Agent
results. Prompt/tool payload capture must be explicit because it can contain
sensitive data.

## Mapping rule

A Python module becomes behavior-complete only when implementation, tests,
upstream evidence, and `PARITY.md` agree. An empty package only reserves the
future dependency boundary and makes no parity claim.

Upstream upgrades require a new source-map diff before any Python parity status changes.
