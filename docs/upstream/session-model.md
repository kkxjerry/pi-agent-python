# Session model — pi v0.84.4

Session behavior belongs to the coding-product layer, not the low-level Agent
Loop. This document is the implementation contract for later phases.

## Storage shape

The default durable representation is append-only JSONL. A session starts with a
header and then appends typed entries. Every branchable entry has an `id` and a
`parentId`, forming a tree rather than a single overwritten transcript.

Representative entry families:

```text
session header
message
model change
thinking-level change
compaction
branch summary
custom/extension entry
label or metadata
```

## Active branch

A session has an active leaf. Rebuilding the model context walks parent links from
that leaf to the root and then restores chronological order. Navigating or
forking changes the active leaf; it does not delete sibling history.

## Message integrity

Assistant Tool Calls and corresponding ToolResults must remain structurally
valid after save/resume. Session reconstruction may repair or report an
incomplete tail, but it may not silently invent successful tool output.

## Append semantics

- existing entries are immutable;
- each accepted state transition appends one or more records;
- a durable write is flushed before the product reports persistence success;
- compaction and branch summaries append entries instead of rewriting messages.

This makes audit, fork, export, and crash recovery possible.

## Recovery boundaries

The reader must explicitly handle:

- a file with no final newline;
- a partially written final JSON object;
- malformed tail data after valid records;
- duplicate IDs or missing parents;
- cycles or impossible active leaves;
- schema-version migration;
- concurrent writers.

A damaged final record must not invalidate prior complete records. Conversely, a
parser failure must not be reinterpreted as an empty valid session.

## Run/session separation

A durable user session records conversational history, branches, model settings,
and summaries. Future execution checkpoints may store transient tool/process
state separately. Combining both into one mutable row would make branch replay
and crash recovery ambiguous.

## Product operations

Later Python phases will implement:

```text
new / list / resume
name / export / import
branch / fork / navigate / tree
compact
no-session ephemeral mode
```

Print, JSON, RPC, SDK, and TUI must use the same SessionManager behavior.
