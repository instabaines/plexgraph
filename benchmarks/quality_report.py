"""Layout quality comparison against established engines.

python benchmarks/quality_report.py --datasets karate,sbm1000,grid30 --out /tmp/quality

Writes quality.json, quality.md and one gallery PNG per dataset. Lower is better
for stress, edge_length_cv, crossings and overlap; higher is better for
neighbourhood_precision and community_silhouette. The random layout is the floor.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "packages/core"):
    sys.path.insert(0, str(p))

from benchmarks.quality import ENGINES, load_dataset, run  # noqa: E402

DEFAULT_DATASETS = "karate,lesmis,tree6,grid30,sbm1000,sbm5000,ba5000,facebook,sbm20000"
COLUMNS = [("stress", "stress"), ("neighbourhood_precision", "nbr-prec"),
           ("community_silhouette", "silhouette"), ("edge_length_cv", "edge-CV"),
           ("crossings_per_edge_pair", "crossings"), ("overlap_fraction", "overlap"),
           ("seconds", "sec")]


def draw(ax, ds, pos, title):
    edges, n = ds["edges"], ds["n"]
    if len(edges) > 60_000:
        edges = edges[np.random.default_rng(0).choice(len(edges), 60_000, replace=False)]
    alpha = float(np.clip(3000 / max(len(edges), 1), .03, .5))
    from matplotlib.collections import LineCollection
    ax.add_collection(LineCollection(pos[edges], colors="#64748b", linewidths=.3, alpha=alpha))
    color = ds["labels"] if ds["labels"] is not None else np.log1p(np.bincount(ds["edges"].ravel(), minlength=n))
    ax.scatter(pos[:, 0], pos[:, 1], c=color, s=float(np.clip(400 / np.sqrt(n), .3, 14)),
               cmap="tab10" if ds["labels"] is not None else "viridis", linewidths=0)
    ax.set_title(title, fontsize=8)
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_xticks([])
    ax.set_yticks([])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--datasets", default=DEFAULT_DATASETS)
    ap.add_argument("--engines", default=",".join(ENGINES))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--iterations", type=int, default=100)
    ap.add_argument("--out", type=Path, default=Path("/tmp/hyperloom-quality"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    engines = args.engines.split(",")
    rows, lines = [], ["| dataset | n | edges | engine | " + " | ".join(h for _, h in COLUMNS) + " |",
                       "|" + "---|" * (4 + len(COLUMNS))]
    for name in args.datasets.split(","):
        ds = load_dataset(name)
        if ds is None:
            print(f"{name}: unavailable (download failed)", file=sys.stderr)
            continue
        drawn = []
        for engine in engines:
            pos, result = run(ds, engine, args.seed, args.iterations)
            rows.append(dict(dataset=name, nodes=ds["n"], edges=len(ds["edges"]), engine=engine, **result))
            print(f"{name:10} {engine:16} {result['status']:8}"
                  f" stress={result.get('stress', float('nan')):.3f} sec={result.get('seconds', float('nan')):.1f}",
                  file=sys.stderr, flush=True)
            cells = [f"{result[k]:.3f}" if isinstance(result.get(k), float) else "-" for k, _ in COLUMNS]
            lines.append(f"| {name} | {ds['n']} | {len(ds['edges'])} | {engine} | " + " | ".join(cells) + " |")
            if pos is not None:
                drawn.append((engine, pos, result))
        if drawn:
            fig, axes = plt.subplots(1, len(drawn), figsize=(3.2 * len(drawn), 3.4), squeeze=False)
            for ax, (engine, pos, result) in zip(axes[0], drawn):
                draw(ax, ds, pos, f"{engine}\nstress {result['stress']:.2f} · {result['seconds']:.1f}s")
            fig.suptitle(f"{name}  (n={ds['n']}, m={len(ds['edges'])})", fontsize=10)
            fig.tight_layout()
            fig.savefig(args.out / f"gallery-{name}.png", dpi=80)
            plt.close(fig)
    (args.out / "quality.json").write_text(json.dumps(rows, indent=2) + "\n")
    (args.out / "quality.md").write_text("\n".join(lines) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
