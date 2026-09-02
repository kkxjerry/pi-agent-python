from __future__ import annotations

from pathlib import Path

FORBIDDEN = ("paicli", "langchain", "langgraph", "autogen", "crewai", "openhands")


def test_runtime_does_not_import_other_agent_projects() -> None:
    violations: list[str] = []
    for path in Path("src").rglob("*.py"):
        lowered = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN:
            if term in lowered:
                violations.append(f"{path}: {term}")
    assert violations == []
