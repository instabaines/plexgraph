import pandas as pd
import pytest

from plexgraph_core.io import from_edgelist, from_pandas_edgelist


def test_from_edgelist_plain_pairs():
    g = from_edgelist([("a", "b"), ("b", "c")])
    assert g.num_nodes == 3
    assert g.num_edges == 2
    assert not g.connector(0).directed


def test_from_edgelist_with_weight():
    g = from_edgelist([("a", "b", 2.5)])
    assert g.connector(0).weight == 2.5


def test_from_edgelist_with_attrs_dict():
    g = from_edgelist([("a", "b", {"kind": "friend", "weight": 1.5})])
    edge = g.connector(0)
    assert edge.weight == 1.5
    assert edge.attrs["kind"] == "friend"


def test_from_edgelist_directed():
    g = from_edgelist([("a", "b")], directed=True)
    assert g.connector(0).directed


def test_from_edgelist_rejects_bad_tuple_length():
    with pytest.raises(ValueError, match="length 2 or 3"):
        from_edgelist([("a", "b", "c", "d")])


def test_from_edgelist_nodes_reused_across_edges():
    g = from_edgelist([("a", "b"), ("a", "c")])
    assert g.num_nodes == 3  # not 4 — "a" is shared, not duplicated


def test_from_pandas_edgelist_basic():
    df = pd.DataFrame({"source": ["a", "b"], "target": ["b", "c"]})
    g = from_pandas_edgelist(df)
    assert g.num_nodes == 3
    assert g.num_edges == 2


def test_from_pandas_edgelist_custom_columns():
    df = pd.DataFrame({"u": ["a", "b"], "v": ["b", "c"]})
    g = from_pandas_edgelist(df, source="u", target="v")
    assert g.num_edges == 2


def test_from_pandas_edgelist_with_weight_column():
    df = pd.DataFrame({"source": ["a"], "target": ["b"], "weight": [3.0]})
    g = from_pandas_edgelist(df, edge_attr="weight")
    assert g.connector(0).weight == 3.0


def test_from_pandas_edgelist_edge_attr_true_takes_all_columns():
    df = pd.DataFrame({"source": ["a"], "target": ["b"], "kind": ["friend"], "weight": [1.0]})
    g = from_pandas_edgelist(df, edge_attr=True)
    edge = g.connector(0)
    assert edge.weight == 1.0
    assert edge.attrs["kind"] == "friend"


def test_edge_attr_false_means_no_extra_attributes_like_none():
    # False used to crash with "'bool' object is not iterable"
    import pandas as pd

    from plexgraph_core import from_pandas_edgelist, from_pandas_temporal_edgelist

    df = pd.DataFrame({"s": ["a"], "t": ["b"], "w": [3], "extra": ["x"]})
    assert next(iter(from_pandas_edgelist(df, "s", "t", edge_attr=False).connectors())).attrs == {}
    dft = pd.DataFrame({"s": ["a"], "t": ["b"], "when": [5]})
    assert next(iter(from_pandas_temporal_edgelist(dft, "s", "t", "when", edge_attr=False).connectors())).t_start == 5.0


# ---- read_edgelist -------------------------------------------------------

from plexgraph_core.io import read_edgelist, to_pandas_edgelist, write_edgelist  # noqa: E402


