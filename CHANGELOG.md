# Changelog

## 0.6.0.dev0 — Phase 25–31

- Added a real interactive TUI over `AgentSession`, including incremental input, bracketed paste, Unicode cell layout, multi-line editing, completion, commands, selectors, transcript scrolling, and row-differential rendering.
- Added bounded image ingestion, PNG/JPEG/GIF/WebP dimension parsing, model `ImageContent`, optional Pillow resize, and Kitty/iTerm rendering helpers.
- Added failure-isolated run/turn/model/tool tracing, token/cost/tool metrics, redaction, and memory/JSONL/HTTP exporters.
- Added pre-tool approval policy, TUI approvals, audit logging, explicit default security posture, and shell-free Docker/Podman command construction.
- Added fault-injection tests, property-style tests, local performance benchmarks, and a reproducible TypeScript-vs-Python Agent trace comparison using isolated homes and workspaces.
- Added user documentation, examples, phase gates, wheel/sdist builds, and clean-install validation.
- Consolidated duplicate TUI and telemetry drafts into compatibility adapters over one implementation, and removed the competing authentication, extension, package, and multi-session runtime drafts so Phase 21–24 remains the canonical product foundation.

## 0.5.0.dev0

- Added process-safe credential storage, redacted credential metadata, environment resolution, and single-flight OAuth refresh.
- Added a Python-native extension API with deterministic ordering, contribution collision checks, transactional activation, rollback, reload, event delivery, and reverse-order disposal.
- Added local package manifests, dependency validation, staging, atomic install/update/remove, integrity locking, verification, rollback receipts for install/update/remove, and stale-transaction protection.
- Added `CodingAgentRuntime` to transactionally attach extension tools, prompts, commands, package resources, package lifecycle, and credentials to one `AgentSession` without importing product concerns into Agent Core.
- Added the `pi-pkg` headless package-management command.
- Removed the unreachable `modes/rpc.py` duplicate that was shadowed by the canonical `modes/rpc/` package.

## 0.4.0.dev0

- Added the shared `AgentSession` product API over Agent, execution environment, resources, settings, JSONL persistence, session-tree navigation, and compaction.
- Added SDK construction with persistent or in-memory sessions, model restoration, resource loading, coding-tool composition, and model-backed summarization.
- Added headless Print mode with final-text and JSONL event output.
- Added stable JSON event serialization that omits cumulative partial snapshots while preserving tool-call identity and usage.
- Added stdin/stdout JSONL RPC with background prompts, live steering/follow-up/abort, session and model controls, compaction, resources, tree inspection, and shell execution.
- Added `pi-py -p`, `pi-py --mode json`, and `pi-py rpc` command-line entry points.
- Added Phase 17–20 integration, lifecycle, compaction-boundary, RPC, and local SSE CLI tests.

## 0.3.0.dev0

- Added parallel tool batches with completion-order execution events and source-order ToolResult artifacts.
- Added the reusable stateful `Agent`, ordered awaited listeners, steering/follow-up queues, abort, and post-abort reuse.
- Added tool hooks, timeouts, termination hints, execution-environment interfaces, coding tools, and the session-independent harness.
- Added append-only JSONL sessions, validation, recoverable final-tail handling, tree navigation, labels, and repository operations.
- Added token estimation, safe compaction cut points, split-turn handling, iterative and branch summaries, hooks, usage tracking, and model-boundary compaction control.
- Added layered resource discovery and provenance-aware settings resolution and persistence.
- Added Phase 8–16 tests, structural gates, and audit documentation.

## 0.2.0.dev0

- Rebuilt and accepted the Phase 0–3 local baseline.
- Added provider-neutral AI types and serialization.
- Added generic and assistant-message event streams.
- Added the deterministic Faux provider and OpenAI-compatible SSE provider.
- Added the low-level agent loop, continue mode, context transformation, tool validation, tool execution, and error recovery.
