from __future__ import annotations

from pathlib import Path

from pi_agent.cli import build_parser


def test_parser_exposes_interactive_telemetry_approval_and_extensions(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "--mode",
            "interactive",
            "--cwd",
            str(tmp_path),
            "--extension",
            str(tmp_path / "extension.py"),
            "--package-root",
            str(tmp_path / "packages"),
            "--approval",
            "prompt",
            "--approval-audit",
            str(tmp_path / "approval.jsonl"),
            "--telemetry-jsonl",
            str(tmp_path / "telemetry.jsonl"),
            "--telemetry-payloads",
        ]
    )
    assert args.mode == "interactive"
    assert args.approval == "prompt"
    assert args.extension == [tmp_path / "extension.py"]
    assert args.telemetry_payloads is True


def test_print_shorthand_and_rpc_mode_remain_compatible() -> None:
    printed = build_parser().parse_args(["-p", "hello"])
    rpc = build_parser().parse_args(["--mode", "rpc"])
    assert printed.prompt == "hello"
    assert rpc.mode == "rpc"
