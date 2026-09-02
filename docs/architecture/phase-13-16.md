# Phase 13–16 architecture

## Ownership

```text
SettingsResolver
  ├─ resolves values + provenance
  ├─ builds ResourceLoaderConfig
  └─ builds CompactionSettings

ResourceLoader
  ├─ discovers user/project/explicit resources
  └─ produces immutable ResourceSnapshot
        └─ SystemPromptBuilder

SessionManager
  ├─ appends typed records
  ├─ persists branch cursor
  └─ projects SessionTree
        └─ reconstruct_context()

CompactionController
  ├─ checks context at the provider boundary
  ├─ calls summarizer with retry/hooks
  ├─ appends CompactionEntry
  └─ reconstructs active context
```

## Session invariants

1. The first JSONL record is exactly one session header.
2. Tree-entry IDs are unique.
3. A parent must already exist when a child is appended.
4. A ToolResult must have an ancestor Assistant ToolCall with the same ID.
5. Navigation is a cursor record and does not delete branches.
6. Compaction is another tree record; historical messages remain in the file.
7. Only an invalid unterminated final record is recoverable automatically; middle corruption is an error.

## Compaction invariants

1. The model-boundary check runs after tool results and before another provider request.
2. A cut cannot occur while a ToolCall batch still has unresolved ToolResults.
3. If the newest completed batch alone exceeds the keep budget, the whole batch is summarized instead of retaining an invalid or over-limit partial batch.
4. Summary usage and file operations are persisted in CompactionEntry details.
5. The latest compaction controls active-context reconstruction; older compactions and messages remain auditable.

## Resource precedence

```text
user root
  < ancestor project .pi roots (root to leaf)
  < explicit roots
```

Named resources are replaced by the later source. Context files and `APPEND_SYSTEM.md` are concatenated in source order. `SYSTEM.md` is replaced by the highest-priority source.

## Settings precedence

```text
default < global < project < environment < CLI < runtime
```

Each final key retains `layer`, `location`, and `key`. Persistence is explicit through either the global or project target; runtime values are not written automatically.
