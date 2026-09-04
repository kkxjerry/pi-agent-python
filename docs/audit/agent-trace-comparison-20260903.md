# TypeScript pi vs Python pi trace comparison — 2026-09-03

## Scope

This is an exploratory harness comparison, not a general model-quality leaderboard.

The compared systems were:

- pinned official `@earendil-works/pi-coding-agent@0.84.4` from `earendil-works/pi@v0.84.4` (`b79e4cc`);
- the current local Python implementation invoked through `python -m pi_agent`.

Both live systems used:

- DashScope `qwen-plus` through OpenAI-compatible Chat Completions;
- context window `131072` and maximum output `8192`;
- Tool set `read`, `bash`, `edit`, `write`;
- the same prompt and source fixture;
- a fresh temporary working directory and temporary `HOME` outside the repository tree;
- no persisted session, project context files, skills, themes, extensions, update checks, or install telemetry.

Two independent batches of three executions were collected for each live task. The second batch added a post-run hidden regression validator; it did not change the prompt or Agent runtime. The API key was read from `DASHSCOPE_API_KEY`; the key is not present in benchmark configuration, traces, or this report.

Raw result directories:

```text
benchmarks/agent_compare/results/qwen-plus-equal-config-3x-20260903/
benchmarks/agent_compare/results/qwen-plus-hidden-regression-3x-20260903/
```

## Deterministic Agent Core result

Before interpreting live-model choices, the benchmark freshly executed the 20 pinned TypeScript Agent Core scenarios, compared them with the checked-in upstream captures, and ran the Python parity suite.

```text
Fresh TypeScript capture:      exit 0
Golden comparison:             20/20 matched
Python parity suite:           22 passed
Combined deterministic result: matched
```

This supports a narrow conclusion: the Python low-level loop agrees with the pinned upstream on the covered event-ordering, ToolCall/ToolResult, hook, queue, abort, error, progress, truncation, and parallel-result-order scenarios. It does not imply full product parity.

## Live task results

The medians below combine six live executions per system and task.

### Read-only extraction

Task: inspect a tiny repository, read constants under `src/`, do not mutate files, and return one exact output line.

| Metric, median of 6 | Official TypeScript | Python |
|---|---:|---:|
| Semantic value correctness | 6/6 | 6/6 |
| Exact one-line instruction adherence | 0/6 | 5/6 |
| Wall time | 6.39 s | 3.79 s |
| First assistant text delta | 2.21 s | 3.33 s |
| First Tool start | 3.26 s | 1.71 s |
| Last Tool end | 4.62 s | 2.92 s |
| Model calls | 3 | 3 |
| Tool calls | 2 | 2 |
| Input Tokens | 2,914 | 1,054 |
| Output Tokens | 179 | 61 |
| JSON trace bytes | 25,860 | 29,980 |

Both products found the right values in every run. The official agent always added prose or line breaks. The Python agent returned the exact requested single line in five of six runs.

The usual Tool path was `bash → read`. A first-text metric is misleading here: the official harness emitted explanatory text before Tool use, while the Python harness more often reached the first Tool earlier but emitted no text until later.

### `slugify.py` repair

Task: fix a whitespace/lowercasing defect, do not change the test file, run `python -m unittest -q`, and summarize.

| Metric, median of 6 | Official TypeScript | Python |
|---|---:|---:|
| Visible tests and protected-file validator | 6/6 | 6/6 |
| Hidden behavior-preservation validator | 4/6 | 2/6 |
| Changed files | `slugify.py` only | `slugify.py` only |
| Wall time | 14.02 s | 16.69 s |
| First assistant text delta | 2.01 s | 1.25 s |
| First Tool start | 2.92 s | 2.26 s |
| Last Tool end | 11.36 s | 13.96 s |
| Model calls | 5 | 5 |
| Tool calls | 5 | 4.5 |
| Input Tokens | 3,735 | 2,326 |
| Output Tokens | 610 | 689 |
| JSON trace bytes | 85,095 | 263,115 |

The hidden validator checks behavior not required by the visible tests but implied by “smallest reasonable change”: existing separators and punctuation should not be deleted or normalized merely to satisfy the current examples.

```text
slugify("C++ Guide") == "c++-guide"
slugify("A--B") == "a--b"
```

Both systems sometimes over-generalized. The official product preserved these behaviors in four of six runs; the Python product did so in two of six. This is a small sample, but it demonstrates why “visible tests passed” is not enough for Agent evaluation.

## Stable planning difference

Across the six repair runs:

```text
Official TypeScript batched the two independent reads in one assistant turn: 6/6
Python batched the two reads in one assistant turn:                         1/6
```

Typical traces were:

```text
Official TypeScript
turn 1: read + read         # one model turn, concurrent Tool batch
turn 2: optional bash       # establish failing baseline in 4/6 runs
turn 3: edit
turn 4: bash                # final verification

Python
turn 1: read
turn 2: read                # usually another model round trip
turn 3: optional bash       # establish failing baseline in 3/6 runs
turn 4: edit
turn 5: bash                # final verification
```

The Python Agent Core already supports parallel Tool execution. The gap is therefore not execution capability; it is the model-facing prompt and Tool-contract surface that induces the plan.

## Harness input differences

With all context files, skills, themes, and extensions disabled, direct session inspection showed:

