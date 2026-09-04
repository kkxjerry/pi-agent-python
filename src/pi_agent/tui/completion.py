from __future__ import annotations

import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pi_agent.ai import Model


@dataclass(slots=True, frozen=True)
class CompletionItem:
    value: str
    display: str | None = None
    description: str = ""
    score: int = 0

    @property
    def label(self) -> str:
        return self.display or self.value


Completion = CompletionItem


@dataclass(slots=True, frozen=True)
class CompletionRequest:
    text: str
    cursor: int
    cwd: Path

    @property
    def prefix(self) -> str:
        return self.text[: self.cursor]


class Completer(Protocol):
    def complete(self, request: CompletionRequest) -> Sequence[CompletionItem]: ...


class CompositeCompleter:
    def __init__(self, completers: Iterable[Completer] = ()) -> None:
        self.completers = list(completers)

    def add(self, completer: Completer) -> None:
        self.completers.append(completer)

    def complete(self, request: CompletionRequest) -> tuple[CompletionItem, ...]:
        selected: dict[str, CompletionItem] = {}
        for completer in self.completers:
            for item in completer.complete(request):
                current = selected.get(item.value)
                if current is None or item.score > current.score:
                    selected[item.value] = item
        return tuple(
            sorted(
                selected.values(),
                key=lambda item: (-item.score, item.label.casefold(), item.value),
            )
        )


class CommandCompleter:
    def __init__(self, commands: Iterable[tuple[str, str]]) -> None:
        self.commands = tuple(commands)

    def complete(self, request: CompletionRequest) -> Sequence[CompletionItem]:
        prefix = request.prefix.strip()
        if not prefix.startswith("/") or " " in prefix:
            return ()
        return tuple(
            CompletionItem(
                f"/{name}",
                description=description,
                score=_prefix_score(prefix[1:], name),
            )
            for name, description in self.commands
            if _matches(prefix[1:], name)
        )


class ModelCompleter:
    def __init__(self, models: Iterable[Model]) -> None:
        self.models = tuple(models)

    def complete(self, request: CompletionRequest) -> Sequence[CompletionItem]:
        try:
            parts = shlex.split(request.prefix)
        except ValueError:
            return ()
        if not parts or parts[0] != "/model":
            return ()
        token = parts[-1] if len(parts) > 1 else ""
        return tuple(
            CompletionItem(
                f"{model.provider}/{model.id}",
                display=f"{model.provider}/{model.id}",
                description=(
                    f"{model.context_window} context" + (" · reasoning" if model.reasoning else "")
                ),
                score=_prefix_score(token, f"{model.provider}/{model.id}"),
            )
            for model in self.models
            if _matches(token, f"{model.provider}/{model.id}")
        )


class FileCompleter:
    def __init__(
        self,
        *,
        max_results: int = 100,
        include_hidden: bool = False,
    ) -> None:
        self.max_results = max_results
        self.include_hidden = include_hidden

    def complete(self, request: CompletionRequest) -> Sequence[CompletionItem]:
        token = _active_token(request.prefix)
        if token is None or not token.startswith("@"):
            return ()
        raw = token[1:]
        expanded = Path(raw).expanduser()
        if expanded.is_absolute():
            base = expanded.parent
            fragment = expanded.name
            display_root = str(expanded.parent)
        else:
            parent = expanded.parent
            base = (request.cwd / parent).resolve()
            fragment = expanded.name
            display_root = "" if str(parent) == "." else parent.as_posix()
        try:
            base.relative_to(request.cwd.resolve())
        except ValueError:
            return ()
        try:
            entries = sorted(
                base.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
            )
        except OSError:
            return ()
        results: list[CompletionItem] = []
        for path in entries:
            if not self.include_hidden and path.name.startswith("."):
                continue
            if not _matches(fragment, path.name):
                continue
            relative = f"{display_root}/{path.name}" if display_root else path.name
            relative = relative.lstrip("/")
            suffix = "/" if path.is_dir() else ""
            results.append(
                CompletionItem(
                    f"@{relative}{suffix}",
                    description="directory" if path.is_dir() else _size(path),
                    score=_prefix_score(fragment, path.name) + (10 if path.is_dir() else 0),
                )
            )
            if len(results) >= self.max_results:
                break
        return results


def apply_completion(text: str, cursor: int, item: CompletionItem) -> tuple[str, int]:
    prefix = text[:cursor]
    token = _active_token(prefix)
    if token is None:
        replacement_start = cursor
    else:
        replacement_start = cursor - len(token)
        if prefix.strip().startswith("/model") and not token.startswith("/"):
            replacement_start = cursor - len(token)
    updated = text[:replacement_start] + item.value + text[cursor:]
    return updated, replacement_start + len(item.value)


def _active_token(value: str) -> str | None:
    if not value:
        return ""
    index = len(value) - 1
    quote: str | None = None
    while index >= 0:
        character = value[index]
        if character in {'"', "'"}:
            quote = None if quote == character else character
        if quote is None and character.isspace():
            break
        index -= 1
    return value[index + 1 :]


def _matches(query: str, value: str) -> bool:
    if not query:
        return True
    folded_query = query.casefold()
    folded_value = value.casefold()
    if folded_value.startswith(folded_query):
        return True
    iterator = iter(folded_value)
    return all(any(character == candidate for candidate in iterator) for character in folded_query)


def _prefix_score(query: str, value: str) -> int:
    if not query:
        return 0
    folded_query = query.casefold()
    folded_value = value.casefold()
    if folded_value == folded_query:
        return 100
    if folded_value.startswith(folded_query):
        return 80 - max(0, len(value) - len(query))
    return 30 - max(0, len(value) - len(query))


def _size(path: Path) -> str:
    try:
        size = float(path.stat().st_size)
    except OSError:
        return "file"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}"
        size /= 1024
    return "file"
