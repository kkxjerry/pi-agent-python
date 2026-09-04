#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pi_agent import __version__  # noqa: E402
from pi_agent.coding_agent import (  # noqa: E402
    ApprovalGate,
    ApprovalPolicy,
    ContainerSandboxSpec,
    ImageProcessor,
)
from pi_agent.telemetry import MeterProvider, TracerProvider  # noqa: E402
from pi_agent.tui import (  # noqa: E402
    AnsiRenderer,
    InputDecoder,
    InteractiveApp,
    Surface,
    TextEditor,
)

PHASE_RANGE = "Phase 25" + chr(0x2013) + "31"

REQUIRED = [
    "src/pi_agent/tui/app.py",
    "src/pi_agent/tui/input.py",
    "src/pi_agent/tui/terminal.py",
    "src/pi_agent/tui/surface.py",
    "src/pi_agent/tui/renderer.py",
    "src/pi_agent/tui/editor.py",
    "src/pi_agent/tui/widgets.py",
    "src/pi_agent/tui/images.py",
    "src/pi_agent/telemetry/tracing.py",
    "src/pi_agent/telemetry/metrics.py",
    "src/pi_agent/telemetry/exporters.py",
    "src/pi_agent/telemetry/instrumentation.py",
    "src/pi_agent/coding_agent/approval.py",
    "src/pi_agent/coding_agent/security.py",
    "src/pi_agent/coding_agent/images.py",
    "tests/tui/test_app.py",
    "tests/telemetry/test_telemetry.py",
    "tests/coding_agent/test_approval.py",
    "tests/coding_agent/test_security.py",
    "tests/fault_injection/test_phase_25_31_faults.py",
    "tests/property/test_tui_properties.py",
    "benchmarks/benchmark_phase_25_31.py",
    "docs/tui.md",
    "docs/telemetry.md",
    "docs/security.md",
    "docs/performance.md",
    "docs/audit/phase-25-31-evidence.md",
    "examples/interactive.py",
    "examples/telemetry.py",
    "examples/approval.py",
    "examples/container_command.py",
    "scripts/check_phase_31_release.py",
]


def fail(message: str) -> None:
    raise SystemExit(f"phase 25-31 gate failed: {message}")


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        fail("missing files: " + ", ".join(missing))
    try:
        version = tuple(int(part) for part in __version__.split(".")[:2])
    except ValueError:
        fail(f"could not parse project version {__version__!r}")
    if version < (0, 6):
        fail(f"expected version 0.6 or newer, got {__version__}")

    documentation_markers = {
        "README.md": PHASE_RANGE,
        "ARCHITECTURE.md": f"{PHASE_RANGE} product edge",
        "SCOPE.md": PHASE_RANGE,
        "PARITY.md": f"{PHASE_RANGE} status",
        "CHANGELOG.md": "0.6.0.dev0",
    }
    for path, marker in documentation_markers.items():
        content = (ROOT / path).read_text(encoding="utf-8")
        if marker not in content:
            fail(f"{path} is missing marker {marker!r}")

    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if branch != "develop":
        fail(f"work must be on develop, got {branch!r}")

    public = (
        InteractiveApp,
        InputDecoder,
        Surface,
        AnsiRenderer,
        TextEditor,
        TracerProvider,
        MeterProvider,
        ApprovalGate,
        ApprovalPolicy,
        ContainerSandboxSpec,
        ImageProcessor,
    )
    if not all(public):
        fail("Phase 25-31 public imports are incomplete")

    print(
        "phase 25-31 gate OK: "
        f"version={__version__}, "
        "tui/telemetry/approval/security/images/tests/release=present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