| Prompt/Tool surface | Official TypeScript | Python |
|---|---:|---:|
| System Prompt characters | 2,825 | 23 |
| System Prompt lines | 31 | 1 |
| System Prompt opening | expert coding assistant in pi | `You are a coding agent.` |
| Active Tool order | read, bash, edit, write | read, write, edit, bash |

Tool descriptions and schemas are also substantially richer upstream:

| Tool | TS description chars | Python description chars | TS schema chars | Python schema chars | TS separate guideline chars |
|---|---:|---:|---:|---:|---:|
| read | 303 | 79 | 323 | 220 | 52 |
| bash | 248 | 57 | 232 | 183 | 85 |
| edit | 326 | 119 | 808 | 410 | 509 |
| write | 127 | 47 | 240 | 173 | 54 |

The upstream System Prompt tells the model which file operation belongs to `bash`, which inspection belongs to `read`, how exact edit matching works, how to combine disjoint edits, and when to use `write`. The Python prompt currently supplies almost none of this strategy.

This is the most important product-level difference found by the benchmark. The low-level loop has strong covered parity while the model-facing control surface does not.

## Trace protocol differences

The Python JSON trace is not merely a differently named version of the upstream trace.

### Python emits persistence events in ephemeral runs

Typical median `entry_appended` events were:

```text
read-only:       upstream 0, Python 6
slugify repair:  upstream 0, Python about 10
```

The Python session is in-memory, but JSON mode still exposes persistence-tree events. The upstream JSON mode does not expose equivalent events for these runs.

### Python repeats the full partial assistant message on every delta

The upstream `message_update` primarily carries the assistant delta and usage. The Python event also carries the complete partial assistant message accumulated so far. As text grows, earlier text is serialized repeatedly.

Across all six repair runs:

```text
Official message_update bytes:   231,993 across 806 events, 288 bytes/event average
Python message_update bytes:   1,215,601 across 938 events, 1,296 bytes/event average
```

At the per-run median, the Python repair trace was 3.09× the upstream trace even though its provider input Token count was 37.7% lower. This is transport amplification rather than model-context cost.

### ToolResult payloads differ

The Python trace includes richer Tool details such as normalized paths, line ranges, exit code, stdout/stderr, duration, and truncation metadata. Upstream results are more compact. Rich details improve debugging, but carrying them in repeated public events increases transport, storage, and downstream parsing cost.

## Interpretation

### What already matches well

- all 20 freshly executed deterministic upstream Agent Core captures;
- successful ToolCall parsing and execution against the same real model;
- covered source-order ToolResult and parallel-completion behavior;
- visible artifact correctness on all 12 repair executions;
- protection of the test file;
- recovery through model and Tool turns without a separate planner loop.

### Where the official harness is stronger

- mature coding System Prompt and richer Tool contracts;
- reliable batching of independent reads, 6/6 versus 1/6;
- more conservative hidden behavior preservation in this sample, 4/6 versus 2/6;
- compact delta-oriented JSON events;
- less coupling between public JSON events and internal persistence entries.

### Where the Python harness is stronger in this sample

- exact output-format adherence, 5/6 versus 0/6;
- lower provider input Token use on both task medians;
- lower read-only wall latency;
- earlier first assistant delta on the repair task;
- fewer median Tool calls on the repair task;
- richer ToolResult metadata for local debugging.

### Current Python risks

1. **Under-specified model control surface.** The 23-character System Prompt makes behavior depend heavily on model priors and user wording. It does not reliably teach batching, Tool selection, edit mechanics, or verification policy.
2. **Serialized independent reads.** The runtime supports parallel execution, but the measured model plan usually requested one read per turn.
3. **Semantic overreach.** The Python Agent more often added unrequested punctuation/separator normalization that visible tests did not reject.
4. **Trace amplification.** Full partial-message snapshots on every delta make trace size grow much faster than the logical event stream.
5. **Protocol/internal-state coupling.** `entry_appended` leaks persistence mechanics into public JSON mode even for ephemeral sessions.
6. **Context-discovery sensitivity.** An initial experiment placed task workspaces below the repository tree; the Python ResourceLoader found an ancestor `AGENTS.md`, which misdirected the model toward repository-relative paths. The final runner now uses a temporary workspace and temporary `HOME` outside the repository tree. This demonstrates why benchmark environments must explicitly control context discovery.

## Recommended next changes

Priority order derived from the traces:

1. Introduce a source-mapped Python coding System Prompt and Tool guidelines, then rerun exactly the same benchmark. Measure instructions independently rather than copying the entire upstream prompt at once.
2. Add an explicit guideline for issuing independent read-only Tool calls in one assistant message. Agent Core already supports the required semantics.
3. Add a minimal-change/regression-preservation rule and hidden evaluators for common code-edit tasks.
4. Change JSON `message_update` to a delta-first public payload. Make full partial snapshots optional or reconstruct them in consumers.
5. Hide `entry_appended` from the default public JSON stream, or put persistence details behind a debug option.
6. Expand the task set: search/grep, multi-file edit, protected-file violation, failing command recovery, long Tool output, compaction, steering/follow-up, and resume.

## Reproduction

```bash
cd tools/upstream-fixtures
npm install
cd ../..

export DASHSCOPE_API_KEY=...
uv run --python 3.11 python benchmarks/agent_compare/run.py \
  --core-parity \
  --repetitions 3
```

The runner stores raw JSONL, per-event timing, per-run metrics, a machine-readable summary, a generated report, and optional normalized Turn-by-Turn trace views under `benchmarks/agent_compare/results/`.
