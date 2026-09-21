"""Check a built release the way a user would meet it: install it into a fresh environment and use it.

    python scripts/check_release.py                 # checks dist/*.whl and dist/*.tar.gz
    python scripts/check_release.py --browser       # also renders the bundled viewer in Chrome (needs Node and Playwright)
    python scripts/check_release.py path/to/dist --only wheel

For each artifact this creates an empty virtual environment (no access to this checkout), installs only that
artifact, and then runs a smoke test in it: version, the implementation packages, `python -m plexgraph info`, the
`plexgraph` command, the bundled viewer served over HTTP, a WebSocket client receiving the graph and its style, a live
style change, and a clear error for an optional dependency that is not installed. It also inspects the archive
contents and runs `twine check`.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SMOKE = r'''
import json, os, re, subprocess, sys, urllib.request
import msgpack
from websockets.sync.client import connect

import plexgraph as pg
import plexgraph_bridge, plexgraph_core
from plexgraph_bridge import show as implementation_show
from plexgraph_core import Graph as ImplementationGraph

expected = sys.argv[1]
report = {}
assert pg.__version__ == expected, (pg.__version__, expected)
assert pg.Graph is ImplementationGraph and pg.show is implementation_show
where = os.path.dirname(os.path.abspath(pg.__file__))
assert "site-packages" in where, f"imported from {where}, not from an installed package"
report["installed_at"] = where

info = subprocess.run([sys.executable, "-m", "plexgraph", "info"], capture_output=True, text=True)
assert info.returncode == 0 and "viewer    bundled" in info.stdout, info.stdout + info.stderr
script = os.path.join(os.path.dirname(sys.executable), "plexgraph" + (".exe" if os.name == "nt" else ""))
out = subprocess.run([script, "--version"], capture_output=True, text=True)
assert out.returncode == 0 and out.stdout.strip() == expected, (out.stdout, out.stderr)

g = pg.Graph()
for i in range(20):
    g.add_node(i, team=f"t{i % 3}")
for i in range(20):
    g.add_edge(i, (i + 1) % 20)
    g.add_edge(i, (i * 7 + 3) % 20, weight=float(i % 4 + 1))
handle = pg.show(g, open_browser=False, block=False, return_handle=True, seed=0, layout_iterations=6,
                 node_color=pg.by_attribute("team", palette="tab10"), node_size=pg.size_by_degree((6, 14)), edge_curvature=0.1)
try:
    assert handle.http_port, "no viewer was served: the frontend is not bundled"
    base = f"http://localhost:{handle.http_port}"
    page = urllib.request.urlopen(base + "/index.html").read().decode()
    assert "<title>plexgraph</title>" in page
    bundle = re.search(r'src="(/assets/[^"]+\.js)"', page).group(1)
    assert len(urllib.request.urlopen(base + bundle).read()) > 50_000, "the viewer script is missing or truncated"
    report["viewer_bytes"] = len(urllib.request.urlopen(base + bundle).read())

    with connect(handle.ws_url, max_size=None) as ws:
        kinds = []
        for _ in range(6):
            message = msgpack.unpackb(ws.recv(timeout=15), raw=False)
            kinds.append(message["type"] + (":" + message["op"] if message["type"] == "style" else ""))
        assert kinds[0] == "graph" and kinds[1] == "style:replace" and "layout_step" in kinds, kinds
        handle.style(node_shape="s")
        seen = []
        for _ in range(20):
            message = msgpack.unpackb(ws.recv(timeout=15), raw=False)
            if message["type"] == "style":
                seen.append(message)
                break
        assert seen and seen[0]["spec"] == {"node": {"shape": "square"}}, seen
    handle.color_nodes([0, 1], "crimson")
    handle.reset_style()
    assert handle.get_style() == {}
finally:
    handle.close()

# optional dependencies fail clearly, and named errors are useful
try:
    pg.from_networkx(object())
except ImportError as exc:
    assert "networkx" in str(exc)
else:
    if "networkx" not in sys.modules:
        raise AssertionError("from_networkx did not complain")
try:
    pg.show(g, node_color=[1, 2, 3, 4, 5], cmap="viridis", open_browser=False, block=False)
except ValueError as exc:
    assert "entries but the graph has 20 nodes" in str(exc)
else:
    raise AssertionError("a wrong-length node_color was accepted")

g2 = pg.from_temporal_edgelist([("a", "b", "2024-01-01"), ("b", "c", "2024-01-02")])
assert g2.time_unit == "epoch_seconds"
print(json.dumps(report))
'''


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def inspect_wheel(path: Path) -> list[str]:
    problems = []
    names = zipfile.ZipFile(path).namelist()
    if not path.name.endswith("-py3-none-any.whl"):
        problems.append(f"{path.name} is not a pure-Python universal wheel")
    for required in ("plexgraph/__init__.py", "plexgraph/py.typed", "plexgraph_core/py.typed", "plexgraph_bridge/py.typed",
                     "plexgraph_bridge/static/index.html", "plexgraph_core/model/ir.py", "plexgraph_bridge/style.py"):
        if required not in names:
            problems.append(f"wheel is missing {required}")
    if not any(n.startswith("plexgraph_bridge/static/assets/") and n.endswith(".js") for n in names):
        problems.append("wheel has no viewer script")
    if not any(n.endswith("licenses/LICENSE") for n in names):
        problems.append("wheel has no LICENSE")
    for bad in ("__pycache__", "/tests/", ".pyc", ".map", ".ipynb"):
        hits = [n for n in names if bad in n]
        if hits:
            problems.append(f"wheel contains {bad}: {hits[:2]}")
    return problems


def inspect_sdist(path: Path) -> list[str]:
    problems = []
    with tarfile.open(path) as archive:
        names = [n.split("/", 1)[1] for n in archive.getnames() if "/" in n]
    for required in ("pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", "plexgraph_bridge/static/index.html", "plexgraph/__init__.py"):
        if required not in names:
            problems.append(f"sdist is missing {required}")
    return problems


def smoke(artifact: Path, version: str, workdir: Path, extra_pip: list[str]) -> tuple[bool, str]:
    environment = workdir / "venv"
    created = run([sys.executable, "-m", "venv", str(environment)])
    if created.returncode:
        return False, "could not create a virtual environment:\n" + created.stderr
    python = venv_python(environment)
    installed = run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(artifact), *extra_pip])
    if installed.returncode:
        return False, "pip install failed:\n" + (installed.stdout + installed.stderr)[-2500:]
    # run from an empty directory so nothing from this checkout can be imported by accident
    empty = workdir / "empty"
    empty.mkdir()
    result = run([str(python), "-c", SMOKE, version], cwd=empty, timeout=180)
    if result.returncode:
        return False, "smoke test failed:\n" + (result.stdout + result.stderr)[-2500:]
    return True, result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def browser_check(artifact: Path, workdir: Path) -> tuple[bool, str]:
    """Render the bundled viewer from the installed package in real Chrome (needs Node, Playwright and Chrome)."""
    node = shutil.which("node")
    if node is None or not (ROOT / "node_modules/playwright").is_dir():
        return False, "--browser needs Node and `pnpm install` (Playwright) in this checkout"
    python = venv_python(workdir / "venv")
    server = subprocess.Popen(
        [str(python), "-u", "-c",
         "import time, plexgraph as pg\n"
         "g = pg.Graph()\n"
         "[g.add_node(i, t=i % 3) for i in range(40)]\n"
         "[g.add_edge(i, (i + 1) % 40) for i in range(40)]\n"
         "h = pg.show(g, open_browser=False, block=False, return_handle=True, layout_iterations=30, node_color=pg.by_attribute('t'))\n"
         "print(h.url, flush=True)\n"
         "time.sleep(60)\n"],
        cwd=workdir / "empty", stdout=subprocess.PIPE, text=True)
    try:
        url = server.stdout.readline().strip()  # type: ignore[union-attr]
        check = run([node, str(ROOT / "scripts/verify-render.mjs"), url], cwd=ROOT, timeout=120)
        return check.returncode == 0 and "RESULT: PASS" in check.stdout, (check.stdout + check.stderr)[-600:]
    finally:
        server.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dist", nargs="?", type=Path, default=ROOT / "dist")
    parser.add_argument("--only", choices=["wheel", "sdist"], help="check just one artifact")
    parser.add_argument("--browser", action="store_true", help="also render the installed viewer in Chrome")
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text().strip()
    wheels = sorted(args.dist.glob("plexgraph-*.whl"))
    sdists = sorted(args.dist.glob("plexgraph-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print(f"expected exactly one wheel and one sdist in {args.dist}; run scripts/build_release.py first", file=sys.stderr)
        return 2
    failures = 0

    def report(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and ok else ""))
        if detail and not ok:
            print("     " + detail.replace("\n", "\n     "))

    twine = run([sys.executable, "-m", "twine", "check", "--strict", *map(str, [*wheels, *sdists])])
    report("twine check (metadata and README render)", twine.returncode == 0, twine.stdout + twine.stderr)
    problems = inspect_wheel(wheels[0])
    report("wheel contents", not problems, "; ".join(problems))
    problems = inspect_sdist(sdists[0])
    report("sdist contents", not problems, "; ".join(problems))
    if not re.fullmatch(re.escape(f"plexgraph-{version}") + r"(-py3-none-any\.whl|\.tar\.gz)", wheels[0].name) and wheels[0].name.split("-")[1] != version:
        report("artifact version matches VERSION", False, f"{wheels[0].name} vs {version}")

    for kind, artifact in (("wheel", wheels[0]), ("sdist", sdists[0])):
        if args.only and args.only != kind:
            continue
        with tempfile.TemporaryDirectory(prefix=f"plexgraph-check-{kind}-") as tmp:
            workdir = Path(tmp)
            ok, detail = smoke(artifact, version, workdir, [])
            report(f"install {kind} in a clean environment and use it", ok, detail)
            if ok and args.browser and kind == "wheel":
                ok, detail = browser_check(artifact, workdir)
                report("render the installed viewer in Chrome", ok, detail)
    print("\n" + ("all checks passed" if not failures else f"{failures} check(s) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
