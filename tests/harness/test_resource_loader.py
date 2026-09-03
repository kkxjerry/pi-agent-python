from __future__ import annotations

import json
from pathlib import Path

from pi_agent.harness import ResourceLoader, build_system_prompt


def test_resource_loader_merges_scopes_and_tracks_shadowing(tmp_path: Path) -> None:
    home = tmp_path / "home" / ".pi"
    project = tmp_path / "repo"
    nested = project / "src"
    for root in (home, project / ".pi", nested / ".pi"):
        (root / "skills" / "review").mkdir(parents=True)
        (root / "prompts").mkdir(parents=True)
    home.joinpath("SYSTEM.md").write_text("user system")
    project.joinpath(".pi", "SYSTEM.md").write_text("project system")
    nested.joinpath(".pi", "APPEND_SYSTEM.md").write_text("nested appendix")
    project.joinpath("AGENTS.md").write_text("project instructions")
    home.joinpath("skills", "review", "SKILL.md").write_text(
        "---\nname: review\ndescription: user review\n---\nuser body"
    )
    project.joinpath(".pi", "skills", "review", "SKILL.md").write_text(
        "---\nname: review\ndescription: project review\n---\nproject body"
    )
    nested.joinpath(".pi", "prompts", "fix.md").write_text(
        "---\ndescription: fix code\n---\nFix $ARGUMENTS"
    )

    resources = ResourceLoader(cwd=nested, user_dir=home).load()

    assert resources.system_prompt == "project system"
    assert resources.append_system_prompts == ["nested appendix"]
    assert resources.skills["review"].description == "project review"
    assert resources.prompts["fix"].content == "Fix $ARGUMENTS"
    assert any(warning.code == "resource_shadowed" for warning in resources.warnings)
    prompt = build_system_prompt("fallback", cwd=nested, resources=resources)
    assert "project system" in prompt
    assert "project instructions" in prompt
    assert "project review" in prompt
    assert "project body" not in prompt
    assert "nested appendix" in prompt


def test_resource_loader_reports_invalid_json_without_failing(tmp_path: Path) -> None:
    root = tmp_path / ".pi"
    (root / "themes").mkdir(parents=True)
    (root / "themes" / "broken.json").write_text("{")
    (root / "packages" / "ok").mkdir(parents=True)
    (root / "packages" / "ok" / "pi-package.json").write_text(json.dumps({"name": "ok"}))

    resources = ResourceLoader(cwd=tmp_path, user_dir=tmp_path / "missing").load()

    assert "ok" in resources.packages
    assert any(warning.code == "resource_json_error" for warning in resources.warnings)
