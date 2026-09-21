"""Print one version's section of CHANGELOG.md (used for GitHub release notes): `python scripts/changelog_section.py 0.1.0`."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(version: str, text: str | None = None) -> str:
    text = (ROOT / "CHANGELOG.md").read_text() if text is None else text
    match = re.search(rf"(?ms)^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )", text)
    if not match or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no entry for version {version}")
    return match.group(1).strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sys.stdout.write(section(sys.argv[1]))
