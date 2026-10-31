import json

from plexgraph_bridge.launcher import show
from plexgraph_bridge.project import open_project, save_project
from plexgraph_core.model.ir import Graph


def _small_graph() -> Graph:
    g = Graph()
    for i in range(4):
        g.add_node(i, kind="a" if i % 2 == 0 else "b")
    for i in range(3):
        g.add_edge(i, i + 1, weight=float(i + 1))
    return g


def test_save_project_writes_graph_layout_and_style(tmp_path):
    handle = show(
        _small_graph(), seed=7, layer_gravity=0.3, hub_inertia=0.4, layout_iterations=5,
        node_color_by="kind", open_browser=False, block=False, return_handle=True,
    )
    try:
        path = tmp_path / "project.json"
        save_project(handle, path)
        data = json.loads(path.read_text())

        assert data["plexgraph_project"] == 1
        assert data["graph"]["nodes"][0]["attrs"]["kind"] == "a"
        assert len(data["graph"]["connectors"]) == 3
        assert data["layout"] == {"seed": 7, "layer_gravity": 0.3, "hub_inertia": 0.4, "layout_iterations": 5}
        assert data["style"]["initial"].get("nodeColorBy") == "kind"
    finally:
        handle.close()


def test_handle_save_project_convenience_method_matches_the_free_function(tmp_path):
    handle = show(_small_graph(), open_browser=False, block=False, return_handle=True)
    try:
        path = tmp_path / "project.json"
        handle.save_project(path)
        data = json.loads(path.read_text())
        assert data["plexgraph_project"] == 1
    finally:
        handle.close()


def test_open_project_reconstructs_the_same_graph(tmp_path):
    handle = show(_small_graph(), seed=3, open_browser=False, block=False, return_handle=True)
    try:
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False)
    try:
        g = reopened._controller()._graph
        assert g.num_nodes == 4 and g.num_edges == 3
        assert g.node(0).attrs["kind"] == "a"
    finally:
        reopened.close()


def test_open_project_restores_layout_params(tmp_path):
    handle = show(_small_graph(), seed=42, layer_gravity=0.5, hub_inertia=0.2, layout_iterations=9,
                  open_browser=False, block=False, return_handle=True)
    try:
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False)
    try:
        assert reopened._layout_params == {
            "seed": 42, "layer_gravity": 0.5, "hub_inertia": 0.2, "layout_iterations": 9,
        }
    finally:
        reopened.close()


def test_open_project_restores_the_simple_style_dict(tmp_path):
    handle = show(_small_graph(), node_color_by="kind", open_browser=False, block=False, return_handle=True)
    try:
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False)
    try:
        assert reopened._initial_style.get("nodeColorBy") == "kind"
    finally:
        reopened.close()


def test_open_project_restores_the_style_spec(tmp_path):
    handle = show(_small_graph(), node_color="red", open_browser=False, block=False, return_handle=True)
    try:
        original_spec = handle.get_style()
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False)
    try:
        assert reopened.get_style() == original_spec
        assert original_spec  # sanity: the fixture actually produced a non-empty spec to restore
    finally:
        reopened.close()


def test_open_project_restores_painted_nodes(tmp_path):
    handle = show(_small_graph(), open_browser=False, block=False, return_handle=True)
    try:
        handle.color_nodes([0, 1], "crimson")
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False)
    try:
        painted = reopened._controller().painted()
        assert 0 in painted and 1 in painted
    finally:
        reopened.close()


def test_open_project_passes_through_show_overrides(tmp_path):
    handle = show(_small_graph(), open_browser=False, block=False, return_handle=True)
    try:
        path = tmp_path / "project.json"
        save_project(handle, path)
    finally:
        handle.close()

    reopened = open_project(path, open_browser=False, block=False, layout_iterations=1)
    try:
        assert reopened._layout_params["layout_iterations"] == 1
    finally:
        reopened.close()
