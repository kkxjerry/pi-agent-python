from __future__ import annotations

import argparse

from pi_agent import cli


class _Input:
    def __init__(self, *, tty: bool, value: str = "") -> None:
        self.tty = tty
        self.value = value

    def isatty(self) -> bool:
        return self.tty

    def read(self) -> str:
        return self.value


def test_mode_selection_preserves_headless_and_adds_interactive(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _Input(tty=True))
    assert (
        cli._selected_mode(argparse.Namespace(mode=None, prompt=None, message=[])) == "interactive"
    )
    assert cli._selected_mode(argparse.Namespace(mode=None, prompt="hello", message=[])) == "print"
    assert cli._selected_mode(argparse.Namespace(mode="json", prompt=None, message=[])) == "json"
    assert cli._selected_mode(argparse.Namespace(mode="rpc", prompt=None, message=[])) == "rpc"


def test_piped_prompt_is_read_without_changing_explicit_prompt(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _Input(tty=False, value="from stdin"))
    assert cli._prompt_from_args(argparse.Namespace(prompt=None, message=[])) == "from stdin"
    assert cli._prompt_from_args(argparse.Namespace(prompt="explicit", message=[])) == "explicit"
