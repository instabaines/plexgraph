import math
import re
from pathlib import Path

import msgpack
import numpy as np
import pytest

from hyperloom_bridge import style as st
from hyperloom_bridge.style import (
    RESET, StyleController, build_style, by_attribute, by_degree, by_time_bucket, by_values, by_weight, merge_style,
    shape_by_attribute, size_by_attribute, size_by_degree, size_by_weight,
)
from hyperloom_core import Graph, from_temporal_edgelist

REPO = Path(__file__).resolve().parents[3]


def small_graph() -> Graph:
    g = Graph()
    for i in range(4):
        g.add_node(f"n{i}", team="a" if i % 2 else "b", score=float(i * 10))
    g.add_edge("n0", "n1", weight=1.0, kind="x")
    g.add_edge("n1", "n2", weight=3.0, kind="y")
    g.add_edge("n2", "n3", directed=True, kind="x")
    return g


def temporal_graph() -> Graph:
    return from_temporal_edgelist([("a", "b", 1), ("b", "c", 5), ("c", "d", 9)])


# ------------------------------------------------------------------ names shared with the viewer

def test_colormap_palette_and_shape_names_match_the_viewer():
    ts = (REPO / "packages/viz-core/src/style/colormaps.ts").read_text()
    if not ts:
        pytest.skip("viewer sources not available")
    continuous = re.search(r"const CONTINUOUS[^=]*=\s*\{(.*?)\n\};", ts, re.S).group(1)
    categorical = re.search(r"const CATEGORICAL[^=]*=\s*\{(.*?)\n\};", ts, re.S).group(1)
    names = lambda block: re.findall(r"^\s*([A-Za-z0-9]+):\s*\[", block, re.M)  # noqa: E731
    assert tuple(names(continuous)) == st.COLORMAPS
    assert ("default", *names(categorical)) == st.PALETTES
    shapes = re.search(r"NODE_SHAPES = \[(.*?)\]", (REPO / "packages/viz-core/src/style/spec.ts").read_text()).group(1)
    assert tuple(re.findall(r'"(\w+)"', shapes)) == st.SHAPES


# ------------------------------------------------------------------------------------------ colors

def test_a_string_or_tuple_is_one_color_and_a_list_is_per_node():
    g = small_graph()
    assert build_style(g, node_color="crimson")["node"]["color"] == {"kind": "constant", "color": [220 / 255, 20 / 255, 60 / 255, 1.0]}
    assert build_style(g, node_color=(0.1, 0.2, 0.3))["node"]["color"]["color"] == [0.1, 0.2, 0.3, 1.0]
    per_node = build_style(g, node_color=["red", "blue", (0, 1, 0), "#fff"])["node"]["color"]
    assert per_node["kind"] == "colors" and per_node["colors"].shape == (16,) and "present" not in per_node
    assert list(per_node["colors"][:4]) == [1, 0, 0, 1]


def test_numbers_are_mapped_through_a_colormap_like_matplotlib():
    g = small_graph()
    enc = build_style(g, node_color=[0, 1, 2, 3], cmap="plasma", vmin=0, vmax=10)["node"]["color"]
    assert enc["kind"] == "values" and enc["colormap"] == "plasma" and enc["domain"] == [0.0, 10.0]
    assert enc["values"].dtype == np.float64 and list(enc["values"]) == [0, 1, 2, 3]
    assert build_style(g, node_color=np.array([0.5, 1, 2, 3]))["node"]["color"]["colormap"] == "viridis"


def test_a_two_dimensional_array_of_rgb_rows_is_per_node_colors():
    g = small_graph()
    enc = build_style(g, node_color=np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], dtype=float))["node"]["color"]
    assert enc["kind"] == "colors" and list(enc["colors"][4:8]) == [0, 1, 0, 1]


def test_a_dict_colors_only_the_named_nodes():
    g = small_graph()
    enc = build_style(g, node_color={"n1": "red", "n3": "#0000ff"})["node"]["color"]
    assert enc["kind"] == "colors" and list(enc["present"]) == [0, 1, 0, 1]
    assert list(enc["colors"][4:8]) == [1, 0, 0, 1]
    numeric = build_style(g, node_color={"n0": 1.0, "n2": 5.0}, cmap="magma")["node"]["color"]
    assert numeric["kind"] == "values" and np.isnan(numeric["values"][1]) and numeric["values"][2] == 5.0
    with pytest.raises(KeyError, match="unknown node 'zz'"):
        build_style(g, node_color={"zz": "red"})


