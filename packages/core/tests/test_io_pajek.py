import re

import pytest

from plexgraph_core.io.pajek import PajekParseError, read_pajek


def test_the_canonical_small_arcs_example(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        '*Vertices 3\n'
        '1 "Alice"\n'
        '2 "Bob"\n'
        '3 "Carol"\n'
        "*Arcs\n"
        "1 2 1.0\n"
        "2 3 2.5\n"
    )
    g = read_pajek(f)
    assert g.num_nodes == 3 and g.num_edges == 2
    assert g.node("1").attrs["label"] == "Alice"
    assert g.connector(0).directed
    assert g.connector(0).endpoints == (g.node("1").id, g.node("2").id)
    assert g.connector(0).weight == 1.0
    assert g.connector(1).weight == 2.5


def test_edges_section_is_undirected(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Edges\n1 2\n')
    g = read_pajek(f)
    assert not g.connector(0).directed


def test_directed_argument_overrides_every_section(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Edges\n1 2\n')
    g = read_pajek(f, directed=True)
    assert g.connector(0).directed


def test_missing_weight_is_none_not_defaulted_to_one(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Arcs\n1 2\n')
    g = read_pajek(f)
    assert g.connector(0).weight is None


# ---- vertices: labels, missing labels, coordinates ------------------------------------------------


def test_a_vertex_with_no_label_falls_back_to_its_id(tmp_path):
    f = tmp_path / "g.net"
    f.write_text("*Vertices 1\n1\n")
    g = read_pajek(f)
    assert g.node("1").attrs["label"] == "1"


def test_vertex_coordinates_are_kept_as_plain_attributes(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 1\n1 "a" 0.5 0.25\n')
    g = read_pajek(f)
    assert g.node("1").attrs["x"] == 0.5
    assert g.node("1").attrs["y"] == 0.25


def test_vertex_coordinates_and_shape_are_kept(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 1\n1 "a" 0.5 0.25 ellipse\n')
    g = read_pajek(f)
    assert g.node("1").attrs["shape"] == "ellipse"


# ---- label_as_key ------------------------------------------------------------------------------------


def test_label_as_key_uses_the_label_instead(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "alice"\n2 "bob"\n*Arcs\n1 2\n')
    g = read_pajek(f, label_as_key=True)
    assert g.node("alice") is not None and g.node("bob") is not None


def test_label_as_key_raises_on_a_missing_label(tmp_path):
    f = tmp_path / "g.net"
    f.write_text("*Vertices 1\n1\n")
    with pytest.raises(PajekParseError, match="no label"):
        read_pajek(f, label_as_key=True)


def test_label_as_key_raises_on_a_duplicate_label(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "x"\n2 "x"\n')
    with pytest.raises(PajekParseError, match="not unique"):
        read_pajek(f, label_as_key=True)


# ---- multiple relations: genuine native multiplex support --------------------------------------------


def test_a_single_relation_section_creates_no_layer(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Arcs\n1 2\n')
    g = read_pajek(f)
    assert g.connector(0).layer_id is None
    assert len(g.layers()) == 0


def test_multiple_relation_sections_become_layers(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        '*Vertices 3\n1 "a"\n2 "b"\n3 "c"\n'
        '*Arcs "friendship"\n1 2\n'
        '*Edges "coworker"\n2 3\n'
    )
    g = read_pajek(f)
    layer_keys = {l.key for l in g.layers()}
    assert layer_keys == {"friendship", "coworker"}
    assert g.connector(0).layer_id != g.connector(1).layer_id
    # each section keeps its own directedness even though they're different layers
    assert g.connector(0).directed
    assert not g.connector(1).directed


def test_a_relation_section_with_no_label_falls_back_to_an_auto_numbered_layer(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 3\n1 "a"\n2 "b"\n3 "c"\n*Arcs\n1 2\n*Edges\n2 3\n')
    g = read_pajek(f)
    assert len(g.layers()) == 2
    assert g.connector(0).layer_id != g.connector(1).layer_id


def test_duplicate_relation_labels_fall_back_to_index_to_avoid_a_collision(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        '*Vertices 3\n1 "a"\n2 "b"\n3 "c"\n'
        '*Arcs "same"\n1 2\n'
        '*Edges "same"\n2 3\n'
    )
    g = read_pajek(f)
    assert len(g.layers()) == 2


# ---- arcslist / edgeslist (adjacency-list form, never weighted) --------------------------------------


def test_arcslist_creates_one_edge_per_target(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 3\n1 "a"\n2 "b"\n3 "c"\n*Arcslist\n1 2 3\n')
    g = read_pajek(f)
    assert g.num_edges == 2
    assert all(c.directed for c in g.connectors())
    assert all(c.weight is None for c in g.connectors())


def test_edgeslist_is_undirected(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Edgeslist\n1 2\n')
    g = read_pajek(f)
    assert not g.connector(0).directed


# ---- matrix form: always directed, bounded by the vertex count ---------------------------------------


def test_matrix_nonzero_entries_become_directed_weighted_edges(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Matrix\n0 1\n2 0\n')
    g = read_pajek(f)
    assert g.num_edges == 2
    assert all(c.directed for c in g.connectors())
    weights = sorted(c.weight for c in g.connectors())
    assert weights == [1.0, 2.0]


def test_matrix_is_directed_even_when_symmetric(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Matrix\n0 1\n1 0\n')
    g = read_pajek(f)
    assert all(c.directed for c in g.connectors())


# ---- unrecognized sections are skipped gracefully, not misread as edges ------------------------------


def test_a_partition_section_is_skipped_without_raising(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        '*Vertices 2\n1 "a"\n2 "b"\n'
        "*Partition clustering\n1\n2\n"
        "*Arcs\n1 2\n"
    )
    g = read_pajek(f)
    assert g.num_edges == 1


def test_an_unrecognized_section_before_the_arcs_section_does_not_swallow_it(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        '*Vertices 2\n1 "a"\n2 "b"\n'
        "*Vector values\n1\n2\n"
        "*Edges\n1 2\n"
    )
    g = read_pajek(f)
    assert g.num_edges == 1
    assert not g.connector(0).directed


# ---- comments ------------------------------------------------------------------------------------------


def test_percent_comment_lines_are_skipped(tmp_path):
    f = tmp_path / "g.net"
    f.write_text(
        "% a leading comment\n"
        '*Vertices 2\n1 "a"\n% a comment between vertices\n2 "b"\n*Arcs\n1 2\n'
    )
    g = read_pajek(f)
    assert g.num_nodes == 2 and g.num_edges == 1


# ---- t_start/t_end convention mapping -------------------------------------------------------------------


def test_t_start_and_t_end_keys_are_read_as_temporal_bounds(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Arcs\n1 2 1.0 start 3 end 7\n')
    g = read_pajek(f, t_start_key="start", t_end_key="end")
    assert g.connector(0).t_start == 3 and g.connector(0).t_end == 7


def test_no_time_keys_configured_reads_a_plain_static_graph(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Arcs\n1 2\n')
    g = read_pajek(f)
    assert g.connector(0).is_always_present


# ---- errors are clear and located -----------------------------------------------------------------------


def test_missing_vertices_section_raises(tmp_path):
    f = tmp_path / "g.net"
    f.write_text("*Arcs\n1 2\n")
    with pytest.raises(PajekParseError, match="Vertices"):
        read_pajek(f)


def test_fewer_vertex_lines_than_declared_raises(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 3\n1 "a"\n2 "b"\n')
    with pytest.raises(PajekParseError, match="expected 3 vertices"):
        read_pajek(f)


def test_an_edge_referencing_an_undefined_vertex_raises(tmp_path):
    f = tmp_path / "g.net"
    f.write_text('*Vertices 1\n1 "a"\n*Arcs\n1 999\n')
    with pytest.raises(PajekParseError, match="not defined"):
        read_pajek(f)


def test_the_path_is_included_in_an_error_message(tmp_path):
    f = tmp_path / "g.net"
    f.write_text("not a pajek file")
    with pytest.raises(PajekParseError, match=re.escape(str(f))):
        read_pajek(f)
