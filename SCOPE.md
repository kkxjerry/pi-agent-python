# Scope

The goal is a Python reimplementation of the pinned official pi behavior, not source compatibility with TypeScript extensions.

## Included through Phase 20

- pinned upstream metadata, source maps, and executed behavior contracts;
- Python packaging, CI, lint, type, build, and test gates;
- provider-neutral model/message/content/usage types and async provider streams;
- Faux and OpenAI-compatible Chat Completions providers;
- low-level prompt/continue loop, context conversion, tool validation, lifecycle hooks, and turn callbacks;
- sequential/parallel tool batches with completion-order events and source-order ToolResult artifacts;
- stateful Agent, ordered awaited subscribers, steering/follow-up queues, abort, and reuse;
- injectable filesystem/shell execution environment;
- read, write, edit, and bash coding tools;
- session-independent AgentHarness;
- append-only JSONL SessionManager with resume, navigation, branching, fork, import/export, and explicit corrupt-tail repair;
- compaction and branch summaries with safe ToolCall/ToolResult boundaries;
- user/project/explicit resource loading for context, systems prompts, skills, prompts, themes, extensions, and package manifests;
- layered settings with source attribution and atomic JSON/TOML persistence;
- one product-level AgentSession shared by SDK-style use, Print, JSON, and RPC modes;
- LF-delimited RPC command responses, streamed events, and run notifications.

## Deliberately not included yet

- interactive TUI and terminal rendering;
- executable Python extension activation and lifecycle hooks;
- package install/update/remove workflows;
- provider breadth and OAuth/subscription authentication beyond the current provider layer;
- image preprocessing/resizing beyond supported direct read attachments;
- telemetry exporters and remote observability;
- a security sandbox or approval policy;
- TypeScript extension source compatibility.

## Source boundary

Product design and behavior are derived only from the pinned official TypeScript baseline:

```text
earendil-works/pi
v0.84.4
b79e4cc
```

PaiCLI, Java ports, LangChain, LangGraph, AutoGen, and other agent frameworks are not implementation references.

## Security boundary

The default local execution environment uses the permissions of the Python process. It is not a sandbox. Filesystem restrictions, command policies, containers, and approval workflows belong in later optional environments/extensions rather than being silently presented as core guarantees.

### Credentials, extensions, packages, and product integration

- process-safe API-key/OAuth credential persistence and refresh coordination;
- Python-native extension registration, transactional activation/reload, event delivery, and disposal;
- local package manifests, dependency checks, integrity lock, atomic install/update/remove, and rollback;
- `CodingAgentRuntime` integration with AgentSession tools, prompts, commands, resources, and package lifecycle;
- headless `pi-pkg` command.

