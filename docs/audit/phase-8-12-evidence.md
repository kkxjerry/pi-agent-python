# Phase 8–12 acceptance evidence

## Scope

This milestone extends the pinned Phase 0–7 baseline without changing the upstream reference (`earendil-works/pi@v0.84.4`, commit `b79e4cc`).

## Implemented

### Phase 8 — parallel tools

- sequential preflight and schema validation;
- concurrent execution for an allowed parallel batch;
- `tool_execution_end` in actual completion order;
- ToolResult message events and provider transcript in assistant source order;
- a sequential tool forces the whole batch to run sequentially;
- batch cancellation and timeout cleanup.

### Phase 9 — stateful Agent

- reusable model/system/tools/messages state;
- prompt and continue entry points;
- ordered, awaited event subscribers;
- separate steering and follow-up FIFO queues;
- `all` and `one-at-a-time` queue modes;
- active abort token and reuse after abort;
- streaming message, pending Tool Call, and last-error state.

### Phase 10 — tool runtime controls

- argument preparation and JSON Schema validation;
- before-tool blocking and termination hints;
- after-tool content/details/error/usage/termination replacement;
- progress events;
- per-tool timeout;
- graceful stop-after-turn and prepare-next-turn callbacks.

### Phase 11 — coding tools

- `read`: UTF-8 line ranges, binary rejection, bounded output;
- `write`: parent creation and atomic replacement;
- `edit`: exact unique replacements, multi-edit all-or-nothing behavior;
- `bash`: streaming stdout/stderr, exit details, truncation, timeout, cancellation, and process-group cleanup.

### Phase 12 — harness

- injectable FileSystem and Shell protocols;
- local filesystem/shell adapters;
- ExecutionEnv path resolution;
- session-independent AgentHarness;
- coding-tool composition and duplicate-name rejection;
- structured final result and usage aggregation.

## Direct upstream parity

The previous milestone executed ten official TypeScript scenarios. Phase 8–9 adds Python coverage for the three previously deferred goldens:

```text
parallel_out_of_order
steering_one
follow_up_one
```

Together with the seven Phase 7 comparisons, all ten executed upstream fixtures are now represented by Python parity tests. The fixture comparison preserves event order, queue insertion positions, stop reasons, Tool Call IDs, ToolResult IDs, and transcript ordering.

## Review findings addressed during implementation

1. Parallel execution cannot append ToolResult messages from `as_completed()`; doing so corrupts provider transcript order.
2. Preflight must finish before allowed tasks start, otherwise argument preparation and execution interleave unpredictably.
3. Caller cancellation of `Agent.prompt()` must propagate to the low-level stream and wait for cleanup.
4. A cancelled EventStream waiter must not starve a later consumer.
5. Write/edit operations must remain atomic when a later edit fails.
6. Shell cancellation must terminate the process group rather than only the direct shell process.
7. A Provider failure after streaming begins must not be retried and duplicated.

## Verification commands

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy --strict src
uv run pytest -q
python scripts/check_phase_0_3.py
python scripts/check_phase_8_12.py

cd tools/upstream-fixtures
npm ci --ignore-scripts
npm run typecheck
npm run capture -- --out /tmp/pi-upstream-capture
cd ../..
python scripts/compare_upstream_capture.py /tmp/pi-upstream-capture
```

## Deliberate boundaries

- The default local environment is not a sandbox and runs with process permissions.
- Non-zero bash exit codes are returned as observable tool results; transport/runtime failures remain tool errors.
- Persistent sessions, compaction, branch summaries, AgentSession, CLI coding modes, RPC, extensions, and TUI are not claimed by this milestone.
- Live paid-provider calls are not required for deterministic acceptance; the OpenAI-compatible wire adapter is tested through an injected transport.
