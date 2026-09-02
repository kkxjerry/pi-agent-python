# AGENTS.md

This repository reimplements the pinned official TypeScript pi release in Python.

## Hard rules

- Work only on `develop` or one worktree based on `develop`.
- The sole behavior reference is the official `earendil-works/pi` baseline in `UPSTREAM.md`.
- Read `UPSTREAM.md`, `SCOPE.md`, `ARCHITECTURE.md`, and `PARITY.md` before parity-sensitive changes.
- Do not call fixtures `upstream-execution` unless the pinned TypeScript package was actually executed.
- Keep AI protocol, Agent Core, Harness, Coding Agent, TUI, and telemetry independent.
- Do not introduce planner, team, RAG, memory, todo, or sub-agent behavior into the core.
- Do not use local Docker, Lima, Colima, or `limactl`.
- Every behavior change needs tests, parity status, and audit evidence.
- Explicitly report skipped network checks and unavailable tooling.

## Required checks

```bash
python3.11 scripts/check_phase_0_3.py
uv run --python 3.11 pytest -q
uv run --python 3.11 ruff check .
uv run --python 3.11 ruff format --check .
```
