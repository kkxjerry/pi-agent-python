#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.6.0.dev0"


def run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout.strip()


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_script(directory: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return directory / ("Scripts" if os.name == "nt" else "bin") / f"{name}{suffix}"


def verify_install(
    artifact: Path,
    directory: Path,
    environment: dict[str, str],
) -> None:
    run(
        [sys.executable, "-m", "venv", str(directory)],
        cwd=directory.parent,
        environment=environment,
    )
    python = venv_python(directory)
    run(
        ["uv", "pip", "install", "--python", str(python), str(artifact)],
        cwd=directory.parent,
        environment=environment,
    )
    version = run(
        [str(python), "-m", "pi_agent", "--version"],
        cwd=directory.parent,
        environment=environment,
    )
    if version != f"pi-py {EXPECTED_VERSION}":
        raise RuntimeError(f"unexpected installed version: {version!r}")
    run(
        [str(python), "-m", "pi_agent", "upstream", "--json"],
        cwd=directory.parent,
        environment=environment,
    )
    run(
        [str(python), "-m", "pi_agent", "parity", "--json"],
        cwd=directory.parent,
        environment=environment,
    )
    import_smoke = (
        "from pi_agent.coding_agent import "
        "ApprovalGate, ContainerSandboxSpec, ImageProcessor; "
        "from pi_agent.telemetry import MeterProvider, TracerProvider; "
        "from pi_agent.tui import InteractiveApp, Surface, TextEditor; "
        "print('imports-ok')"
    )
    run(
        [str(python), "-c", import_smoke],
        cwd=directory.parent,
        environment=environment,
    )
    run(
        [str(venv_script(directory, "pi-py")), "--version"],
        cwd=directory.parent,
        environment=environment,
    )
    run(
        [str(venv_script(directory, "pi-pkg")), "--help"],
        cwd=directory.parent,
        environment=environment,
    )


def main() -> int:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="pi-agent-release-") as temporary:
        root = Path(temporary)
        output = root / "dist"
        run(
            ["uv", "build", "--out-dir", str(output)],
            cwd=ROOT,
            environment=environment,
        )
        wheels = sorted(output.glob("*.whl"))
        sdists = sorted(output.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise RuntimeError(
                f"expected one wheel and one sdist, got {len(wheels)} wheel(s) "
                f"and {len(sdists)} sdist(s)"
            )
        verify_install(wheels[0], root / "wheel-env", environment)
        verify_install(sdists[0], root / "sdist-env", environment)
    print(
        "phase 31 release gate OK: "
        f"version={EXPECTED_VERSION}, wheel/sdist clean installs and CLI/import smokes passed"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(ROOT / "build", ignore_errors=True)
