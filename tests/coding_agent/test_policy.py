from __future__ import annotations

from pathlib import Path

from pi_agent.coding_agent.policy import ToolPolicy


def test_policy_compatibility_alias_uses_single_approval_implementation(tmp_path: Path) -> None:
    policy = ToolPolicy(tmp_path)
    read = policy.evaluate("read", {"path": "README.md"})
    write = policy.evaluate("write", {"path": "README.md", "content": "x"})
    assert read.action == "allow"
    assert write.action == "prompt"
