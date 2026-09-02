# Phase 13–16 acceptance evidence

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

## Phase 13 — append-only session tree

Implemented under `src/pi_agent/harness/session/`:

- typed session header, message, model-change, thinking-level, compaction, branch-summary, custom, label, and cursor records;
- stable camelCase JSON codec for persisted messages and records;
- append-only JSONL store with process/thread locking;
- valid final records without a trailing newline;
- explicit recovery of only an invalid, unterminated final record;
- refusal to append after an unrepaired corrupt tail;
- duplicate-id, unknown-parent, cursor, and orphan ToolResult validation;
- unresolved ToolCall warnings for interrupted sessions;
- branch tree projection, common-ancestor and branch-delta queries;
- persistent navigation cursor, fork-on-next-append behavior, labels, repository listing, import, export, rename, and delete;
- active-context reconstruction from the latest compaction entry.

The store does not reinterpret corruption in the middle of a file as an empty or valid session. It fails explicitly and preserves the source bytes for audit.

## Phase 14 — compaction and branch summaries

Implemented under `src/pi_agent/harness/compaction/`:

- deterministic token estimation and latest-assistant usage accounting;
- threshold, reserve-token, and keep-recent-token policy;
- safe cut-point calculation that never separates a ToolCall from its ToolResult batch;
- whole-batch split-turn fallback when the newest completed tool batch alone exceeds the keep budget;
- iterative summaries with previous-summary input;
- summary retry with cancellation propagation;
- before/after interception hooks;
- summary-model usage accounting;
- read/modified file-operation tracking;
- append-only CompactionEntry persistence;
- active-context reconstruction without deleting original history;
- abandoned-branch collection, summarization, and destination-branch attachment;
- a `CompactionController` model-boundary gate intended to run after tool results and before the next provider request.

## Phase 15 — resource loading and system prompt construction

Implemented in `resource_loader.py`, `resource_types.py`, and `system_prompt.py`:

- user root, root-to-leaf project `.pi` roots, and explicit roots;
- `SYSTEM.md`, `APPEND_SYSTEM.md`, ancestor `AGENTS.md`/`CLAUDE.md` context files;
- skills, prompt templates, themes, extension references, and package manifests;
- deterministic priority, name-based shadowing, and source metadata;
- include/exclude globs and symlink policy;
- tolerant warnings for malformed UTF-8, JSON, and frontmatter;
- reload generations;
- full context injection but skill-catalog-only system prompt injection;
- on-demand skill body and prompt-template lookup.

Extension files and packages are discovered only. They are not executed or installed in this phase.

## Phase 16 — settings and provenance

Implemented in `coding_agent/settings.py` and `coding_agent/configuration.py`:

- built-in defaults;
- global JSON/TOML settings;
- project JSON/TOML settings;
- environment variables;
- CLI overrides;
- runtime-only overrides;
- deterministic precedence in that order;
- type coercion, enum/range checks, cross-field compaction checks, and explicit unknown-field handling;
- per-key origin layer and location;
- separate global/project persistence targets;
- atomic settings-file replacement;
- conversion into ResourceLoader and Compaction runtime configuration.

Runtime overrides are never persisted implicitly, and project writes do not target the global file.

## Verification commands

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest -q
uv run python scripts/check_phase_0_3.py
uv run python scripts/check_phase_13_16.py
npm --prefix tools/upstream-fixtures run typecheck
npm --prefix tools/upstream-fixtures run capture -- --out /tmp/pi-upstream-capture
uv run python scripts/compare_upstream_capture.py /tmp/pi-upstream-capture
```

## Deliberate boundaries

- The original JSONL history is retained; physical pruning/archival is not part of compaction.
- A corrupt middle record is not auto-repaired.
- Resource extensions are not imported or activated before the extension-system phase.
- Package manifests are discovered, not installed.
- Settings do not contain or persist provider secrets in this phase.
- SQLite and remote session backends remain later work.
