# pi-agent-python

> Current milestone: **Phase 25–31 (`0.6.0.dev0`)**. Interactive TUI, bounded attachments, telemetry, approval/security boundaries, fault/property tests, benchmarks, documentation, and release packaging all run through the same `AgentSession` product path.

Python reimplementation of the pinned official TypeScript pi baseline:

```text
repository:       earendil-works/pi
upstream tag:     v0.84.4
upstream commit:  b79e4cc
Python scope:     Phase 0–31
```

The project follows observable behavior and package ownership from the official TypeScript implementation. It does not copy another Python or Java agent framework and does not claim TypeScript extension source compatibility.

## Implemented product surface

- provider-neutral messages, models, usage/cost, cancellation, retries, registries, and streams;
- OpenAI-compatible streaming plus deterministic `FauxProvider` tests;
- reusable Agent loop with sequential/parallel Tools, hooks, steering, follow-up, abort, and turn callbacks;
- injectable filesystem/shell environment and `read`, `write`, `edit`, and `bash` coding Tools;
- append-only JSONL session trees, branch navigation, compaction, resource loading, and layered settings;
- one `AgentSession` shared by Print, JSONL, RPC, Interactive TUI, extensions, approval, and telemetry;
- private `AuthStorage`, `CredentialResolver`, model/provider selection, and refreshable credentials;
- transactional Python `ExtensionHost` activation and local-directory `PackageManager` installs;
- Unicode-aware terminal rendering, multiline editing, completion, scrolling, and live Agent/Tool events;
- bounded PNG/JPEG/GIF/WebP processing and TUI `@file` / `@image:path` attachments;
- Run/Turn/Tool traces, Token/Cost metrics, JSONL and generic HTTP JSON exporters;
- fail-closed approval gates, exact session grants, explicit glob grants, audit records, and documented host-permission boundaries;
- deterministic TypeScript-vs-Python Agent Core parity plus repeated live Coding Agent trace comparison.

