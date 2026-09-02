# Architecture baseline

The Python project preserves the ownership boundaries of the pinned official
TypeScript pi release. It is a behavioral reimplementation, not a wrapper around
another Python agent framework.

## Dependency direction

```text
pi_agent.ai
    ↓
pi_agent.agent
    ↓
pi_agent.harness
    ↓
pi_agent.coding_agent
    ↓
pi_agent.tui

pi_agent.telemetry is optional and observes the other layers.
```

Dependencies only point downward. In particular, the low-level loop cannot import
CLI, TUI, session persistence, coding tools, or a concrete provider SDK.

## Package responsibilities

| Upstream | Python target | Responsibility |
|---|---|---|
| `packages/ai` | `pi_agent.ai` | Messages, models, usage/cost, provider contracts, assistant streams |
| `packages/agent` | `pi_agent.agent` | Agent events, tool protocol, raw loop, queues, stateful Agent |
| Agent harness | `pi_agent.harness` | Environment, run orchestration, context management, recovery |
| `packages/coding-agent` | `pi_agent.coding_agent` | AgentSession, resources, settings, coding tools, modes, extensions |
| `packages/tui` | `pi_agent.tui` | Terminal abstraction, editor, components, differential rendering |
| `packages/telemetry` | `pi_agent.telemetry` | Optional typed spans and exporters |

## Core invariants

### Message boundary

The application transcript and provider transcript are different types:

```text
AgentMessage[]
  → transform_context()
  → AgentMessage[]
  → convert_to_llm()
  → Message[]
  → Provider
```

Transformation happens immediately before every provider request. It does not
rewrite the caller's durable transcript.

### Event ownership

The provider emits assistant-content events. Agent Core translates them into
agent lifecycle and tool events. Later, AgentSession adds persistence, retry,
compaction, branch, and resource behavior. CLI/TUI/RPC are consumers; they never
own execution semantics.

### Tool ordering

Phase 7 implements the official sequential path. Phase 8 will add parallel
execution while preserving two independent orders:

- completion events follow real completion order;
- ToolResult transcript messages follow the assistant's original Tool Call order.

A tool marked sequential makes the whole batch sequential in the official loop.

### Stream termination

A terminal provider or agent event is observable by async iteration and also
resolves `result()`. The first terminal result wins. Producers are retained until
completion, and explicit `end()` wakes pending consumers without replacing an
already resolved result.

### Session boundary

Session persistence is not part of Agent Core. The future session store is an
append-only JSONL tree. Branch changes move the active leaf instead of deleting
history. Compaction and branch summaries append entries rather than mutating old
messages.

### Shared product session

Print, JSON, RPC, SDK, and TUI will all drive the same `AgentSession`. A run mode
adds only input/output adaptation.

## TypeScript-to-Python runtime mapping

| TypeScript | Python |
|---|---|
| `Promise<T>` | coroutine / `Awaitable[T]` |
| `AsyncIterable<T>` | `AsyncIterator[T]` |
| `AbortSignal` | `CancellationToken` plus asyncio task cancellation |
| discriminated union | tagged dataclasses and type aliases |
| TypeBox/JSON Schema | explicit JSON-Schema validation at the tool boundary |
| Node stream | async line transport and injectable provider adapter |
| extension module | Python module/entry point with transactional activation |
