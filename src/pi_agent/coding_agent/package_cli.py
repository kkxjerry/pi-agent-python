from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .packages import PackageManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-pkg", description="Manage local pi packages")
    parser.add_argument("--root", default="~/.pi/agent", help="pi user resource root")
    parser.add_argument("--json", action="store_true", dest="json_output")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="install a local package directory")
    install.add_argument("source")
    update = commands.add_parser("update", help="update an installed local package")
    update.add_argument("name")
    update.add_argument("source", nargs="?")
    remove = commands.add_parser("remove", help="remove an installed package")
    remove.add_argument("name")
    commands.add_parser("list", help="list installed packages")
    verify = commands.add_parser("verify", help="verify one or all installed packages")
    verify.add_argument("name", nargs="?")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(_main(argv))
    except KeyboardInterrupt:
        return 130


async def _main(argv: Sequence[str] | None) -> int:
    args = build_parser().parse_args(argv)
    manager = PackageManager(Path(args.root).expanduser())
    try:
        result = await _dispatch(manager, args)
    except Exception as exc:
        if args.json_output:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}")
        return 1
    if args.json_output:
        print(json.dumps({"success": True, "result": result}, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(args.command, result)
    return 0


async def _dispatch(manager: PackageManager, args: argparse.Namespace) -> Any:
    if args.command == "install":
        receipt = await manager.install(args.source)
        return receipt.entry.to_dict()
    if args.command == "update":
        receipt = await manager.update(args.name, args.source)
        return receipt.entry.to_dict()
    if args.command == "remove":
        return {"name": args.name, "removed": await manager.remove(args.name)}
    if args.command == "list":
        return [entry.to_dict() for entry in await manager.list()]
    if args.command == "verify":
        values = (
            (await manager.verify(args.name),)
            if args.name is not None
            else await manager.verify_all()
        )
        return [
            {
                "name": item.name,
                "valid": item.valid,
                "expectedIntegrity": item.expected_integrity,
                "actualIntegrity": item.actual_integrity,
                "error": item.error,
            }
            for item in values
        ]
    raise ValueError(f"unknown command: {args.command}")


def _print_human(command: str, result: Any) -> None:
    if command in {"install", "update"}:
        print(f"{result['name']} {result['version']}")
    elif command == "remove":
        print("removed" if result["removed"] else "not installed")
    elif command == "list":
        for item in result:
            print(f"{item['name']} {item['version']}")
    elif command == "verify":
        for item in result:
            print(f"{item['name']}: {'ok' if item['valid'] else 'invalid'}")


if __name__ == "__main__":
    raise SystemExit(main())
