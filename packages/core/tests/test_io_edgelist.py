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
