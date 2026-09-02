#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

TAG = "v0.84.4"
ARCHIVE_NAME = "pi-v0.84.4.tar.gz"
ARCHIVE_URL = f"https://github.com/earendil-works/pi/releases/download/{TAG}/{ARCHIVE_NAME}"
EXPECTED_SHA256 = "ca3958559b60f87ee44c84d94df8c3ee0b7eda575370402abb2d0ad9155cde4a"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != destination and destination not in target.parents:
                raise RuntimeError(f"archive path escapes destination: {member.name}")
            if member.issym() or member.islnk():
                raise RuntimeError(f"archive links are not accepted: {member.name}")
        bundle.extractall(destination, filter="data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the pinned pi source release")
    parser.add_argument("--destination", type=Path, default=Path(".upstream"))
    parser.add_argument("--extract", action="store_true")
    args = parser.parse_args()

    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / ARCHIVE_NAME
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with (
            urllib.request.urlopen(ARCHIVE_URL, timeout=120) as response,
            temporary.open("wb") as output,
        ):
            shutil.copyfileobj(response, output)
        digest = sha256(temporary)
        if digest != EXPECTED_SHA256:
            raise RuntimeError(f"checksum mismatch: expected {EXPECTED_SHA256}, got {digest}")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)

    print(f"archive OK: {archive} ({EXPECTED_SHA256})")
    if args.extract:
        source = destination / f"source-{TAG}"
        if source.exists():
            shutil.rmtree(source)
        source.mkdir()
        safe_extract(archive, source)
        print(f"source extracted: {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
