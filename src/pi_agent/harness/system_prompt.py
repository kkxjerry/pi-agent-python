from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from xml.sax.saxutils import escape

from .resource_types import LoadedResources, Skill


def format_skills_for_system_prompt(skills: Iterable[Skill]) -> str:
    visible = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible:
        return ""
    lines = ["<available_skills>"]
    for skill in visible:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{escape(skill.name)}</name>",
                f"    <description>{escape(skill.description)}</description>",
                f"    <location>{escape(str(skill.file_path))}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def build_system_prompt(
    base_prompt: str = "",
    *,
    cwd: str | Path | None = None,
    resources: LoadedResources | None = None,
    context_files: Iterable[tuple[Path, str]] | None = None,
    skills: Iterable[Skill] | None = None,
    append_prompts: Iterable[str] | None = None,
) -> str:
    """Compose the model-visible prompt without inlining skill bodies."""

    loaded = resources or LoadedResources()
    base = loaded.system_prompt if loaded.system_prompt is not None else base_prompt
    sections: list[str] = [base.strip()] if base.strip() else []
    if cwd is not None:
        sections.append(f"Working directory: {Path(cwd).expanduser().resolve()}")
    contexts = list(context_files if context_files is not None else loaded.context_files)
    if contexts:
        rendered = ["<project_context>"]
        for path, content in contexts:
            rendered.append(f'<file path="{escape(str(path))}">')
            rendered.append(content.strip())
            rendered.append("</file>")
        rendered.append("</project_context>")
        sections.append("\n".join(rendered))
    skill_block = format_skills_for_system_prompt(
        skills if skills is not None else loaded.model_skills()
    )
    if skill_block:
        sections.append(skill_block)
    additions = list(append_prompts if append_prompts is not None else loaded.append_system_prompts)
    sections.extend(prompt.strip() for prompt in additions if prompt.strip())
    return "\n\n".join(section for section in sections if section)
