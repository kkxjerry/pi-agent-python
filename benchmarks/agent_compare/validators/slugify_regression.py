from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from slugify import slugify

CASES = {
    "C++ Guide": "c++-guide",
    "A--B": "a--b",
    " tabs\tand\nlines ": "tabs-and-lines",
}

for value, expected in CASES.items():
    actual = slugify(value)
    if actual != expected:
        raise SystemExit(
            f"regression: slugify({value!r}) returned {actual!r}, expected {expected!r}"
        )
