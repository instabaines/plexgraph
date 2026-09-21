"""Build the single `plexgraph` distribution (sdist and wheel).

    python scripts/build_release.py                     # build into dist/, building the frontend if it is missing
    python scripts/build_release.py --build-frontend    # always rebuild the frontend first
    python scripts/build_release.py --outdir out --keep-stage

The monorepo keeps the graph model, the viewer bridge and the frontend in separate places. This script copies them into
one self-contained staging tree (`plexgraph`, `plexgraph_core`, `plexgraph_bridge` with the built frontend inside it, plus
the licence, README and changelog) and builds from that tree. Building from a staging tree, rather than reaching into
sibling directories, is what lets the sdist install on its own: everything it needs is inside it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache")

SOURCES = {  # staged directory name -> where it comes from in the checkout
    "plexgraph": ROOT / "packages/plexgraph/plexgraph",
    "plexgraph_core": ROOT / "packages/core/plexgraph_core",
    "plexgraph_bridge": ROOT / "packages/bridge/plexgraph_bridge",
}
FRONTEND = ROOT / "packages/app/dist"


def read_version() -> str:
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?", version):
        raise SystemExit(f"VERSION contains {version!r}, which is not a valid version")
    return version


def check_versions(version: str) -> None:
    """Refuse to build if any copy of the version disagrees with VERSION."""
    problems = []
    module = (SOURCES["plexgraph"] / "_version.py").read_text()
    if f'__version__ = "{version}"' not in module:
        problems.append("packages/plexgraph/plexgraph/_version.py")
    for package in ("core", "bridge", "plexgraph"):
        text = (ROOT / "packages" / package / "pyproject.toml").read_text()
        if not re.search(rf'(?m)^version = "{re.escape(version)}"$', text):
            problems.append(f"packages/{package}/pyproject.toml")
    if problems:
        raise SystemExit(f"these files do not say version {version}: {', '.join(problems)}\nRun: python scripts/bump_version.py {version}")


def build_frontend() -> None:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("the viewer is not built and pnpm was not found. Install Node.js and pnpm, then run:\n"
                         "  pnpm install && pnpm --filter @plexgraph/app build")
    print("building the viewer ...", flush=True)
    subprocess.run([pnpm, "install", "--frozen-lockfile"], cwd=ROOT, check=True)
    subprocess.run([pnpm, "--filter", "@plexgraph/app", "build"], cwd=ROOT, check=True)


def check_frontend() -> None:
    if not (FRONTEND / "index.html").is_file() or not list((FRONTEND / "assets").glob("*.js")):
        raise SystemExit(f"{FRONTEND} does not contain a built viewer (index.html and assets/*.js)")


def stage(destination: Path, version: str) -> None:
    for name, source in SOURCES.items():
        if not source.is_dir():
            raise SystemExit(f"missing source directory {source}")
        shutil.copytree(source, destination / name, ignore=IGNORE)
    shutil.copytree(FRONTEND, destination / "plexgraph_bridge" / "static")
    for marker in ("plexgraph/py.typed", "plexgraph_core/py.typed", "plexgraph_bridge/py.typed"):
        if not (destination / marker).is_file():
            raise SystemExit(f"{marker} is missing; the package declares itself typed")
    shutil.copy(ROOT / "LICENSE", destination / "LICENSE")
    shutil.copy(ROOT / "CHANGELOG.md", destination / "CHANGELOG.md")
    shutil.copy(ROOT / "packaging/README.md", destination / "README.md")
    template = (ROOT / "packaging/pyproject.release.toml").read_text()
    (destination / "pyproject.toml").write_text(template.replace("@VERSION@", version))


def build(stage_dir: Path, outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(outdir), str(stage_dir)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outdir", type=Path, default=ROOT / "dist", help="where the sdist and wheel go (default: dist/)")
    parser.add_argument("--build-frontend", action="store_true", help="rebuild the viewer even if a build exists")
    parser.add_argument("--keep-stage", action="store_true", help="leave the staging tree in place and print its path")
    args = parser.parse_args()

    version = read_version()
    check_versions(version)
    if args.build_frontend or not (FRONTEND / "index.html").is_file():
        build_frontend()
    check_frontend()

    stale = [p for p in args.outdir.glob("plexgraph-*") if p.is_file()] if args.outdir.exists() else []
    for path in stale:
        path.unlink()  # so the output directory only ever holds this build

    stage_dir = Path(tempfile.mkdtemp(prefix="plexgraph-stage-"))
    try:
        stage(stage_dir, version)
        build(stage_dir, args.outdir)
    finally:
        if args.keep_stage:
            print(f"staging tree kept at {stage_dir}")
        else:
            shutil.rmtree(stage_dir, ignore_errors=True)

    print(f"\nplexgraph {version}:")
    for path in sorted(args.outdir.glob("plexgraph-*")):
        print(f"  {path}  ({path.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
