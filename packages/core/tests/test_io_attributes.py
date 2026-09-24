import pandas as pd
import pytest

from plexgraph_core.io import read_edgelist, read_node_attributes, read_pandas_node_attributes
from plexgraph_core.model.ir import Graph


def _edge_graph() -> Graph:
    g = Graph()
    for key in ("alice", "bob", "carol"):
        g.add_node(key)
    g.add_edge("alice", "bob")
    g.add_edge("bob", "carol")
    return g


# ---- read_node_attributes --------------------------------------------------


def test_header_row_names_the_attribute_columns_automatically(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category,importance\nalice,staff,10\nbob,guest,3\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    assert g.node("alice").attrs == {"category": "staff", "importance": 10}
    assert g.node("bob").attrs == {"category": "guest", "importance": 3}
    assert g.node("carol").attrs == {}  # never mentioned in the table


def test_explicit_attrs_mapping_overrides_header_auto_detection(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category,importance\nalice,staff,10\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",", attrs={"importance": 2})
    assert g.node("alice").attrs == {"importance": 10}  # category (column 1) not requested


def test_attribute_value_coercion(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,a,b,c\nalice,3,2.5,text\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    attrs = g.node("alice").attrs
    assert attrs == {"a": 3, "b": 2.5, "c": "text"}
    assert isinstance(attrs["a"], int) and isinstance(attrs["b"], float)


def test_create_missing_true_by_default_adds_a_new_node(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\nalice,staff\ndave,staff\n")  # dave has no edge
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    assert g.num_nodes == 4
    assert g.node("dave").attrs == {"category": "staff"}


def test_create_missing_false_skips_unknown_nodes(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\nalice,staff\ndave,staff\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",", create_missing=False)
    assert g.num_nodes == 3
    assert g.node("alice").attrs == {"category": "staff"}


def test_key_column_can_be_something_other_than_the_first(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("category,id\nstaff,alice\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",", key_column=1)
    assert g.node("alice").attrs == {"category": "staff"}


def test_layering_two_separate_attribute_tables_merges_rather_than_replaces(tmp_path):
    # A realistic reason to call this more than once: separate lookup tables (category from one system,
    # importance from another) both enriching the same graph. The second call must not erase the first's work.
    categories = tmp_path / "categories.csv"
    categories.write_text("id,category\nalice,staff\n")
    importance = tmp_path / "importance.csv"
    importance.write_text("id,importance\nalice,10\n")
    g = _edge_graph()
    read_node_attributes(g, categories, delimiter=",")
    read_node_attributes(g, importance, delimiter=",")
    assert g.node("alice").attrs == {"category": "staff", "importance": 10}


def test_an_empty_csv_field_is_a_real_empty_value_and_does_overwrite(tmp_path):
    # Distinct from the above: within a SINGLE table, an empty field is a value (an empty string), not an
    # instruction to leave the attribute alone -- the same as how any CSV reader treats a blank cell.
    f = tmp_path / "nodes.csv"
    f.write_text("id,a\nalice,1\nalice,\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    assert g.node("alice").attrs == {"a": ""}


def test_returns_and_mutates_the_same_graph_so_it_composes(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\nalice,staff\n")
    g = _edge_graph()
    result = read_node_attributes(g, f, delimiter=",")
    assert result is g


def test_composes_directly_with_read_edgelist(tmp_path):
    edges = tmp_path / "edges.csv"
    edges.write_text("source,target\nalice,bob\n")
    nodes = tmp_path / "nodes.csv"
    nodes.write_text("id,category\nalice,staff\nbob,guest\n")
    g = read_node_attributes(read_edgelist(edges, delimiter=",", header=True), nodes, delimiter=",")
    assert g.node("alice").attrs == {"category": "staff"}
    assert g.node("bob").attrs == {"category": "guest"}


def test_comments_and_blank_lines_are_skipped(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\n# a comment\nalice,staff\n\nbob,guest\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    assert g.node("alice").attrs == {"category": "staff"}
    assert g.node("bob").attrs == {"category": "guest"}


def test_int_nodes_coercion_matches_int_keyed_nodes(tmp_path):
    g = Graph()
    g.add_node(1)
    g.add_node(2)
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\n1,staff\n")
    read_node_attributes(g, f, delimiter=",")
    assert g.node(1).attrs == {"category": "staff"}  # "1" from the file matched the int key 1


def test_header_false_with_no_explicit_attrs_keeps_every_node_unenriched(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("alice,staff\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",", header=False)
    # no header to name columns from, and no explicit attrs given: nothing to set, but no crash either
    assert g.node("alice").attrs == {}


def test_key_column_out_of_range_for_the_header_is_a_clear_error(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\nalice,staff\n")
    with pytest.raises(ValueError, match="key_column 5 is out of range"):
        read_node_attributes(_edge_graph(), f, delimiter=",", key_column=5)


def test_row_too_short_for_key_column_reports_file_and_line(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\nalice\n")  # missing the category value entirely
    with pytest.raises(ValueError, match=r"nodes\.csv:2.*expected at least"):
        read_node_attributes(_edge_graph(), f, delimiter=",")


def test_empty_file_after_the_header_adds_nothing(tmp_path):
    f = tmp_path / "nodes.csv"
    f.write_text("id,category\n")
    g = read_node_attributes(_edge_graph(), f, delimiter=",")
    assert g.num_nodes == 3
    assert g.node("alice").attrs == {}


# ---- read_pandas_node_attributes -------------------------------------------


def test_pandas_basic():
    df = pd.DataFrame({"id": ["alice", "bob"], "category": ["staff", "guest"]})
    g = read_pandas_node_attributes(_edge_graph(), df)
    assert g.node("alice").attrs == {"category": "staff"}
    assert g.node("bob").attrs == {"category": "guest"}


def test_pandas_attrs_true_takes_every_other_column():
    df = pd.DataFrame({"id": ["alice"], "category": ["staff"], "importance": [10]})
    g = read_pandas_node_attributes(_edge_graph(), df)
    assert g.node("alice").attrs == {"category": "staff", "importance": 10}


def test_pandas_attrs_selection():
    df = pd.DataFrame({"id": ["alice"], "category": ["staff"], "importance": [10]})
    only = read_pandas_node_attributes(_edge_graph(), df, attrs="category")
    assert only.node("alice").attrs == {"category": "staff"}
    none = read_pandas_node_attributes(_edge_graph(), df, attrs=False)
    assert none.node("alice").attrs == {}
    none2 = read_pandas_node_attributes(_edge_graph(), df, attrs=None)
    assert none2.node("alice").attrs == {}
    several = read_pandas_node_attributes(_edge_graph(), df, attrs=["category", "importance"])
    assert several.node("alice").attrs == {"category": "staff", "importance": 10}


def test_pandas_custom_key_column():
    df = pd.DataFrame({"node": ["alice"], "category": ["staff"]})
    g = read_pandas_node_attributes(_edge_graph(), df, key="node")
    assert g.node("alice").attrs == {"category": "staff"}


def test_pandas_create_missing_default_true_and_false():
    df = pd.DataFrame({"id": ["dave"], "category": ["staff"]})
    added = read_pandas_node_attributes(_edge_graph(), df)
    assert added.num_nodes == 4

    skipped = read_pandas_node_attributes(_edge_graph(), df, create_missing=False)
    assert skipped.num_nodes == 3


def test_pandas_returns_and_mutates_the_same_graph():
    df = pd.DataFrame({"id": ["alice"], "category": ["staff"]})
    g = _edge_graph()
    assert read_pandas_node_attributes(g, df) is g


def test_pandas_a_string_id_matches_an_int_keyed_node_like_read_node_attributes_does():
    # An int-keyed node (as read_edgelist's default int_nodes=True produces) enriched from a DataFrame whose id
    # column is text (e.g. read with dtype=str) must still match -- not silently create a duplicate node.
    g = Graph()
    g.add_node(1)
    df = pd.DataFrame({"id": ["1"], "category": ["staff"]})
    read_pandas_node_attributes(g, df)
    assert g.num_nodes == 1
    assert g.node(1).attrs == {"category": "staff"}


def test_pandas_int_nodes_false_leaves_a_string_id_as_text():
    g = Graph()
    g.add_node(1)
    df = pd.DataFrame({"id": ["1"], "category": ["staff"]})
    g2 = read_pandas_node_attributes(g, df, int_nodes=False)
    assert g2.num_nodes == 2  # "1" (text) is a different node from 1 (int)
    assert g2.node("1").attrs == {"category": "staff"}


def test_pandas_an_already_numeric_id_column_is_used_as_is():
    # pandas hands back a real int/float for a numeric dtype column already -- int_nodes must not re-parse it as
    # text (which could, in principle, behave differently for a value like a numpy int64).
    g = Graph()
    g.add_node(1)
    df = pd.DataFrame({"id": [1], "category": ["staff"]})  # int64 column, not text
    read_pandas_node_attributes(g, df)
    assert g.num_nodes == 1
    assert g.node(1).attrs == {"category": "staff"}
