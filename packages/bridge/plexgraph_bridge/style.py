"""Styling for the viewer: networkx-style arguments, encodings, and live updates.

Turns things like `node_color=[0.1, 0.5, ...]`, `node_size=by_degree(...)` or `edge_curvature=0.2` into the
viewer's style spec, and keeps the current style so tabs that connect later see it too. Everything is checked
here, in Python, so mistakes (unknown colormap, wrong number of colors, coloring by time on a graph with no
times) raise immediately instead of failing silently in the browser.

Where a value is one color: a string ("crimson", "#1f77b4"), or a *tuple* of 3-4 numbers. A list or NumPy array
has one entry per node (or edge); a dict maps node keys (or `(u, v)` pairs) to values. (For backward compatibility a
plain list of 3-4 numbers is still one color, unless the graph has exactly that many nodes.)
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Callable

import numpy as np

from plexgraph_bridge.color import parse_color
from plexgraph_core.model.ir import NEG_INF, POS_INF, Graph
from plexgraph_core.wire.protocol import encode_style

# Keep in sync with packages/viz-core/src/style/colormaps.ts and spec.ts (a test compares them).
COLORMAPS = ("viridis", "plasma", "inferno", "magma", "cividis", "coolwarm", "RdBu", "Spectral", "Blues", "Greens",
             "Reds", "Oranges", "Purples", "Greys", "YlOrRd")
PALETTES = ("default", "tab10", "Set1", "Set2", "Dark2", "Paired", "Pastel1")
SHAPES = ("circle", "square", "triangle", "diamond", "cross")
# matplotlib / networkx marker letters
_MARKERS = {"o": "circle", "s": "square", "^": "triangle", "d": "diamond", "D": "diamond", "+": "cross", "P": "cross"}


class _Reset:
    """Marks a field to be restored to its default (`None` means "leave it alone")."""

    def __repr__(self) -> str:
        return "RESET"


RESET = _Reset()

_SETTABLE = {
    "node_color", "node_size", "node_shape", "node_alpha", "node_outline_color", "node_outline_width",
    "edge_color", "edge_width", "edge_alpha", "edge_curvature", "arrow_scale",
    "label_mode", "label_size", "label_color", "label_halo", "label_attribute",
    "cmap", "vmin", "vmax", "edge_cmap", "edge_vmin", "edge_vmax", "alpha", "background_color",
}
# networkx names for the same things
_ALIASES = {"edgecolors": "node_outline_color", "linewidths": "node_outline_width", "font_size": "label_size",
            "font_color": "label_color"}
STYLE_OPTIONS = tuple(sorted(_SETTABLE | set(_ALIASES) | {"with_labels", "connectionstyle"}))


# ------------------------------------------------------------------------------------------- encodings

def _check_cmap(cmap: Any) -> str:
    name = getattr(cmap, "name", cmap)  # accepts a matplotlib Colormap too
    if name not in COLORMAPS:
        raise ValueError(f"unknown colormap {name!r}; choose one of {', '.join(COLORMAPS)}")
    return name


def _domain(vmin: float | None, vmax: float | None) -> list[float] | None:
    if vmin is None and vmax is None:
        return None
    if vmin is None or vmax is None:
        raise ValueError("give both vmin and vmax, or neither")
    if not (math.isfinite(vmin) and math.isfinite(vmax)) or vmin > vmax:
        raise ValueError(f"vmin and vmax must be finite with vmin <= vmax, got {vmin}, {vmax}")
    return [float(vmin), float(vmax)]


def _colormap_options(cmap: Any, reverse: bool, vmin: float | None, vmax: float | None, missing: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if cmap is not None:
        out["colormap"] = _check_cmap(cmap)
    if reverse:
        out["reverse"] = True
    domain = _domain(vmin, vmax)
    if domain:
        out["domain"] = domain
    if missing is not None:
        out["missing"] = parse_color(missing)
    return out


def by_attribute(name: str, *, palette: str | Sequence[Any] | None = None, cmap: Any = None, reverse: bool = False,
                 vmin: float | None = None, vmax: float | None = None, scale: str = "auto", missing: Any = None) -> dict:
    """Color by a node (or edge) attribute: one color per distinct value, or a colormap when the attribute is a
    number with many values. `scale` forces "categorical" or "continuous"; `palette` is a name from PALETTES or a
    list of colors; `missing` colors elements without the attribute."""
    if scale not in ("auto", "categorical", "continuous"):
        raise ValueError(f"scale must be 'auto', 'categorical' or 'continuous', got {scale!r}")
    out: dict[str, Any] = {"kind": "attribute", "attribute": str(name), **_colormap_options(cmap, reverse, vmin, vmax, missing)}
    if scale != "auto":
        out["scale"] = scale
    if palette is not None:
        if isinstance(palette, str):
            if palette not in PALETTES:
                raise ValueError(f"unknown palette {palette!r}; choose one of {', '.join(PALETTES)}")
            out["palette"] = palette
        else:
            out["palette"] = [parse_color(c) for c in palette]
    return out


def _numeric_source(kind: str):
    def make(cmap: Any = "viridis", *, reverse: bool = False, vmin: float | None = None, vmax: float | None = None, missing: Any = None) -> dict:
        return {"kind": kind, **_colormap_options(cmap, reverse, vmin, vmax, missing)}
    make.__name__ = f"by_{kind}"
    return make


by_degree = _numeric_source("degree")
by_degree.__doc__ = "Color nodes by how many neighbours they have, through a colormap."
by_weight = _numeric_source("weight")
by_weight.__doc__ = "Color edges by their weight, through a colormap (unweighted edges keep their default color)."
by_time = _numeric_source("time")
by_time.__doc__ = "Color by time through a colormap: an edge's start time, or a node's first activity."


def by_time_bucket(buckets: int = 6, *, split: str = "time", cmap: Any = None, reverse: bool = False,
                   node_time: str = "first", missing: Any = None) -> dict:
    """Split the graph's time span into buckets (as the time ribbon does) and color by bucket.

    `split="events"` gives every bucket about the same number of events instead of the same duration. With no
    `cmap` the colors match the ribbon (blue early, orange late). For nodes, `node_time` picks the bucket of a node's
    "first" or "last" activity."""
    if not isinstance(buckets, (int, np.integer)) or isinstance(buckets, bool) or not 1 <= buckets <= 64:
        raise ValueError(f"buckets must be a whole number from 1 to 64, got {buckets!r}")
    if split not in ("time", "events"):
        raise ValueError(f"split must be 'time' or 'events', got {split!r}")
    if node_time not in ("first", "last"):
        raise ValueError(f"node_time must be 'first' or 'last', got {node_time!r}")
    out: dict[str, Any] = {"kind": "timeBucket", "buckets": int(buckets), "split": split, "nodeTime": node_time}
    if cmap is not None:
        out["colormap"] = _check_cmap(cmap)
    if reverse:
        out["reverse"] = True
    if missing is not None:
        out["missing"] = parse_color(missing)
    return out


def by_values(values: Sequence[float], cmap: Any = "viridis", *, reverse: bool = False, vmin: float | None = None,
              vmax: float | None = None, missing: Any = None) -> dict:
    """Color by one number per node (or edge) through a colormap. NaN leaves an element on its default color.
    Passing the list or array directly as `node_color` does the same."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError("values must be one-dimensional")
    return {"kind": "values", "values": arr, **_colormap_options(cmap, reverse, vmin, vmax, missing)}


