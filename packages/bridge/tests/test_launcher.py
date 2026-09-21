import sys
import types
import urllib.parse
import urllib.request

import msgpack
import pytest
import websockets
from websockets.asyncio.client import connect as ws_connect  # the new client, which every supported version has

from plexgraph_bridge import launcher
from plexgraph_bridge.server import BridgeServer
from plexgraph_bridge.launcher import _build_style_dict, _in_jupyter, _notebook_kind, _viewer_url, show
from plexgraph_core.model.ir import Graph


def _small_graph() -> Graph:
    g = Graph()
    for i in range(6):
        g.add_node(i)
    for i in range(5):
        g.add_edge(i, i + 1)
    return g


@pytest.mark.asyncio
async def test_show_serves_static_app_and_streams_graph():
    handle = show(
        _small_graph(),
        seed=1,
        layout_iterations=5,
        open_browser=False,
        block=False,
        return_handle=True,
    )
    assert handle.http_port is not None, (
        "expected packages/app/dist to exist (run `pnpm --filter @plexgraph/app build`); "
        "the built app is required for this end-to-end check"
    )

    assert handle.url == _viewer_url("localhost", handle.http_port, handle.ws_port, {}, handle.token)

    with urllib.request.urlopen(f"http://localhost:{handle.http_port}/index.html") as resp:
        assert resp.status == 200
        html = resp.read().decode()
    assert "<title>plexgraph</title>" in html

    async with ws_connect(handle.ws_url) as ws:
        first = msgpack.unpackb(await ws.recv(), raw=False)
        assert first["type"] == "graph"
        assert len(first["nodes"]) == 6


def test_not_in_jupyter_under_plain_pytest():
    # Sanity check that the detection doesn't false-positive just because
    # IPython happens to be installed (it's a dev/test dependency here).
    assert _in_jupyter() is False


def test_build_style_dict_maps_snake_case_to_camel_case():
    style = _build_style_dict({"node_color": [1, 0, 0, 1], "node_radius_px": 8})
    assert style == {"nodeColor": [1, 0, 0, 1], "nodeRadiusPx": 8}


def test_build_style_dict_omits_unset_options():
    style = _build_style_dict({"node_color": None, "edge_color": [0, 0, 0, 1]})
    assert style == {"edgeColor": [0, 0, 0, 1]}


def test_build_style_dict_maps_edge_width_px():
    style = _build_style_dict({"edge_width_px": 4})
    assert style == {"edgeWidthPx": 4}


def test_build_style_dict_rejects_unknown_option():
    with pytest.raises(TypeError, match="unknown style option"):
        _build_style_dict({"not_a_real_option": 1})


def test_build_style_dict_node_color_dict_becomes_overrides():
    style = _build_style_dict({"node_color": {"alice": "red", "bob": [0, 0, 1, 1]}})
    assert style == {
        "nodeColorOverrides": {"alice": [1.0, 0.0, 0.0, 1.0], "bob": [0, 0, 1, 1]}
    }


def test_build_style_dict_node_color_plain_still_uniform():
    style = _build_style_dict({"node_color": "red"})
    assert style == {"nodeColor": [1.0, 0.0, 0.0, 1.0]}


def test_build_style_dict_node_color_by_passes_through():
    style = _build_style_dict({"node_color_by": "group"})
    assert style == {"nodeColorBy": "group"}


def test_build_style_dict_edge_color_dict_becomes_overrides_keyed_by_pair():
    style = _build_style_dict({"edge_color": {("alice", "bob"): "tomato"}})
    assert "edgeColorOverrides" in style
    assert "alice||bob" in style["edgeColorOverrides"]


def test_build_style_dict_edge_color_dict_rejects_non_pair_keys():
    with pytest.raises(TypeError, match="source, target"):
        _build_style_dict({"edge_color": {"alice": "red"}})


def test_build_style_dict_edge_color_by_passes_through():
    style = _build_style_dict({"edge_color_by": "kind"})
    assert style == {"edgeColorBy": "kind"}


def test_build_style_dict_node_label_true_passes_through():
    style = _build_style_dict({"node_label": True})
    assert style == {"nodeLabel": True}


def test_build_style_dict_node_label_attr_name_passes_through():
    style = _build_style_dict({"node_label": "name"})
    assert style == {"nodeLabel": "name"}


def test_build_style_dict_node_label_dict_stringifies_keys_and_values():
    style = _build_style_dict({"node_label": {0: "Alice", "bob": 42}})
    assert style == {"nodeLabel": {"0": "Alice", "bob": "42"}}


