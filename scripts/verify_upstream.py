#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

EXPECTED = "ca3958559b60f87ee44c84d94df8c3ee0b7eda575370402abb2d0ad9155cde4a"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    path = parser.parse_args().archive
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != EXPECTED:
        raise SystemExit(f"checksum mismatch: expected {EXPECTED}, got {digest}")
    print(f"archive OK: {path} ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