def _size_encoding(kind: str, name: str | None, size_range: tuple[float, float], scale: str, vmin: float | None,
                   vmax: float | None, missing: float | None) -> dict:
    lo, hi = size_range
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo < 0 or hi < 0:
        raise ValueError(f"range must be two non-negative pixel sizes, got {size_range!r}")
    if scale not in ("linear", "sqrt", "log"):
        raise ValueError(f"scale must be 'linear', 'sqrt' or 'log', got {scale!r}")
    out: dict[str, Any] = {"kind": kind, "range": [float(lo), float(hi)]}
    if name is not None:
        out["attribute"] = str(name)
    if scale != "linear":
        out["scale"] = scale
    domain = _domain(vmin, vmax)
    if domain:
        out["domain"] = domain
    if missing is not None:
        out["missing"] = float(missing)
    return out


def size_by_attribute(name: str, range: tuple[float, float] = (4, 24), *, scale: str = "linear",  # noqa: A002
                      vmin: float | None = None, vmax: float | None = None, missing: float | None = None) -> dict:
    """Size by a numeric attribute, mapping its values onto `range` (pixels: node diameter or edge width)."""
    return _size_encoding("attribute", name, range, scale, vmin, vmax, missing)


def size_by_degree(range: tuple[float, float] = (4, 24), *, scale: str = "sqrt", vmin: float | None = None,  # noqa: A002
                   vmax: float | None = None) -> dict:
    """Size nodes by degree onto `range` (pixels)."""
    return _size_encoding("degree", None, range, scale, vmin, vmax, None)