def test_viewer_url_omits_style_param_when_empty():
    url = _viewer_url("localhost", 8080, 9090, {})
    assert url == "http://localhost:8080/?ws=9090"


def test_viewer_url_json_encodes_style():
    url = _viewer_url("localhost", 8080, 9090, {"nodeRadiusPx": 8})
    assert "style=" in url
    assert "%22nodeRadiusPx%22" in url  # URL-encoded '"nodeRadiusPx"'


def test_show_accepts_and_applies_style_kwargs():
    # Regression check: show() shouldn't raise when passed style kwargs,
    # and the resulting handle should still work normally.
    handle = show(
        _small_graph(),
        open_browser=False,
        block=False,
        node_color=[1.0, 0.0, 0.0, 1.0],
        node_radius_px=10,
        return_handle=True,
    )
    assert handle.ws_port > 0


def test_show_returns_none_by_default():
    # Matches matplotlib's plt.show()/plotly's fig.show() convention:
    # calling show(g) bare as a notebook cell's last line shouldn't
    # trigger Jupyter's automatic display of a ShowHandle repr.
    result = show(_small_graph(), open_browser=False, block=False)
    assert result is None


def test_show_handle_closes_servers_idempotently():
    handle = show(_small_graph(), open_browser=False, block=False, return_handle=True)
    handle.close()
    handle.close()
    assert not handle.thread.is_alive()


def _write_app(directory):
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("<title>plexgraph</title>")
    return directory


def test_static_app_prefers_the_bundled_copy_then_the_repo_build_then_the_placeholder(tmp_path, monkeypatch):
    from plexgraph_bridge import launcher

    bundled, dist, public = tmp_path / "static", tmp_path / "dist", tmp_path / "public"
    monkeypatch.setattr(launcher, "_APP_BUNDLED", bundled)
    monkeypatch.setattr(launcher, "_APP_DIST", dist)
    monkeypatch.setattr(launcher, "_APP_PUBLIC", public)

    assert launcher._static_app_dir() == public  # nothing built yet
    _write_app(dist)
    assert launcher._static_app_dir() == dist  # a repo checkout with a build
    _write_app(bundled)
    assert launcher._static_app_dir() == bundled  # an installed wheel wins


def test_an_empty_build_directory_is_not_mistaken_for_a_frontend(tmp_path, monkeypatch):
    from plexgraph_bridge import launcher

    (tmp_path / "static").mkdir()
    monkeypatch.setattr(launcher, "_APP_BUNDLED", tmp_path / "static")
    monkeypatch.setattr(launcher, "_APP_DIST", tmp_path / "dist")
    monkeypatch.setattr(launcher, "_APP_PUBLIC", tmp_path / "public")
    assert launcher._static_app_dir() == tmp_path / "public"


class _ColabShell:
    """Stands in for google.colab._shell.Shell, which is not a ZMQInteractiveShell."""


_ColabShell.__module__ = "google.colab._shell"


@pytest.fixture
def colab(monkeypatch):
    """Pretend to be a Colab kernel: its shell class, and the call Colab provides for showing a kernel port."""
    import IPython

    monkeypatch.setattr(IPython, "get_ipython", lambda: _ColabShell())
    framed = []
    module = types.ModuleType("google.colab")
    module.output = types.SimpleNamespace(
        serve_kernel_port_as_iframe=lambda port, path="/", width="100%", height="400": framed.append((port, path, width, height)))
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.colab", module)
    return framed


def test_colab_is_recognised_as_a_notebook_and_not_as_plain_jupyter(colab):
    assert _notebook_kind() == "colab"
    assert _in_jupyter() is True


