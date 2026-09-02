# Compaction behavior — pi v0.84.4

Compaction is controlled by AgentSession/Harness. It is not an ad-hoc summary
inside the provider or Agent Core.

## Purpose

When active context approaches the model limit, compaction replaces older active
context with a summary while preserving the append-only session history. It is a
context projection operation, not destructive deletion.

## Trigger points

The budget must be checked:

- before a provider request when current history is already large;
- after adding ToolResults, because one large tool output can cross the threshold;
- after an overflow error when policy allows compact-and-retry.

Checking only when the next user prompt arrives is too late for long autonomous
runs.

## Cut-point rules

A valid cut point:

- preserves a configurable recent token budget;
- does not begin at a ToolResult;
- keeps an assistant Tool Call paired with its ToolResult;
- retains enough current-turn state to continue the task;
- supports a split-turn path when one turn alone exceeds the normal budget.

## Summary content

The summary should preserve information needed for continuation, including:

- current goal and user constraints;
- decisions and rejected alternatives;
- completed and pending work;
- relevant errors and test outcomes;
- files read and files modified;
- model/tool state that cannot be reconstructed cheaply.

A summary is evidence-carrying state, not generic prose.

## Session representation

Compaction appends a compaction entry with summary and boundary metadata. Original
entries remain available for audit, branch switching, and export. Repeated
compaction summarizes the previous summary plus newly compacted material rather
than recursively paraphrasing the entire raw history each time.

## Branch summaries

When navigating away from a branch, a branch summary can preserve branch-specific
work for later re-entry. It applies only to that branch and must not contaminate
a sibling branch's active context.

## Failure semantics

Summary model failure, cancellation, or malformed output must leave the original
context usable. Usage/cost for compaction is accounted separately. Automatic
retry must not append duplicate compaction entries.

## Deferred Python phases

Token estimation, budget policy, split-turn summarization, branch summaries, and
session entries are Phase 14. Phase 7 only exposes the `transform_context`
boundary where compacted context will later be applied.