def test_edge_dicts_use_source_target_pairs_and_undirected_edges_match_either_order():
    g = small_graph()
    enc = build_style(g, edge_color={("n1", "n0"): "red"})["edge"]["color"]  # the n0-n1 edge is undirected
    assert list(enc["present"]) == [1, 0, 0]
    with pytest.raises(KeyError, match="no edge between"):
        build_style(g, edge_color={("n3", "n2"): "red"})  # n2->n3 is directed, so the reverse pair is not an edge
    with pytest.raises(ValueError, match="source, target"):
        build_style(g, edge_color={"n0": "red"})


def test_encodings_are_checked_against_the_graph():
    g = small_graph()
    assert build_style(g, node_color=by_attribute("team", palette="tab10"))["node"]["color"]["palette"] == "tab10"
    with pytest.raises(ValueError, match=r"no node has an attribute named 'nope'; node attributes are: score, team"):
        build_style(g, node_color=by_attribute("nope"))
    with pytest.raises(ValueError, match="no edge has an attribute named 'team'"):
        build_style(g, edge_color=by_attribute("team"))
    with pytest.raises(ValueError, match="degree is a node property"):
        build_style(g, edge_color=by_degree())
    with pytest.raises(ValueError, match="weight is an edge property"):
        build_style(g, node_color=by_weight())
    with pytest.raises(ValueError, match="no time information"):
        build_style(g, edge_color=by_time_bucket(4))
    assert build_style(temporal_graph(), edge_color=by_time_bucket(3, split="events"))["edge"]["color"]["split"] == "events"


def test_wrong_counts_and_bad_arguments_explain_themselves():
    g = small_graph()
    with pytest.raises(ValueError, match=r"node_color has 3 entries but the graph has 4 nodes"):
        build_style(g, node_color=["red", "blue", "green"])
    with pytest.raises(ValueError, match="unknown colormap 'rainbowz'; choose one of viridis"):
        build_style(g, node_color=[1, 2, 3, 4], cmap="rainbowz")
    with pytest.raises(ValueError, match="entry 1 is not a color"):
        build_style(g, node_color=["red", "notacolor", "blue", "green"])
    with pytest.raises(TypeError, match="must be a color, a list/array"):
        build_style(g, node_color=object())
    with pytest.raises(ValueError, match="both vmin and vmax"):
        by_degree(vmin=0)
    with pytest.raises(ValueError, match="unknown palette"):
        by_attribute("team", palette="nope")


def test_typos_in_option_names_list_the_valid_ones():
    with pytest.raises(TypeError, match=r"unknown style option 'node_colour'; valid options: alpha, arrow_scale"):
        build_style(small_graph(), node_colour="red")


# ----------------------------------------------------------------------------- sizes, shapes, misc

def test_sizes_are_pixels_and_come_as_a_number_a_list_a_dict_or_an_encoding():
    g = small_graph()
    assert build_style(g, node_size=12)["node"]["size"] == 12.0
    enc = build_style(g, node_size=[4, 8, 12, 16])["node"]["size"]
    assert enc["kind"] == "pixels" and list(enc["values"]) == [4, 8, 12, 16]
    sparse = build_style(g, node_size={"n2": 30})["node"]["size"]
    assert np.isnan(sparse["values"][0]) and sparse["values"][2] == 30
    assert build_style(g, node_size=size_by_degree((6, 20)))["node"]["size"] == {"kind": "degree", "range": [6.0, 20.0], "scale": "sqrt"}
    assert build_style(g, edge_width=size_by_weight((1, 5)))["edge"]["width"]["kind"] == "weight"
    assert build_style(g, node_size=size_by_attribute("score", (5, 25), scale="log", vmin=0, vmax=30))["node"]["size"]["domain"] == [0.0, 30.0]
    for bad in (-3, [1, 2], [1, -2, 3, 4], "big"):
        with pytest.raises((ValueError, TypeError)):
            build_style(g, node_size=bad)


