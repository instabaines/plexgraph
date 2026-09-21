"""Serve one quality dataset in the real viewer; prints {"url": ...} then blocks.

python benchmarks/serve_case.py sbm5000 [iterations]
"""
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "packages/core", ROOT / "packages/bridge"):
    sys.path.insert(0, str(p))

from benchmarks.quality import build_hyperloom_graph, load_dataset  # noqa: E402
from hyperloom_bridge import show  # noqa: E402

ds = load_dataset(sys.argv[1])
if ds is None:
    sys.exit(f"dataset {sys.argv[1]} unavailable")
handle = show(build_hyperloom_graph(ds), open_browser=False, block=False, return_handle=True, seed=0,
              layout_iterations=int(sys.argv[2]) if len(sys.argv) > 2 else 100,
              node_color_by="label" if ds["labels"] is not None else None)
print(json.dumps({"url": handle.url}), flush=True)
try:
    threading.Event().wait()
finally:
    handle.close()
