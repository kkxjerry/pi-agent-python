from __future__ import annotations

import json
from pathlib import Path

from pi_agent.coding_agent.package_cli import main


def test_package_cli_install_list_verify_and_remove(
    tmp_path: Path,
    capsys: object,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pi-package.json").write_text(
        json.dumps({"name": "cli-package", "version": "1.0.0"}),
        encoding="utf-8",
    )
    root = tmp_path / "user"

    assert main(["--root", str(root), "--json", "install", str(source)]) == 0
    install = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert install["result"]["name"] == "cli-package"

    assert main(["--root", str(root), "--json", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert listed["result"][0]["version"] == "1.0.0"

    assert main(["--root", str(root), "--json", "verify"]) == 0
    verified = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert verified["result"][0]["valid"] is True

    assert main(["--root", str(root), "--json", "remove", "cli-package"]) == 0
    removed = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert removed["result"]["removed"] is True
