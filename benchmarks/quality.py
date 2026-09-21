"""Layout quality metrics, datasets and baseline engines.

Everything here is scale-free: metrics are invariant to translation/scale of the
coordinates, so different engines can be compared. Metrics are sampled above a
few thousand nodes and say so via the `sampled` field.
"""
from __future__ import annotations

import time
import urllib.request
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import shortest_path
from scipy.spatial import cKDTree

DATA_DIR = Path(__file__).resolve().parent / "data"


# ----------------------------------------------------------------- datasets

def _clean(edges: np.ndarray, n: int) -> np.ndarray:
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    edges = edges[edges[:, 0] != edges[:, 1]]
    edges.sort(axis=1)
    return np.unique(edges, axis=0)


def sbm(n: int, blocks: int = 4, degree: float = 10, mixing: float = .05, seed: int = 0):
    """Stochastic block model with known communities (mixing = outside fraction)."""
    rng = np.random.default_rng(seed)
    labels = np.arange(n) * blocks // n
    m = int(n * degree / 2)
    u = rng.integers(0, n, m)
    inside = rng.random(m) >= mixing
    size = n // blocks
    lo = labels[u] * size
    v_in = np.minimum(lo + rng.integers(0, size, m), n - 1)
    v = np.where(inside, v_in, rng.integers(0, n, m))
    return _clean(np.column_stack([u, v]), n), labels


def barabasi_albert(n: int, m: int = 3, seed: int = 0):
    rng = np.random.default_rng(seed)
    targets = list(range(m))
    repeated: list[int] = []
    edges = []
    for src in range(m, n):
        edges.extend((src, t) for t in targets)
        repeated.extend(targets)
        repeated.extend([src] * m)
        targets = list({repeated[i] for i in rng.integers(0, len(repeated), m * 2)})[:m]
        while len(targets) < m:
            targets.append(int(rng.integers(0, src)))
    return _clean(np.array(edges), n), None


def grid(side: int):
    idx = np.arange(side * side).reshape(side, side)
    edges = np.concatenate([np.column_stack([idx[:, :-1].ravel(), idx[:, 1:].ravel()]),
                            np.column_stack([idx[:-1].ravel(), idx[1:].ravel()])])
    return edges, None