Exact boundaries are documented in [SCOPE.md](SCOPE.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [PARITY.md](PARITY.md).

## Requirements

- Python 3.11 or newer;
- `uv` for the repository workflow;
- Node.js only for re-running the pinned TypeScript parity/benchmark harness;
- Pillow only when an oversized image must be resized.

```bash
uv sync --all-extras --python 3.11
```

## CLI

### Interactive

With a terminal and no explicit prompt, interactive mode is selected automatically:

```bash
uv run pi-py
uv run pi-py --mode interactive
uv run pi-py --approval prompt --telemetry-jsonl .pi/telemetry.jsonl
```

The interactive client is a view over `AgentSession`; it does not own a second Agent loop. Built-in commands are:

```text
/help
/model [provider/model]
/thinking [level]
/new [name]
/session
/compact
/reload
/packages
/clear
/quit
```

Files can be attached in the editor with `@path`; use `@image:path` to require image handling. Workspace-bound path validation prevents attachment traversal outside approved roots.

### Print, JSON, and RPC

```bash
uv run pi-py -p "Read README.md and summarize it"
uv run pi-py --mode json -p "Inspect the repository"
uv run pi-py --mode rpc
```

Print emits the final assistant text. JSON mode emits LF-delimited AgentSession events. RPC accepts LF-delimited commands while using the same session, queues, compaction controller, and Tool runtime.

### DashScope / Qwen

```bash
export DASHSCOPE_API_KEY="..."
uv run pi-py --provider dashscope --no-session -p "Reply with exactly PONG"
```

For `dashscope`, the CLI defaults to `qwen-plus` and the Beijing OpenAI-compatible endpoint. `--model` and `--base-url` can override those defaults. The key and endpoint must belong to the same region.

### Approval

Approval is opt-in at the CLI boundary:

```bash
uv run pi-py --approval prompt -p "Modify the requested file"
uv run pi-py --approval deny -p "Inspect without mutations"
```

Supported modes are `off`, `prompt`, `deny`, and `allow`. `--approval-audit PATH` appends redacted decision records. The gate is authorization logic, not an operating-system sandbox.

### Telemetry

```bash
uv run pi-py \
  --telemetry-jsonl .pi/telemetry.jsonl \
  -p "Run the tests"
```

Prompt, Tool argument, and Tool result payloads are omitted by default. `--telemetry-payloads` enables them, but known credentials and sensitive fields are still redacted before export.

### Metadata

```bash
uv run pi-py upstream --json
uv run pi-py parity --json
```

## Local package CLI

`pi-pkg` installs trusted local package directories transactionally:

```bash
uv run pi-pkg install ./my-package
uv run pi-pkg list
uv run pi-pkg verify
uv run pi-pkg update my-package ./my-package-v2
uv run pi-pkg remove my-package
```

A package can contribute Python extensions and resource paths through `pi-package.json`:

```json
{
  "name": "example-package",
  "version": "1.0.0",
  "extensions": ["extensions/main.py"],
  "skills": ["skills"],
  "prompts": ["prompts"],
  "themes": ["themes"],
  "dependencies": []
}
```

Install, update, remove, lock persistence, checksum verification, `ExtensionHost` reconciliation, and resource reload form one rollback chain. Hosted registries and automatic dependency installation remain outside scope.

## Python SDK

```python
import asyncio
from pathlib import Path

from pi_agent.ai import Model, OpenAICompatibleProvider
from pi_agent.coding_agent import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalPolicy,
    ApprovalRequest,
    CreateAgentSessionOptions,
    create_agent_session,
)


async def approve(request: ApprovalRequest) -> ApprovalDecision:
    print(request.tool_name, request.reasons)
    return ApprovalDecision("deny", "SDK example is read-only")


async def main() -> None:
    provider = OpenAICompatibleProvider()
    model = Model(
        api="openai-completions",
        provider="openai",
        id="gpt-4.1-mini",
        name="gpt-4.1-mini",
        base_url="https://api.openai.com/v1",
        context_window=128_000,
        max_tokens=16_384,
    )
    gate = ApprovalGate(
        ApprovalPolicy(Path.cwd()),
        session_id="pending",
        prompt=approve,
    )
    created = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=".",
            model=model,
            stream_fn=provider.stream,
            approval_gate=gate,
        )
    )
    session = created.session
    try:
        result = await session.prompt("Read README.md")
        print(result.final_assistant)
    finally:
        await session.close()


asyncio.run(main())
```

For deterministic tests, inject `FauxProvider`, an in-memory session, and a fake or local `ExecutionEnv`.

## Python extensions

```python
class ReviewTool:
    name = "review"


def activate(api):
    api.register_tool(ReviewTool())
    api.register_command(
        "review.run",
        lambda args, context: f"review {' '.join(args)}",
    )
    api.append_system_prompt("Keep repository reviews concise.")
```

`ExtensionHost` stages every contribution, validates capability and name conflicts, and only swaps the live runtime after activation succeeds. Project extensions are trusted in-process Python code; trust approval prevents accidental import but is not code isolation.

## Images and attachments

```python
from pi_agent.coding_agent import ImageProcessor

attachment = ImageProcessor().process_path("screenshot.png")
```

The processor checks byte size, MIME signature, dimensions, and decoded pixel count before constructing model content. Pillow is imported lazily only when resizing is necessary:

```bash
uv sync --extra image
```

## Telemetry SDK

```python
from pi_agent.telemetry import (
    JsonlTelemetryExporter,
    MeterProvider,
    SimpleSpanProcessor,
    TracerProvider,
    instrument_agent_session,
)

exporter = JsonlTelemetryExporter(".pi/telemetry.jsonl")
traces = TracerProvider((SimpleSpanProcessor(exporter),))
metrics = MeterProvider((exporter,))
instrumentation = instrument_agent_session(session, traces, metrics)
```

Exporter failures are retained as diagnostics and do not change Agent or Tool results.

## TypeScript-vs-Python Agent benchmark

The comparison harness runs the pinned official TypeScript Coding Agent and this Python implementation with the same model, prompt, Tool budget, context window, output limit, isolated HOME, and isolated workspace:

```bash
export DASHSCOPE_API_KEY="..."
make benchmark-agents
```

It records raw JSONL, per-event timings, Tool paths and batches, Token usage, changed files, validators, and a Markdown report. Live model runs are observational and should use multiple repetitions; deterministic Agent Core parity is checked separately. See [benchmarks/agent_compare/README.md](benchmarks/agent_compare/README.md).

## Repository checks

```bash
python3.11 scripts/check_phase_0_3.py
uv run --python 3.11 python scripts/check_phase_8_12.py
uv run --python 3.11 python scripts/check_phase_13_16.py
uv run --python 3.11 python scripts/check_phase_17_20.py
uv run --python 3.11 python scripts/check_phase_21_24.py
uv run --python 3.11 python scripts/check_phase_25_31.py
uv run --python 3.11 pytest -q
uv run --python 3.11 ruff check .
uv run --python 3.11 ruff format --check .
uv run --python 3.11 mypy --strict src
uv build
uv run --python 3.11 python scripts/check_phase_31_release.py
```

No local Docker, Lima, Colima, or `limactl` is required or used by the verification suite.