def test_shapes_accept_names_matplotlib_markers_lists_dicts_and_attributes():
    g = small_graph()
    assert build_style(g, node_shape="s")["node"]["shape"] == "square"
    assert build_style(g, node_shape="^")["node"]["shape"] == "triangle"
    assert build_style(g, node_shape=["o", "s", None, "d"])["node"]["shape"]["values"] == ["circle", "square", "circle", "diamond"]
    assert build_style(g, node_shape={"n1": "cross"})["node"]["shape"]["values"] == ["circle", "cross", "circle", "circle"]
    assert build_style(g, node_shape=shape_by_attribute("team", ["triangle", "square"]))["node"]["shape"]["shapes"] == ["triangle", "square"]
    with pytest.raises(ValueError, match="unknown node shape 'hexagon'"):
        build_style(g, node_shape="hexagon")
    with pytest.raises(ValueError, match="no node has an attribute"):
        build_style(g, node_shape=shape_by_attribute("nothing"))


def test_alpha_outline_curvature_and_labels():
    g = small_graph()
    spec = build_style(g, alpha=0.4, edgecolors="#000000", linewidths=1.5, connectionstyle="arc3,rad=0.3", with_labels=True, font_size=13, font_color="navy", label_halo=True, arrow_scale=2)
    assert spec["node"]["opacity"] == 0.4 and spec["edge"]["opacity"] == 0.4
    assert spec["node"]["outline"] == {"color": [0.0, 0.0, 0.0, 1.0], "width": 1.5}
    assert spec["edge"]["curvature"] == 0.3 and spec["edge"]["arrowScale"] == 2.0
    assert spec["node"]["label"] == {"mode": "all", "fontSize": 13.0, "color": [0.0, 0.0, 128 / 255, 1.0], "halo": True}
    assert build_style(g, alpha=0.4, edge_alpha=0.9)["edge"]["opacity"] == 0.9
    for bad in ({"alpha": 2}, {"edge_curvature": 5}, {"connectionstyle": "straight"}, {"label_mode": "some"}, {"label_size": 2}, {"arrow_scale": 0}, {"node_outline_width": -1}):
        with pytest.raises(ValueError):
            build_style(g, **bad)
    with pytest.raises(TypeError, match="either 'edgecolors' or 'node_outline_color'"):
        build_style(g, edgecolors="red", node_outline_color="blue")


def test_reset_restores_a_default_and_none_leaves_it_alone():
    g = small_graph()
    spec = build_style(g, node_color=RESET, node_size=None, edge_curvature=RESET, background_color=RESET)
    assert spec == {"node": {"color": None}, "edge": {"curvature": None}, "background": None}
    assert build_style(g) == {}


# --------------------------------------------------------------------------------- merging and state

def test_merge_style_matches_the_viewers_rules():
    a = merge_style({}, {"node": {"color": "c", "size": 10}, "edge": {"width": 2}})
    assert merge_style(a, {"node": {"size": 20}}) == {"node": {"color": "c", "size": 20}, "edge": {"width": 2}}
    assert merge_style(a, {"node": {"color": None}}) == {"node": {"size": 10}, "edge": {"width": 2}}
    assert merge_style(a, {"node": None}) == {"edge": {"width": 2}}
    assert merge_style({"node": {"label": {"mode": "all", "fontSize": 14}}}, {"node": {"label": {"halo": True}}})["node"]["label"] == {"mode": "all", "fontSize": 14, "halo": True}
    assert merge_style({"background": [1, 1, 1, 1]}, {"background": None}) == {}


def decode(message: bytes) -> dict:
    return msgpack.unpackb(message, raw=False)


