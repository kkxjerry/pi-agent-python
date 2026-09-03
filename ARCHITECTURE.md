# Architecture

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
packages/coding-agent print/json/rpc      pi_agent.coding_agent.modes
packages/tui                              later pi_agent.tui phases
```

The implementation preserves ownership and observable behavior, not TypeScript file-for-file structure.

## Dependency direction

```text
pi_agent.ai
    ↓
pi_agent.agent
    ↓
pi_agent.harness
    ↓
pi_agent.coding_agent
    ↓
later TUI / application integrations
```

Lower layers never import persistence, CLI modes, or terminal UI code.

## AI protocol

`pi_agent.ai` owns provider-neutral models, messages, content blocks, usage/cost, provider event streams, cancellation, retry classification, partial JSON, model/provider registries, and provider adapters. It does not execute tools or own a transcript.

## Agent Core

The low-level loop owns one invocation:

```text
prompt/continue
→ transform AgentMessage context
→ convert to provider Message context
→ stream assistant response
→ execute tool batch
→ append ToolResult artifacts
→ drain steering/follow-up queues
→ prepare the next turn
→ emit agent_end
```

Parallel tool execution keeps two orders separate:

```text
tool_execution_end events      actual completion order
ToolResult messages            assistant source order
provider transcript            assistant source order
```

`Agent` adds reusable model/system/tools/messages state, ordered awaited subscribers, steering and follow-up queues, abort/reuse, and pending-tool state.

## Harness and execution environment

`ExecutionEnv` is the injectable filesystem/process capability. The default local implementation uses the Python process's permissions; it is not a sandbox. The default coding tools are:

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

Storage invariants include unique IDs, existing parents, valid cursors, and ToolResult ancestry to the matching assistant ToolCall. A truncated final JSON fragment may be repaired explicitly; corruption in the middle of a file is fatal.

## Compaction

Compaction is a provider-boundary concern, not history deletion:

```text
completed turn/tool results
→ estimate active context
→ choose complete-turn cut point
→ summarize prefix
→ retain recent complete turns
→ append compaction entry
→ send synthetic summary + retained tail to provider
```

An incomplete ToolCall batch cannot be compacted. Later compactions pass the prior summary separately and strip the old synthetic summary before summarizing new history, preventing recursive duplication.

## Resources and settings

`ResourceLoader` merges user, project-ancestor, nearest-project, and explicit roots. High-priority named resources shadow lower-priority resources with structured warnings. Skill bodies are not inserted into the initial system prompt; only name, description, and file location are listed.

Settings precedence is:

```text
default < global < project < environment < CLI < runtime
```

Every resolved setting records its source. Global/project persistence uses atomic replacement and runtime overrides are not written implicitly.

## AgentSession

`AgentSession` is the shared product API. It composes Agent Core, execution environment, coding tools, settings, resources, session storage, and compaction. It owns:

- prompt, continue, steering, follow-up, abort, and idle waiting;
- ordered persistence from `message_end` events;
- model/thinking changes and active-branch restoration;
- explicit/automatic compaction;
- new/resume session and tree navigation;
- resource reload and structured state.

Print, JSON, and RPC modes do not implement their own loops. They subscribe to and invoke this same `AgentSession`.

## Headless modes

```text
Print  assistant text to stdout; errors to stderr
JSON   one complete Agent event per LF-delimited JSON line
RPC    command responses + streamed events + notifications over JSONL
```

RPC serializes all output behind one asynchronous lock and acknowledges an accepted prompt before starting the run task. Interactive TUI remains a later layer over the same session API.


## Credential, extension, and package boundary

Phase 21–24 preserves this dependency direction:

```text
pi_agent.ai / pi_agent.agent / pi_agent.harness
                    ↓
              AgentSession
                    ↓
      CodingAgentRuntime (product layer)
          ↙          ↓           ↘
   AuthStorage  ExtensionHost  PackageManager
```

`AuthStorage`, extension import, and package mutation never flow into Agent Core. An extension first registers contributions in an isolated staging object. Only a fully successful activation becomes visible. Package replacement similarly retains a backup and prior lock entry until a complete candidate ExtensionHost and package-aware resource reload succeed; stale receipts cannot roll back a newer cross-process update.

Python extensions are trusted code in the host process. Path/capability policy prevents accidental activation outside configured roots, but it does not claim operating-system sandboxing.
