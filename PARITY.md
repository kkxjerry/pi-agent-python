# Parity status

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

Legend: `DONE` = implemented and covered by executable local evidence, `BOUNDARY` = intentionally deferred.

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
| Phase 7 low-level loop and recovery | DONE | `tests/agent/test_agent_loop.py` |
| Phase 8 parallel completion/result ordering | DONE | Agent and parity tests |
| Phase 9 stateful Agent and queues | DONE | state/listener/queue tests |
| Phase 10 hooks, timeout, stop, next-turn control | DONE | tool/turn-control tests |
| Phase 11 read/write/edit/bash | DONE | coding-tool tests |
| Phase 12 execution environment and AgentHarness | DONE | harness/environment tests |
| Phase 13 append-only JSONL session tree | DONE | `tests/session/test_session_manager.py` |
| Phase 14 compaction and branch summaries | DONE | `tests/harness/test_compaction.py` |
| Phase 15 resource discovery and prompt composition | DONE | `tests/harness/test_resource_loader.py` |
| Phase 16 layered settings and source attribution | DONE | `tests/coding_agent/test_settings.py` |
| Phase 17 unified AgentSession | DONE | `tests/coding_agent/test_agent_session.py` |
| Phase 18 Print mode | DONE | `tests/coding_agent/test_modes.py` |
| Phase 19 JSON event mode | DONE | `tests/coding_agent/test_modes.py` |
| Phase 20 LF-delimited RPC mode | DONE | `tests/coding_agent/test_modes.py`, `docs/upstream/rpc-protocol.md` |
| Executable extension activation/package manager | BOUNDARY | later phases |
| Interactive TUI | BOUNDARY | later phase |
| Provider/OAuth breadth and telemetry exporters | BOUNDARY | later phases |

## Executed fixture coverage

The pinned TypeScript runner has executed 20 deterministic scenarios. Python parity tests preserve event ordering, queue injection positions, stop reasons, ToolCall IDs, ToolResult IDs, tool completion order, and transcript order. Timestamps, random IDs not fixed by a scenario, and provider token counts remain normalized.

Phase 13–20 storage and product-mode behavior is tested against the documented and source-mapped upstream contracts. New golden files are not labelled `upstream-execution` unless the pinned TypeScript implementation itself was executed for that exact scenario.
