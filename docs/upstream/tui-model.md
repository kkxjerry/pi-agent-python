# TUI model — pi v0.84.4

The TUI is a terminal application framework above AgentSession. It renders state
and submits commands; it does not own provider or tool execution.

## Terminal abstraction

The upstream design separates terminal I/O from widgets and Agent behavior. The
Python layer will need:

```text
raw-mode lifecycle
key and escape-sequence parsing
resize events
Unicode display width
ANSI-safe clipping
cursor ownership
bracketed paste
main/alternate screen support
synchronized output
differential rendering
```

## Rendering model

The application retains a logical component tree and computes terminal updates.
It does not clear/repaint the entire screen on every token. The viewport and
scroll position are application-owned so streaming output does not fight user
navigation.

## Main components

```text
startup/header
message list
assistant text and thinking blocks
Tool Call and ToolResult views
errors, retries, and notifications
multiline editor
working indicator
footer/status
selectors, dialogs, and overlays
```

Markdown and code rendering must respect terminal width and ANSI sequences.
Large tool output needs collapsing/truncation without changing the model-visible
ToolResult.

## Input behavior

The editor owns history, multiline input, completion, paste handling, and
shortcut dispatch. Slash commands, file completion, model/thinking selectors,
session navigation, and extension-provided UI all route through AgentSession or
the extension API.

## Event consumption

Streaming assistant deltas update an existing message component. Tool progress
updates a tool component without adding durable transcript entries. Terminal
resize reflows components but does not alter Agent state.

## Testability

A virtual terminal must make key parsing, width, viewport, differential output,
and resize behavior deterministic. Tests cannot rely only on screenshots from a
single terminal emulator.

Interactive TUI work is deferred to Phase 25, after Print/JSON/RPC and
AgentSession semantics are stable.