def test_the_controller_sends_updates_and_remembers_the_style_for_late_viewers():
    sent: list[tuple[int, list[dict]]] = []
    ctl = StyleController(small_graph())
    ctl.bind(lambda version, messages: sent.append((version, [decode(m) for m in messages])))
    ctl.update(node_color=by_degree("plasma"), node_size=10)
    ctl.update(node_size=14, edge_curvature=0.2)
    assert [v for v, _ in sent] == [1, 2]
    assert sent[1][1][0]["op"] == "set" and sent[1][1][0]["spec"] == {"node": {"size": 14.0}, "edge": {"curvature": 0.2}}
    version, replay = ctl.replay()
    assert version == 2
    message = decode(replay[0])
    assert message["op"] == "replace"
    assert message["spec"] == {"node": {"color": {"kind": "degree", "colormap": "plasma"}, "size": 14.0}, "edge": {"curvature": 0.2}}


def test_painting_nodes_is_remembered_and_replayed_grouped_by_color():
    sent = []
    ctl = StyleController(small_graph())
    ctl.bind(lambda v, ms: sent.append([decode(m) for m in ms]))
    ctl.paint(["n0", "n2"], "red")
    ctl.paint({"n1": "blue", "n3": "blue"})
    assert ctl.painted() == {"n0": (1.0, 0.0, 0.0, 1.0), "n2": (1.0, 0.0, 0.0, 1.0), "n1": (0.0, 0.0, 1.0, 1.0), "n3": (0.0, 0.0, 1.0, 1.0)}
    first = sent[0][0]
    assert first["op"] == "paint" and list(np.frombuffer(first["ids"], dtype=np.uint32)) == [0, 2] and first["color"] == [1.0, 0.0, 0.0, 1.0]
    _, replay = ctl.replay()
    decoded = [decode(m) for m in replay]
    assert {tuple(np.frombuffer(m["ids"], dtype=np.uint32)) for m in decoded} == {(0, 2), (1, 3)}
    ctl.paint("n0", None)
    assert "n0" not in ctl.painted()
    ctl.clear_paint()
    assert ctl.painted() == {}
    ctl.paint(["n0"], "red"); ctl.reset()
    assert ctl.painted() == {} and ctl.spec() == {} and ctl.replay()[1] == []
    with pytest.raises(KeyError, match="unknown node 'zz'"):
        ctl.paint(["zz"], "red")
    with pytest.raises(TypeError, match="either a dict"):
        ctl.paint({"n0": "red"}, "blue")


def test_an_invalid_update_changes_nothing_and_sends_nothing():
    sent = []
    ctl = StyleController(small_graph())
    ctl.bind(lambda v, ms: sent.append(ms))
    ctl.update(node_size=9)
    with pytest.raises(ValueError):
        ctl.update(node_size=12, node_color=by_attribute("nope"))
    assert ctl.spec() == {"node": {"size": 9.0}} and len(sent) == 1


def test_arrays_travel_as_typed_bytes_the_viewer_can_revive():
    ctl = StyleController(small_graph())
    out = []
    ctl.bind(lambda v, ms: out.extend(decode(m) for m in ms))
    ctl.update(node_color=[0.5, 1.5, np.nan, 3])
    values = out[0]["spec"]["node"]["color"]["values"]
    assert values["$dtype"] == "f64"
    restored = np.frombuffer(values["$data"], dtype=np.float64)
    assert restored[0] == 0.5 and math.isnan(restored[2])


def test_show_options_are_exactly_the_documented_names():
    assert "node_color" in st.STYLE_OPTIONS and "connectionstyle" in st.STYLE_OPTIONS and "with_labels" in st.STYLE_OPTIONS


def test_a_plain_rgba_list_is_still_one_color_unless_it_could_be_one_entry_per_node():
    g = small_graph()  # 4 nodes, 3 edges
    assert build_style(g, node_color=[1, 0, 0])["node"]["color"] == {"kind": "constant", "color": [1.0, 0.0, 0.0, 1.0]}
    assert build_style(g, edge_color=[0.2, 0.4, 0.6, 0.5, ][:4])["edge"]["color"]["kind"] == "constant"
    # four numbers on a four-node graph are one number per node, mapped through a colormap
    assert build_style(g, node_color=[0.1, 0.2, 0.3, 0.4])["node"]["color"]["kind"] == "values"
    # the 0-255 form parse_color has always accepted
    assert build_style(g, node_color=[255, 0, 0])["node"]["color"]["color"] == [1.0, 0.0, 0.0, 1.0]
