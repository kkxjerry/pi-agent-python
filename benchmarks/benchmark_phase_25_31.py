from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass

from pi_agent.telemetry import MemoryTelemetryExporter, SimpleSpanProcessor, TracerProvider
from pi_agent.tui import AnsiRenderer, InputDecoder, Style, Surface


@dataclass(slots=True, frozen=True)
class BenchmarkResult:
    name: str
    operations: int
    seconds: float

    @property
    def operations_per_second(self) -> float:
        return self.operations / self.seconds if self.seconds else float("inf")

    def to_dict(self) -> dict[str, float | int | str]:
        value = asdict(self)
        value["operations_per_second"] = self.operations_per_second
        return value


def benchmark_surface(iterations: int) -> BenchmarkResult:
    output: list[str] = []
    renderer = AnsiRenderer(output.append)
    surface = Surface(120, 40)
    started = time.perf_counter()
    for iteration in range(iterations):
        row = iteration % surface.height
        surface.fill(surface_rect(surface, row))
        surface.draw_text(
            0,
            row,
            f"iteration={iteration} Unicode=你 emoji=👩\u200d💻",
            style=Style(bold=bool(iteration % 2)),
        )
        renderer.render(surface)
        output.clear()
    elapsed = time.perf_counter() - started
    return BenchmarkResult("surface_diff_render", iterations, elapsed)


def benchmark_input(iterations: int) -> BenchmarkResult:
    payload = b"hello\x1b[A\x1b[200~multi\nline\x1b[201~" + "你".encode()
    started = time.perf_counter()
    events = 0
    for _ in range(iterations):
        decoder = InputDecoder()
        for byte in payload:
            events += len(decoder.feed(bytes((byte,))))
        events += len(decoder.flush_escape())
    elapsed = time.perf_counter() - started
    return BenchmarkResult("input_decode_events", events, elapsed)


async def benchmark_telemetry(iterations: int) -> BenchmarkResult:
    exporter = MemoryTelemetryExporter()
    provider = TracerProvider((SimpleSpanProcessor(exporter),))
    tracer = provider.get_tracer("benchmark")
    started = time.perf_counter()
    for iteration in range(iterations):
        with tracer.span("operation", attributes={"iteration": iteration}):
            pass
    await provider.force_flush()
    elapsed = time.perf_counter() - started
    await provider.shutdown()
    return BenchmarkResult("telemetry_spans", len(exporter.spans), elapsed)


def surface_rect(surface: Surface, row: int):
    from pi_agent.tui import Rect

    return Rect(0, row, surface.width, 1)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=10_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("--iterations must be positive")
    results = [
        benchmark_surface(args.iterations),
        benchmark_input(max(1, args.iterations // 10)),
        await benchmark_telemetry(args.iterations),
    ]
    if args.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            print(
                f"{result.name}: {result.operations:,} ops in {result.seconds:.3f}s "
                f"({result.operations_per_second:,.0f} ops/s)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
