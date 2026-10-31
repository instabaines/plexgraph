import pytest

from plexgraph_core.io.auto import UnknownGraphFormatError, read_graph
from plexgraph_core.io.json_io import write_json
from plexgraph_core.model.ir import Graph


def _small_graph() -> Graph:
    g = Graph()
    a = g.add_node("a")
    b = g.add_node("b")
    g.add_edge(a, b, weight=2.0)
    return g


def test_reads_json_by_extension(tmp_path):
    path = tmp_path / "g.json"
    write_json(_small_graph(), path)
    g = read_graph(path)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_reads_gml_by_extension(tmp_path):
    path = tmp_path / "g.gml"
    path.write_text("graph [\n  node [ id 0 ]\n  node [ id 1 ]\n  edge [ source 0 target 1 ]\n]\n")
    g = read_graph(path)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_reads_pajek_by_net_extension(tmp_path):
    path = tmp_path / "g.net"
    path.write_text('*Vertices 2\n1 "a"\n2 "b"\n*Edges\n1 2\n')
    g = read_graph(path)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_reads_dot_by_gv_extension(tmp_path):
    path = tmp_path / "g.gv"
    path.write_text("graph { a -- b }\n")
    g = read_graph(path)
    assert g.num_nodes == 2 and g.num_edges == 1


def test_reads_plain_edgelist_by_txt_extension(tmp_path):
    path = tmp_path / "g.txt"
    path.write_text("0 1\n1 2\n")
    g = read_graph(path)
    assert g.num_nodes == 3 and g.num_edges == 2


def test_unknown_extension_raises_rather_than_guessing(tmp_path):
    path = tmp_path / "g.weird"
    path.write_text("whatever")
    with pytest.raises(UnknownGraphFormatError, match=r"\.weird"):
        read_graph(path)


def test_extension_matching_is_case_insensitive(tmp_path):
    path = tmp_path / "g.JSON"
    write_json(_small_graph(), path)
    g = read_graph(path)
    assert g.num_nodes == 2
