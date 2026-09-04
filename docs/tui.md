# Interactive TUI

The interactive mode is another view over `AgentSession`; it does not own an
independent agent loop.

```bash
pi-py
pi-py --mode interactive
pi-py --mode interactive "Inspect this repository"
```

When stdin is a terminal and no print prompt is supplied, `pi-py` selects the
interactive mode. Print, JSON, RPC, SDK, and TUI therefore share the same model,
tools, session tree, compaction, resources, and cancellation behavior.

## Data flow

```text
Terminal bytes
  -> InputDecoder
  -> InteractiveApp event queue
  -> TextEditor / selector / command router
  -> AgentSession
  -> AgentSession events
  -> TranscriptView
  -> Surface
  -> AnsiRenderer row diffs
  -> terminal
```

The renderer keeps a previous `Surface` and only emits rows whose cells changed.
Wide glyphs use a continuation cell so replacing the second half of a CJK or
emoji cluster also clears the first half. Terminal output can be wrapped in the
synchronized-output protocol when the terminal advertises support.

## Editing and navigation

The editor supports:

- multi-line input and bracketed paste;
- left/right, home/end, and vertical cursor movement;
- Ctrl-A, Ctrl-E, Ctrl-B, Ctrl-F, Ctrl-K, Ctrl-U, and Ctrl-W;
- input history;
- slash-command and project-path completion;
- Page Up/Page Down transcript scrolling;
- command, model, thinking-level, and session selectors.

Ctrl-C aborts a running agent. With no active run and no editor text, it exits.
F1 or Ctrl-P opens the command selector.

## Commands

The built-in command router includes:

```text
/help                    show commands
/model [provider/model]  select or switch model
/thinking [level]        select or set reasoning level
/new [name]              create a session
/resume [session-id]     resume a session
/sessions                list sessions
/tree                    inspect the active session tree
/compact                 compact the active branch
/reload                  reload resources
/reload extensions       reload the extension runtime
/extensions              inspect extensions
/packages                inspect installed packages
/install PATH            install a local package
/update NAME [PATH]      update a package
/remove NAME             remove a package
/verify                  verify package integrity
/image PATH              attach an image to the next prompt
/state                   inspect session state
/quit                    exit
```

Extension commands are exposed through the same router when a
`CodingAgentRuntime` is attached.

## Images

`/image` validates the file, parses dimensions without a mandatory image
library, enforces byte/pixel limits, and creates an `ImageContent` block for the
next prompt. Oversized inputs use the optional Pillow backend:

```bash
pip install 'pi-agent-python[image]'
```

The terminal helper can emit Kitty or iTerm inline-image sequences when the
host advertises the protocol. Other terminals receive a bounded textual
placeholder. Model input and terminal rendering remain separate: failure to
render inline does not remove the image from the model request.

## Testing

`MemoryTerminal` is a deterministic terminal host. It records ANSI writes and
feeds byte chunks without changing the real terminal, allowing the complete
TUI-to-`AgentSession` path to run in CI.

## Boundaries

The current implementation owns terminal rendering rather than delegating to a
large TUI framework. It supports POSIX raw mode directly and provides a basic
Windows/non-raw fallback. Clipboard integrations and sixel encoding are not
required for the Phase 25 milestone. The TUI never claims to sandbox tools; see
`docs/security.md`.