def size_by_weight(range: tuple[float, float] = (1, 6), *, scale: str = "linear", vmin: float | None = None,  # noqa: A002
                   vmax: float | None = None, missing: float | None = None) -> dict:
    """Size edges by weight onto `range` (pixel widths)."""
    return _size_encoding("weight", None, range, scale, vmin, vmax, missing)


def size_by_time(range: tuple[float, float] = (1, 6), *, scale: str = "linear", vmin: float | None = None,  # noqa: A002
                 vmax: float | None = None) -> dict:
    """Size by time onto `range`: an edge's start time, or a node's first activity."""
    return _size_encoding("time", None, range, scale, vmin, vmax, None)


def shape_by_attribute(name: str, shapes: Sequence[str] | None = None) -> dict:
    """One shape per distinct value of an attribute, cycling through `shapes` (default: all of SHAPES)."""
    out: dict[str, Any] = {"kind": "attribute", "attribute": str(name)}
    if shapes is not None:
        out["shapes"] = [_shape_name(s) for s in shapes]
    return out


def _shape_name(shape: Any) -> str:
    if isinstance(shape, str):
        name = _MARKERS.get(shape, shape)
        if name in SHAPES:
            return name
    raise ValueError(f"unknown node shape {shape!r}; use one of {', '.join(SHAPES)} (or the matplotlib markers o s ^ d +)")


# --------------------------------------------------------------------------------------- graph context

