from __future__ import annotations

from pathlib import Path

from pi_agent.coding_agent import AuthStorage, Credential, CredentialResolver


def test_auth_public_types_remain_available(tmp_path: Path) -> None:
    storage = AuthStorage(tmp_path / "auth.json")
    resolver = CredentialResolver(storage=storage, environment={})
    assert storage.path == tmp_path / "auth.json"
    assert resolver is not None
    assert Credential.__name__ == "Credential"
