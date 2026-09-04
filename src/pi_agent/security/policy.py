from __future__ import annotations

import fnmatch
import json
import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .types import PolicyDecision, PolicyRule, RiskLevel, ToolRequest


class ToolPolicy:
    """Ordered policy rules with an explicit exact-vs-glob distinction."""

    def __init__(
        self,
        rules: Iterable[PolicyRule] = (),
        *,
        default_effect: str = "ask",
    ) -> None:
        if default_effect not in {"allow", "deny", "ask"}:
            raise ValueError("default_effect must be allow, deny, or ask")
        self.rules = list(rules)
        self.default_effect = default_effect

    def evaluate(self, request: ToolRequest) -> PolicyDecision:
        risk = classify_request(request)
        for rule in self.rules:
            if _matches_rule(rule, request):
                return PolicyDecision(
                    rule.effect,
                    rule.reason or f"matched policy rule {rule.id}",
                    rule.id,
                    rule.risk or risk,
                )
        return PolicyDecision(
            self.default_effect,  # type: ignore[arg-type]
            f"no policy rule matched {request.tool_name}",
            risk=risk,
        )

    def add_exact(
        self,
        rule_id: str,
        effect: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        reason: str = "",
    ) -> None:
        if effect not in {"allow", "deny", "ask"}:
            raise ValueError("effect must be allow, deny, or ask")
        self.rules.append(
            PolicyRule(
                rule_id,
                effect,  # type: ignore[arg-type]
                tool_name,
                "exact",
                dict(arguments or {}),
                reason,
            )
        )

    def add_pattern(
        self,
        rule_id: str,
        effect: str,
        tool_pattern: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        reason: str = "",
    ) -> None:
        if effect not in {"allow", "deny", "ask"}:
            raise ValueError("effect must be allow, deny, or ask")
        self.rules.append(
            PolicyRule(
                rule_id,
                effect,  # type: ignore[arg-type]
                tool_pattern,
                "glob",
                dict(arguments or {}),
                reason,
            )
        )


def classify_request(request: ToolRequest) -> RiskLevel:
    name = request.tool_name.casefold()
    if name in {"read", "grep", "find", "ls"}:
        return "low"
    if name in {"write", "edit", "patch", "apply_patch"}:
        path = _argument_string(request.arguments, ("path", "file", "filePath"))
        if path and _is_sensitive_path(path):
            return "critical"
        return "high"
    if name in {"bash", "shell", "exec", "command"}:
        command = _argument_string(request.arguments, ("command", "cmd")) or ""
        return classify_command(command)
    if name in {"delete", "remove", "unlink"}:
        return "critical"
    return request.risk


def classify_command(command: str) -> RiskLevel:
    lowered = command.casefold()
    critical = (
        r"(^|[;&|]\s*)rm\s+-[^\n]*r[^\n]*\s+/(?:\s|$)",
        r"\bmkfs(?:\.|\s)",
        r"\bdd\s+[^\n]*\bof=/dev/",
        r"\bshutdown\b|\breboot\b|\bhalt\b",
        r"\bchmod\s+-R\s+777\s+/",
        r"\bcurl\b[^\n|]*\|\s*(?:sh|bash)\b",
        r"\bwget\b[^\n|]*\|\s*(?:sh|bash)\b",
    )
    if any(re.search(pattern, lowered) for pattern in critical):
        return "critical"
    high_tokens = {
        "rm",
        "mv",
        "chmod",
        "chown",
        "sudo",
        "git",
        "pip",
        "npm",
        "uv",
        "kill",
        "pkill",
    }
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return "high"
    commands = {
        token
        for index, token in enumerate(tokens)
        if index == 0 or tokens[index - 1] in {";", "&&", "||", "|"}
    }
    if commands & high_tokens:
        return "high"
    if any(operator in command for operator in (">", "2>", ">>", "|")):
        return "high"
    if not tokens:
        return "low"
    return "medium"


def canonical_arguments(arguments: Mapping[str, Any]) -> str:
    return json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _matches_rule(rule: PolicyRule, request: ToolRequest) -> bool:
    if rule.matcher == "exact":
        if request.tool_name != rule.tool:
            return False
    elif not fnmatch.fnmatchcase(request.tool_name, rule.tool):
        return False
    for key, expected in rule.arguments.items():
        actual = _nested_get(request.arguments, key)
        if rule.matcher == "exact":
            if actual != expected:
                return False
        elif not fnmatch.fnmatchcase(_stringify(actual), _stringify(expected)):
            return False
    return True


def _nested_get(value: Mapping[str, Any], key: str) -> Any:
    current: Any = value
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _argument_string(arguments: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str):
            return value
    return None


def _is_sensitive_path(value: str) -> bool:
    path = Path(value).expanduser()
    parts = {part.casefold() for part in path.parts}
    if path.is_absolute() and len(path.parts) <= 2:
        return True
    return bool(parts & {".ssh", ".gnupg", "etc", "system32", "private", "secrets"})
