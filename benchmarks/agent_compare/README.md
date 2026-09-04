# TypeScript pi vs Python pi Agent benchmark

This benchmark compares the pinned official TypeScript coding agent with this Python implementation at two levels.

## 1. Deterministic Agent Core parity

`--core-parity` performs a fresh execution of the pinned TypeScript Agent Core scenarios, compares the capture with checked-in upstream goldens, and runs the Python parity suite. This removes model nondeterminism and answers whether event ordering, ToolCall/ToolResult behavior, queue semantics, and recovery behavior match.

## 2. Live coding-agent comparison

The live benchmark runs both complete coding-agent products with:

- the same DashScope `qwen-plus` model, context window, and maximum output;
- the same isolated fixture and prompt;
- the same effective Tool set: `read`, `bash`, `edit`, and `write`;
- no session persistence, project context files, skills, themes, extensions, or update telemetry;
- separate temporary workspaces and temporary home directories outside the repository tree, so neither Agent inherits an ancestor `AGENTS.md` or user resources and one Agent cannot observe the other Agent's changes.

It records raw JSONL events, per-event wall-clock timing, Tool sequence, model-call count, provider Token usage, trace size, final answer, changed files, visible validators, and post-run hidden regression validators.

Live runs are not deterministic. Use at least three repetitions before treating latency, Tool choice, or Token differences as stable.

## Setup

```bash
cd tools/upstream-fixtures
npm install
cd ../..

export DASHSCOPE_API_KEY=...
```

The pinned upstream dependency is:

```text
@earendil-works/pi-coding-agent@0.84.4
```

The upstream model configuration references `$DASHSCOPE_API_KEY`; no key is stored in the repository or benchmark output.

## Run

Fast exploratory comparison:

```bash
uv run --python 3.11 python benchmarks/agent_compare/run.py
```

Include deterministic core parity and use three live repetitions:

```bash
uv run --python 3.11 python benchmarks/agent_compare/run.py \
  --core-parity \
  --repetitions 3
```

Run one task or one system:

```bash
uv run --python 3.11 python benchmarks/agent_compare/run.py \
  --task fix_slugify \
  --system python
```

When the key is exported only from Zsh login configuration:

```bash
zsh -lic 'cd /absolute/path/to/pi-agent-python && \
  uv run --python 3.11 python benchmarks/agent_compare/run.py --core-parity'
```

## Output

Each run creates an ignored directory under `benchmarks/agent_compare/results/` unless `--output` is supplied:

```text
metadata.json
core-parity.json                 # when --core-parity is enabled
summary.json
report.md
traces/
  upstream-ts/<task>-rN.jsonl
  upstream-ts/<task>-rN.timed.jsonl
  upstream-ts/<task>-rN.metrics.json
  python/<task>-rN.jsonl
  python/<task>-rN.timed.jsonl
  python/<task>-rN.metrics.json
workspaces/                      # retained only with --keep-workspaces
```

Render any two raw traces as an aligned Turn/Tool report:

```bash
uv run --python 3.11 python benchmarks/agent_compare/trace_diff.py \
  results/<run>/traces/upstream-ts/fix_slugify-r1.jsonl \
  results/<run>/traces/python/fix_slugify-r1.jsonl \
  --output results/<run>/trace-diff-fix_slugify-r1.md
```

## What the numbers mean

- **Model calls**: completed assistant messages, including ToolCall turns.
- **Input/Output Tokens**: sums of provider-reported usage for completed assistant turns.
- **First text ms**: process start to the first assistant `text_delta`; Tool-only turns may delay this metric.
- **Trace bytes**: raw JSONL size, useful for detecting overly verbose event protocols.
- **Validator**: artifact correctness, protected-file checks, and exact output requirements.

Do not interpret one live run as a model-quality leaderboard. The main purpose is to reveal harness differences: prompt overhead, parallel versus sequential Tool planning, redundant verification, semantic overreach caught by hidden regressions, event payload design, persistence events, recovery paths, and final artifact correctness.

The first controlled report is documented in `docs/audit/agent-trace-comparison-20260903.md`.