def test_show_in_colab_does_not_block_or_open_a_browser_and_serves_everything_from_one_port(colab, monkeypatch):
    opened, inline = [], []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: inline.append(url))
    bound = []

    class Recording(BridgeServer):
        def __init__(self, *args, **kwargs):
            bound.append(kwargs["host"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("plexgraph_bridge.server.BridgeServer", Recording)  # show() imports it when it needs it
    handle = show(_small_graph(), widget=False, layout_iterations=2, height=480, return_handle=True)  # would hang forever if it blocked
    try:
        assert opened == [] and inline == []
        assert bound == ["0.0.0.0"]  # Colab's proxy cannot reach a server bound to localhost
        assert handle.http_port == handle.ws_port  # a second proxied port would be a different origin
        assert colab == [(handle.ws_port, f"/?ws=same-origin&token={urllib.parse.quote(handle.token)}", "100%", "480")]
        # the page really is served by that port
        page = urllib.request.urlopen(f"http://localhost:{handle.ws_port}/").read().decode()
        assert "<title>plexgraph</title>" in page
    finally:
        handle.close()


def test_colab_path_carries_the_style():
    path = launcher._colab_viewer_path({"nodeColor": [1, 0, 0, 1]})
    assert path.startswith("/?ws=same-origin&style=")
    assert launcher._colab_viewer_path({"a": 1}, "tok").startswith("/?ws=same-origin&token=tok&style=")
    assert launcher._colab_viewer_path({}) == "/?ws=same-origin"


def test_when_no_browser_can_open_the_address_is_printed(monkeypatch, capsys):
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: False)  # a server or container has no desktop
    handle = show(_small_graph(), layout_iterations=2, block=False, return_handle=True)
    try:
        err = capsys.readouterr().err
        assert handle.url in err and "ssh -L" in err
        assert f"-L {handle.http_port}:" in err and f"-L {handle.ws_port}:" in err
    finally:
        handle.close()


def test_a_browser_that_raises_does_not_take_the_session_down(monkeypatch, capsys):
    def broken(url):
        raise RuntimeError("no display")
    monkeypatch.setattr(launcher.webbrowser, "open", broken)
    handle = show(_small_graph(), layout_iterations=2, block=False, return_handle=True)
    try:
        assert handle.url in capsys.readouterr().err
    finally:
        handle.close()


def test_a_successful_browser_open_prints_nothing(monkeypatch, capsys):
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)
    handle = show(_small_graph(), layout_iterations=2, block=False, return_handle=True)
    try:
        assert capsys.readouterr().err == ""
    finally:
        handle.close()


def test_colab_has_no_url_to_hand_out(colab):
    handle = show(_small_graph(), widget=False, layout_iterations=2, return_handle=True)
    try:
        assert handle.url is None
    finally:
        handle.close()


@pytest.mark.parametrize("variable, name", [("KAGGLE_KERNEL_RUN_TYPE", "Kaggle"), ("JUPYTERHUB_USER", "JupyterHub"), ("DATABRICKS_RUNTIME_VERSION", "Databricks")])
def test_a_hosted_notebook_gets_a_warning_instead_of_a_silent_blank_frame(monkeypatch, variable, name):
    import IPython

    class Shell:  # a plain Jupyter kernel
        pass
    Shell.__name__ = "ZMQInteractiveShell"
    monkeypatch.setattr(IPython, "get_ipython", lambda: Shell())
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: None)
    monkeypatch.setenv(variable, "1")
    with pytest.warns(RuntimeWarning, match=name):
        handle = show(_small_graph(), widget=False, layout_iterations=2, return_handle=True)
    handle.close()


def test_no_warning_in_an_ordinary_local_notebook(monkeypatch, recwarn):
    import IPython

    class Shell:
        pass
    Shell.__name__ = "ZMQInteractiveShell"
    monkeypatch.setattr(IPython, "get_ipython", lambda: Shell())
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: None)
    for variable in launcher._HOSTED_NOTEBOOKS:
        monkeypatch.delenv(variable, raising=False)
    handle = show(_small_graph(), widget=False, layout_iterations=2, return_handle=True)
    handle.close()
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_show_protects_the_socket_with_a_secret_that_only_its_own_viewer_url_carries():
    import websockets.sync.client as sync_client
    from websockets.exceptions import InvalidStatus

    handle = show(_small_graph(), layout_iterations=2, open_browser=False, block=False, return_handle=True)
    try:
        assert handle.token and f"token={urllib.parse.quote(handle.token)}" in handle.url
        # what a page from another site can do: it knows the port, not the secret
        with pytest.raises(InvalidStatus) as refused:
            with sync_client.connect(f"ws://localhost:{handle.ws_port}", origin="https://evil.example.com"):
                pass
        assert refused.value.response.status_code == 403
        with sync_client.connect(handle.ws_url, max_size=None) as ws:
            assert msgpack.unpackb(ws.recv(timeout=10), raw=False)["type"] == "graph"
    finally:
        handle.close()


def test_every_session_gets_its_own_secret():
    first = show(_small_graph(), layout_iterations=1, open_browser=False, block=False, return_handle=True)
    second = show(_small_graph(), layout_iterations=1, open_browser=False, block=False, return_handle=True)
    try:
        assert first.token != second.token and len(first.token) >= 16
    finally:
        first.close()
        second.close()


def test_the_colab_path_carries_the_secret(colab):
    handle = show(_small_graph(), widget=False, layout_iterations=2, return_handle=True)
    try:
        port, path, _, _ = colab[0]
        assert path == f"/?ws=same-origin&token={urllib.parse.quote(handle.token)}"
    finally:
        handle.close()