def test_read_edgelist_plain_whitespace(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("a b\nb c\n")
    g = read_edgelist(f)
    assert g.num_nodes == 3 and g.num_edges == 2
    assert not g.connector(0).directed


def test_read_edgelist_comma_delimited_with_header(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("source,target,color,weight\nalice,bob,red,2.5\nbob,carol,blue,1.0\n")
    g = read_edgelist(f, delimiter=",", header=True, attrs={"color": 2, "weight": 3})
    assert g.num_edges == 2
    edge = g.connector(0)
    assert edge.weight == 2.5
    assert edge.attrs == {"color": "red"}  # weight popped out, not left as a generic attr too


def test_read_edgelist_custom_source_target_columns(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("note,u,v\nhello,a,b\n")
    g = read_edgelist(f, delimiter=",", header=True, columns=(1, 2))
    assert g.num_edges == 1
    assert {n.key for n in g.nodes()} == {"a", "b"}


def test_read_edgelist_attr_value_coercion(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("a,b,3,2.5,text\n")
    g = read_edgelist(f, delimiter=",", attrs={"i": 2, "f": 3, "s": 4})
    attrs = g.connector(0).attrs
    assert attrs == {"i": 3, "f": 2.5, "s": "text"}
    assert isinstance(attrs["i"], int) and isinstance(attrs["f"], float)


def test_read_edgelist_comments_and_blank_lines_skipped(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("# a comment\na b\n\nb c\n# another\n")
    g = read_edgelist(f)
    assert g.num_edges == 2


def test_read_edgelist_custom_comment_marker(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("% skip me\na b\n")
    g = read_edgelist(f, comments="%")
    assert g.num_edges == 1


def test_read_edgelist_int_nodes_default_coerces_plain_integers(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("1 2\n")
    g = read_edgelist(f)
    assert {n.key for n in g.nodes()} == {1, 2}
    assert all(isinstance(n.key, int) for n in g.nodes())


def test_read_edgelist_int_nodes_false_keeps_text(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("1 2\n")
    g = read_edgelist(f, int_nodes=False)
    assert {n.key for n in g.nodes()} == {"1", "2"}


def test_read_edgelist_leading_zeros_and_huge_numbers_stay_text(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("007 24811812513198111524\n")
    g = read_edgelist(f)
    keys = {n.key for n in g.nodes()}
    assert keys == {"007", "24811812513198111524"}  # not real 64-bit ids


def test_read_edgelist_directed(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("a b\n")
    g = read_edgelist(f, directed=True)
    assert g.connector(0).directed


def test_read_edgelist_nodes_reused_across_rows(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("a b\na c\n")
    g = read_edgelist(f)
    assert g.num_nodes == 3


def test_read_edgelist_too_few_columns_reports_file_and_line(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("a,b,c\nonlyone\n")
    with pytest.raises(ValueError, match=r"e\.csv:2.*expected at least 2 columns, got 1"):
        read_edgelist(f, delimiter=",")


def test_read_edgelist_missing_attr_column_reports_file_and_line(tmp_path):
    f = tmp_path / "e.csv"
    f.write_text("a,b\n")
    with pytest.raises(ValueError, match=r"e\.csv:1.*expected at least 3 columns, got 2"):
        read_edgelist(f, delimiter=",", attrs={"weight": 2})


def test_read_edgelist_columns_must_name_exactly_two():
    with pytest.raises(ValueError, match="exactly 2 columns"):
        read_edgelist("unused", columns=(0, 1, 2))


def test_read_edgelist_strips_byte_order_mark(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("a b\nb a\n", encoding="utf-8-sig")
    g = read_edgelist(f)
    assert sorted(n.key for n in g.nodes()) == ["a", "b"]  # not ['﻿a', 'a', 'b']


def test_read_edgelist_trailing_empty_field_kept_with_explicit_delimiter(tmp_path):
    f = tmp_path / "e.tsv"
    f.write_text("a\tb\t\nb\tc\tok\n")
    g = read_edgelist(f, delimiter="\t", attrs={"note": 2})
    assert [c.attrs.get("note") for c in g.connectors()] == ["", "ok"]


def test_read_edgelist_rejects_temporal_shaped_rows_gracefully(tmp_path):
    # A (u, v, t) file fed to read_edgelist (rather than read_temporal_edgelist) just treats the third column as a
    # plain, uninterpreted attribute if asked for -- no special timestamp handling, and no crash.
    f = tmp_path / "e.txt"
    f.write_text("a b 12345\n")
    g = read_edgelist(f, attrs={"t": 2})
    assert g.connector(0).attrs["t"] == 12345


# ---- to_pandas_edgelist ---------------------------------------------------


def test_to_pandas_edgelist_basic_round_trip():
    g = from_edgelist([("a", "b", 2.5), ("b", "c")])
    df = to_pandas_edgelist(g)
    assert list(df["source"]) == ["a", "b"]
    assert list(df["target"]) == ["b", "c"]
    assert df["weight"][0] == 2.5


def test_to_pandas_edgelist_includes_attrs_layer_and_time_by_default():
    from plexgraph_core.model.ir import Graph

    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_layer("friends")
    g.add_edge(a, b, layer="friends", t_start=1.0, t_end=5.0, kind="close")
    df = to_pandas_edgelist(g)
    row = df.iloc[0]
    assert row["layer"] == "friends" and row["t_start"] == 1.0 and row["t_end"] == 5.0 and row["kind"] == "close"


def test_to_pandas_edgelist_always_present_connector_has_no_time_columns():
    g = from_edgelist([("a", "b")])
    df = to_pandas_edgelist(g)
    assert "t_start" not in df.columns and "t_end" not in df.columns


def test_to_pandas_edgelist_edge_attr_selection():
    g = from_edgelist([("a", "b", {"weight": 1.0, "color": "red", "kind": "x"})])
    only_color = to_pandas_edgelist(g, edge_attr="color")
    assert set(only_color.columns) == {"source", "target", "color"}
    several = to_pandas_edgelist(g, edge_attr=["color", "kind"])
    assert set(several.columns) == {"source", "target", "color", "kind"}
    none = to_pandas_edgelist(g, edge_attr=False)
    assert set(none.columns) == {"source", "target"}
    none2 = to_pandas_edgelist(g, edge_attr=None)
    assert set(none2.columns) == {"source", "target"}


def test_to_pandas_edgelist_custom_column_names():
    g = from_edgelist([("a", "b")])
    df = to_pandas_edgelist(g, source="u", target="v")
    assert list(df.columns[:2]) == ["u", "v"]


def test_to_pandas_edgelist_skips_hyperedges_with_a_warning():
    from plexgraph_core.model.ir import Graph

    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1, weight=1.0)
    g.add_hyperedge([0, 1, 2, 3])
    with pytest.warns(UserWarning, match="skipped 1 hyperedge"):
        df = to_pandas_edgelist(g)
    assert len(df) == 1


def test_to_pandas_edgelist_empty_graph_has_source_target_columns():
    from plexgraph_core.model.ir import Graph

    df = to_pandas_edgelist(Graph())
    assert list(df.columns) == ["source", "target"]
    assert len(df) == 0


# ---- write_edgelist --------------------------------------------------------


def test_write_edgelist_round_trips_with_read_edgelist(tmp_path):
    g = from_edgelist([("a", "b", {"color": "red", "weight": 2.5})])
    out = tmp_path / "out.txt"
    write_edgelist(g, out, attrs=["color", "weight"])
    back = read_edgelist(out, attrs={"color": 2, "weight": 3})
    assert back.connector(0).weight == 2.5
    assert back.connector(0).attrs["color"] == "red"


def test_write_edgelist_default_attrs_includes_weight_only_if_any_present(tmp_path):
    g = from_edgelist([("a", "b", 1.0)])
    out = tmp_path / "out.txt"
    write_edgelist(g, out)
    assert open(out).read() == "a b 1.0\n"

    g2 = from_edgelist([("a", "b")])
    out2 = tmp_path / "out2.txt"
    write_edgelist(g2, out2)
    assert open(out2).read() == "a b\n"  # no weight column at all, not "a b "


def test_write_edgelist_missing_attr_on_one_connector_is_an_empty_field_not_misaligned(tmp_path):
    g = from_edgelist([("a", "b", {"weight": 1.5}), ("b", "c", {"color": "blue"})])
    out = tmp_path / "out.csv"
    write_edgelist(g, out, delimiter=",", attrs=["weight", "color"])
    lines = open(out).read().splitlines()
    # Row 1: color is missing AND last -> trimmed entirely (no dangling comma). Row 2: weight is missing but NOT
    # last (color follows it) -> kept as an empty field so the columns stay aligned.
    assert lines[0] == "a,b,1.5"
    assert lines[1] == "b,c,,blue"


def test_write_edgelist_header(tmp_path):
    g = from_edgelist([("a", "b", {"weight": 1.0})])
    out = tmp_path / "out.txt"
    write_edgelist(g, out, attrs=["weight"], header=True)
    assert open(out).read().splitlines()[0] == "source target weight"


def test_write_edgelist_custom_delimiter(tmp_path):
    g = from_edgelist([("a", "b")])
    out = tmp_path / "out.csv"
    write_edgelist(g, out, delimiter=",")
    assert open(out).read() == "a,b\n"


def test_write_edgelist_skips_hyperedges_with_a_warning(tmp_path):
    from plexgraph_core.model.ir import Graph

    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1)
    g.add_hyperedge([0, 1, 2, 3])
    out = tmp_path / "out.txt"
    with pytest.warns(UserWarning, match="skipped 1 hyperedge"):
        write_edgelist(g, out)
    assert len(open(out).read().splitlines()) == 1


# ---- a node/layer's explicit key colliding with another's internal id must not misresolve exports ---------------


def _graph_with_a_key_colliding_internal_id():
    # b's internal id is 1; c is deliberately given the explicit key 1, colliding with it. Both to_pandas_edgelist
    # and write_edgelist must still report the edge a->b as going to "b", not to c. Edges are built from the string
    # keys ("a", "b"), which resolve unambiguously -- the collision only matters for the *export* functions, which
    # must turn the connector's already-resolved internal endpoint ids back into keys without re-resolving them
    # (re-resolving 1 the same way add_edge did would hit the identical ambiguity a second time).
    from plexgraph_core.model.ir import Graph

    g = Graph()
    g.add_node("a")
    g.add_node("b")
    g.add_node(1)  # key 1 == b's internal id
    g.add_edge("a", "b", weight=1.0)
    return g


def test_to_pandas_edgelist_does_not_confuse_a_node_key_with_another_nodes_internal_id():
    df = to_pandas_edgelist(_graph_with_a_key_colliding_internal_id())
    assert df.iloc[0]["source"] == "a" and df.iloc[0]["target"] == "b"


def test_write_edgelist_does_not_confuse_a_node_key_with_another_nodes_internal_id(tmp_path):
    out = tmp_path / "out.txt"
    write_edgelist(_graph_with_a_key_colliding_internal_id(), out, attrs=["weight"])
    assert open(out).read().splitlines() == ["a b 1.0"]


# ---- edge_attr selects t_start/t_end independently, not as a pair gated on "t_start" alone ------------------------


def test_to_pandas_edgelist_edge_attr_selects_t_start_and_t_end_independently():
    from plexgraph_core.model.ir import Graph

    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, t_start=1.0, t_end=5.0)

    only_end = to_pandas_edgelist(g, edge_attr=["t_end"])
    assert "t_end" in only_end.columns and "t_start" not in only_end.columns
    assert only_end.iloc[0]["t_end"] == 5.0

    only_start = to_pandas_edgelist(g, edge_attr=["t_start"])
    assert "t_start" in only_start.columns and "t_end" not in only_start.columns


# ---- a present-but-empty trailing attribute must not be confused with a missing (trimmed) one --------------------


def test_write_edgelist_a_trailing_empty_string_value_is_written_not_dropped(tmp_path):
    from plexgraph_core.model.ir import Graph

    # A single connector whose LAST requested attr is present but happens to be "": before the fix this rendered
    # identically to "attr entirely absent" and was trimmed away, silently shortening the row.
    g = Graph()
    a, b = g.add_node("a"), g.add_node("b")
    g.add_edge(a, b, weight=1.5, color="")

    out = tmp_path / "out.csv"
    write_edgelist(g, out, delimiter=",", attrs=["weight", "color"])
    assert open(out).read().splitlines() == ["a,b,1.5,"]  # trailing "," is the present (empty) color, not trimmed

    back = read_edgelist(out, delimiter=",", attrs={"weight": 2, "color": 3})
    assert back.connector(0).attrs["color"] == ""

    # Contrast: an attr genuinely absent (not just empty) on the LAST row is still trimmed, as before.
    g2 = Graph()
    c, d = g2.add_node("c"), g2.add_node("d")
    g2.add_edge(c, d, weight=2.0)
    out2 = tmp_path / "out2.csv"
    write_edgelist(g2, out2, delimiter=",", attrs=["weight", "color"])
    assert open(out2).read().splitlines() == ["c,d,2.0"]


# ---- the default attrs=["weight"] decision, and directedness/export, ignore hyperedges that get skipped ----------


def test_write_edgelist_default_attrs_ignores_a_weighted_hyperedge(tmp_path):
    from plexgraph_core.model.ir import Graph

    g = Graph()
    for i in range(4):
        g.add_node(i)
    g.add_edge(0, 1)  # unweighted
    g.add_hyperedge([0, 1, 2, 3], weight=5.0)  # the only weighted connector, but it's skipped
    out = tmp_path / "out.txt"
    with pytest.warns(UserWarning):
        write_edgelist(g, out)
    assert open(out).read().splitlines() == ["0 1"]  # no spurious weight column


# ---- a non-numeric weight column is rejected with a clear, located error, not stored as the wrong type -----------


def test_read_edgelist_non_numeric_weight_raises_a_clear_error(tmp_path):
    f = tmp_path / "edges.txt"
    f.write_text("a b N/A\n")
    with pytest.raises(ValueError, match=r"edges\.txt:1.*weight must be a number"):
        read_edgelist(f, attrs={"weight": 2})


# ---- node_key text coercion: a crash on multiple leading minus signs, and the true (asymmetric) int64 range ------


def test_read_edgelist_a_double_leading_minus_stays_text_instead_of_crashing(tmp_path):
    f = tmp_path / "edges.txt"
    f.write_text("a --5\n")
    g = read_edgelist(f)
    assert g.node("--5") is not None  # did not raise, and stayed text rather than being misparsed


def test_read_edgelist_the_true_int64_minimum_becomes_an_int(tmp_path):
    f = tmp_path / "edges.txt"
    f.write_text(f"a {-(2**63)}\n")
    g = read_edgelist(f)
    assert g.node(-(2**63)) is not None
