# Parity status

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

Legend:

- `DONE`: implemented and covered by local tests.
- `UPSTREAM`: compared with an actually executed official TypeScript fixture.
- `SOURCE`: implemented from the pinned source contract; no executed upstream golden yet.
- `LATER`: outside the current phase.

| Area | Status | Evidence |
|---|---|---|
| Phase 0 baseline lock | DONE | `UPSTREAM.md`, metadata gate |
| Phase 1 source behavior map | DONE | `docs/upstream/*` |
| Phase 2 scenario registry | DONE | 30 registered scenarios |
| Phase 2 TypeScript goldens | UPSTREAM | 10 official npm executions |
| Phase 3 Python project gate | DONE | `scripts/check_phase_0_3.py`, CI |
| Phase 4 AI data model | DONE | `tests/ai/test_types.py` |
| Phase 5 EventStream | DONE | `tests/ai/test_event_stream.py` |
| Phase 6 Faux/OpenAI-compatible providers | DONE | `tests/ai/*` |
| Phase 7 low-level Agent Loop | UPSTREAM | executed text/tool/error/continue fixtures |
| Phase 8–12 prerequisite runtime | DONE | current `develop` implementation and full regression gate |
| Phase 13 JSONL session tree | SOURCE | `tests/harness/session/*` |
| Phase 13 tail recovery and branch navigation | SOURCE | session store/tree tests |
| Phase 14 safe compaction cut points | SOURCE | `tests/harness/compaction/*` |
| Phase 14 branch summaries/model-boundary controller | SOURCE | compaction controller and branch tests |
| Phase 15 layered resources and system prompt | SOURCE | `tests/harness/resources/*` |
| Phase 16 settings precedence and provenance | SOURCE | `tests/coding_agent/test_settings.py` |
| AgentSession product API | LATER | Phase 17 |
| Print/JSON/RPC modes | LATER | Phase 18–20 |
| Extension activation | LATER | Phase 22 |
| Interactive TUI | LATER | Phase 25 |

`SOURCE` is not presented as byte-for-byte or source-compatible parity. It means the Python behavior follows the pinned ownership, ordering, and failure contracts and is covered by deterministic local tests; an executed TypeScript fixture is still required before promotion to `UPSTREAM`.
