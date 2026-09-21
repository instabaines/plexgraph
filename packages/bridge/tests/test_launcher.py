import urllib.request

import msgpack
import pytest
import websockets

from plexgraph_bridge.launcher import _build_style_dict, _in_jupyter, _viewer_url, show
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

    assert handle.url == _viewer_url("localhost", handle.http_port, handle.ws_port, {})

    with urllib.request.urlopen(f"http://localhost:{handle.http_port}/index.html") as resp:
        assert resp.status == 200
        html = resp.read().decode()
    assert "<title>plexgraph</title>" in html

    async with websockets.connect(f"ws://localhost:{handle.ws_port}") as ws:
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
