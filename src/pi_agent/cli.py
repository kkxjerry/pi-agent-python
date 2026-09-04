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
    parser.add_argument("-p", "--print", dest="prompt", nargs="?", const="")
    parser.add_argument(
        "--mode",
        choices=("interactive", "print", "json", "rpc"),
        default=None,
    )
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
    parser.add_argument("--extension", action="append", type=Path, default=[])
    parser.add_argument("--no-extensions", action="store_true")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--extension-capability", action="append", default=[])
    parser.add_argument("--allow-all-extension-capabilities", action="store_true")
    parser.add_argument(
        "--approval",
        choices=("off", "prompt", "deny", "allow"),
        default="off",
    )
    parser.add_argument("--approval-audit", type=Path)
    parser.add_argument("--telemetry-jsonl", type=Path)
    parser.add_argument("--telemetry-payloads", action="store_true")
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
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


async def _run_agent_mode(args: argparse.Namespace) -> int:
    from .ai import Model, OpenAICompatibleProvider
    from .coding_agent import (
        CodingAgentRuntime,
        CodingAgentRuntimeOptions,
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
    model_id = args.model or _default_model(provider_id)
    base_url = args.base_url or _default_base_url(provider_id)
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

    mode = _selected_mode(args)
    approval_broker = None
    if args.approval == "prompt" and mode == "interactive":
        from .tui.approval import TuiApprovalBroker

        approval_broker = TuiApprovalBroker()
    approval_gate = _approval_gate(args, cwd, approval_broker)
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
            approval_gate=approval_gate,
        )
    )
    session = created.session
    runtime: CodingAgentRuntime | None = None
    instrumentation = None
    tracer_provider = None
    meter_provider = None
    try:
        if not args.no_extensions:
            runtime = CodingAgentRuntime(
                session,
                options=CodingAgentRuntimeOptions(
                    package_root=args.package_root,
                    extension_paths=tuple(args.extension),
                    extension_capabilities=frozenset(args.extension_capability),
                    allow_all_extension_capabilities=args.allow_all_extension_capabilities,
                ),
            )
            await runtime.start()
        if args.telemetry_jsonl is not None:
            from .telemetry import (
                JsonlTelemetryExporter,
                MeterProvider,
                SimpleSpanProcessor,
                TelemetryRedactor,
                TracerProvider,
                instrument_agent_session,
            )

            redactor = TelemetryRedactor(secrets=(api_key,) if api_key else ())
            exporter = JsonlTelemetryExporter(args.telemetry_jsonl, redactor=redactor)
            tracer_provider = TracerProvider(
                (SimpleSpanProcessor(exporter),),
                redactor=redactor,
            )
            meter_provider = MeterProvider((exporter,), redactor=redactor)
            instrumentation = instrument_agent_session(
                session,
                tracer_provider,
                meter_provider,
                include_content=args.telemetry_payloads,
            )

        if mode == "rpc":
            await run_rpc_stdio(session)
            return 0
        prompt = _prompt_from_args(args)
        if mode == "interactive":
            from .tui import TuiOptions, run_interactive_mode

            return await run_interactive_mode(
                session,
                options=TuiOptions(initial_prompt=prompt or None),
            )
        if not prompt:
            print("A prompt is required for print/json mode", file=sys.stderr)
            return 2
        return await run_print_mode(
            session,
            PrintModeOptions(
                output_mode="json" if mode == "json" else "text",
                initial_message=prompt,
            ),
        )
    finally:
        if instrumentation is not None:
            await instrumentation.close()
        if tracer_provider is not None:
            await tracer_provider.shutdown()
        if meter_provider is not None:
            await meter_provider.shutdown()
        if runtime is not None:
            await runtime.close()
        await session.close()


def _selected_mode(args: argparse.Namespace) -> str:
    if args.mode is not None:
        return str(args.mode)
    if args.prompt is not None or args.message or not sys.stdin.isatty():
        return "print"
    return "interactive"


def _prompt_from_args(args: argparse.Namespace) -> str:
    prompt = args.prompt if args.prompt not in {None, ""} else " ".join(args.message).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read()
    return str(prompt or "")


def _approval_gate(args: argparse.Namespace, cwd: Path, broker: Any) -> Any:
    if args.approval == "off":
        return None
    from .coding_agent import ApprovalAuditSink, ApprovalGate, ApprovalPolicy

    policy = ApprovalPolicy(
        cwd,
        prompt_write_tools=args.approval not in {"allow"},
        prompt_shell_tools=args.approval not in {"allow"},
    )
    audit = ApprovalAuditSink(args.approval_audit) if args.approval_audit else None
    return ApprovalGate(
        policy,
        session_id="pending",
        prompt=broker if args.approval == "prompt" else None,
        audit_sink=audit,
    )


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


def _default_model(provider: str) -> str:
    if provider.casefold() == "dashscope":
        return "qwen-plus"
    return "gpt-4.1-mini"


def _default_base_url(provider: str) -> str:
    if provider.casefold() == "dashscope":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return "https://api.openai.com/v1"


def _api_key_from_environment(provider: str) -> str | None:
    normalized = provider.casefold()
    candidates = [
        "DASHSCOPE_API_KEY" if normalized == "dashscope" else "",
        f"{provider.upper().replace('-', '_')}_API_KEY",
        "OPENAI_API_KEY" if normalized == "openai" else "",
        "ANTHROPIC_API_KEY" if normalized == "anthropic" else "",
    ]
    for name in candidates:
        if name and os.environ.get(name):
            return os.environ[name]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
