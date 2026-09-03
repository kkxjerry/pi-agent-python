# Phase 17–20 acceptance evidence

## Baseline correction

The repository history was inspected before implementation. The previous narrative claimed a committed Phase 13–16 milestone, but the actual `develop` history still ended at the Phase 0–7 commit and contained an uncommitted Phase 8–12 worktree. This milestone therefore treats repository files and executable gates as the source of truth. Session, compaction, resource, and settings layers were implemented as explicit prerequisites rather than assuming they already existed.

The sole behavior reference remains `earendil-works/pi@v0.84.4` (`b79e4cc`).

## Phase 17 — AgentSession

`AgentSession` is the single product-level runtime used by every headless mode. It composes:

- the stateful `Agent` and low-level loop;
- an injectable execution environment and coding tools;
- append-only `SessionManager` persistence;
- settings and source tracking;
- resource discovery and system-prompt composition;
- automatic and explicit compaction;
- model/thinking changes, tree navigation, session replacement, resume, abort, steering, and follow-up messages.

Message persistence is performed from ordered `message_end` events. Persistence errors are surfaced after the run rather than silently leaving the in-memory transcript ahead of the session file. Model and thinking metadata are restored from the active branch on initial open, resume, and tree navigation.

## Phase 18 — Print mode

Print mode streams assistant text deltas to stdout, keeps diagnostics/errors on stderr, appends one final newline when needed, and returns a non-zero status for failed or aborted runs. It invokes `AgentSession.prompt()` and contains no independent agent loop.

## Phase 19 — JSON mode

JSON mode emits one complete JSON value per LF-delimited line. It serializes the same Agent events used by SDK/RPC consumers, including message lifecycle, tool progress, turn boundaries, and final agent state. Arbitrary extension/tool details use a deterministic JSON fallback rather than corrupting stdout with logging text.

## Phase 20 — RPC mode

RPC mode uses LF-delimited JSON on stdin/stdout and separates:

- command responses (`type=response`);
- streamed agent events (`type=event`);
- run completion/failure notifications (`type=notification`).

Supported commands include prompt, continue, steer, follow-up, abort, state/model inspection, model/thinking changes, compaction, tree inspection/navigation, resource reload, new/resume session, and shutdown. Prompt acceptance is written before the asynchronous run can emit events. All writes pass through one lock so command responses and event frames cannot interleave at the byte level.

## Session and compaction prerequisites

The implementation added append-only JSONL sessions with explicit tail repair, branch cursors, tree reconstruction, ToolCall/ToolResult validation, no-session memory storage, import/export, fork, and active-context rebuilding. Valid final records without a trailing newline remain appendable; malformed final fragments require explicit repair, while middle-file corruption is rejected.

Compaction uses complete conversational turns as cut units and rejects incomplete ToolCall batches. It preserves original session entries, inserts a synthetic summary only into the active provider context, supports previous-summary iteration without repeatedly summarizing the old synthetic summary, and runs before the next provider request after a completed turn.

## Deterministic verification

The milestone is covered by tests for:

- JSONL create/resume/branch/corrupt-tail/no-newline behavior;
- ToolResult ancestry validation;
- safe compaction cuts, explicit compaction, prior-summary handling, and file tracking;
- resource precedence, shadow warnings, skills, prompts, invalid JSON, and prompt construction;
- settings precedence, source attribution, validation, and atomic JSON/TOML persistence;
- AgentSession tool execution, resume, model/thinking restoration, compaction, navigation, and resource reload;
- Print, JSON, and RPC framing/ordering/error behavior.

Required final gates:

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy --strict src
uv run pytest -q
python3.11 scripts/check_phase_0_3.py
python3.11 scripts/check_phase_8_12.py
python3.11 scripts/check_phase_17_20.py
git diff --check
```

The deterministic Faux provider is used for acceptance. No paid provider request, local Docker, Lima, or Colima is required.
