from __future__ import annotations

from dataclasses import dataclass

from .resource_types import PromptTemplate, ResourceSnapshot


@dataclass(slots=True, frozen=True)
class SystemPromptSections:
    base: str
    appended: tuple[str, ...]
    context: tuple[str, ...]
    skills_catalog: str | None


class SystemPromptBuilder:
    def __init__(self, default_prompt: str = "You are a coding agent.") -> None:
        self.default_prompt = default_prompt

    def sections(self, snapshot: ResourceSnapshot) -> SystemPromptSections:
        base = snapshot.system_prompt if snapshot.system_prompt is not None else self.default_prompt
        appended = tuple(
            item.content.strip() for item in snapshot.append_system_prompts if item.content.strip()
        )
        context = tuple(
            f"## Context from {item.source.path}\n\n{item.content.strip()}"
            for item in snapshot.context_files
            if item.content.strip()
        )
        skills_catalog: str | None = None
        if snapshot.skills:
            lines = [
                "## Available skills",
                "Load a skill's SKILL.md only when its description matches the task.",
            ]
            for skill in snapshot.skills:
                description = skill.description or "No description provided."
                lines.append(f"- {skill.name}: {description} ({skill.source.path})")
            skills_catalog = "\n".join(lines)
        return SystemPromptSections(base.strip(), appended, context, skills_catalog)

    def build(self, snapshot: ResourceSnapshot) -> str:
        sections = self.sections(snapshot)
        blocks = [sections.base, *sections.appended, *sections.context]
        if sections.skills_catalog:
            blocks.append(sections.skills_catalog)
        return "\n\n".join(block for block in blocks if block).strip()


def render_prompt_template(template: PromptTemplate, arguments: str = "") -> str:
    """Render the deliberately small prompt-template argument contract."""

    body = template.body
    if "{{args}}" in body:
        return body.replace("{{args}}", arguments)
    if arguments:
        return f"{body.rstrip()}\n\n{arguments}"
    return body
