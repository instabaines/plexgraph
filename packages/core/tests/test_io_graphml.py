import re

import pytest

from plexgraph_core.io.graphml import GraphMLParseError, read_graphml


def test_the_canonical_hello_world_example(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <graphml xmlns="http://graphml.graphdrawing.org/xmlns">
          <graph id="G" edgedefault="undirected">
            <node id="0"/>
            <node id="1"/>
            <edge id="e0" source="0" target="1"/>
          </graph>
        </graphml>
        """
    )
    g = read_graphml(f)
    assert g.num_nodes == 2 and g.num_edges == 1
    assert not g.connector(0).directed
    assert g.connector(0).endpoints == (g.node("0").id, g.node("1").id)


# ---- direction: graph-level default, per-edge override --------------------------------------------


def test_edgedefault_undirected_is_honored(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph edgedefault="undirected"><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1"/></graph></graphml>')
    g = read_graphml(f)
    assert not g.connector(0).directed


def test_edgedefault_directed_is_honored(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph edgedefault="directed"><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1"/></graph></graphml>')
    g = read_graphml(f)
    assert g.connector(0).directed


def test_missing_edgedefault_defaults_to_undirected(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1"/></graph></graphml>')
    g = read_graphml(f)
    assert not g.connector(0).directed


def test_directed_argument_overrides_the_files_own_default(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph edgedefault="undirected"><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1"/></graph></graphml>')
    g = read_graphml(f, directed=True)
    assert g.connector(0).directed


def test_a_per_edge_directed_attribute_overrides_the_graph_default(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph edgedefault="undirected"><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1" directed="true"/></graph></graphml>')
    g = read_graphml(f)
    assert g.connector(0).directed


def test_per_edge_directed_accepts_0_and_1(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph edgedefault="directed"><node id="0"/><node id="1"/>'
                 '<edge source="0" target="1" directed="0"/></graph></graphml>')
    g = read_graphml(f)
    assert not g.connector(0).directed


# ---- key/data: declarations, type coercion, defaults, undeclared references -----------------------


def test_declared_key_types_are_coerced(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml>'
        '<key id="d0" for="node" attr.name="age" attr.type="int"/>'
        '<key id="d1" for="node" attr.name="active" attr.type="boolean"/>'
        '<graph edgedefault="undirected">'
        '<node id="0"><data key="d0">42</data><data key="d1">false</data></node>'
        "</graph></graphml>"
    )
    g = read_graphml(f)
    assert g.node("0").attrs["age"] == 42
    assert g.node("0").attrs["active"] is False


def test_boolean_false_string_is_not_truthy(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="flag" attr.type="boolean"/>'
        '<graph><node id="0"><data key="d0">false</data></node></graph></graphml>'
    )
    g = read_graphml(f)
    assert g.node("0").attrs["flag"] is False


def test_a_missing_data_element_falls_back_to_the_declared_default(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="age" attr.type="int"><default>0</default></key>'
        '<graph><node id="0"/></graph></graphml>'
    )
    g = read_graphml(f)
    assert g.node("0").attrs["age"] == 0


def test_a_data_element_referencing_an_undeclared_key_raises(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node id="0"><data key="bogus">x</data></node></graph></graphml>')
    with pytest.raises(GraphMLParseError, match="undeclared key"):
        read_graphml(f)


def test_for_all_key_applies_to_both_nodes_and_edges(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="all" attr.name="note" attr.type="string"/>'
        '<graph><node id="0"><data key="d0">on a node</data></node>'
        '<node id="1"/>'
        '<edge source="0" target="1"><data key="d0">on an edge</data></edge>'
        "</graph></graphml>"
    )
    g = read_graphml(f)
    assert g.node("0").attrs["note"] == "on a node"
    assert g.connector(0).attrs["note"] == "on an edge"


def test_a_key_declared_for_edge_only_is_not_applied_to_nodes(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="edge" attr.name="relation" attr.type="string"/>'
        '<graph><node id="0"/><node id="1"/>'
        '<edge source="0" target="1"><data key="d0">friend</data></edge>'
        "</graph></graphml>"
    )
    g = read_graphml(f)
    assert "relation" not in g.node("0").attrs
    assert g.connector(0).attrs["relation"] == "friend"


# ---- weight/layer/t_start/t_end convention mapping -------------------------------------------------


def test_weight_key_is_read_as_the_connectors_weight(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="edge" attr.name="weight" attr.type="double"/>'
        '<graph><node id="0"/><node id="1"/>'
        '<edge source="0" target="1"><data key="d0">2.5</data></edge></graph></graphml>'
    )
    g = read_graphml(f)
    assert g.connector(0).weight == 2.5


def test_a_custom_weight_key_name_is_supported(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="edge" attr.name="value" attr.type="double"/>'
        '<graph><node id="0"/><node id="1"/>'
        '<edge source="0" target="1"><data key="d0">7.0</data></edge></graph></graphml>'
    )
    g = read_graphml(f, weight_key="value")
    assert g.connector(0).weight == 7.0


def test_layer_key_creates_layers_and_assigns_edges_to_them(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="edge" attr.name="rel" attr.type="string"/>'
        '<graph><node id="0"/><node id="1"/><node id="2"/>'
        '<edge source="0" target="1"><data key="d0">friend</data></edge>'
        '<edge source="1" target="2"><data key="d0">coworker</data></edge>'
        "</graph></graphml>"
    )
    g = read_graphml(f, layer_key="rel")
    layer_keys = {l.key for l in g.layers()}
    assert layer_keys == {"friend", "coworker"}
    assert g.connector(0).layer_id != g.connector(1).layer_id


def test_t_start_and_t_end_keys_are_read_as_temporal_bounds(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="edge" attr.name="start" attr.type="double"/>'
        '<key id="d1" for="edge" attr.name="end" attr.type="double"/>'
        '<graph><node id="0"/><node id="1"/>'
        '<edge source="0" target="1"><data key="d0">3</data><data key="d1">7</data></edge>'
        "</graph></graphml>"
    )
    g = read_graphml(f, t_start_key="start", t_end_key="end")
    assert g.connector(0).t_start == 3 and g.connector(0).t_end == 7


def test_no_layer_or_time_keys_configured_reads_a_plain_static_graph(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node id="0"/><node id="1"/><edge source="0" target="1"/></graph></graphml>')
    g = read_graphml(f)
    assert g.connector(0).layer_id is None
    assert g.connector(0).is_always_present


# ---- node identity: id by default, label with label_as_key=True -----------------------------------


def test_node_key_is_the_graphml_id_by_default(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="label" attr.type="string"/>'
        '<graph><node id="7"><data key="d0">seven</data></node></graph></graphml>'
    )
    g = read_graphml(f)
    assert g.node("7") is not None
    assert g.node("7").attrs["label"] == "seven"


def test_label_as_key_uses_the_label_instead(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="label" attr.type="string"/>'
        '<graph><node id="7"><data key="d0">alice</data></node>'
        '<node id="8"><data key="d0">bob</data></node>'
        '<edge source="7" target="8"/></graph></graphml>'
    )
    g = read_graphml(f, label_as_key=True)
    assert g.node("alice") is not None and g.node("bob") is not None


def test_label_as_key_raises_on_a_missing_label(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node id="1"/></graph></graphml>')
    with pytest.raises(GraphMLParseError, match="no 'label'"):
        read_graphml(f, label_as_key=True)


def test_label_as_key_raises_on_a_duplicate_label(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="label" attr.type="string"/>'
        '<graph><node id="1"><data key="d0">x</data></node>'
        '<node id="2"><data key="d0">x</data></node></graph></graphml>'
    )
    with pytest.raises(GraphMLParseError, match="not unique"):
        read_graphml(f, label_as_key=True)


# ---- things real files have that this reader deliberately ignores --------------------------------


def test_a_second_top_level_graph_is_ignored(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><graph id="G1"><node id="0"/><node id="1"/><edge source="0" target="1"/></graph>'
        '<graph id="G2"><node id="x"/></graph></graphml>'
    )
    g = read_graphml(f)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_a_data_element_with_xml_children_is_skipped_not_misparsed(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text(
        '<graphml><key id="d0" for="node" attr.name="shape" attr.type="string"/>'
        '<graph><node id="0"><data key="d0"><y:ShapeNode xmlns:y="urn:y"/></data></node></graph></graphml>'
    )
    g = read_graphml(f)
    assert "shape" not in g.node("0").attrs


# ---- errors are clear and located -------------------------------------------------------------------


def test_missing_graph_element_raises(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text("<graphml></graphml>")
    with pytest.raises(GraphMLParseError, match="no 'graph' element"):
        read_graphml(f)


def test_node_without_id_raises(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node/></graph></graphml>')
    with pytest.raises(GraphMLParseError, match="no 'id'"):
        read_graphml(f)


def test_edge_referencing_an_undefined_node_raises(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text('<graphml><graph><node id="0"/><edge source="0" target="999"/></graph></graphml>')
    with pytest.raises(GraphMLParseError, match="not defined"):
        read_graphml(f)


def test_malformed_xml_raises_with_the_path(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text("<graphml><graph><node id=0></graph>")
    with pytest.raises(GraphMLParseError, match=re.escape(str(f))):
        read_graphml(f)


def test_a_graph_with_no_nodes_or_edges_reads_as_empty(tmp_path):
    f = tmp_path / "g.graphml"
    f.write_text("<graphml><graph/></graphml>")
    g = read_graphml(f)
    assert g.num_nodes == 0 and g.num_edges == 0
