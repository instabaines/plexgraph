import re

import pytest

from plexgraph_core.io.dot import DOTParseError, read_dot


def test_the_canonical_hello_world_example(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { Hello -> World; }")
    g = read_dot(f)
    assert g.num_nodes == 2 and g.num_edges == 1
    assert g.connector(0).directed
    assert g.connector(0).endpoints == (g.node("Hello").id, g.node("World").id)


def test_graph_keyword_means_undirected(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("graph G { a -- b; }")
    g = read_dot(f)
    assert not g.connector(0).directed


def test_strict_keyword_is_accepted(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("strict digraph G { a -> b; }")
    g = read_dot(f)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_directed_argument_overrides_the_files_own_keyword(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("graph G { a -- b; }")
    g = read_dot(f, directed=True)
    assert g.connector(0).directed


def test_anonymous_graph_name_is_optional(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph { a -> b; }")
    g = read_dot(f)
    assert g.num_nodes == 2


# ---- node/edge statements, chains, attribute lists -------------------------------------------------


def test_a_node_declared_alone_with_no_edges(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a [color="red"]; }')
    g = read_dot(f)
    assert g.num_nodes == 1 and g.num_edges == 0
    assert g.node("a").attrs["color"] == "red"


def test_an_edge_chain_expands_to_pairwise_edges_sharing_attrs(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b -> c [color="blue"]; }')
    g = read_dot(f)
    assert g.num_nodes == 3 and g.num_edges == 2
    assert all(c.attrs["color"] == "blue" for c in g.connectors())


def test_multiple_bracket_groups_after_one_statement_merge(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b [color="red"] [style="bold"]; }')
    g = read_dot(f)
    assert g.connector(0).attrs == {"color": "red", "style": "bold"}


def test_re_declaring_a_node_merges_attributes_instead_of_raising(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a [color="red"]; a [shape="box"]; }')
    g = read_dot(f)
    assert g.num_nodes == 1
    assert g.node("a").attrs == {"color": "red", "shape": "box"}


def test_a_node_only_mentioned_as_an_edge_endpoint_is_auto_created(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a -> b; }")
    g = read_dot(f)
    assert g.node("b") is not None


def test_self_loops_are_supported(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a -> a; }")
    g = read_dot(f)
    assert g.num_edges == 1
    assert g.connector(0).endpoints == (g.node("a").id, g.node("a").id)


# ---- attribute default scoping: node/edge/graph [...] statements -----------------------------------


def test_node_default_attribute_applies_to_nodes_declared_afterward(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { node [color="red"]; a; b; }')
    g = read_dot(f)
    assert g.node("a").attrs["color"] == "red"
    assert g.node("b").attrs["color"] == "red"


def test_node_default_does_not_apply_retroactively(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a; node [color="red"]; b; }')
    g = read_dot(f)
    assert "color" not in g.node("a").attrs
    assert g.node("b").attrs["color"] == "red"


def test_an_explicit_node_attribute_overrides_the_default(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { node [color="red"]; a [color="blue"]; }')
    g = read_dot(f)
    assert g.node("a").attrs["color"] == "blue"


def test_edge_default_attribute_applies_to_edges_declared_afterward(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { edge [weight="2"]; a -> b; }')
    g = read_dot(f)
    assert g.connector(0).weight == 2.0


def test_graph_attribute_statement_is_discarded_without_error(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { graph [rankdir="LR"]; a -> b; }')
    g = read_dot(f)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_top_level_id_equals_id_assignment_is_discarded_without_error(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { rankdir=LR; a -> b; }")
    g = read_dot(f)
    assert g.num_nodes == 2 and g.num_edges == 1


# ---- subgraphs: flattened, defaults copied at definition time, not leaked back out -----------------


def test_subgraph_nodes_and_edges_are_flattened_into_the_main_graph(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { subgraph cluster_0 { a; b; a -> b; } c; }")
    g = read_dot(f)
    assert g.num_nodes == 3 and g.num_edges == 1


def test_anonymous_subgraph_is_also_flattened(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { { a; b; } c; }")
    g = read_dot(f)
    assert g.num_nodes == 3


def test_subgraph_inherits_defaults_at_definition_time(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { node [color="red"]; subgraph s { a; } }')
    g = read_dot(f)
    assert g.node("a").attrs["color"] == "red"


def test_subgraph_default_changes_do_not_leak_back_to_the_parent_scope(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { subgraph s { node [color="red"]; a; } b; }')
    g = read_dot(f)
    assert g.node("a").attrs["color"] == "red"
    assert "color" not in g.node("b").attrs


def test_subgraph_used_as_an_edge_endpoint_raises(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { { a; b; } -> c; }")
    with pytest.raises(DOTParseError, match="subgraph used as an edge endpoint"):
        read_dot(f)


# ---- strings, comments, ports, HTML-like labels -----------------------------------------------------


def test_quoted_strings_support_concatenation(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a [label="hello " + "world"]; }')
    g = read_dot(f)
    assert g.node("a").attrs["label"] == "hello world"


def test_backslash_escaped_quote_is_read_as_a_literal_quote(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text(r'digraph G { a [label="say \"hi\""]; }')
    g = read_dot(f)
    assert g.node("a").attrs["label"] == 'say "hi"'


def test_other_backslash_sequences_pass_through_unchanged(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text(r'digraph G { a [label="line1\nline2"]; }')
    g = read_dot(f)
    assert g.node("a").attrs["label"] == "line1\\nline2"


def test_line_and_block_comments_are_skipped(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text(
        "// a leading comment\n"
        "digraph G {\n"
        "  a -> b; # a hash comment\n"
        "  /* a block\n     comment */\n"
        "  b -> c;\n"
        "}\n"
    )
    g = read_dot(f)
    assert g.num_nodes == 3 and g.num_edges == 2


def test_ports_are_stripped_and_do_not_affect_node_identity(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a:port1 -> b:port2:n; }")
    g = read_dot(f)
    assert g.num_nodes == 2
    assert g.node("a") is not None and g.node("b") is not None


def test_an_html_like_label_is_kept_as_one_opaque_string(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a [label=<<B>bold</B>>]; }")
    g = read_dot(f)
    assert g.node("a").attrs["label"] == "<<B>bold</B>>"


# ---- weight/layer/t_start/t_end convention mapping --------------------------------------------------


def test_weight_key_is_read_as_the_connectors_weight(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b [weight="2.5"]; }')
    g = read_dot(f)
    assert g.connector(0).weight == 2.5


def test_a_non_numeric_weight_raises_clearly(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b [weight="not-a-number"]; }')
    with pytest.raises(DOTParseError, match="not a number"):
        read_dot(f)


def test_layer_key_creates_layers_and_assigns_edges_to_them(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b [rel="friend"]; b -> c [rel="coworker"]; }')
    g = read_dot(f, layer_key="rel")
    layer_keys = {l.key for l in g.layers()}
    assert layer_keys == {"friend", "coworker"}
    assert g.connector(0).layer_id != g.connector(1).layer_id


def test_t_start_and_t_end_keys_are_read_as_temporal_bounds(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a -> b [start="3" end="7"]; }')
    g = read_dot(f, t_start_key="start", t_end_key="end")
    assert g.connector(0).t_start == 3 and g.connector(0).t_end == 7


def test_no_layer_or_time_keys_configured_reads_a_plain_static_graph(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a -> b; }")
    g = read_dot(f)
    assert g.connector(0).layer_id is None
    assert g.connector(0).is_always_present


# ---- node identity: id by default, label with label_as_key=True ------------------------------------


def test_label_as_key_uses_the_label_instead(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a [label="alice"]; b [label="bob"]; a -> b; }')
    g = read_dot(f, label_as_key=True)
    assert g.node("alice") is not None and g.node("bob") is not None


def test_label_as_key_raises_on_a_missing_label(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a; }")
    with pytest.raises(DOTParseError, match="no 'label'"):
        read_dot(f, label_as_key=True)


def test_label_as_key_raises_on_a_duplicate_label(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text('digraph G { a [label="x"]; b [label="x"]; }')
    with pytest.raises(DOTParseError, match="not unique"):
        read_dot(f, label_as_key=True)


# ---- errors are clear and located --------------------------------------------------------------------


def test_missing_graph_keyword_raises(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("{ a -> b; }")
    with pytest.raises(DOTParseError, match="expected 'graph' or 'digraph'"):
        read_dot(f)


def test_unterminated_brace_raises(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a -> b;")
    with pytest.raises(DOTParseError, match="unterminated"):
        read_dot(f)


def test_malformed_input_raises_with_the_path(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G { a -> ; }")
    with pytest.raises(DOTParseError, match=re.escape(str(f))):
        read_dot(f)


def test_an_unrecognized_character_raises_with_a_line_number(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G {\n  a -> b @ c;\n}")
    with pytest.raises(DOTParseError, match="line 2"):
        read_dot(f)


def test_an_empty_graph_reads_as_empty(tmp_path):
    f = tmp_path / "g.dot"
    f.write_text("digraph G {}")
    g = read_dot(f)
    assert g.num_nodes == 0 and g.num_edges == 0
