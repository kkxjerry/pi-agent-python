# Scope

The goal is a Python reimplementation of the pinned official TypeScript pi behavior. It is not TypeScript source or extension-ABI compatibility.

## Phase 25–31

Included:

- an interactive terminal application over the existing `AgentSession`;
- incremental UTF-8/key/paste decoding, multiline editing, commands, completion, scrolling, Unicode cell layout, and differential rendering;
- workspace-bounded text/image attachments and optional Pillow resizing;
- failure-isolated spans, counters/histograms, JSONL/generic HTTP JSON exporters, and secret redaction;
- Tool approval gates, session-scoped exact/pattern grants, audit records, explicit host-permission documentation, and optional container command construction;
- fault-injection/property tests, local performance benchmarks, TypeScript-vs-Python trace comparison, examples, build, and clean-install verification.

Still outside this milestone:

- a vendor-specific browser OAuth implementation;
- an operating-system sandbox enabled by default;
- hosted package registries and automatic dependency installation;
- TypeScript extension source compatibility;
- sixel, clipboard integration, and pixel-identical parity with the upstream TypeScript TUI.

## Included through Phase 31

### Phase 0–12 — protocol, Agent Core, and Harness

- pinned upstream metadata, source maps, and executed behavior contracts;
- Python packaging, CI, lint, type, build, and test gates;
- provider-neutral model/message/content/usage types and async provider streams;
- Faux and OpenAI-compatible Chat Completions providers;
- low-level prompt/continue loop, context conversion, Tool validation, lifecycle hooks, and turn callbacks;
- sequential/parallel Tool batches with completion-order events and source-order ToolResult artifacts;
- stateful Agent, ordered awaited subscribers, steering/follow-up queues, abort, and reuse;
- injectable filesystem/shell execution environment;
- `read`, `write`, `edit`, and `bash` coding Tools;
- session-independent `AgentHarness`.

### Phase 13–20 — persistence and headless product modes

- append-only JSONL `SessionManager` with resume, navigation, branching, fork, import/export, and explicit corrupt-tail repair;
- compaction and branch summaries with safe ToolCall/ToolResult boundaries;
- user/project/explicit resource loading for context, system prompts, skills, prompts, themes, extensions, and package manifests;
- layered settings with source attribution and atomic JSON/TOML persistence;
- one product-level `AgentSession` shared by SDK, Print, JSON, and RPC modes;
- LF-delimited RPC responses, streamed events, and run notifications.

### Phase 21–24 — product access, extensions, and packages

- private atomic `AuthStorage`, `CredentialResolver`, environment fallback, and refreshable API/OAuth credentials;
- model lookup through `ProviderRegistry`, provider-scoped ambiguity checks, credentialized stream options, and dynamic provider model refresh;
- Python-native `ExtensionHost` activation for Tools, commands, services, event handlers, prompt fragments, and disposal hooks;
- capability/root policy checks, transactional activation/reload, conflict detection, rollback, and lifecycle disposal;
- trusted local-directory packages with manifests, SHA-256 integrity, atomic install/update/remove, lock persistence, dependency checks, and runtime reconciliation;
- single-session `CodingAgentRuntime` attachment that applies package/extension Tools and prompts while restoring the original session state on close;
- `pi-pkg` local package-management CLI.

### Phase 25–28 — terminal UI and interaction model

- Unicode-aware grapheme and terminal-column width handling;
- line frames plus clipped cell surfaces, wide-cell invariants, ANSI style rendering, and bounded differential redraws;
- terminal capability detection, alternate-screen/raw-mode lifecycle, bracketed paste, key, mouse, and resize events;
- grapheme-safe multiline editing, history, word movement/deletion, wrapping, and scrolling;
- reusable text, border, padding, transcript, status, completion, and selection components;
- one interactive application driven by the same `AgentSession` events and methods as Print/JSON/RPC;
- background Prompt execution, live assistant/Tool updates, abort, follow-up queueing, model/thinking selection, Slash Commands, and path/command completion;
- extension commands and attached local package state exposed through the interactive command router.

### Phase 29 — image and file attachments

- PNG, JPEG, GIF, and WebP signature/dimension inspection;
- byte, dimension, decoded-pixel, and workspace-root limits;
- base64 `ImageContent` construction from paths/bytes and editor attachment syntax;
- optional Pillow-backed resize and bounded re-encoding;
- text and image attachments in the Interactive/SDK paths.

### Phase 30 — telemetry

- provider-neutral Run/Turn/Tool spans with parentage, events, status, and failure-isolated processors;
- counters and histograms for Tool calls, Tokens, Cost, and duration-relevant events;
- in-memory, JSONL, composite, no-op, and generic HTTP JSON exporters;
- payload collection disabled by default and exporter-boundary credential/header/token redaction.

### Phase 31 — approval, security boundary, and release hardening

- fail-closed Tool decisions for reads, filesystem mutations, shell execution, and unknown Tools;
- approved-root checks and destructive-command denials;
- one-shot, exact session, and explicit-pattern session grants;
- Console and TUI approval handlers plus JSONL audit output;
- exact grants compare literal argument structures and never interpret wildcard characters as patterns;
- documented distinction between authorization and sandboxing;
- phase gates, benchmarks, build/install smoke checks, and release documentation.

## Deliberately not included

- TypeScript extension source or ABI compatibility;
- an operating-system sandbox, syscall policy, privilege separation, or protection from trusted in-process Python extensions;
- automatic package install scripts, remote package resolution, or dependency download;
- OS keychain integration or vendor-specific browser/device OAuth interfaces;
- automatic migration between incompatible provider wire protocols;
- OTLP protobuf encoding or a hosted telemetry backend;
- pixel-identical terminal rendering across every terminal emulator;
- local Docker, Lima, Colima, or `limactl` execution in the verification suite.

## Source boundary

Product design and behavior are derived only from the pinned official TypeScript baseline:

```text
earendil-works/pi
v0.84.4
b79e4cc
```

PaiCLI, Java ports, LangChain, LangGraph, AutoGen, and other agent frameworks are not implementation references.

## Security boundary

The default local `ExecutionEnv` inherits the permissions of the Python process. `ApprovalGate` and `ApprovalManager` are authorization and audit layers, not sandboxes. Approved shell commands and trusted in-process Python extensions can perform anything permitted to that process. Strong isolation requires a separately supplied restricted `ExecutionEnv` or an external operating-system/container boundary.
