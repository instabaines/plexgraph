import re
import subprocess
import sys
from pathlib import Path

import pytest

import plexgraph as pg
import plexgraph_bridge
import plexgraph_core

REPO = Path(__file__).resolve().parents[3]


def test_every_public_name_resolves_and_is_the_same_object_as_the_underlying_package():
    for name in pg.__all__:
        assert hasattr(pg, name), name
    for name in ("Graph", "Node", "Connector", "Layer", "from_edgelist", "from_temporal_edgelist", "read_temporal_edgelist"):
        assert getattr(pg, name) is getattr(plexgraph_core, name)
    for name in ("show", "by_degree", "by_time_bucket", "size_by_degree", "RESET", "COLORMAPS", "BridgeServer"):
        assert getattr(pg, name) is getattr(plexgraph_bridge, name)


def test_the_implementation_packages_are_installed_alongside():
    from plexgraph_bridge import show  # noqa: F401
    from plexgraph_core import Graph  # noqa: F401
    from plexgraph_core.algorithms.layout import force_directed_layout  # noqa: F401

    assert plexgraph_core.Graph is pg.Graph


def test_the_top_level_api_works_end_to_end():
    g = pg.Graph()
    g.add_node("a", team="x"); g.add_node("b", team="y")
    g.add_edge("a", "b", weight=2.0)
    handle = pg.show(g, open_browser=False, block=False, return_handle=True, layout_iterations=3,
                     node_color=pg.by_attribute("team"), node_size=pg.size_by_degree((6, 12)))
    try:
        assert handle.ws_port
        handle.style(edge_curvature=0.2)
        assert handle.get_style()["edge"] == {"curvature": 0.2}
    finally:
        handle.close()
    assert isinstance(pg.ShowHandle, type)


def test_the_version_is_the_same_everywhere():
    """One release version: the VERSION file, the package, and both source packages must agree."""
    version = (REPO / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+([abrc.]+\d+)?", version), version
    assert pg.__version__ == version
    for package in ("core", "bridge", "plexgraph"):
        text = (REPO / "packages" / package / "pyproject.toml").read_text()
        assert re.search(rf'^version = "{re.escape(version)}"$', text, re.M), f"packages/{package}/pyproject.toml is not {version}"


def test_python_dash_m_plexgraph_reports_the_version_and_the_bundled_viewer():
    out = subprocess.run([sys.executable, "-m", "plexgraph", "--version"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == pg.__version__
    info = subprocess.run([sys.executable, "-m", "plexgraph", "info"], capture_output=True, text=True, timeout=60)
    assert f"plexgraph {pg.__version__}" in info.stdout and "numpy" in info.stdout
    if not (REPO / "packages/app/dist/index.html").exists():
        pytest.skip("frontend not built")
    assert "viewer    bundled" in info.stdout and info.returncode == 0


def test_the_changelog_has_an_entry_for_the_current_version():
    """A release without release notes fails here rather than at publish time."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        from changelog_section import section
    finally:
        sys.path.pop(0)
    notes = section(pg.__version__)
    assert "###" in notes or notes.strip()


def test_release_metadata_lists_every_python_the_ci_matrix_tests():
    text = (REPO / "packaging/pyproject.release.toml").read_text()
    ci = (REPO / ".github/workflows/ci.yml").read_text()
    for minor in re.findall(r"Programming Language :: Python :: (3\.\d+)", text):
        assert f'"{minor}"' in ci, f"Python {minor} is a classifier but is not in the CI matrix"


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)


def test_importing_plexgraph_does_not_load_the_notebook_stack_or_the_socket_library():
    # A hosted notebook manages ipywidgets, IPython and traitlets itself: importing us must not touch them.
    result = _run("import sys, plexgraph\n"
                  "loaded = [m for m in ('anywidget', 'ipywidgets', 'IPython', 'traitlets', 'websockets') if m in sys.modules]\n"
                  "print(loaded)\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_the_widget_route_works_without_the_websockets_package():
    # `pip install --no-deps` into a locked-down environment: the widget never needs a socket.
    result = _run("import sys\n"
                  "sys.modules['websockets'] = None  # importing it now raises ImportError\n"
                  "import IPython, IPython.display\n"
                  "class Shell: pass\n"
                  "Shell.__name__ = 'ZMQInteractiveShell'\n"
                  "IPython.get_ipython = lambda: Shell()\n"
                  "shown = []\n"
                  "IPython.display.display = lambda obj, *a, **k: shown.append(obj)\n"
                  "import plexgraph as pg\n"
                  "g = pg.Graph(); g.add_node('a'); g.add_node('b'); g.add_edge('a', 'b')\n"
                  "h = pg.show(g, layout_iterations=1, return_handle=True, node_color='red')\n"
                  "h.color_nodes(['a'], 'blue')\n"
                  "print(type(h.widget).__name__, len(shown), 'websockets' in sys.modules and sys.modules['websockets'] is not None)\n"
                  "h.close()\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["GraphWidget", "1", "False"], result.stdout


def test_the_server_route_without_websockets_says_what_to_install():
    result = _run("import sys\n"
                  "sys.modules['websockets'] = None\n"
                  "import plexgraph as pg\n"
                  "g = pg.Graph(); g.add_node('a'); g.add_node('b'); g.add_edge('a', 'b')\n"
                  "try:\n"
                  "    pg.show(g, open_browser=False, block=False, layout_iterations=1)\n"
                  "except ImportError as e:\n"
                  "    print('ImportError', 'websockets' in str(e), 'plexgraph[jupyter]' in str(e))\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["ImportError", "True", "True"], result.stdout + result.stderr


def _first_python_block(text: str) -> str:
    return re.search(r"```python\n(.*?)```", text, re.S).group(1)


@pytest.fixture
def quiet_show(monkeypatch):
    """`pg.show` that starts the viewer without opening a browser or blocking, and closes it afterwards."""
    real, opened = pg.show, []

    def show(graph, **options):
        wanted = options.pop("return_handle", False)  # what the example asked for; the test always needs the handle to close
        handle = real(graph, **{"open_browser": False, "block": False, "layout_iterations": 1, **options, "return_handle": True})
        opened.append(handle)
        return handle if wanted else None

    monkeypatch.setattr(pg, "show", show)
    yield opened
    for handle in opened:
        handle.close()


@pytest.mark.parametrize("source", ["README.md", "packaging/README.md"])
def test_the_first_example_in_each_readme_runs_as_written(source, quiet_show):
    root = Path(__file__).resolve().parents[3]
    exec(compile(_first_python_block((root / source).read_text(encoding="utf-8")), source, "exec"), {})
    assert quiet_show, f"the example in {source} never called show()"


def test_the_example_in_the_package_docstring_runs_as_written(quiet_show):
    code = "\n".join(line[4:] for line in pg.__doc__.splitlines() if line.startswith("    "))
    exec(compile(code, "plexgraph.__doc__", "exec"), {})
    assert quiet_show
