# Parity status

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

Legend:

- `DONE` — implemented and covered by executable parity tests or phase gates;
- `SOURCE` — mapped to the pinned TypeScript source contract and covered locally, but no exact new TypeScript execution fixture exists for that product scenario;
- `BOUNDARY` — intentionally not claimed.

| Area | Status | Evidence |
|---|---|---|
| Phase 0 baseline lock | DONE | `UPSTREAM.md`, metadata tests |
| Phase 1 source behavior map | DONE | `docs/upstream/*` |
| Phase 2 scenario registry | DONE | 30 registered scenarios |
| Phase 2 executed TypeScript goldens | DONE | 20 official npm executions in `fixtures/upstream` |
| Phase 3 Python project gate | DONE | `scripts/check_phase_0_3.py`, CI |
| Phase 4 AI data model | DONE | `tests/ai/test_types.py` |
| Phase 5 EventStream | DONE | `tests/ai/test_event_stream.py` |
| Phase 6 Faux/OpenAI-compatible providers | DONE | `tests/ai/*` |
| Phase 7 low-level loop and recovery | DONE | Agent-loop parity tests |
| Phase 8 parallel completion/result ordering | DONE | Agent and parity tests |
| Phase 9 stateful Agent and queues | DONE | state/listener/queue tests |
| Phase 10 hooks, timeout, stop, next-turn control | DONE | Tool/turn-control tests |
| Phase 11 read/write/edit/bash | DONE | coding-Tool tests |
| Phase 12 ExecutionEnv and AgentHarness | DONE | harness/environment tests |
| Phase 13 append-only JSONL session tree | DONE | session store/manager tests |
| Phase 14 compaction and branch summaries | DONE | compaction tests |
| Phase 15 resource discovery and prompt composition | DONE | resource-loader tests |
| Phase 16 layered settings and source attribution | DONE | settings tests |
| Phase 17 unified AgentSession | DONE | AgentSession tests |
| Phase 18 Print mode | DONE | mode and CLI runtime tests |
| Phase 19 JSON event mode | DONE | mode and CLI runtime tests |
| Phase 20 LF-delimited RPC mode | DONE | RPC tests and protocol map |
| Phase 21 AuthStorage and model access | SOURCE | auth/model-access tests and phase gate |
| Phase 22 Python ExtensionHost runtime | SOURCE | activation/reload/rollback tests and source map |
| Phase 23 local package manager | SOURCE | package/runtime/`pi-pkg` tests |
| Phase 24 AgentSession runtime attachment | SOURCE | runtime integration and rollback tests |
| Phase 25 terminal frame/surface and ANSI renderer | SOURCE | TUI primitive/property tests |
| Phase 26 editor and reusable components | SOURCE | editor/component tests |
| Phase 27 interactive AgentSession client | SOURCE | scripted `MemoryTerminal` tests |
| Phase 28 commands and completion | SOURCE | command/completion/app tests |
| Phase 29 bounded attachments and image processing | SOURCE | attachment/image tests |
| Phase 30 traces, metrics, and exporters | SOURCE | telemetry, fault, and CLI tests |
| Phase 31 approval/security/release gate | SOURCE | approval/security/integration/release tests |
| TypeScript extension ABI/source compatibility | BOUNDARY | Python activation API only |
| Operating-system sandbox and privilege separation | BOUNDARY | approval is authorization, not isolation |
| Hosted package registry/dependency solver | BOUNDARY | trusted local package directories only |
| Vendor browser/device OAuth UI and OS keychain | BOUNDARY | injected refresh callback and file storage only |
| OTLP protobuf/backend compatibility | BOUNDARY | project JSONL and generic HTTP JSON only |

## Phase 25–31 status

Phase 25–31 remains source-mapped and locally tested. No row in that range claims a new
official TypeScript execution fixture.

## Executed fixture coverage

The pinned TypeScript runner executes 20 deterministic Agent Core scenarios. Python parity tests preserve event ordering, queue injection positions, stop reasons, ToolCall IDs, ToolResult IDs, Tool completion order, and transcript order. Timestamps, generated IDs not fixed by a scenario, and provider token counts are normalized.

No Phase 21–31 row is labelled as a new `upstream-execution` fixture. Those product layers are source-mapped and executable locally, but the pinned TypeScript package was not used to generate exact product-level golden records for every scenario.

## Comparative product benchmark

`benchmarks/agent_compare/` runs the pinned TypeScript Coding Agent and this Python CLI with the same model, isolated task fixture, Tool set, context window, output limit, HOME, and workspace. It combines deterministic core parity with repeated live-model traces and artifact validators. The initial three-run analysis is recorded in `docs/audit/agent-trace-comparison-20260903.md`.

Live `qwen-plus` runs are observational evidence, not deterministic fixtures. Model Tool plans can vary between repetitions, so those runs do not change a `SOURCE` row to `DONE`.

## SWE runner audit — 2026-09-04

The maintained SWE entry point is `benchmarks/agent_compare/swe_run.py`, not
`benchmarks/swe5/`. The audit and its explicit remaining blockers are recorded in
`docs/audit/swe5-runner-audit-20260904.md`.

- The generic CLI `--sampling-params` option forwards existing model sampling parameters.
- The OpenAI-compatible request builder falls back to `model.max_tokens` when a call
  does not provide an explicit limit; direct request tests cover the override rule.
- `tests/benchmarks/test_swe_run.py` covers capture faults, large JSON events, timeout
  cleanup, cache-inclusive usage, patch export, grading isolation, and trace comparison.
- `tests/benchmarks/test_swe_offline_cli.py` executes both actual CLIs against a scripted
  localhost SSE server. Both receive a failed baseline and failed edit, then execute a
  successful edit and verification. Request bodies verify thinking-off and output limits.

The scripted provider makes no real Qwen calls and does not measure repair reasoning.
This is executed CLI integration evidence, not a new checked-in upstream golden fixture,
not full product parity, and not a SWE-bench score. Formal five-task runs remain unexecuted.

## Product invariants through Phase 31

- Agent Core parity remains separated from product-layer source mapping;
- package mutations and extension replacement validate a candidate before finalization and restore prior state on failure;
- Print, JSON, RPC, Interactive, approval, attachments, and telemetry use one `AgentSession` path;
- terminal wide-cell and grapheme cursor invariants are property-tested;
- attachment bytes, dimensions, pixels, and workspace roots are bounded before model submission;
- telemetry payload collection is opt-in and exporters apply redaction;
- exact approval caches compare literal canonical arguments, while glob matching requires an explicit pattern path;
- release verification builds and clean-installs both wheel and sdist.
