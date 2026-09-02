# Scope through Phase 7

The project is a clean Python behavioral reimplementation of the pinned official
TypeScript pi release. It does not import or derive architecture from PaiCLI,
Java agents, LangChain, LangGraph, AutoGen, CrewAI, OpenHands, or another Python
agent framework.

## Accepted Phase 0–3 foundation

- official repository/tag/commit/archive hash pinned;
- upstream license attribution retained;
- package, event, loop, session, compaction, extension, RPC, and TUI behavior maps;
- 30-scenario registry;
- ten deterministic scenarios executed with exact official `0.84.4` npm packages;
- Python packaging, CLI metadata, CI, lint, type, test, and fixture gates.

## Implemented Phase 4–7

### Phase 4 — AI contracts

- models, cost tiers, input capabilities, compatibility and sampling metadata;
- user, assistant, and ToolResult messages;
- text, thinking, image, and Tool Call content blocks;
- usage/cache/cost accounting, stop reasons, diagnostics;
- stream options, cancellation, request/response callbacks, and serialization.

### Phase 5 — Event streams

- generic asynchronous EventStream;
- assistant-message stream specialization;
- terminal event delivery plus separately awaitable result;
- first-result-wins behavior, explicit closure, waiter cleanup, and retained producers.

### Phase 6 — Providers

- deterministic FauxProvider;
- OpenAI-compatible Chat Completions SSE adapter;
- partial Tool Call JSON view without early execution;
- retry only before stream start;
- Retry-After parsing and maximum-delay guard;
- stream idle timeout and cancellation-aware retry sleep;
- structured provider diagnostics and usage/cost;
- header suppression, payload/response hooks, cache fields, sampling overrides;
- ProviderRegistry and ModelRegistry with provider-owned model refresh.

### Phase 7 — low-level Agent Loop

- prompt and continue entry points;
- context transform and AgentMessage-to-LLM conversion boundary;
- assistant transcript accumulation;
- tool lookup, argument preparation/schema validation, progress, success, and errors;
- unknown-tool, invalid-argument, exception, cancellation, and truncated-call recovery;
- deterministic sequential ToolResult event/transcript ordering;
- all-results termination rule;
- seven direct Python-to-executed-upstream golden comparisons.

## Explicitly not included

- parallel tool execution and completion-order handling: Phase 8;
- stateful Agent, subscribers, steering and follow-up queues: Phase 9;
- coding tools and execution environment: Phases 10–12;
- sessions and compaction: Phases 13–14;
- AgentSession, CLI run modes, SDK, extensions, TUI, and telemetry: later phases;
- live paid-provider acceptance: not performed in this milestone.