def balanced_tree(branching: int, height: int):
    n = (branching ** (height + 1) - 1) // (branching - 1)
    child = np.arange(1, n)
    return np.column_stack([(child - 1) // branching, child]), None


def _download(name: str, url: str) -> Path | None:
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / name
    if not path.exists():
        try:
            urllib.request.urlretrieve(url, path)
        except Exception:
            return None
    return path


def snap_edge_list(name: str, url: str):
    import gzip
    path = _download(name, url)
    if path is None:
        return None
    raw = np.loadtxt(gzip.open(path, "rt"), dtype=np.int64, comments="#")
    _, inverse = np.unique(raw, return_inverse=True)
    return _clean(inverse.reshape(-1, 2), int(inverse.max()) + 1), None


def load_dataset(name: str):
    """Return dict(name, n, edges, labels|None, source) or None if unavailable."""
    import networkx as nx
    if name == "karate":
        g = nx.karate_club_graph()
        labels = np.array([0 if g.nodes[i]["club"] == "Mr. Hi" else 1 for i in g])
        edges, n = np.array(g.edges()), len(g)
    elif name == "lesmis":
        g = nx.convert_node_labels_to_integers(nx.les_miserables_graph())
        edges, labels, n = np.array(g.edges()), None, len(g)
    elif name.startswith("sbm"):
        n = int(name[3:])
        edges, labels = sbm(n)
    elif name.startswith("ba"):
        n = int(name[2:])
        edges, labels = barabasi_albert(n)
    elif name.startswith("grid"):
        side = int(name[4:])
        edges, labels = grid(side)
        n = side * side
    elif name.startswith("tree"):
        edges, labels = balanced_tree(3, int(name[4:]))
        n = int(edges.max()) + 1
    elif name == "facebook":
        loaded = snap_edge_list("facebook_combined.txt.gz",
                                "https://snap.stanford.edu/data/facebook_combined.txt.gz")
        if loaded is None:
            return None
        (edges, labels), n = loaded, int(loaded[0].max()) + 1
    elif name == "grqc":
        loaded = snap_edge_list("ca-GrQc.txt.gz", "https://snap.stanford.edu/data/ca-GrQc.txt.gz")
        if loaded is None:
            return None
        (edges, labels), n = loaded, int(loaded[0].max()) + 1
    else:
        raise ValueError(f"unknown dataset {name}")
    return dict(name=name, n=n, edges=_clean(edges, n), labels=labels)


def build_plexgraph_graph(ds):
    from plexgraph_core import Graph
    g = Graph()
    labels = ds["labels"]
    for i in range(ds["n"]):
        g.add_node(i, **({"label": int(labels[i])} if labels is not None else {}))
    for u, v in ds["edges"]:
        g.add_edge(int(u), int(v))
    return g


# ------------------------------------------------------------------ engines

def layout_plexgraph(ds, seed=0, iterations=100):
    from plexgraph_core.algorithms.layout import force_directed_layout
    g = build_plexgraph_graph(ds)
    last = None
    for last in force_directed_layout(g, seed=seed, iterations=iterations):
        pass
    return last.positions


def layout_random(ds, seed=0, **_):
    return np.random.default_rng(seed).uniform(-1, 1, (ds["n"], 2))


def layout_networkx_spring(ds, seed=0, iterations=100):
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(range(ds["n"]))
    g.add_edges_from(ds["edges"].tolist())
    pos = nx.spring_layout(g, iterations=iterations, seed=seed)
    return np.array([pos[i] for i in range(ds["n"])])


def _igraph(ds):
    import igraph
    return igraph.Graph(n=ds["n"], edges=ds["edges"].tolist())


def layout_igraph_fr(ds, seed=0, iterations=100):
    import random
    random.seed(seed)
    return np.array(_igraph(ds).layout_fruchterman_reingold(niter=iterations).coords)


def layout_igraph_drl(ds, seed=0, **_):
    import random
    random.seed(seed)
    return np.array(_igraph(ds).layout_drl().coords)


def layout_igraph_kk(ds, seed=0, **_):
    import random
    random.seed(seed)
    return np.array(_igraph(ds).layout_kamada_kawai().coords)


# name -> (function, max nodes it is asked to run at)
ENGINES = {
    "random": (layout_random, 10 ** 9),
    "plexgraph": (layout_plexgraph, 10 ** 9),
    "igraph-fr": (layout_igraph_fr, 25_000),
    "igraph-drl": (layout_igraph_drl, 60_000),
    "igraph-kk": (layout_igraph_kk, 1_500),
    "networkx-spring": (layout_networkx_spring, 6_000),
}


# ------------------------------------------------------------------ metrics

def _sources(n, rng, k):
    return np.arange(n) if n <= k else np.sort(rng.choice(n, k, replace=False))


def metrics(ds, pos: np.ndarray, seed: int = 0, viewport=(1000, 700), marker_px=6) -> dict:
    n, edges, labels = ds["n"], ds["edges"], ds["labels"]
    rng = np.random.default_rng(seed + 1234)
    pos = np.asarray(pos, dtype=float)
    out: dict = dict(finite=bool(np.isfinite(pos).all()), sampled=n > 3000)
    if not out["finite"]:
        return out
    adj = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(n, n)).tocsr()
    adj = adj + adj.T

    # Normalised stress: 0 = graph distances reproduced up to a global scale.
    src = _sources(n, rng, 150)
    dist = shortest_path(adj, unweighted=True, indices=src)
    euclid = np.linalg.norm(pos[src, None, :] - pos[None, :, :], axis=2)
    ok = np.isfinite(dist) & (dist > 0)
    d, e = dist[ok], euclid[ok]
    w = 1.0 / (d * d)
    alpha = np.sum(w * d * e) / max(np.sum(w * e * e), 1e-30)
    out["stress"] = float(np.sum(w * (alpha * e - d) ** 2) / np.sum(w * d * d))

    # Neighbourhood preservation: share of each node's graph neighbours that
    # are among its k nearest layout neighbours (k = degree). Chance is ~k/n.
    deg = np.asarray(adj.sum(axis=1)).ravel().astype(int)
    tree = cKDTree(pos)
    probe = _sources(n, rng, 1500)
    probe = probe[deg[probe] > 0]
    hits = []
    for i in probe:
        kk = int(min(deg[i], n - 1))
        _, nn = tree.query(pos[i], kk + 1)
        nbrs = set(adj.indices[adj.indptr[i]:adj.indptr[i + 1]])
        hits.append(len(nbrs.intersection(nn[1:].tolist())) / kk)
    out["neighbourhood_precision"] = float(np.mean(hits)) if hits else None

    # Ground-truth community separation (silhouette in layout space).
    if labels is not None and len(np.unique(labels)) > 1:
        pr = _sources(n, rng, 400)
        cand = _sources(n, rng, 4000)
        dd = np.linalg.norm(pos[pr, None, :] - pos[None, cand, :], axis=2)
        sil = []
        for row, i in enumerate(pr):
            same = labels[cand] == labels[i]
            same &= cand != i
            if not same.any():
                continue
            a = dd[row, same].mean()
            b = min(dd[row, labels[cand] == c].mean() for c in np.unique(labels) if c != labels[i])
            sil.append((b - a) / max(a, b, 1e-30))
        out["community_silhouette"] = float(np.mean(sil))

    # Edge-length uniformity (lower is more even) and crossing density.
    el = np.linalg.norm(pos[edges[:, 0]] - pos[edges[:, 1]], axis=1)
    out["edge_length_cv"] = float(el.std() / max(el.mean(), 1e-30))
    m = min(len(edges), 1500)
    pick = edges[rng.choice(len(edges), m, replace=False)]
    p, q = pos[pick[:, 0]], pos[pick[:, 1]]

    def orient(a, b, c):
        return (b[..., 0] - a[..., 0]) * (c[..., 1] - a[..., 1]) - (b[..., 1] - a[..., 1]) * (c[..., 0] - a[..., 0])

    o1 = orient(p[:, None], q[:, None], p[None])
    o2 = orient(p[:, None], q[:, None], q[None])
    o3 = orient(p[None], q[None], p[:, None])
    o4 = orient(p[None], q[None], q[:, None])
    cross = (o1 * o2 < 0) & (o3 * o4 < 0)
    share = (pick[:, None, 0] == pick[None, :, 0]) | (pick[:, None, 0] == pick[None, :, 1]) | \
            (pick[:, None, 1] == pick[None, :, 0]) | (pick[:, None, 1] == pick[None, :, 1])
    cross &= ~share
    out["crossings_per_edge_pair"] = float(np.triu(cross, 1).sum() / (m * (m - 1) / 2))

    # Resolution: nodes whose nearest neighbour is closer than a marker
    # diameter once the layout is fitted into the viewport.
    span = np.ptp(pos, axis=0)
    scale = min(viewport[0] / max(span[0], 1e-30), viewport[1] / max(span[1], 1e-30))
    nn_d = cKDTree(pos * scale).query(pos * scale, 2)[0][:, 1]
    out["overlap_fraction"] = float(np.mean(nn_d < marker_px))
    out["aspect_ratio"] = float(max(span) / max(min(span), 1e-30))
    return out


def run(ds, engine: str, seed: int = 0, iterations: int = 100):
    fn, cap = ENGINES[engine]
    if ds["n"] > cap:
        return None, dict(status="skipped", reason=f"n>{cap}")
    started = time.perf_counter()
    try:
        pos = fn(ds, seed=seed, iterations=iterations)
    except Exception as exc:  # engine failure is a result, not a crash
        return None, dict(status="error", reason=f"{type(exc).__name__}: {exc}")
    seconds = time.perf_counter() - started
    return pos, dict(status="ok", seconds=seconds, **metrics(ds, pos, seed))
