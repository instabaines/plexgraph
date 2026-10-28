import pytest

from plexgraph_core.io.gml import GMLParseError, read_gml


def test_the_canonical_wikipedia_example(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text(
        """
        graph [
          comment "This is a sample graph"
          directed 1
          id 42
          label "Hello, I am a graph"
          node [ id 1 label "node 1" thisIsASampleAttribute 42 ]
          node [ id 2 label "node 2" thisIsASampleAttribute 43 ]
          node [ id 3 label "node 3" thisIsASampleAttribute 44 ]
          edge [ source 1 target 2 label "Edge from node 1 to node 2" ]
          edge [ source 2 target 3 label "Edge from node 2 to node 3" ]
          edge [ source 3 target 1 label "Edge from node 3 to node 1" ]
        ]
        """
    )
    g = read_gml(f)
    assert g.num_nodes == 3 and g.num_edges == 3
    assert g.node(1).attrs == {"label": "node 1", "thisIsASampleAttribute": 42}
    assert g.connector(0).directed
    assert g.connector(0).endpoints == (g.node(1).id, g.node(2).id)


def test_directed_graph_level_key_controls_connector_direction(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ directed 0 node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 ] ]")
    g = read_gml(f)
    assert not g.connector(0).directed


def test_directed_argument_overrides_the_files_own_key(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ directed 0 node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 ] ]")
    g = read_gml(f, directed=True)
    assert g.connector(0).directed


def test_directed_defaults_to_false_when_the_key_is_absent(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 ] ]")
    g = read_gml(f)
    assert not g.connector(0).directed


# ---- lenient quoting: both of GML's real-world conventions, plus the formal one -----------------


def test_backslash_escaped_quote_is_read_as_a_literal_quote(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text(r'graph [ node [ id 1 label "say \"hi\"" ] ]')
    g = read_gml(f)
    assert g.node(1).attrs["label"] == 'say "hi"'


def test_entity_escaped_quote_and_ampersand_are_decoded(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 label "&quot;hi&quot; &amp; bye" ] ]')
    g = read_gml(f)
    assert g.node(1).attrs["label"] == '"hi" & bye'


def test_numeric_character_reference_is_decoded(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 label "caf&#233;" ] ]')  # &#233; = e with acute accent
    g = read_gml(f)
    assert g.node(1).attrs["label"] == "café"


def test_a_string_may_contain_a_literal_newline(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 label "line one\nline two" ] ]')
    g = read_gml(f)
    assert g.node(1).attrs["label"] == "line one\nline two"


# ---- comments and lenient key names --------------------------------------------------------------


def test_full_line_and_trailing_comments_are_skipped(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text(
        "# a leading comment\n"
        "graph [\n"
        "  node [ id 1 ]  # trailing comment\n"
        "  # another full-line comment\n"
        "  node [ id 2 ]\n"
        "]\n"
    )
    g = read_gml(f)
    assert g.num_nodes == 2


def test_a_hash_inside_a_quoted_string_is_not_a_comment(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 label "not # a comment" ] ]')
    g = read_gml(f)
    assert g.node(1).attrs["label"] == "not # a comment"


def test_underscored_keys_are_accepted(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 node_type 7 ] ]")
    g = read_gml(f)
    assert g.node(1).attrs["node_type"] == 7


# ---- node identity: id by default, label with label_as_key=True -----------------------------------


def test_node_key_is_the_gml_id_by_default(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 7 label "seven" ] ]')
    g = read_gml(f)
    assert g.node(7) is not None


def test_label_as_key_uses_the_label_instead(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 7 label "alice" ] node [ id 8 label "bob" ] edge [ source 7 target 8 ] ]')
    g = read_gml(f, label_as_key=True)
    assert g.node("alice") is not None and g.node("bob") is not None


def test_label_as_key_raises_on_a_missing_label(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] ]")
    with pytest.raises(GMLParseError, match="no 'label'"):
        read_gml(f, label_as_key=True)


