# Architecture

## Upstream mapping

```text
Official TypeScript pi                 Python package
────────────────────────────────────────────────────────────────
packages/ai                            pi_agent.ai
packages/agent low-level loop          pi_agent.agent.agent_loop
packages/agent stateful Agent          pi_agent.agent.agent
packages/agent harness environment     pi_agent.harness
packages/coding-agent                  later AgentSession/product phases
packages/tui                           later pi_agent.tui phases
```

The mapping preserves ownership and behavior boundaries rather than translating files line by line.

## Dependency direction

```text
pi_agent.ai
    ↓
pi_agent.agent
    ↓
pi_agent.harness
    ↓
later coding_agent / CLI / RPC / TUI
```

Lower layers never import product modes, persistence, or terminal UI code.

## AI layer

`pi_agent.ai` owns:

- provider-neutral model and message types;
- text, thinking, image, and Tool Call content blocks;
- usage/cost accounting and model registries;
- provider event streams;
- Faux and OpenAI-compatible providers;
- retry, cancellation, partial JSON, and transport errors.

It does not execute tools or own a conversation transcript.

## Low-level loop

`pi_agent.agent.agent_loop` owns a single invocation:

```text
prompt/continue
→ transform AgentMessage context
→ convert to provider Message context
→ stream assistant response
→ execute a tool batch
→ append ToolResult artifacts
→ drain steering/follow-up messages
→ continue or emit agent_end
```

The input `AgentContext` is caller-owned and not mutated. The loop works on a top-level copy and returns only messages produced by the current invocation.

## Parallel tool invariant

Parallel execution deliberately separates two orders:

```text
Tool execution end events      actual completion order
ToolResult message artifacts   assistant source order
Provider transcript            assistant source order
```

Preflight is sequential. If global mode is `sequential`, or any called tool declares `execution_mode="sequential"`, the entire batch is sequential. This prevents write/edit/bash side effects from racing with sibling calls.

Tool lifecycle hooks run at these points:

```text
lookup → prepare arguments → schema validation → before_tool_call
→ execute/timeout/cancel → after_tool_call
→ tool_execution_end → ToolResult message
```

## Stateful Agent

`pi_agent.agent.Agent` owns reusable runtime state:

- system prompt, model, thinking level, tools, and transcript;
- current streamed assistant message;
- pending Tool Call IDs;
- steering and follow-up FIFO queues;
- active cancellation token;
- ordered event subscribers and listener failures.

Listeners are awaited in registration order. `is_streaming` remains true while `agent_end` listeners settle. A new cancellation token is created for every run, so an aborted Agent can be reused.

## Execution environment and coding tools

`ExecutionEnv` composes a `FileSystem`, `Shell`, and working directory. Coding tools depend only on that interface:

```text
read   UTF-8 line ranges and bounded output
write  atomic create/replace
edit   exact unique replacements, all-or-nothing
bash   streamed stdout/stderr, timeout, cancellation, process cleanup
```

`LocalFileSystem` and `LocalShell` are the default adapters. Tests can inject fake implementations without touching the user's machine.

## Harness

`AgentHarness` is session-independent orchestration. It composes:

- a stateful Agent;
- an ExecutionEnv;
- the default coding tools plus optional custom tools;
- structured run results and usage aggregation.

It intentionally does not yet own persistent sessions, compaction, resource discovery, extensions, RPC, or TUI. Those remain separate later phases rather than being hidden inside the low-level runtime.

## Persistence boundary

Phase 12 ends before persistence. Later layers will add:

```text
append-only session tree
→ compaction and branch summaries
→ AgentSession
→ print/json/rpc/interactive modes
```

This order prevents storage and UI concerns from changing the already-tested Agent Loop semantics.
