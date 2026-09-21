"""Core intermediate representation (IR) for plexgraph.

Design: a single relation primitive (`Connector`) generalizes edges and
hyperedges, parameterized along three independent axes — layer, time, and
arity — so that "plain graph", "multiplex", "hypergraph", and "temporal"
are all the same structure with different subsets of fields populated,
rather than four separate class hierarchies. See docs/architecture/plan.md
section 1 for the full rationale.

Storage is struct-of-arrays (columnar) for the fixed-width connector fields
(layer, time bounds, weight) so that filtering by layer/time is a columnar
predicate and serialization to the wire protocol can ship typed arrays
instead of per-element JSON objects. Endpoints are variable-length (2 for
ordinary edges, N for hyperedges) and attrs are heterogeneous, so those stay
as Python lists rather than fixed-width arrays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Hashable

import numpy as np

IR_SCHEMA_VERSION = 1

NodeId = int
ConnectorId = int
LayerId = int

NEG_INF = float("-inf")
POS_INF = float("inf")


@dataclass(frozen=True, slots=True)
class Node:
    """Read-only view of a node. Nodes are layer- and time-agnostic by
    default — layer/time membership is expressed via connector participation,
    not by duplicating nodes per layer (see NodeLayerPresence)."""

    id: NodeId
    key: Hashable
    attrs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Layer:
    """A named layer/edge-type in a multiplex graph. Coupling across layers
    is categorical by default (same node id = same entity in every layer);
    general/ordinal coupling is out of scope until v2 (see plan section 6)."""

    id: LayerId
    key: Hashable
    attrs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Connector:
    """Read-only view of a connector: the single primitive for edges and
    hyperedges. `endpoints` has length 2 for an ordinary edge, length N for
    a hyperedge. `t_start`/`t_end` default to (-inf, +inf), meaning "always
    present" — a static graph is the degenerate case where every connector
    has that default and `layer_id` is None."""

    id: ConnectorId
    endpoints: tuple[NodeId, ...]
    directed: bool
    layer_id: LayerId | None
    t_start: float
    t_end: float
    weight: float | None
    roles: dict[NodeId, str] | None
    attrs: dict[str, Any]

    @property
    def is_hyperedge(self) -> bool:
        return len(self.endpoints) > 2

    @property
    def is_event(self) -> bool:
        """An instantaneous connector (t_start == t_end) vs. an interval
        connector spanning a duration."""
        return self.t_start == self.t_end

    @property
    def is_always_present(self) -> bool:
        return self.t_start == NEG_INF and self.t_end == POS_INF


@dataclass(slots=True)
class NodeLayerPresence:
    """Optional sparse overlay declaring that a node is explicitly present
    (or absent) in a layer over a time interval, for cases where presence
    can't be inferred from connector participation alone."""

    node_id: NodeId
    layer_id: LayerId
    t_start: float
    t_end: float


