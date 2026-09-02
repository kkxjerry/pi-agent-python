# Parity status

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

Legend: `DONE` means implementation/evidence/tests agree; `PARTIAL` means an
intentional later-phase boundary remains; `TODO` means not implemented.

| Area | Status | Evidence |
|---|---|---|
| Phase 0 upstream lock and attribution | DONE | `UPSTREAM.md`, license, checksum gate |
| Phase 1 behavior/source map | DONE | eight detailed `docs/upstream/*` contracts and source map |
| Phase 2 scenario registry | DONE | 30 unique scenarios |
| Phase 2 official TypeScript execution | DONE | 10 exact npm `0.84.4` captures, checked-in as `upstream-execution` |
| Phase 2 source-contract verification | DONE | all 10 executed captures exactly matched prior contracts |
| Phase 3 Python project gate | DONE | Python 3.11/3.12, CI, packaging, Ruff, mypy, pytest |
| Phase 4 AI data model | DONE | serialization, cost tiers/cache, options, diagnostics tests |
| Phase 5 EventStream | DONE | termination, result, cancellation/waiter, producer-lifetime tests |
| Phase 6 FauxProvider | DONE | deterministic text/thinking/tool/error/abort tests |
| Phase 6 OpenAI-compatible provider | DONE | SSE, partial args, callbacks, cache/sampling, retry, idle/error tests |
| Phase 6 Provider/Model registry | DONE | routing, ownership, refresh, ambiguity, atomicity tests |
| Phase 7 text loop and continue | DONE | unit tests plus executed-upstream golden parity |
| Phase 7 tool validation/errors/truncation | DONE | unit tests plus executed-upstream golden parity |
| Phase 7 cancellation and termination | DONE | lifecycle, no-replay retry, all-results termination tests |
| Parallel execution/completion-order parity | TODO | Phase 8; upstream golden already captured |
| Stateful Agent/subscriber barrier | TODO | Phase 9 |
| Steering and follow-up | TODO | Phase 9; upstream goldens already captured |
| Session/compaction/product modes | TODO | Phases 13+ |
