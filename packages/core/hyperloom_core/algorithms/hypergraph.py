"""Hypergraph-specific algorithms.

bipartite_expansion() turns each hyperedge into an explicit "hyperedge
node" connected by plain edges to its members — the standard trick for
running ordinary graph algorithms (and, per docs/architecture/plan.md
section 5, for one of the two candidate hypergraph rendering strategies)
on a structure that graph algorithms don't natively understand. HyperNetX
uses the same incidence-expansion idea internally; here it's exposed as a
first-class transform on the IR rather than a private implementation
detail, per the plan's note that this utility should be reusable by both
rendering and existing centrality/community algorithms.
"""

from __future__ import annotations

from hyperloom_core.model.ir import Graph


def bipartite_expansion(graph: Graph) -> Graph:
    """Return a new plain graph where every hyperedge (connector with >2
    endpoints) is replaced by a fresh "hyperedge node" connected to each of
    its original members. Ordinary (2-endpoint) connectors are copied over
    unchanged. Node/layer attrs are preserved; hyperedge attrs are copied
    onto the new hyperedge node.

    The result has no hyperedges of its own (every connector in it has
    exactly 2 endpoints), so any algorithm that only understands plain
    graphs — centrality, community detection, force-directed layout — can
    run on it directly.
    """
    expanded = Graph()

    node_id_map: dict[int, int] = {}
    for node in graph.nodes():
        new_id = expanded.add_node(f"node:{node.key!r}", **node.attrs)
        node_id_map[node.id] = new_id

    layer_id_map: dict[int, int] = {}
    for layer in graph.layers():
        new_id = expanded.add_layer(f"layer:{layer.key!r}", **layer.attrs)
        layer_id_map[layer.id] = new_id

    for connector in graph.connectors():
        layer = layer_id_map.get(connector.layer_id) if connector.layer_id is not None else None
        t_start = None if connector.t_start == float("-inf") else connector.t_start
        t_end = None if connector.t_end == float("inf") else connector.t_end

        if not connector.is_hyperedge:
            u, v = connector.endpoints
            expanded.add_edge(
                node_id_map[u],
                node_id_map[v],
                directed=connector.directed,
                layer=layer,
                t_start=t_start,
                t_end=t_end,
                weight=connector.weight,
                **connector.attrs,
            )
            continue

        hyperedge_node = expanded.add_node(
            f"hyperedge:{connector.id}", is_hyperedge_node=True, **connector.attrs
        )
        for member in connector.endpoints:
            expanded.add_edge(
                hyperedge_node,
                node_id_map[member],
                layer=layer,
                t_start=t_start,
                t_end=t_end,
                weight=connector.weight,
            )

    return expanded
