# Architecture

## Phase 25–31 product edge

```text
AgentSession
├── InteractiveTui
│   ├── KeyDecoder / InputDecoder
│   ├── TextEditor / CommandRouter / completion
│   ├── TranscriptView / Canvas / Surface
│   └── AnsiRenderer / Terminal
├── ApprovalGate or ApprovalManager
├── AgentSessionInstrumentation
│   ├── TracerProvider / SpanProcessor
│   └── MeterProvider / TelemetryExporter
├── AttachmentResolver / ImageProcessor
└── CodingAgentRuntime
    ├── ExtensionHost
    ├── PackageManager
    └── AuthStorage / CredentialResolver
```

Interactive, Print, JSON, RPC, and SDK modes do not implement separate Agent loops. They are views and integration layers over one `AgentSession`, one session tree, one Tool runtime, and one provider stream.

## Upstream mapping

```text
Official TypeScript pi                    Python package
──────────────────────────────────────────────────────────────────
packages/ai                               pi_agent.ai
packages/agent low-level loop             pi_agent.agent.agent_loop
packages/agent stateful Agent             pi_agent.agent.agent
packages/agent harness                    pi_agent.harness
packages/agent session/compaction         pi_agent.harness.session/compaction
packages/coding-agent AgentSession        pi_agent.coding_agent.AgentSession
packages/coding-agent auth/models         pi_agent.coding_agent.auth/model_access
packages/coding-agent extensions          pi_agent.coding_agent.extensions
packages/coding-agent packages            pi_agent.coding_agent.packages
packages/coding-agent print/json/rpc      pi_agent.coding_agent.modes
packages/tui                              pi_agent.tui
telemetry integration                     pi_agent.telemetry
```

The implementation preserves ownership and observable behavior, not TypeScript file-for-file layout or TypeScript extension compatibility.

## Dependency direction

```text
pi_agent.ai
    ↓
pi_agent.agent
    ↓
pi_agent.harness
    ↓
pi_agent.coding_agent
    ↓                    ↘
pi_agent.tui              pi_agent.telemetry
```

Agent Core does not import persistence, package management, TUI, approval UI, or telemetry exporters. Product-edge modules observe or configure Agent Core through explicit interfaces.

## AI protocol

`pi_agent.ai` owns provider-neutral models, messages, content blocks, Tool definitions, usage/cost, cancellation, retry classification, partial JSON, model/provider registries, and provider adapters. It neither executes Tools nor owns a conversation transcript.

## Agent Core

One low-level invocation follows this path:

```text
prompt/continue
→ transform AgentMessage context
→ convert to provider Message context
→ stream assistant response
→ validate and execute Tool batch
→ append ToolResult artifacts
→ drain steering/follow-up queues
→ prepare the next turn
→ emit agent_end
```

Parallel Tool execution keeps two orders separate:

```text
tool_execution_end events      actual completion order
ToolResult messages            assistant source order
provider transcript            assistant source order
```

`Agent` adds reusable model/system/Tools/messages state, ordered awaited subscribers, steering and follow-up queues, abort/reuse, pending Tool state, and provider-boundary hooks. It remains unaware of sessions, extensions, packages, TUI, and telemetry.

## Harness and execution environment

`ExecutionEnv` is the injectable filesystem/process capability. The default local implementation uses the Python process permissions; it is not a sandbox. The built-in coding Tools are:

```text
read   bounded text/image reads
write  atomic create/replace
edit   exact all-or-nothing replacements
bash   streamed subprocess execution and cleanup
```

`AgentHarness` remains session-independent so embedding applications can use Agent Core without product persistence.

## Append-only session tree

`SessionManager` writes one JSON object per line:

```text
session header
message/model/thinking/compaction/branch/custom/label entries
cursor entries for navigation
```

Normal entries form a tree through `parentId`. Navigation appends a cursor instead of deleting history. Compaction appends metadata and a summary while original messages remain available for audit and alternate branches.

Storage validation enforces unique IDs, existing parents, valid cursors, and ToolResult ancestry to the matching assistant ToolCall. An unterminated final fragment can be repaired explicitly; corruption in the middle of a session remains fatal.

## Compaction

Compaction is a provider-boundary transformation, not history deletion:

```text
completed turn/Tool results
→ estimate active context
→ choose a complete-turn cut point
→ summarize prefix
→ retain recent complete turns
→ append compaction entry
→ send synthetic summary plus retained tail
```

An incomplete ToolCall batch cannot be compacted. Later compactions pass the previous summary separately and strip the old synthetic summary before summarizing new history, preventing recursive duplication.

## Resources and settings

`ResourceLoader` merges user, project-ancestor, nearest-project, package, and explicit roots. Higher-priority named resources shadow lower-priority ones with structured warnings. Skill bodies are not inserted wholesale into the initial prompt; the system prompt contains the catalog and file locations.

Settings precedence is:

```text
default < global < project < environment < CLI < runtime
```

Every resolved setting records its origin. Global/project persistence uses atomic replacement, and runtime overrides are not written implicitly.

## Authentication and model access

`AuthStorage` keeps credentials in a private atomic JSON file. `CredentialResolver` resolves an API key in this order:

```text
explicit call secret
→ stored credential
→ provider environment variable
```

Expired refreshable credentials can invoke an injected refresh callback; concurrent refresh is serialized. Secret values are redacted from representations and are supplied at the provider boundary rather than through global mutation.

