"""Reproducible layout diagnostics; every case runs in a bounded subprocess.

python benchmarks/layout_audit.py --suite quick --output /tmp/layout-audit.json
python benchmarks/layout_audit.py --suite scale --timeout 30 --memory-mb 1024

Timings are observations, not portable pass/fail criteria. Finite coordinates
and early termination do not establish layout quality or browser scalability.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def build_case(kind, n, seed=0):
    import numpy as np
    from hyperloom_core import Graph
    rng = np.random.default_rng(seed)
    g = Graph()
    for i in range(n):
        g.add_node(i, community=min(3, i * 4 // max(n, 1)))
    if kind in ("path", "star"):
        for i in range(1, n):
            g.add_edge(i - 1 if kind == "path" else 0, i)
    elif kind == "hyperedge":
        g.add_hyperedge(list(range(n)))
    elif kind != "isolates":
        if kind == "multiplex":
            for layer in range(8):
                g.add_layer(str(layer))
        for _ in range(n * 3):
            u = int(rng.integers(n))
            if kind in ("communities", "disconnected") and (kind == "disconnected" or rng.random() < .95):
                start = (u // max(1, n // 4)) * max(1, n // 4)
                v = int(rng.integers(start, min(n, start + max(1, n // 4))))
            else:
                v = int(rng.integers(n))
            if u == v:
                continue
            kwargs = {}
            if kind == "multiplex":
                kwargs["layer"] = str(int(rng.integers(8)))
            if kind == "temporal":
                start = int(rng.integers(12))
                kwargs.update(t_start=start, t_end=start + 2, directed=True)
            g.add_edge(u, v, **kwargs)
    return g


def measure(kind, n, seed, iterations):
    import resource
    import numpy as np
    from hyperloom_core.algorithms.layout import force_directed_layout
    started = time.perf_counter()
    graph = build_case(kind, n, seed)
    build_seconds = time.perf_counter() - started
    connectors = list(graph.connectors())
    expanded_pairs = sum(len(c.endpoints) * (len(c.endpoints) - 1) // 2 for c in connectors)
    started = time.perf_counter()
    first_seconds = None
    last = None
    count = 0
    # Stream: never retain all iterations' coordinates.
    for step in force_directed_layout(graph, seed=seed, iterations=iterations):
        if first_seconds is None:
            first_seconds = time.perf_counter() - started
        last = step
        count += 1
    elapsed = time.perf_counter() - started
    p = last.positions
    finite = bool(np.isfinite(p).all())
    span = np.ptp(p, axis=0) if n else np.zeros(2)
    # Fit into a 1000x700 viewport; 8px occupancy cells are a crowding proxy,
    # not an exact node-overlap count. Preserve aspect ratio.
    screen = (p - p.min(axis=0)) * min(1000 / max(span[0], 1e-9), 700 / max(span[1], 1e-9)) if n else p
    occupied = len(np.unique(np.floor(screen / 8).astype(np.int64), axis=0)) if n else 0
    edges = np.array([c.endpoints for c in connectors if len(c.endpoints) == 2], dtype=np.int64)
    edge_length = float(np.median(np.linalg.norm(p[edges[:, 0]] - p[edges[:, 1]], axis=1))) if len(edges) else None
    rng = np.random.default_rng(seed + 100)
    pairs = rng.integers(0, n, (min(10000, n * 10), 2)) if n else np.zeros((0, 2), dtype=int)
    random_distance = float(np.median(np.linalg.norm(p[pairs[:, 0]] - p[pairs[:, 1]], axis=1))) if n else 0
    return dict(status="ok" if finite else "nonfinite", kind=kind, nodes=n, seed=seed,
        connectors=len(connectors), clique_pairs_if_expanded=expanded_pairs,
        attraction_terms=sum(len(c.endpoints) if c.is_hyperedge else 1 for c in connectors),
        build_seconds=build_seconds, first_step_seconds=first_seconds, layout_seconds=elapsed,
        steps=count, termination_flag=last.converged, finite=finite,
        peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 1024 * 1024),
        span=span.tolist(), crowded_cell_fraction=1 - occupied / n if n else 0,
        edge_to_random_distance=edge_length / random_distance if edge_length is not None and random_distance > 0 else None)


def run_case(kind, n, seed=0, iterations=60, timeout=30, memory_mb=1024):
    config = dict(kind=kind, nodes=n, seed=seed, iterations=iterations, memory_mb=memory_mb)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "packages/core"), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(config)],
            env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(config, status="timeout", timeout_seconds=timeout)
    if result.returncode:
        return dict(config, status="worker_error", returncode=result.returncode, error=result.stderr[-2000:])
    return dict(config, **json.loads(result.stdout))


def run_suite(suite="quick", timeout=30, memory_mb=1024):
    cases = [(kind, 120) for kind in ("path", "star", "communities", "disconnected", "isolates", "multiplex", "temporal", "hyperedge")]
    seeds = [0, 1, 2]
    if suite == "scale":
        cases = [("random", n) for n in (1000, 3000, 5000, 5001, 10000, 100000)] + [("hyperedge", n) for n in (1000, 5001)]
        seeds = [0]
    rows = []
    for kind, n in cases:
        for seed in seeds:
            row = run_case(kind, n, seed, iterations=60 if suite == "quick" else 10, timeout=timeout, memory_mb=memory_mb)
            rows.append(row)
            print(f'{kind:14} n={n:7} seed={seed}: {row["status"]}', file=sys.stderr, flush=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return dict(suite=suite, python=sys.version, platform=platform.platform(), git_revision=revision,
        working_tree_dirty=bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout),
        timeout_seconds=timeout, memory_limit_mb=memory_mb, results=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["quick", "scale"], default="quick")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--memory-mb", type=int, default=1024)
    parser.add_argument("--output", type=Path, default=Path("/tmp/hyperloom-layout-audit.json"))
    parser.add_argument("--worker")
    args = parser.parse_args()
    if args.worker:
        config = json.loads(args.worker)
        import resource
        # Limit virtual memory before importing NumPy or allocating graph data.
        cap = config["memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        print(json.dumps(measure(config["kind"], config["nodes"], config["seed"], config["iterations"])))
    else:
        report = run_suite(args.suite, args.timeout, args.memory_mb)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(args.output)
