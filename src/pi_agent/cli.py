from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, Sequence

from ._upstream import UPSTREAM
from ._version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-py", description="Python implementation of pi")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("-p", "--print", dest="prompt")
    parser.add_argument("--mode", choices=("print", "json", "rpc"), default="print")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--api")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument(
        "--thinking", choices=("off", "minimal", "low", "medium", "high", "xhigh", "max")
    )
    parser.add_argument("--session", type=Path)
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--repair-session-tail", action="store_true")
    parser.add_argument("message", nargs="*")

    subparsers = parser.add_subparsers(dest="command")
    upstream = subparsers.add_parser("upstream", help="show the pinned upstream baseline")
    upstream.add_argument("--json", action="store_true")
    parity = subparsers.add_parser("parity", help="show bundled parity scenario counts")
    parity.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"pi-py {__version__}")
        return 0
    if args.command == "upstream":
        data = UPSTREAM.to_dict()
        if args.json:
            print(json.dumps(data, indent=2, sort_keys=True))
        else:
            for key, value in data.items():
                print(f"{key}: {value}")
        return 0
    if args.command == "parity":
        manifest = _load_manifest()
        scenarios = manifest.get("scenarios", [])
        if not isinstance(scenarios, list):
            raise ValueError("Bundled scenarios must be a list")
        counts: dict[str, int] = {}
        for scenario in scenarios:
            if isinstance(scenario, dict):
                status = str(scenario.get("status", "unknown"))
                counts[status] = counts.get(status, 0) + 1
        result = {"total": len(scenarios), "byStatus": dict(sorted(counts.items()))}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"registered scenarios: {result['total']}")
            for status, count in result["byStatus"].items():
                print(f"{status}: {count}")
        return 0
    return asyncio.run(_run_agent_mode(args))


async def _run_agent_mode(args: argparse.Namespace) -> int:
    from .coding_agent import (
        AgentSessionOptions,
        create_agent_session,
        run_json_mode,
        run_print_mode,
        run_rpc_mode,
    )

    cli_settings: dict[str, Any] = {}
    _set_nested(cli_settings, "model.id", args.model)
    _set_nested(cli_settings, "model.provider", args.provider)
    _set_nested(cli_settings, "model.api", args.api)
    _set_nested(cli_settings, "model.base_url", args.base_url)
    _set_nested(cli_settings, "thinking.level", args.thinking)

    from .coding_agent.configuration import load_settings, model_from_settings

    settings = load_settings(cwd=args.cwd, cli=cli_settings)
    model = model_from_settings(settings)
    api_key = args.api_key or _api_key_from_environment(model.provider)
    session = await create_agent_session(
        AgentSessionOptions(
            cwd=args.cwd,
            model=model,
            settings=settings,
            api_key=api_key,
            session_path=args.session,
            no_session=args.no_session,
            repair_session_tail=args.repair_session_tail,
        )
    )
    try:
        if args.mode == "rpc":
            await run_rpc_mode(session)
            return 0
        prompt = args.prompt or " ".join(args.message).strip()
        if not prompt and not sys.stdin.isatty():
            prompt = sys.stdin.read()
        if not prompt:
            raise SystemExit("A prompt is required for print/json mode")
        if args.mode == "json":
            return await run_json_mode(session, prompt)
        return await run_print_mode(session, prompt)
    finally:
        if args.mode != "rpc":
            await session.close()


def _load_manifest() -> dict[str, Any]:
    path = files("pi_agent.data").joinpath("fixture_manifest.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bundled parity manifest must be an object")
    return value


def _set_nested(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    cursor = target
    parts = key.split(".")
    for part in parts[:-1]:
        next_value = cursor.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"CLI setting path collision at {key}")
        cursor = next_value
    cursor[parts[-1]] = value


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