`ModelAccess` resolves a model from `ProviderRegistry`, rejects ambiguous unscoped IDs, obtains the credential through `CredentialResolver`, and produces one `PreparedModel` containing the model, stream function, and request options. Provider model catalogs can be refreshed through the registry.

## Extension transaction model

An extension receives a staging API. Registration does not mutate the active session while activation is running:

```text
validate descriptor/root/capabilities
→ import trusted Python module
→ activate into staged contribution
→ validate Tool/command/service conflicts
→ publish complete handle
```

The contribution can contain Tools, commands, services, event handlers, prompt fragments, and disposal hooks. `ExtensionHost.activate_many()` can operate strictly; on failure, staged contributions are disposed and the previous live host remains intact. Reload creates a candidate before replacing the active handle.

Project and package extensions are trusted in-process Python code after policy approval. The root/capability gate prevents accidental activation; it is not isolation.

## Local package transaction model

`PackageManager` owns a package root, an atomic lock file, installed payloads, SHA-256 integrity, and dependency checks:

```text
copy local source to staging
→ reject symlinks/path traversal
→ parse pi-package.json
→ compute path-and-content integrity
→ atomically replace package directory
→ atomically replace package lock
→ validate candidate ExtensionHost/resources
→ finalize backup removal
```

`install`, `update`, and `remove` can return staged receipts. `CodingAgentRuntime` only finalizes the receipt after a candidate extension set and resource reload succeed. If reconciliation fails, package files and lock state are rolled back, then the previous extension/resource state is restored.

Hosted package registries and automatic dependency installation are intentionally outside scope.

## CodingAgentRuntime

`CodingAgentRuntime` attaches above one existing `AgentSession`. It owns:

```text
AuthStorage / CredentialResolver
PackageManager
ExtensionPolicy
active ExtensionHost
base Tool list and base system prompt snapshots
```

Startup discovers explicit, project-resource, and installed-package descriptors; builds a candidate host; checks Tool collisions; then attaches contributed Tools and prompt fragments to the session. Closing the runtime waits for the session to become idle, disposes extensions, restores resource roots, and restores the original Tools/system prompt.

The runtime does not share mutable transcript, queue, or branch state between sessions. Applications that need multiple sessions create one runtime attachment per session while reusing lower-level provider objects explicitly.

## AgentSession

`AgentSession` is the shared product API. It composes Agent Core, execution environment, coding Tools, settings, resources, session storage, compaction, model registry, and optional approval hooks. It owns:

- Prompt, Continue, steering, follow-up, abort, and idle waiting;
- ordered persistence from `message_end` events;
- model/thinking changes and active-branch restoration;
- explicit and automatic compaction;
- new/resumed sessions and tree navigation;
- resource reload;
- structured state used by Print, JSON, RPC, TUI, and telemetry.

`CodingAgentRuntime`, approval, attachments, and telemetry remain optional layers around that same object.

## Headless modes

```text
Print  final assistant text to stdout; errors to stderr
JSON   one complete AgentSession event per LF-delimited JSON line
RPC    command responses, events, and notifications over JSONL
```

RPC serializes output behind one asynchronous lock and acknowledges an accepted Prompt before starting the run task.

## Terminal UI

The TUI is layered as follows:

```text
Terminal bytes/events
→ KeyDecoder / InputDecoder
→ InteractiveTui event loop
→ AgentSession methods/events
→ editor, transcript, commands, completion
→ Canvas or Surface
→ AnsiRenderer differential output
```

Unicode grapheme segmentation and terminal column width are shared by the editor, line canvas, cell surface, completion views, and status output. Prompt execution runs in a background task so abort, queued follow-up, scrolling, completion, and approval remain responsive. EOF during an active run exits only after the run settles.

## Attachments and images

`AttachmentResolver` parses workspace-bound `@path` and `@image:path` references. Text attachments become prompt text; image attachments pass through `ImageProcessor`.

`ImageProcessor` performs signature-based format detection before constructing provider content. Limits are checked on input bytes, output bytes, dimensions, and decoded pixels. Pillow is imported lazily only when an oversized image must be resized.

## Telemetry

`pi_agent.telemetry` is an observer layer:

```text
AgentSession events
→ Run / Turn / Tool spans
→ Token, Cost, and Tool-call metrics
→ redaction
→ memory / JSONL / HTTP JSON exporters
```

Two public styles share the same `SpanData` and exporter protocol:

- asynchronous `Tracer`/`Meter` for direct integration;
- `TracerProvider` plus `SimpleSpanProcessor` or `BatchSpanProcessor` for processor-based integration.

Prompt, result, and Tool argument content are disabled by default in `AgentSessionInstrumentation`. Export failures become diagnostics and do not alter Agent execution.

## Approval and security boundary

The SDK supports two compatible authorization adapters:

- `ApprovalGate`, composed into the Agent `before_tool_call` hook;
- `ApprovalManager`, which wraps Tool execution and supports exact/pattern session grants.

The effective decision path is:

```text
path and destructive-command policy
→ configured allow/deny/ask decision
→ optional Console or TUI approval
→ session grant/cache
→ audit record
→ Tool execution or blocked ToolResult
```

Exact grants compare literal argument structures. Wildcards only become patterns through an explicit pattern workflow.

Approval is not a sandbox. Approved shell commands and trusted Python extensions retain the permissions of the host Python process. `ContainerSandboxSpec` only constructs a shell-free Docker/Podman command; the verification suite does not launch a local container runtime.