class _Graph:
    """The pieces of a graph the resolvers need, computed once."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.nodes = list(graph.nodes())
        self.key_to_id = {n.key: n.id for n in self.nodes}
        connectors = list(graph.connectors())
        self.edges = [c for c in connectors if len(c.endpoints) == 2]  # what edge styles apply to, in wire order
        self.has_time = any(c.t_start != NEG_INF or c.t_end != POS_INF for c in connectors)
        self._pairs: dict[tuple[Any, Any], list[int]] | None = None

    def count(self, target: str) -> int:
        return len(self.nodes) if target == "node" else len(self.edges)

    def attribute_names(self, target: str) -> set[str]:
        items = self.nodes if target == "node" else self.edges
        return {k for item in items for k in item.attrs}

    def edge_lookup(self, pair: tuple[Any, Any]) -> list[int]:
        if self._pairs is None:
            self._pairs = {}
            for i, c in enumerate(self.edges):
                a, b = (self.nodes[c.endpoints[0]].key, self.nodes[c.endpoints[1]].key)
                self._pairs.setdefault((a, b), []).append(i)
                if not c.directed:
                    self._pairs.setdefault((b, a), []).append(i)
        return self._pairs.get(pair, [])


def _label(target: str) -> str:
    return "node" if target == "node" else "edge"


def _validate_encoding(target: str, enc: dict, g: _Graph, what: str) -> dict:
    """Check an encoding dict against the graph so mistakes surface here."""
    kind = enc.get("kind")
    if kind == "attribute":
        name = enc.get("attribute")
        known = g.attribute_names(target)
        if name not in known:
            hint = f"; {_label(target)} attributes are: {', '.join(sorted(known))}" if known else f"; the graph has no {_label(target)} attributes"
            raise ValueError(f"{what}: no {_label(target)} has an attribute named {name!r}{hint}")
    elif kind == "degree" and target != "node":
        raise ValueError(f"{what}: degree is a node property; edges can use weight, time or an attribute")
    elif kind == "weight" and target != "edge":
        raise ValueError(f"{what}: weight is an edge property; nodes can use degree, time or an attribute")
    elif kind == "weight" and not any(c.weight is not None and not (isinstance(c.weight, float) and math.isnan(c.weight)) for c in g.edges):
        raise ValueError(f"{what}: no edge has a weight")
    elif kind in ("time", "timeBucket") and not g.has_time:
        raise ValueError(f"{what}: the graph has no time information; add t_start/t_end to edges (see from_temporal_edgelist)")
    elif kind in ("values", "pixels", "colors"):
        n = g.count(target)
        size = len(enc["colors"] if kind == "colors" else enc["values"])
        if size not in ((n, n * 4) if kind == "colors" else (n,)):
            raise ValueError(f"{what}: has {size} entries but the graph has {n} {_label(target)}s")
    return enc


def _is_encoding(value: Any) -> bool:
    return isinstance(value, dict) and "kind" in value


def _is_single_color(value: Any) -> bool:
    if isinstance(value, str):
        return True
    return isinstance(value, tuple) and len(value) in (3, 4) and all(isinstance(c, (int, float, np.number)) and not isinstance(c, bool) for c in value)


def _is_legacy_color_list(value: Any, n: int) -> bool:
    """`[r, g, b]` / `[r, g, b, a]` as a plain list has always meant one color. It stays one color unless the
    graph has exactly that many nodes (then it is one entry per node, as for a list of any other length)."""
    return (isinstance(value, list) and len(value) in (3, 4) and len(value) != n
            and all(isinstance(c, (int, float, np.number)) and not isinstance(c, bool) for c in value))


def _numeric_array(value: Any) -> np.ndarray | None:
    """A 1-D numeric array if `value` is a list/array of plain numbers (not booleans), else None."""
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        arr = np.asarray(value)
    except (ValueError, TypeError):
        return None
    if arr.ndim == 1 and arr.dtype.kind in "iuf":
        return arr.astype(np.float64)
    return None


def _colors_from_items(items: Sequence[Any], what: str) -> tuple[np.ndarray, np.ndarray]:
    """Flat RGBA float32 plus a 'present' mask, from a list of color-likes (None = leave the default)."""
    flat = np.zeros(len(items) * 4, dtype=np.float32)
    present = np.zeros(len(items), dtype=np.uint8)
    for i, item in enumerate(items):
        if item is None:
            continue
        try:
            flat[i * 4:i * 4 + 4] = parse_color(item)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{what}: entry {i} is not a color ({exc})") from None
        present[i] = 1
    return flat, present


def _color_encoding(target: str, value: Any, g: _Graph, *, cmap: Any, vmin: float | None, vmax: float | None, what: str) -> Any:
    if value is None or isinstance(value, _Reset):
        return value
    if _is_encoding(value):
        return _validate_encoding(target, value, g, what)
    n = g.count(target)
    if _is_single_color(value) or _is_legacy_color_list(value, n):
        return {"kind": "constant", "color": parse_color(value)}
    if isinstance(value, Mapping):
        if target == "node":
            index = {}
            for key, v in value.items():
                if key not in g.key_to_id:
                    raise KeyError(f"{what}: unknown node {key!r}")
                index[g.key_to_id[key]] = v
        else:
            index = {}
            for pair, v in value.items():
                if not (isinstance(pair, tuple) and len(pair) == 2):
                    raise ValueError(f"{what}: edge keys must be (source, target) pairs, got {pair!r}")
                hits = g.edge_lookup(pair)
                if not hits:
                    raise KeyError(f"{what}: no edge between {pair[0]!r} and {pair[1]!r}")
                for i in hits:
                    index[i] = v
        items = [index.get(i) for i in range(n)]
        if items and all(v is None or (isinstance(v, (int, float, np.number)) and not isinstance(v, bool)) for v in items):
            numbers = np.array([np.nan if v is None else v for v in items], dtype=np.float64)
            return {"kind": "values", "values": numbers, "colormap": _check_cmap(cmap or "viridis"), **_colormap_options(None, False, vmin, vmax, None)}
        flat, present = _colors_from_items(items, what)
        return {"kind": "colors", "colors": flat, "present": present}
    if isinstance(value, (list, np.ndarray)):
        if len(value) != n:
            raise ValueError(f"{what} has {len(value)} entries but the graph has {n} {_label(target)}s; give one per {_label(target)}, "
                             f"or a single color as a string or tuple")
        arr = _numeric_array(value)
        if arr is not None:
            return {"kind": "values", "values": arr, "colormap": _check_cmap(cmap or "viridis"), **_colormap_options(None, False, vmin, vmax, None)}
        as_array = value if isinstance(value, np.ndarray) else None
        if as_array is not None and as_array.ndim == 2 and as_array.shape[1] in (3, 4) and as_array.dtype.kind in "iuf":
            items = [tuple(row) for row in as_array.tolist()]
        else:
            items = list(value)
        flat, present = _colors_from_items(items, what)
        enc: dict[str, Any] = {"kind": "colors", "colors": flat}
        if not present.all():
            enc["present"] = present
        return enc
    raise TypeError(f"{what} must be a color, a list/array with one entry per {_label(target)}, a dict, or an encoding like by_degree(); "
                    f"got {type(value).__name__}")


def _size_arg(target: str, value: Any, g: _Graph, what: str) -> Any:
    if value is None or isinstance(value, _Reset):
        return value
    if _is_encoding(value):
        return _validate_encoding(target, value, g, what)
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{what} must be a non-negative number of pixels, got {value}")
        return float(value)
    n = g.count(target)
    sizes: np.ndarray
    if isinstance(value, Mapping):
        sizes = np.full(n, np.nan)
        for key, v in value.items():
            if target == "node":
                if key not in g.key_to_id:
                    raise KeyError(f"{what}: unknown node {key!r}")
                sizes[g.key_to_id[key]] = v
            else:
                hits = g.edge_lookup(key)
                if not hits:
                    raise KeyError(f"{what}: no edge between {key[0]!r} and {key[1]!r}")
                sizes[hits] = v
    else:
        listed = _numeric_array(value)
        if listed is None:
            raise TypeError(f"{what} must be a number of pixels, a list/array of numbers, a dict, or an encoding like size_by_degree(); got {type(value).__name__}")
        if len(listed) != n:
            raise ValueError(f"{what} has {len(listed)} entries but the graph has {n} {_label(target)}s")
        sizes = listed
    arr = sizes
    finite = arr[np.isfinite(arr)]
    if (finite < 0).any():
        raise ValueError(f"{what} must not contain negative sizes")
    return {"kind": "pixels", "values": arr}


def _shape_arg(value: Any, g: _Graph) -> Any:
    if value is None or isinstance(value, _Reset):
        return value
    if _is_encoding(value):
        return _validate_encoding("node", value, g, "node_shape")
    if isinstance(value, str):
        return _shape_name(value)
    n = g.count("node")
    if isinstance(value, Mapping):
        names = ["circle"] * n
        for key, v in value.items():
            if key not in g.key_to_id:
                raise KeyError(f"node_shape: unknown node {key!r}")
            names[g.key_to_id[key]] = _shape_name(v)
        return {"kind": "values", "values": names}
    if isinstance(value, (list, tuple, np.ndarray)):
        if len(value) != n:
            raise ValueError(f"node_shape has {len(value)} entries but the graph has {n} nodes")
        return {"kind": "values", "values": ["circle" if v is None else _shape_name(v) for v in value]}
    raise TypeError(f"node_shape must be a shape name, a list/dict of them, or shape_by_attribute(); got {type(value).__name__}")


def _unit(name: str, value: Any) -> Any:
    if value is None or isinstance(value, _Reset):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a number from 0 to 1, got {value!r}")
    return float(value)


def _curvature(value: Any) -> Any:
    if value is None or isinstance(value, _Reset):
        return value
    if isinstance(value, str):  # networkx style: "arc3,rad=0.2"
        m = re.search(r"rad\s*=\s*(-?\d+(?:\.\d+)?)", value)
        if not m:
            raise ValueError(f"connectionstyle {value!r} not understood; use e.g. 'arc3,rad=0.2', or pass edge_curvature=0.2")
        value = float(m.group(1))
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)) or not -2 <= value <= 2:
        raise ValueError(f"edge_curvature must be a number between -2 and 2 (about 0.2 is typical), got {value!r}")
    return float(value)


# ---------------------------------------------------------------------------------------- build / merge

def build_style(graph: Graph | _Graph, **options: Any) -> dict[str, Any]:
    """Turn networkx-style keyword arguments into a viewer style spec (as plain data).

    Omitted or None options are left unchanged; pass `RESET` to restore an option's default. See STYLE_OPTIONS for
    the names. The graph is used to check the arguments (counts, attribute names, time)."""
    g = graph if isinstance(graph, _Graph) else _Graph(graph)
    opts = dict(options)
    for alias, real in _ALIASES.items():
        if alias in opts:
            if real in opts:
                raise TypeError(f"give either {alias!r} or {real!r}, not both")
            opts[real] = opts.pop(alias)
    if "with_labels" in opts:
        with_labels = opts.pop("with_labels")
        if with_labels is not None:
            opts.setdefault("label_mode", "all" if with_labels else "hover")
    if "connectionstyle" in opts:
        cs = opts.pop("connectionstyle")
        if cs is not None:
            opts.setdefault("edge_curvature", cs)
    unknown = sorted(set(opts) - _SETTABLE)
    if unknown:
        raise TypeError(f"unknown style option{'s' if len(unknown) > 1 else ''} {', '.join(map(repr, unknown))}; valid options: {', '.join(STYLE_OPTIONS)}")

    alpha = opts.get("alpha")
    node: dict[str, Any] = {}
    edge: dict[str, Any] = {}

    def put(group: dict, key: str, value: Any) -> None:
        if value is not None:
            group[key] = None if isinstance(value, _Reset) else value

    put(node, "color", _color_encoding("node", opts.get("node_color"), g, cmap=opts.get("cmap"), vmin=opts.get("vmin"), vmax=opts.get("vmax"), what="node_color"))
    put(node, "size", _size_arg("node", opts.get("node_size"), g, "node_size"))
    put(node, "shape", _shape_arg(opts.get("node_shape"), g))
    put(node, "opacity", _unit("node_alpha", opts.get("node_alpha", alpha)))
    outline_color, outline_width = opts.get("node_outline_color"), opts.get("node_outline_width")
    if isinstance(outline_color, _Reset) or isinstance(outline_width, _Reset):
        node["outline"] = None
    elif outline_color is not None or outline_width is not None:
        outline: dict[str, Any] = {}
        if outline_color is not None:
            outline["color"] = parse_color(outline_color)
        if outline_width is not None:
            if not isinstance(outline_width, (int, float, np.number)) or outline_width < 0:
                raise ValueError(f"node_outline_width must be a non-negative number of pixels, got {outline_width!r}")
            outline["width"] = float(outline_width)
        node["outline"] = outline
    label: dict[str, Any] = {}
    mode = opts.get("label_mode")
    if mode is not None and not isinstance(mode, _Reset):
        if mode not in ("hover", "all", "none"):
            raise ValueError(f"label_mode must be 'hover', 'all' or 'none', got {mode!r}")
        label["mode"] = mode
    if opts.get("label_size") is not None:
        size = opts["label_size"]
        if not isinstance(size, (int, float, np.number)) or not 4 <= size <= 72:
            raise ValueError(f"label_size must be between 4 and 72 pixels, got {size!r}")
        label["fontSize"] = float(size)
    if opts.get("label_color") is not None:
        label["color"] = parse_color(opts["label_color"])
    if opts.get("label_halo") is not None:
        label["halo"] = bool(opts["label_halo"])
    if opts.get("label_attribute") is not None:
        label["attribute"] = None if isinstance(opts["label_attribute"], _Reset) else str(opts["label_attribute"])
    if label:
        node["label"] = label

    put(edge, "color", _color_encoding("edge", opts.get("edge_color"), g, cmap=opts.get("edge_cmap"), vmin=opts.get("edge_vmin"), vmax=opts.get("edge_vmax"), what="edge_color"))
    put(edge, "width", _size_arg("edge", opts.get("edge_width"), g, "edge_width"))
    put(edge, "opacity", _unit("edge_alpha", opts.get("edge_alpha", alpha)))
    put(edge, "curvature", _curvature(opts.get("edge_curvature")))
    scale = opts.get("arrow_scale")
    if scale is not None and not isinstance(scale, _Reset):
        if not isinstance(scale, (int, float, np.number)) or not 0 < scale <= 20:
            raise ValueError(f"arrow_scale must be a positive number up to 20, got {scale!r}")
        edge["arrowScale"] = float(scale)
    elif isinstance(scale, _Reset):
        edge["arrowScale"] = None

    spec: dict[str, Any] = {}
    if node:
        spec["node"] = node
    if edge:
        spec["edge"] = edge
    if opts.get("background_color") is not None:
        bg = opts["background_color"]
        spec["background"] = None if isinstance(bg, _Reset) else parse_color(bg)
    return spec


def merge_style(current: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """Merge `update` into `current` the way the viewer does: omitted fields stay, None clears a field or group."""
    merged: dict[str, Any] = {}
    for group in ("node", "edge"):
        base = dict(current.get(group) or {})
        if group in update:
            change = update[group]
            if change is None:
                base = {}
            else:
                for key, value in change.items():
                    if value is None:
                        base.pop(key, None)
                    elif key == "label" and isinstance(base.get("label"), dict) and isinstance(value, dict):
                        base["label"] = {**base["label"], **value}
                    else:
                        base[key] = value
        if base:
            merged[group] = base
    background = update["background"] if "background" in update else current.get("background")
    if background is not None:
        merged["background"] = background
    return merged


# --------------------------------------------------------------------------------------- controller

class StyleController:
    """The current style of one viewer session, and the way to change it while the viewer is open.

    Changes are pushed to every connected tab, and replayed to tabs that connect later. Safe to call from any
    thread (a notebook cell, for example)."""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph
        self._ctx: _Graph | None = None
        self._lock = threading.Lock()
        self._spec: dict[str, Any] = {}
        self._paint: dict[int, tuple[float, ...]] = {}
        self._version = 0
        self._push: Callable[[int, list[bytes]], None] | None = None

    def bind(self, push: Callable[[int, list[bytes]], None]) -> None:
        """Where messages go once a server exists: `push(version, messages)`."""
        self._push = push

    def _context(self) -> _Graph:
        if self._ctx is None:
            self._ctx = _Graph(self._graph)
        return self._ctx

    def _send(self, messages: list[bytes]) -> None:
        self._version += 1
        version = self._version
        if self._push is not None:
            self._push(version, messages)

    # -- changing the style
    def update(self, **options: Any) -> None:
        """Apply networkx-style options (see build_style); fields you omit stay as they are."""
        with self._lock:
            update = build_style(self._context(), **options)
            if not update:
                return
            self._spec = merge_style(self._spec, update)
            self._send([encode_style("set", spec=update)])

    def paint(self, nodes: Any, color: Any = None) -> None:
        """Give specific nodes (by key) a color of their own, over any encoding.
        `paint(["a", "b"], "red")`, `paint({"a": "red", "b": "#00f"})`, or color=None to remove."""
        ctx = self._context()
        groups: dict[tuple[float, ...] | None, list[int]] = {}
        if isinstance(nodes, Mapping):
            if color is not None:
                raise TypeError("pass either a dict of node -> color, or nodes and one color, not both")
            pairs = list(nodes.items())
        else:
            keys = list(nodes) if isinstance(nodes, (list, tuple, set, np.ndarray)) else [nodes]
            pairs = [(k, color) for k in keys]
        for key, c in pairs:
            if key not in ctx.key_to_id:
                raise KeyError(f"unknown node {key!r}")
            groups.setdefault(None if c is None else tuple(parse_color(c)), []).append(ctx.key_to_id[key])
        with self._lock:
            messages = []
            for rgba, ids in groups.items():
                for i in ids:
                    if rgba is None:
                        self._paint.pop(i, None)
                    else:
                        self._paint[i] = rgba
                messages.append(encode_style("paint", ids=np.array(ids, dtype=np.uint32), color=None if rgba is None else list(rgba)))
            if messages:
                self._send(messages)

    def clear_paint(self) -> None:
        with self._lock:
            self._paint.clear()
            self._send([encode_style("clear_paint")])

    def reset(self) -> None:
        """Back to the default look, and forget all painted nodes."""
        with self._lock:
            self._spec = {}
            self._paint.clear()
            self._send([encode_style("reset")])

    # -- reading it back
    def spec(self) -> dict[str, Any]:
        with self._lock:
            return merge_style({}, self._spec)

    def painted(self) -> dict[Any, tuple[float, ...]]:
        """Painted nodes as {key: (r, g, b, a)}."""
        with self._lock:
            nodes = self._context().nodes
            return {nodes[i].key: rgba for i, rgba in self._paint.items() if i < len(nodes)}

    def replay(self) -> tuple[int, list[bytes]]:
        """Messages that recreate the current style in a freshly connected viewer, with the version they represent."""
        with self._lock:
            messages = [encode_style("replace", spec=self._spec)] if self._spec else []
            by_color: dict[tuple[float, ...], list[int]] = {}
            for i, rgba in self._paint.items():
                by_color.setdefault(rgba, []).append(i)
            for rgba, ids in by_color.items():
                messages.append(encode_style("paint", ids=np.array(sorted(ids), dtype=np.uint32), color=list(rgba)))
            return self._version, messages
