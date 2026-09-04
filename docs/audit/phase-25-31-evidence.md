# Phase 25–31 evidence

Date: 2026-09-03

This milestone consolidates the previously mixed worktree into one product
line built on the Phase 21–24 `AgentSession` and `CodingAgentRuntime` APIs.
It does not claim a second authentication, extension, package, or session
architecture.

## Delivered scope

### Phase 25 — interactive TUI

- incremental terminal byte decoding, including UTF-8 fragments, bracketed
  paste, common key sequences, and SGR mouse events;
- Unicode grapheme/cell-width handling;
- clipped `Surface` with wide-cell continuations;
- row-differential ANSI renderer with synchronized-output support;
- multi-line editor, history, commands, completion, scrolling, selectors,
  real-time AgentSession messages, tool status, abort, and follow-up queues;
- deterministic `MemoryTerminal` end-to-end tests.

### Phase 26 — telemetry

- trace/span context and parentage;
- run, turn, model-stream, and tool spans;
- token, cache, cost, and tool-call metrics;
- no-op, memory, JSONL, and HTTP JSON exporters;
- recursive secret/key redaction;
- exporter failure isolation.

### Phase 27 — security boundary

- before-tool approval policy and TUI broker;
- allow-once, allow-session, and deny decisions;
- path and destructive-command denial;
- argument-free JSONL audit records;
- explicit documentation that approvals are not an OS sandbox;
- shell-free Docker/Podman command construction with environment allowlisting.

### Phase 28 — tests

- unit, integration, fault-injection, and property-style tests for the new
  terminal, telemetry, image, approval, and security layers;
- earlier milestone regression gates remain part of `make check`.

### Phase 29 — performance

- repeatable local benchmark for terminal diffs, byte decoding, and telemetry
  span processing in `benchmarks/benchmark_phase_25_31.py`.

### Phase 30 — documentation

- `docs/tui.md`;
- `docs/telemetry.md`;
- `docs/security.md`;
- runnable examples for interactive mode, telemetry, approvals, and container
  command construction.

### Phase 31 — packaging

- package version `0.6.0.dev0`;
- optional `image` extra for Pillow-backed resize;
- wheel and source-distribution build gate;
- clean-environment install and CLI/import smoke gate;
- Git milestone tag is created only after every verification command passes.

## Verification contract

The final milestone commit is gated by:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest -q
uv run python scripts/check_phase_0_3.py
uv run python scripts/check_phase_8_12.py
uv run python scripts/check_phase_13_16.py
uv run python scripts/check_phase_17_20.py
uv run python scripts/check_phase_21_24.py
uv run python scripts/check_phase_25_31.py
uv run python benchmarks/benchmark_phase_25_31.py --iterations 1000 --json
uv run python scripts/check_phase_31_release.py
```

The release script builds distributions, then creates clean virtual environments for both the wheel and source distribution and validates:

```text
pi-py --version
pi-py upstream --json
pi-py parity --json
imports for coding_agent, tui, telemetry, approval, and security
```

No paid model request, Docker/Podman execution, Lima, or Colima is required by
this evidence gate.
