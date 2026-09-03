from __future__ import annotations

from pathlib import Path

from pi_agent.harness import ResourceLoader, ResourceLoaderConfig, SystemPromptBuilder


def test_product_resource_loader_uses_layered_snapshot(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    resource_root = project / ".pi"
    project.mkdir()
    resource_root.mkdir()
    (resource_root / "SYSTEM.md").write_text("project system", encoding="utf-8")
    (project / "AGENTS.md").write_text("project context", encoding="utf-8")

    snapshot = ResourceLoader(
        ResourceLoaderConfig(cwd=project, user_root=tmp_path / "missing")
    ).load()
    prompt = SystemPromptBuilder().build(snapshot)

    assert snapshot.system_prompt == "project system"
    assert "project context" in prompt