class Graph:
    """The unified IR: {Nodes, Layers, Connectors, NodeLayerPresence?}.

    Internal storage is columnar for connector scalar fields (layer_id,
    t_start, t_end, weight, directed) via numpy arrays; endpoints and attrs
    are variable-length/heterogeneous and stored as parallel Python lists.
    External callers interact via Node/Connector/Layer read-only views, not
    the internal arrays directly.
    """

    schema_version: int = IR_SCHEMA_VERSION

    def __init__(self) -> None:
        # How connector times should be read: "epoch_seconds" when they are calendar times (Unix seconds), else None
        # for plain numbers. Set by the temporal loaders; the viewer uses it to show dates.
        self.time_unit: str | None = None

        # Nodes
        self._node_key_to_id: dict[Hashable, NodeId] = {}
        self._node_attrs: list[dict[str, Any]] = []
        self._node_keys: list[Hashable] = []
        self._next_node_id: NodeId = 0

        # Layers
        self._layer_key_to_id: dict[Hashable, LayerId] = {}
        self._layer_attrs: list[dict[str, Any]] = []
        self._layer_keys: list[Hashable] = []
        self._next_layer_id: LayerId = 0

        # Connectors — columnar for scalar fields, list-of-lists for endpoints
        self._connector_endpoints: list[tuple[NodeId, ...]] = []
        self._connector_directed: list[bool] = []  # promoted to np.ndarray on freeze/export
        self._connector_layer_id: list[int] = []  # -1 sentinel for "no layer"
        self._connector_t_start: list[float] = []
        self._connector_t_end: list[float] = []
        self._connector_weight: list[float] = []  # NaN sentinel for "no weight"
        self._connector_roles: list[dict[NodeId, str] | None] = []
        self._connector_attrs: list[dict[str, Any]] = []

        self._presence: list[NodeLayerPresence] = []

    # -- Nodes ----------------------------------------------------------

    def add_node(self, key: Hashable | None = None, **attrs: Any) -> NodeId:
        """Add a node, returning its internal id. If `key` is omitted, the
        internal id itself is used as the key."""
        node_id = self._next_node_id
        if key is None:
            key = node_id
        if key in self._node_key_to_id:
            raise ValueError(f"node key already exists: {key!r}")
        self._node_key_to_id[key] = node_id
        self._node_keys.append(key)
        self._node_attrs.append(dict(attrs))
        self._next_node_id += 1
        return node_id

    def node(self, key_or_id: Hashable | NodeId) -> Node:
        node_id = self._resolve_node(key_or_id)
        return Node(id=node_id, key=self._node_keys[node_id], attrs=self._node_attrs[node_id])

    def nodes(self) -> list[Node]:
        return [
            Node(id=i, key=self._node_keys[i], attrs=self._node_attrs[i])
            for i in range(self._next_node_id)
        ]

    @property
    def num_nodes(self) -> int:
        return self._next_node_id

    def _resolve_node(self, key_or_id: Hashable | NodeId) -> NodeId:
        # Explicit keys (including default keys, which are the node's own
        # id) take priority; a raw internal id is always resolvable too,
        # even when the node was given a different custom key, since
        # add_node()/add_hyperedge() endpoints are commonly built from the
        # ids that add_node() returned.
        if key_or_id in self._node_key_to_id:
            return self._node_key_to_id[key_or_id]
        if isinstance(key_or_id, int) and 0 <= key_or_id < self._next_node_id:
            return key_or_id
        raise KeyError(f"unknown node: {key_or_id!r}")

    # -- Layers -----------------------------------------------------------

    def add_layer(self, key: Hashable | None = None, **attrs: Any) -> LayerId:
        layer_id = self._next_layer_id
        if key is None:
            key = layer_id
        if key in self._layer_key_to_id:
            raise ValueError(f"layer key already exists: {key!r}")
        self._layer_key_to_id[key] = layer_id
        self._layer_keys.append(key)
        self._layer_attrs.append(dict(attrs))
        self._next_layer_id += 1
        return layer_id

    def layer(self, key_or_id: Hashable | LayerId) -> Layer:
        layer_id = self._resolve_layer(key_or_id)
        return Layer(id=layer_id, key=self._layer_keys[layer_id], attrs=self._layer_attrs[layer_id])

    def layers(self) -> list[Layer]:
        return [
            Layer(id=i, key=self._layer_keys[i], attrs=self._layer_attrs[i])
            for i in range(self._next_layer_id)
        ]

    def _resolve_layer(self, key_or_id: Hashable | LayerId) -> LayerId:
        if key_or_id in self._layer_key_to_id:
            return self._layer_key_to_id[key_or_id]
        if isinstance(key_or_id, int) and 0 <= key_or_id < self._next_layer_id:
            return key_or_id
        raise KeyError(f"unknown layer: {key_or_id!r}")

    # -- Connectors -------------------------------------------------------

    def add_edge(
        self,
        u: Hashable | NodeId,
        v: Hashable | NodeId,
        *,
        directed: bool = False,
        layer: Hashable | LayerId | None = None,
        t_start: float | None = None,
        t_end: float | None = None,
        weight: float | None = None,
        **attrs: Any,
    ) -> ConnectorId:
        """Add an ordinary (2-endpoint) connector."""
        return self._add_connector(
            (self._resolve_node(u), self._resolve_node(v)),
            directed=directed,
            layer=layer,
            t_start=t_start,
            t_end=t_end,
            weight=weight,
            roles=None,
            attrs=attrs,
        )

    def add_hyperedge(
        self,
        nodes: list[Hashable | NodeId],
        *,
        directed: bool = False,
        roles: dict[Hashable | NodeId, str] | None = None,
        layer: Hashable | LayerId | None = None,
        t_start: float | None = None,
        t_end: float | None = None,
        weight: float | None = None,
        **attrs: Any,
    ) -> ConnectorId:
        """Add a connector spanning more than two nodes."""
        if len(nodes) < 2:
            raise ValueError("a connector needs at least 2 endpoints")
        endpoints = tuple(self._resolve_node(n) for n in nodes)
        resolved_roles = (
            {self._resolve_node(n): r for n, r in roles.items()} if roles else None
        )
        return self._add_connector(
            endpoints,
            directed=directed,
            layer=layer,
            t_start=t_start,
            t_end=t_end,
            weight=weight,
            roles=resolved_roles,
            attrs=attrs,
        )

    def _add_connector(
        self,
        endpoints: tuple[NodeId, ...],
        *,
        directed: bool,
        layer: Hashable | LayerId | None,
        t_start: float | None,
        t_end: float | None,
        weight: float | None,
        roles: dict[NodeId, str] | None,
        attrs: dict[str, Any],
    ) -> ConnectorId:
        layer_id = self._resolve_layer(layer) if layer is not None else -1
        ts = NEG_INF if t_start is None else t_start
        te = POS_INF if t_end is None else t_end
        if ts > te:
            raise ValueError(f"t_start ({ts}) must be <= t_end ({te})")

        connector_id = len(self._connector_endpoints)
        self._connector_endpoints.append(endpoints)
        self._connector_directed.append(directed)
        self._connector_layer_id.append(layer_id)
        self._connector_t_start.append(ts)
        self._connector_t_end.append(te)
        self._connector_weight.append(float("nan") if weight is None else weight)
        self._connector_roles.append(roles)
        self._connector_attrs.append(dict(attrs))
        return connector_id

    def connector(self, connector_id: ConnectorId) -> Connector:
        layer_id = self._connector_layer_id[connector_id]
        weight = self._connector_weight[connector_id]
        return Connector(
            id=connector_id,
            endpoints=self._connector_endpoints[connector_id],
            directed=self._connector_directed[connector_id],
            layer_id=None if layer_id == -1 else layer_id,
            t_start=self._connector_t_start[connector_id],
            t_end=self._connector_t_end[connector_id],
            weight=None if weight != weight else weight,  # NaN check
            roles=self._connector_roles[connector_id],
            attrs=self._connector_attrs[connector_id],
        )

    def connectors(self) -> list[Connector]:
        return [self.connector(i) for i in range(len(self._connector_endpoints))]

    # Backward-friendly aliases matching the "edge" vocabulary for the
    # common 2-endpoint case; hyperedges are still connectors underneath.
    edges = connectors
    edge = connector

    @property
    def num_connectors(self) -> int:
        return len(self._connector_endpoints)

    num_edges = num_connectors

    # -- Columnar export (for wire serialization / bulk algorithms) -------

    def connector_arrays(self) -> dict[str, np.ndarray]:
        """Columnar view of connector scalar fields, for the wire protocol
        and vectorized algorithms. Endpoints/attrs are excluded since they
        are variable-length/heterogeneous."""
        return {
            "layer_id": np.array(self._connector_layer_id, dtype=np.int64),
            "t_start": np.array(self._connector_t_start, dtype=np.float64),
            "t_end": np.array(self._connector_t_end, dtype=np.float64),
            "weight": np.array(self._connector_weight, dtype=np.float64),
            "directed": np.array(self._connector_directed, dtype=np.bool_),
        }

    # -- Views (computed, not stored) --------------------------------------

    def snapshot(self, t: float) -> "Graph":
        """A plain-graph view containing only connectors present at time t."""
        return self._filtered(
            lambda c: c.t_start <= t <= c.t_end and c.layer_id is None
        )

    def window(self, t_start: float, t_end: float) -> "Graph":
        """View containing connectors overlapping [t_start, t_end]."""
        return self._filtered(
            lambda c: c.t_start <= t_end and c.t_end >= t_start
        )

    def layer_view(self, layer: Hashable | LayerId) -> "Graph":
        """View containing only connectors belonging to the given layer."""
        layer_id = self._resolve_layer(layer)
        return self._filtered(lambda c: c.layer_id == layer_id)

    def _filtered(self, predicate) -> "Graph":
        g = Graph()
        g._node_key_to_id = dict(self._node_key_to_id)
        g._node_attrs = [dict(a) for a in self._node_attrs]
        g._node_keys = list(self._node_keys)
        g._next_node_id = self._next_node_id
        g._layer_key_to_id = dict(self._layer_key_to_id)
        g._layer_attrs = [dict(a) for a in self._layer_attrs]
        g._layer_keys = list(self._layer_keys)
        g._next_layer_id = self._next_layer_id
        for c in self.connectors():
            if predicate(c):
                g._add_connector(
                    c.endpoints,
                    directed=c.directed,
                    layer=c.layer_id,
                    t_start=None if c.t_start == NEG_INF else c.t_start,
                    t_end=None if c.t_end == POS_INF else c.t_end,
                    weight=c.weight,
                    roles=c.roles,
                    attrs=c.attrs,
                )
        return g

    def __repr__(self) -> str:
        return (
            f"Graph(nodes={self.num_nodes}, connectors={self.num_connectors}, "
            f"layers={self._next_layer_id})"
        )
