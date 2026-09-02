# Scope

The project is a Python reimplementation of the pinned official TypeScript pi behavior. It is not source-compatible with TypeScript extensions and does not borrow architecture from PaiCLI, Java agents, LangChain, LangGraph, AutoGen, or CrewAI.

## Implemented through Phase 16

### Foundation and parity evidence

- pinned upstream metadata and source map;
- official TypeScript fixture runner and executed golden files;
- Python packaging, CI, formatting, lint, strict typing, and test gates.

### AI and low-level agent runtime

- provider-neutral model/message/content/usage types;
- asynchronous assistant and agent event streams;
- deterministic faux provider;
- OpenAI-compatible Chat Completions streaming adapter;
- low-level agent loop, context transforms, message conversion, tool validation, and tool execution;
- prior Phase 8–12 agent/tool/harness work already present on `develop` remains a prerequisite and is exercised by the full regression suite.

### Session, compaction, resources, and settings

- append-only JSONL session records and branch tree;
- resume, navigation, fork, labels, list, import/export, rename, delete, and in-memory/no-session mode;
- corrupt-tail recovery without hiding middle-file corruption;
- compaction threshold, safe cut points, split-turn fallback, summaries, branch summaries, usage, hooks, and file-operation tracking;
- layered resource discovery for context files, system prompts, skills, prompt templates, themes, extensions, and packages;
- source-aware settings merge across defaults, global, project, environment, CLI, and runtime layers.

## Deliberately deferred

- complete `AgentSession` product API;
- Print, JSON, and RPC product modes;
- Python extension activation and rollback;
- package installation/update;
- interactive TUI;
- additional first-party providers and OAuth;
- SQLite/remote session backends;
- telemetry exporters and production sandbox policies.

## Compatibility policy

Behavioral parity is claimed only when backed by an executed upstream fixture or a source-mapped contract. Python-native resource and settings APIs preserve the upstream ownership and precedence model but do not claim TypeScript source compatibility.
