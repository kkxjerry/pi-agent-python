from __future__ import annotations

import json

from pi_agent.cli import build_parser, main


def test_legacy_metadata_commands_remain_unambiguous(capsys) -> None:
    assert main(["upstream", "--json"]) == 0
    upstream = json.loads(capsys.readouterr().out)
    assert upstream["tag"] == "v0.84.4"

    assert main(["parity", "--json"]) == 0
    parity = json.loads(capsys.readouterr().out)
    assert parity["total"] == 30
    assert parity["byStatus"]["upstream-execution"] >= 20


def test_agent_mode_parser_keeps_prompt_and_session_flags_separate() -> None:
    args = build_parser().parse_args(
        ["--mode", "json", "--no-session", "-p", "hello", "--model", "fixture"]
    )
    assert args.mode == "json"
    assert args.no_session is True
    assert args.prompt == "hello"
    assert args.model == "fixture"
