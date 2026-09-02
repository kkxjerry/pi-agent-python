from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from importlib.resources import files as resource_files

from ._upstream import UPSTREAM
from ._version import __version__


def _load_manifest() -> dict[str, object]:
    resource = resource_files("pi_agent.data").joinpath("fixture_manifest.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Bundled fixture manifest must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pi-py", description="Python pi reimplementation")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    upstream = subparsers.add_parser("upstream", help="show the pinned official pi baseline")
    upstream.add_argument("--json", action="store_true")
    parity = subparsers.add_parser("parity", help="summarize parity scenarios")
    parity.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
        by_status = dict(sorted(counts.items()))
        result: dict[str, object] = {"total": len(scenarios), "byStatus": by_status}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"registered scenarios: {len(scenarios)}")
            for status, count in by_status.items():
                print(f"{status}: {count}")
        return 0
    parser.print_help()
    return 0
