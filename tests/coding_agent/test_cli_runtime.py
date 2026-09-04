from __future__ import annotations

import argparse

from pi_agent import cli


class _Input:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def read(self) -> str:
        return ""


def test_selected_mode_defaults_to_interactive_for_tty(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _Input(True))
    args = argparse.Namespace(mode=None, prompt=None, message=[])
    assert cli._selected_mode(args) == "interactive"


def test_selected_mode_uses_print_for_prompt_or_pipe(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _Input(True))
    args = argparse.Namespace(mode=None, prompt="hello", message=[])
    assert cli._selected_mode(args) == "print"

    monkeypatch.setattr(cli.sys, "stdin", _Input(False))
    args = argparse.Namespace(mode=None, prompt=None, message=[])
    assert cli._selected_mode(args) == "print"


def test_dashscope_profile_uses_dashscope_endpoint_and_environment(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "local-dashscope-secret")

    assert cli._default_model("dashscope") == "qwen-plus"
    assert cli._default_base_url("dashscope") == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert cli._api_key_from_environment("dashscope") == "local-dashscope-secret"
    assert cli._default_base_url("openai") == "https://api.openai.com/v1"


def test_main_routes_agent_mode_without_network(monkeypatch) -> None:
    observed = {}

    async def fake_run(args):
        observed["mode"] = args.mode
        observed["approval"] = args.approval
        return 7

    monkeypatch.setattr(cli, "_run_agent_mode", fake_run)
    status = cli.main(["--mode", "interactive", "--approval", "deny"])
    assert status == 7
    assert observed == {"mode": "interactive", "approval": "deny"}
