# SWE-5 paired Harness diagnostic

This suite compares the pinned official TypeScript pi coding-agent Harness with the local Python implementation on five paired SWE-bench Verified instances.

It is deliberately a low-cost diagnostic:

```text
5 Verified instances
× 2 Harnesses
× 1 run
= 10 model-backed Agent runs
```

It is not a leaderboard estimate. The report focuses on paired correctness, budget efficiency, validation discipline, and the first Tool-level divergence.

## Fairness contract

Kept identical:

- dataset instance and Base Commit;
- issue statement;
- DashScope model and endpoint;
- thinking disabled;
- wall, model-call, Tool-call, input-token, and output-token budgets;
- fresh workspace and HOME;
- official SWE instance image used for test commands;
- official SWE-bench Grader.

Left native to each Harness because these are the variables under test:

- System Prompt and Tool descriptions;
- Tool schema and ToolResult representation;
- turn/context organization;
- error recovery and validation strategy;
- event protocol and persistence events.

Gold Patch, Test Patch, `FAIL_TO_PASS`, and `PASS_TO_PASS` are never included in Agent prompts.

## Selection

`select_verified.py` computes production-file count, changed lines, visible failing-test count, regression-test count, statement length, and whether the statement names the eventual target file. It selects one task for each diagnostic bucket:

1. localization;
2. surgical fix;
3. failure recovery;
4. multi-file contract;
5. regression boundary.

It prefers two repositories to reduce image and dependency cost. `swe5.json` contains only public selection metadata; `selection-candidates.json` is for selection audit and is not sent to an Agent.

## Server bootstrap

The intended execution environment is an x86_64 Linux server with Docker and enough image-cache space. The A40 GPU is not used for DashScope inference; it is simply the available server that hosts Docker, repository tests, and the Grader.

```bash
bash benchmarks/swe5/server_bootstrap.sh
```

The bootstrap creates an isolated SWE-bench virtual environment under:

```text
~/.cache/pi-agent-swe5/
```

and installs the existing official TypeScript benchmark package plus the local Python project.

## Select and preflight

```bash
STATE=$HOME/.cache/pi-agent-swe5

$STATE/swebench-venv/bin/python benchmarks/swe5/select_verified.py \
  --output benchmarks/swe5/swe5.json \
  --candidates-output benchmarks/swe5/selection-candidates.json

$STATE/swebench-venv/bin/python benchmarks/swe5/preflight.py \
  --manifest benchmarks/swe5/swe5.json \
  --workdir .swe5/preflight \
  --python $STATE/swebench-venv/bin/python \
  --max-workers 2
```

Preflight must establish:

```text
Gold Patch → resolved
Empty Patch → unresolved
```

for every selected instance. It does not call DashScope.

## Run the 10 Agent jobs

The server process must receive `DASHSCOPE_API_KEY` in its environment. The runner does not write the secret to the result directory.

```bash
STATE=$HOME/.cache/pi-agent-swe5

$STATE/swebench-venv/bin/python benchmarks/swe5/run_swe5.py \
  --manifest benchmarks/swe5/swe5.json \
  --output .swe5/results \
  --model qwen-plus \
  --timeout 900 \
  --max-model-calls 15 \
  --max-tool-calls 60 \
  --max-input-tokens 250000 \
  --max-output-tokens 20000
```

Run order alternates by task:

```text
Task 1: TypeScript → Python
Task 2: Python → TypeScript
Task 3: TypeScript → Python
Task 4: Python → TypeScript
Task 5: TypeScript → Python
```

Each run gets a fresh host checkout copied from the prepared SWE image. Read/Edit/Write operate on that checkout. Common test executables are shimmed into a persistent instance container mounted on the same checkout, so tests use the official image dependencies.

## Grade

```bash
STATE=$HOME/.cache/pi-agent-swe5
RESULT=.swe5/results/<run-id>

$STATE/swebench-venv/bin/python benchmarks/swe5/grade_swe5.py \
  --results "$RESULT" \
  --python $STATE/swebench-venv/bin/python \
  --workdir "$RESULT/grader" \
  --max-workers 2

$STATE/swebench-venv/bin/python benchmarks/swe5/report_swe5.py \
  --results "$RESULT"
```

## Artifacts

```text
metadata.json
swe5.json
agent-runs.json
grades.json
analysis.json
report.md
predictions.upstream-ts.jsonl
predictions.python.jsonl
runs/<instance>/<system>/
  raw.jsonl
  timed.jsonl
  normalized.jsonl
  stderr.log
  patch.diff
  metrics.json
timelines/<instance>.md
```

`raw.jsonl` remains system-native. `normalized.jsonl` keeps only a common event vocabulary for paired analysis. Do not compare raw lines directly because the two Harnesses intentionally expose different event schemas.

## Interpretation

A correctness difference of two or more resolved tasks is treated as a meaningful signal for the five selected capabilities. A one-task difference is directional only. Efficiency comparisons are most useful on tasks both systems resolve. If the result is ambiguous, re-run only the one or two tasks with the most consequential first divergence rather than paying for another full 10-run suite.
