"""Set the release version everywhere it is written: `python scripts/bump_version.py 0.2.0`.

The version lives in the VERSION file, in packages/plexgraph/plexgraph/_version.py, and in the pyproject.toml of the
core, bridge and plexgraph source packages. A test (packages/plexgraph/tests) fails if they ever disagree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?")


def bump(version: str) -> list[Path]:
    if not VERSION_PATTERN.fullmatch(version):
        raise SystemExit(f"{version!r} is not a version like 1.2.3, 1.2.3rc1 or 1.2.3.dev1")
    changed = []
    (ROOT / "VERSION").write_text(version + "\n")
    changed.append(ROOT / "VERSION")
    targets = [
        (ROOT / "packages/plexgraph/plexgraph/_version.py", r'__version__ = "[^"]*"', f'__version__ = "{version}"'),
        *[(ROOT / f"packages/{p}/pyproject.toml", r'(?m)^version = "[^"]*"$', f'version = "{version}"') for p in ("core", "bridge", "plexgraph")],
    ]
    for path, pattern, replacement in targets:
        text = path.read_text()
        new, count = re.subn(pattern, replacement, text, count=1)
        if count != 1:
            raise SystemExit(f"could not find the version in {path}")
        path.write_text(new)
        changed.append(path)
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for path in bump(sys.argv[1]):
        print("updated", path.relative_to(ROOT))
    print("Next: add a CHANGELOG entry, refresh the lock (uv lock), commit, and tag v" + sys.argv[1])
