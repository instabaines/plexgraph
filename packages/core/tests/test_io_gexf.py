import re

import pytest

from plexgraph_core.io.gexf import GEXFParseError, read_gexf


def test_the_canonical_hello_world_example(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <gexf xmlns="http://gexf.net/1.3" version="1.3">
          <graph mode="static" defaultedgetype="undirected">
            <nodes>
              <node id="0" label="Hello" />
              <node id="1" label="World" />
            </nodes>
            <edges>
              <edge id="0" source="0" target="1" />
            </edges>
          </graph>
        </gexf>
        """
    )
    g = read_gexf(f)
    assert g.num_nodes == 2 and g.num_edges == 1
    assert g.node("0").attrs == {"label": "Hello"}
    assert not g.connector(0).directed
    assert g.connector(0).endpoints == (g.node("0").id, g.node("1").id)


# ---- direction: graph-level default, per-edge override, mutual -----------------------------------


def test_default_edge_type_absent_means_undirected(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert not g.connector(0).directed


def test_default_edge_type_directed_is_honored(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph defaultedgetype="directed"><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).directed


def test_directed_argument_overrides_the_files_own_default(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph defaultedgetype="undirected"><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1"/></edges></graph></gexf>')
    g = read_gexf(f, directed=True)
    assert g.connector(0).directed


def test_a_per_edge_type_overrides_the_graph_default(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph defaultedgetype="undirected"><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1" type="directed"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).directed


def test_mutual_edge_type_becomes_two_directed_connectors(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1" type="mutual"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.num_edges == 2
    assert all(c.directed for c in g.connectors())
    assert {c.endpoints for c in g.connectors()} == {(g.node("0").id, g.node("1").id),
                                                       (g.node("1").id, g.node("0").id)}


def test_an_unrecognized_edge_type_raises(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1" type="bogus"/></edges></graph></gexf>')
    with pytest.raises(GEXFParseError, match="unrecognized type"):
        read_gexf(f)


# ---- weight ------------------------------------------------------------------------------------


def test_weight_is_read_directly_from_the_edge_attribute(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1" weight="2.5"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).weight == 2.5


def test_weight_defaults_to_none_when_absent(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).weight is None


# ---- dynamics: single start/end, and multiple spells -------------------------------------------


def test_a_plain_start_end_on_the_edge_sets_its_temporal_window(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph mode="dynamic"><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1" start="3" end="7"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).t_start == 3 and g.connector(0).t_end == 7


def test_missing_start_end_means_always_present(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/><node id="1"/></nodes>'
                 '<edges><edge source="0" target="1"/></edges></graph></gexf>')
    g = read_gexf(f)
    assert g.connector(0).is_always_present


def test_multiple_disjoint_spells_become_multiple_connectors(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph mode="dynamic"><nodes><node id="0"/><node id="1"/></nodes>'
        '<edges><edge source="0" target="1">'
        '<spells><spell start="1" end="3"/><spell start="10" end="12"/></spells>'
        "</edge></edges></graph></gexf>"
    )
    g = read_gexf(f)
    assert g.num_edges == 2
    windows = sorted((c.t_start, c.t_end) for c in g.connectors())
    assert windows == [(1, 3), (10, 12)]
    assert all(c.endpoints == (g.node("0").id, g.node("1").id) for c in g.connectors())


def test_date_timeformat_is_parsed_to_a_consistent_numeric_timeline(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph mode="dynamic" timeformat="date"><nodes><node id="0"/><node id="1"/></nodes>'
        '<edges><edge source="0" target="1" start="2020-01-01" end="2020-01-02"/></edges></graph></gexf>'
    )
    g = read_gexf(f)
    assert g.connector(0).t_end - g.connector(0).t_start == 1.0


# ---- attributes: declarations, type coercion, defaults, undeclared references -------------------


def test_declared_attribute_types_are_coerced(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph>'
        '<attributes class="node">'
        '<attribute id="0" title="age" type="integer"/>'
        '<attribute id="1" title="active" type="boolean"/>'
        "</attributes>"
        '<nodes><node id="0">'
        '<attvalues><attvalue for="0" value="42"/><attvalue for="1" value="false"/></attvalues>'
        "</node></nodes></graph></gexf>"
    )
    g = read_gexf(f)
    assert g.node("0").attrs["age"] == 42
    assert g.node("0").attrs["active"] is False


def test_boolean_false_string_is_not_truthy(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph>'
        '<attributes class="node"><attribute id="0" title="flag" type="boolean"/></attributes>'
        '<nodes><node id="0"><attvalues><attvalue for="0" value="false"/></attvalues></node></nodes>'
        "</graph></gexf>"
    )
    g = read_gexf(f)
    assert g.node("0").attrs["flag"] is False


def test_a_missing_attvalue_falls_back_to_the_declared_default(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph>'
        '<attributes class="node">'
        '<attribute id="0" title="age" type="integer"><default>0</default></attribute>'
        "</attributes>"
        '<nodes><node id="0"/></nodes></graph></gexf>'
    )
    g = read_gexf(f)
    assert g.node("0").attrs["age"] == 0


def test_an_attvalue_referencing_an_undeclared_attribute_raises(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph><nodes><node id="0">'
        '<attvalues><attvalue for="99" value="x"/></attvalues>'
        "</node></nodes></graph></gexf>"
    )
    with pytest.raises(GEXFParseError, match="undeclared attribute"):
        read_gexf(f)


def test_edge_attvalues_use_a_separate_namespace_from_node_attvalues(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph>'
        '<attributes class="node"><attribute id="0" title="kind" type="string"/></attributes>'
        '<attributes class="edge"><attribute id="0" title="relation" type="string"/></attributes>'
        '<nodes><node id="0"><attvalues><attvalue for="0" value="person"/></attvalues></node>'
        '<node id="1"/></nodes>'
        '<edges><edge source="0" target="1">'
        '<attvalues><attvalue for="0" value="friend"/></attvalues>'
        "</edge></edges></graph></gexf>"
    )
    g = read_gexf(f)
    assert g.node("0").attrs["kind"] == "person"
    assert g.connector(0).attrs["relation"] == "friend"


# ---- node identity: id by default, label with label_as_key=True ---------------------------------


def test_node_key_is_the_gexf_id_by_default(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="7" label="seven"/></nodes></graph></gexf>')
    g = read_gexf(f)
    assert g.node("7") is not None
    assert g.node("7").attrs["label"] == "seven"


def test_label_as_key_uses_the_label_instead(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text(
        '<gexf><graph><nodes><node id="7" label="alice"/><node id="8" label="bob"/></nodes>'
        '<edges><edge source="7" target="8"/></edges></graph></gexf>'
    )
    g = read_gexf(f, label_as_key=True)
    assert g.node("alice") is not None and g.node("bob") is not None


def test_label_as_key_raises_on_a_missing_label(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="1"/></nodes></graph></gexf>')
    with pytest.raises(GEXFParseError, match="no 'label'"):
        read_gexf(f, label_as_key=True)


def test_label_as_key_raises_on_a_duplicate_label(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="1" label="x"/><node id="2" label="x"/></nodes></graph></gexf>')
    with pytest.raises(GEXFParseError, match="not unique"):
        read_gexf(f, label_as_key=True)


# ---- errors are clear and located ----------------------------------------------------------------


def test_missing_graph_element_raises(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text("<gexf></gexf>")
    with pytest.raises(GEXFParseError, match="no 'graph' element"):
        read_gexf(f)


def test_node_without_id_raises(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node label="no id"/></nodes></graph></gexf>')
    with pytest.raises(GEXFParseError, match="no 'id'"):
        read_gexf(f)


def test_edge_referencing_an_undefined_node_raises(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text('<gexf><graph><nodes><node id="0"/></nodes><edges><edge source="0" target="999"/></edges></graph></gexf>')
    with pytest.raises(GEXFParseError, match="not defined"):
        read_gexf(f)


def test_malformed_xml_raises_with_the_path(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text("<gexf><graph><nodes><node id=0></nodes>")
    with pytest.raises(GEXFParseError, match=re.escape(str(f))):
        read_gexf(f)


# ---- a graph with no <nodes>/<edges> at all is legal (just empty) --------------------------------


def test_a_graph_with_no_nodes_or_edges_reads_as_empty(tmp_path):
    f = tmp_path / "g.gexf"
    f.write_text("<gexf><graph/></gexf>")
    g = read_gexf(f)
    assert g.num_nodes == 0 and g.num_edges == 0
