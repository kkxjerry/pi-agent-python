# Phase 4–7 acceptance evidence

## Implementation evidence

Phase 4 implements the provider-neutral model/message/content/usage contract.
Phase 5 implements generic and assistant async streams. Phase 6 implements a
deterministic provider, an OpenAI-compatible SSE provider, provider/model
registries, partial JSON, retry/error/timeout behavior, callbacks, and cost
accounting. Phase 7 implements prompt/continue loops and the sequential tool path.

## Defects found during validation

The validation process found and corrected real defects rather than treating the
first green path as completion:

1. OpenAI fragmented Tool Call names were initially concatenated as `echoecho`.
2. Prompt mode initially mutated caller-owned history instead of a runtime copy.
3. Sequential ToolResult message events were initially delayed until the whole batch ended.
4. One terminating result initially stopped a batch; upstream requires all results to terminate.
5. Producer tasks were not retained by streams, risking premature task collection.
6. A cancelled EventStream waiter could starve the next consumer.
7. Provider retries initially lacked an explicit no-replay boundary after stream start.
8. The local Phase 1 docs were initially skeletal and were expanded before acceptance.
9. The npm fixture installation was interrupted several times; incomplete dependencies and a
   damaged lockfile were rejected, then rebuilt and validated before capture.

## Upstream execution evidence

The runner under `tools/upstream-fixtures/` uses exact dependencies:

```text
@earendil-works/pi-ai@0.84.4
@earendil-works/pi-agent-core@0.84.4
```

The runner passed TypeScript `--noEmit`, executed ten scenarios, and wrote results
to `/tmp/pi-upstream-capture`. `scripts/compare_upstream_capture.py` confirmed all
ten files matched the checked-in fixtures exactly. Fixture provenance was then
promoted from `source-contract` to `upstream-execution`.

Python directly compares seven Phase 7 scenarios to those executed fixtures:

```text
text_only
single_tool
invalid_tool_name
tool_throws_exception
truncated_tool_call
transform_context
continue_existing_context
```

The remaining executed fixtures are deliberately reserved for Phase 8/9:
`parallel_out_of_order`, `steering_one`, and `follow_up_one`.

## Verification gates

The final acceptance run must record:

- Phase 0–3 structural/provenance gate;
- official TypeScript typecheck, capture, and exact comparison;
- Python 3.11 and 3.12 tests;
- Ruff format/lint;
- strict mypy;
- compileall;
- sdist/wheel build and clean-wheel install smoke;
- Git whitespace and clean-tree checks.

## Deliberate limitations

- Parallel tool execution is not implemented until Phase 8.
- Stateful Agent, steering/follow-up queues, and async subscriber barriers are Phase 9.
- No live paid-provider request was made; OpenAI wire behavior is verified with injected
  deterministic transports.
- Session, compaction, coding tools, AgentSession, RPC, extensions, and TUI remain later phases.

## Final local acceptance results

```text
Python 3.11 pytest                 56 passed
Python 3.12 isolated pytest        56 passed
Python 3.13 isolated pytest        56 passed
Ruff format check                 passed
Ruff lint                         passed
mypy --strict                     passed (28 source files)
compileall                        passed
Phase 0–3 gate                    passed (30 scenarios, 10 upstream executions)
Python ↔ official golden parity   7/7 scenarios passed
Clean official npm install        passed
Official TypeScript typecheck     passed
Fresh official TS capture         10/10 passed
Fresh TS ↔ checked-in fixtures    10/10 exact match
sdist build                       passed
wheel build                       passed
clean wheel install/import/CLI    passed
clean sdist install/CLI           passed
```

Official TypeScript clean-room execution used Node `22.22.0` and npm `10.9.4`.