def test_label_as_key_raises_on_a_duplicate_label(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 label "x" ] node [ id 2 label "x" ] ]')
    with pytest.raises(GMLParseError, match="not unique"):
        read_gml(f, label_as_key=True)


# ---- weight/layer/t_start/t_end convention mapping -------------------------------------------------


def test_weight_key_is_read_as_the_connectors_weight(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 weight 2.5 ] ]")
    g = read_gml(f, weight_key="weight")
    assert g.connector(0).weight == 2.5


def test_a_custom_weight_key_name_is_supported(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 value 7.0 ] ]")
    g = read_gml(f, weight_key="value")
    assert g.connector(0).weight == 7.0


def test_literal_weight_attribute_is_dropped_not_collided_when_a_different_weight_key_is_set(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 weight 2.5 value 7.0 ] ]")
    g = read_gml(f, weight_key="value")  # must not raise ("weight" would collide with add_edge's own kwarg)
    assert g.connector(0).weight == 7.0
    assert "weight" not in g.connector(0).attrs


def test_layer_key_creates_layers_and_assigns_edges_to_them(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text(
        "graph [ node [ id 1 ] node [ id 2 ] node [ id 3 ]"
        ' edge [ source 1 target 2 rel "friend" ]'
        ' edge [ source 2 target 3 rel "coworker" ]'
        " ]"
    )
    g = read_gml(f, layer_key="rel")
    layer_keys = {l.key for l in g.layers()}
    assert layer_keys == {"friend", "coworker"}
    assert g.connector(0).layer_id != g.connector(1).layer_id


def test_t_start_and_t_end_keys_are_read_as_temporal_bounds(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 start 3 end 7 ] ]")
    g = read_gml(f, t_start_key="start", t_end_key="end")
    assert g.connector(0).t_start == 3 and g.connector(0).t_end == 7


def test_no_layer_or_time_keys_configured_reads_a_plain_static_graph(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ id 1 ] node [ id 2 ] edge [ source 1 target 2 rel "friend" ] ]')
    g = read_gml(f)
    assert g.connector(0).layer_id is None
    assert g.connector(0).is_always_present
    assert g.connector(0).attrs["rel"] == "friend"  # kept as a plain attribute, not interpreted


# ---- errors are clear and located -------------------------------------------------------------------


def test_missing_graph_block_raises(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("node [ id 1 ]")
    with pytest.raises(GMLParseError, match="no top-level 'graph' key"):
        read_gml(f)


def test_node_without_id_raises(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text('graph [ node [ label "no id" ] ]')
    with pytest.raises(GMLParseError, match="no 'id'"):
        read_gml(f)


def test_edge_referencing_an_undefined_node_raises(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ] edge [ source 1 target 999 ] ]")
    with pytest.raises(GMLParseError, match="not defined"):
        read_gml(f)


def test_unterminated_bracket_raises_with_a_message(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [ node [ id 1 ]")
    with pytest.raises(GMLParseError, match="unterminated"):
        read_gml(f)


def test_unrecognized_character_raises_with_a_line_number(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("graph [\n  node [ id 1 @ ]\n]")
    with pytest.raises(GMLParseError, match="line 2"):
        read_gml(f)


def test_error_message_includes_the_file_path(tmp_path):
    f = tmp_path / "g.gml"
    f.write_text("not valid gml [[[")
    with pytest.raises(GMLParseError, match=str(f)):
        read_gml(f)


# ---- duplicate keys at the same level are legal in GML; order is preserved --------------------------


def test_many_nodes_and_edges_are_all_read_in_file_order(tmp_path):
    f = tmp_path / "g.gml"
    lines = ["graph ["]
    for i in range(5):
        lines.append(f"  node [ id {i} ]")
    for i in range(4):
        lines.append(f"  edge [ source {i} target {i + 1} ]")
    lines.append("]")
    f.write_text("\n".join(lines))
    g = read_gml(f)
    assert g.num_nodes == 5 and g.num_edges == 4
    assert [c.endpoints for c in g.connectors()] == [(i, i + 1) for i in range(4)]
