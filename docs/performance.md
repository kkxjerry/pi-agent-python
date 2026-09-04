# Performance and stability

Phase 29 focuses on bounded behavior rather than a marketing throughput target.
The benchmark is repeatable and intentionally does not call a paid model:

```bash
make benchmark
uv run python benchmarks/benchmark_phase_25_31.py --iterations 10000 --json
```

It measures three local paths:

1. repeated row-differential `Surface` rendering;
2. terminal byte decoding under one-byte fragmentation;
3. creation, processing, and in-memory export of telemetry spans.

The benchmark output includes operations, elapsed seconds, and operations per
second. Results depend on Python version, CPU, terminal width, and build mode;
the repository does not hard-code a machine-specific pass threshold.

## Stability bounds

The milestone enforces the following structural limits:

- `TextEditor.max_length` bounds interactive input memory;
- transcript rendering only materializes the active viewport after wrapping;
- terminal images enforce input bytes, output bytes, width, height, and pixel
  limits before model submission;
- tool output remains governed by the harness truncation limits;
- `BatchSpanProcessor.max_batch_size` bounds one export batch;
- telemetry exporters receive sanitized attributes and bounded strings;
- approval requests are serialized by the TUI broker;
- package changes remain transactionally validated before runtime activation.

## Long-session guidance

Session JSONL remains append-only. Context growth is controlled by the existing
compaction layer, not by deleting historical session entries. For very large
interactive sessions, model-context size, JSONL reconstruction, terminal
wrapping, and exporter output should be observed independently; no single
"memory usage" number describes all four.

Fault-injection and property-style tests cover fragmented terminal input,
invalid image data, exporter failure, approval prompt failure, Unicode surface
writes, and deterministic rendering convergence.
