from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any

from ._upstream import UPSTREAM
from ._version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-py", description="Python implementation of pi")
    parser.add_argument("--version", action="store_true")
    # `-p hello` and `-p --model ... hello` are both accepted. The latter is
    # the official CLI-style print shorthand, where the prompt remains positional.
    parser.add_argument("-p", "--print", dest="prompt", nargs="?", const="")
    parser.add_argument("--mode", choices=("print", "json", "rpc"), default="print")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--api", default="openai-completions")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--context-window", type=int, default=128_000)
    parser.add_argument("--max-tokens", type=int, default=16_384)
    parser.add_argument(
        "--thinking",
        choices=("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--session", type=Path)
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--repair-session-tail", action="store_true")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("message", nargs="*")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in {"upstream", "parity"}:
        return _run_metadata_command(raw)
    args = build_parser().parse_args(raw)
    if args.version:
        print(f"pi-py {__version__}")
        return 0
    try:
        return asyncio.run(_run_agent_mode(args))
    except KeyboardInterrupt:
        return 130


async def _run_agent_mode(args: argparse.Namespace) -> int:
    from .ai import Model, OpenAICompatibleProvider
    from .coding_agent import (
        CreateAgentSessionOptions,
        PrintModeOptions,
        SettingsResolver,
        create_agent_session,
        run_print_mode,
        run_rpc_stdio,
    )
    from .harness.session import JsonlSessionStore, SessionManager

    cwd = args.cwd.expanduser().resolve()
    provider_id = args.provider or "openai"
    model_id = args.model or "gpt-4.1-mini"
    base_url = args.base_url or "https://api.openai.com/v1"
    model = Model(
        api=args.api,
        provider=provider_id,
        id=model_id,
        name=model_id,
        base_url=base_url,
        reasoning=args.thinking not in {None, "off"},
        context_window=args.context_window,
        max_tokens=args.max_tokens,
    )
    settings = SettingsResolver().resolve(
        global_path=Path.home() / ".pi" / "agent" / "settings.json",
        project_path=cwd / ".pi" / "settings.json",
        cli={"thinking.level": args.thinking} if args.thinking is not None else None,
    )
    api_key = args.api_key or _api_key_from_environment(provider_id)
    provider = OpenAICompatibleProvider(api_key=api_key)

    manager: SessionManager | None = None
    if args.session is not None:
        session_path = args.session.expanduser().resolve()
        if session_path.exists():
            store = JsonlSessionStore(session_path)
            if args.repair_session_tail:
                store.repair_tail()
            manager = SessionManager(store)
        else:
            manager = SessionManager.create(session_path, cwd=str(cwd))

    created = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=cwd,
            model=model,
            stream_fn=provider.stream,
            settings=settings,
            api_key=api_key,
            session_manager=manager,
            no_session=args.no_session,
            include_coding_tools=not args.no_tools,
        )
    )
    session = created.session
    try:
        if args.mode == "rpc":
            await run_rpc_stdio(session)
            return 0
        prompt = args.prompt if args.prompt not in {None, ""} else " ".join(args.message).strip()
        if not prompt and not sys.stdin.isatty():
            prompt = sys.stdin.read()
        if not prompt:
            print("A prompt is required for print/json mode", file=sys.stderr)
            return 2
        return await run_print_mode(
            session,
            PrintModeOptions(
                output_mode="json" if args.mode == "json" else "text",
                initial_message=prompt,
            ),
        )
    finally:
        await session.close()


def _run_metadata_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"pi-py {argv[0]}")
    parser.add_argument("command", choices=("upstream", "parity"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "upstream":
        value: dict[str, Any] = UPSTREAM.to_dict()
    else:
        manifest = _load_manifest()
        scenarios = manifest.get("scenarios", [])
        if not isinstance(scenarios, list):
            raise ValueError("Bundled scenarios must be a list")
        counts: dict[str, int] = {}
        for scenario in scenarios:
            if isinstance(scenario, dict):
                status = str(scenario.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
        value = {"total": len(scenarios), "byStatus": dict(sorted(counts.items()))}
    if args.json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        for key, item in value.items():
            print(f"{key}: {item}")
    return 0


def _load_manifest() -> dict[str, Any]:
    resource = resource_files("pi_agent.data").joinpath("fixture_manifest.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bundled fixture manifest must be a JSON object")
    return value


def _api_key_from_environment(provider: str) -> str | None:
    candidates = [
        f"{provider.upper().replace('-', '_')}_API_KEY",
        "OPENAI_API_KEY" if provider == "openai" else "",
        "ANTHROPIC_API_KEY" if provider == "anthropic" else "",
    ]
    for name in candidates:
        if name and os.environ.get(name):
            return os.environ[name]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
