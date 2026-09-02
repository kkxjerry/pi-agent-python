from __future__ import annotations

import json
from pathlib import Path

from pi_agent.harness.resource_loader import (
    ResourceLoader,
    ResourceLoaderConfig,
    parse_frontmatter,
)
from pi_agent.harness.system_prompt import SystemPromptBuilder, render_prompt_template


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_resource_precedence_context_order_and_source_tracking(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = tmp_path / "repo"
    cwd = project / "packages" / "app"
    cwd.mkdir(parents=True)

    _write(user / "SYSTEM.md", "user system")
    _write(user / "APPEND_SYSTEM.md", "user append")
    _write(
        user / "skills" / "review" / "SKILL.md",
        "---\nname: review\ndescription: user review\n---\nuser body",
    )
    _write(project / "AGENTS.md", "root context")
    _write(cwd / "AGENTS.md", "leaf context")
    _write(project / ".pi" / "SYSTEM.md", "project system")
    _write(project / ".pi" / "APPEND_SYSTEM.md", "project append")
    _write(
        project / ".pi" / "skills" / "review" / "SKILL.md",
        "---\nname: review\ndescription: project review\n---\nproject body",
    )

    snapshot = ResourceLoader(ResourceLoaderConfig(cwd=cwd, user_root=user)).load()

    assert snapshot.system_prompt == "project system"
    assert snapshot.system_prompt_source is not None
    assert snapshot.system_prompt_source.kind == "project"
    assert [item.content for item in snapshot.append_system_prompts] == [
        "user append",
        "project append",
    ]
    assert [item.content for item in snapshot.context_files] == [
        "root context",
        "leaf context",
    ]
    assert snapshot.skill("review") is not None
    assert snapshot.skill("review").body == "project body"
    assert any(warning.code == "resource_shadowed" for warning in snapshot.warnings)


def test_loader_discovers_prompt_theme_extension_package_and_skill_resources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resources"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(
        root / "skills" / "debug" / "SKILL.md",
        "---\nname: debug\ndescription: Debug failures\n---\nUse evidence.",
    )
    _write(root / "skills" / "debug" / "checklist.txt", "check")
    _write(
        root / "prompts" / "review.md",
        "---\nname: review\ndescription: Review code\nargument-hint: path\n---\nReview {{args}}",
    )
    _write(root / "themes" / "dark.json", json.dumps({"name": "dark", "fg": "white"}))
    _write(root / "extensions" / "audit.py", "def activate(api):\n    pass\n")
    _write(
        root / "packages" / "sample" / "pi-package.json",
        json.dumps({"name": "sample", "version": "1.0.0"}),
    )

    snapshot = ResourceLoader(
        ResourceLoaderConfig(cwd=cwd, user_root=tmp_path / "missing", extra_roots=(root,))
    ).load()

    skill = snapshot.skill("debug")
    assert skill is not None
    assert skill.resources == (root / "skills" / "debug" / "checklist.txt",)
    prompt = snapshot.prompt_template("review")
    assert prompt is not None
    assert prompt.argument_hint == "path"
    assert render_prompt_template(prompt, "src/app.py") == "Review src/app.py"
    assert [theme.name for theme in snapshot.themes] == ["dark"]
    assert [extension.name for extension in snapshot.extensions] == ["audit"]
    assert [package.name for package in snapshot.packages] == ["sample"]


def test_loader_reports_malformed_resources_without_failing_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(root / "skills" / "broken" / "SKILL.md", "---\nname broken\n---\nbody")
    _write(root / "themes" / "broken.json", "{")
    _write(root / "skills" / "undescribed" / "SKILL.md", "body")

    loader = ResourceLoader(
        ResourceLoaderConfig(cwd=cwd, user_root=tmp_path / "missing", extra_roots=(root,))
    )
    first = loader.load()
    second = loader.reload()

    assert first.generation == 1
    assert second.generation == 2
    codes = {warning.code for warning in second.warnings}
    assert {"invalid_frontmatter", "invalid_json", "skill_description_missing"} <= codes
    assert second.skill("undescribed") is not None


def test_system_prompt_builder_keeps_full_context_but_only_catalogs_skills(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(root / "SYSTEM.md", "base")
    _write(root / "APPEND_SYSTEM.md", "append")
    _write(cwd / "AGENTS.md", "context rules")
    _write(
        root / "skills" / "secret" / "SKILL.md",
        "---\nname: secret\ndescription: Load on demand\n---\nVERY LARGE SKILL BODY",
    )
    snapshot = ResourceLoader(
        ResourceLoaderConfig(cwd=cwd, user_root=tmp_path / "missing", extra_roots=(root,))
    ).load()

    prompt = SystemPromptBuilder().build(snapshot)
    assert prompt.startswith("base\n\nappend")
    assert "context rules" in prompt
    assert "secret: Load on demand" in prompt
    assert "VERY LARGE SKILL BODY" not in prompt


def test_include_exclude_filters_are_applied_relative_to_each_root(tmp_path: Path) -> None:
    root = tmp_path / "resources"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    _write(root / "prompts" / "keep.md", "keep")
    _write(root / "prompts" / "drop.md", "drop")
    snapshot = ResourceLoader(
        ResourceLoaderConfig(
            cwd=cwd,
            user_root=tmp_path / "missing",
            extra_roots=(root,),
            include=("prompts/*.md",),
            exclude=("**/drop.md", "prompts/drop.md"),
        )
    ).load()
    assert [prompt.name for prompt in snapshot.prompt_templates] == ["keep"]


def test_parse_frontmatter_is_tolerant_but_deterministic() -> None:
    metadata, body = parse_frontmatter(
        '---\nname: test\nenabled: true\ncount: 2\ntags: ["a", "b"]\n---\nbody'
    )
    assert metadata == {"name": "test", "enabled": True, "count": 2, "tags": ["a", "b"]}
    assert body == "body"
