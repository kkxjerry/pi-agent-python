# Extension system — pi v0.84.4

The official product keeps workflow-specific behavior outside its small core.
Python extensions will preserve API semantics, not TypeScript source compatibility.

## Extension capabilities

An extension may register:

```text
LLM-callable tools
slash commands
keyboard shortcuts
CLI flags
providers/models
event hooks
custom messages and renderers
compaction or resource behavior
```

Plan mode, sub-agents, permission prompts, and MCP-style integrations belong here
unless a future upstream baseline moves them into core.

## Lifecycle

Relevant hook families include:

```text
load / activate / dispose / reload
session start and shutdown
before prompt / before model request
message and turn lifecycle
before tool / after tool
tool progress
before compact / after compact
before branch navigation / after navigation
```

Async hooks are awaited at deterministic barriers where their result can affect
execution.

## Mutation rules

- tool preflight may reject or alter a call before side effects;
- post-tool hooks may transform the model-visible ToolResult while retaining
  audit metadata;
- context hooks operate before provider conversion;
- UI hooks do not directly mutate Agent state behind the event stream.

## Transactional activation

Loading an extension can register multiple resources. If activation fails, all
registrations and subscriptions created by that activation must be rolled back.
A half-loaded provider or duplicate command is not an acceptable degraded state.

## Discovery and precedence

Later phases will distinguish user, project, package, and explicit runtime
sources. Every loaded resource carries provenance so conflicts and reloads are
explainable. Project trust/security decisions must happen before executing
project-provided Python code.

## Isolation and failure

Extension exceptions are surfaced with extension identity. Non-critical observer
failures must not corrupt Agent state; preflight hooks that control side effects
must fail closed. Timeouts and cancellation are explicit policies rather than
prompt-based guesses.

## Python mapping

The target shape is an activation function receiving an API object:

```python
def activate(api: ExtensionAPI) -> None:
    api.register_tool(...)
    api.register_command(...)
    api.register_provider(...)
    api.on("turn_end", ...)
```

Distribution may use Python entry points, local modules, or package metadata.
This contract is implemented by the Python-native Phase 22 extension runtime. TypeScript source compatibility remains outside scope.
