"""Pick a native reader by file extension, for callers that only have a path -- namely the
desktop app's `"load_file"` wire message (a native Open-File dialog hands the sidecar a path with
no other context about its format).

There is no content-sniffing fallback: an unrecognised extension raises rather than guessing, since
a wrong guess silently misreading a file is worse than a clear error naming the unsupported
extension. A caller that knows more about a file than its extension conveys (a non-default
delimiter, no header row, a custom column order, a layer/time key) should call that reader
directly instead of going through this dispatcher.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path

from plexgraph_core.io.dot import read_dot
from plexgraph_core.io.edgelist import read_edgelist
from plexgraph_core.io.gexf import read_gexf
from plexgraph_core.io.gml import read_gml
from plexgraph_core.io.graphml import read_graphml
from plexgraph_core.io.json_io import read_json
from plexgraph_core.io.pajek import read_pajek
from plexgraph_core.model.ir import Graph


class UnknownGraphFormatError(ValueError):
    """Raised by `read_graph` for a file extension none of plexgraph's native readers claim."""


_READERS = {
    ".gml": read_gml,
    ".gexf": read_gexf,
    ".graphml": read_graphml,
    ".dot": read_dot,
    ".gv": read_dot,
    ".net": read_pajek,
    ".json": read_json,
    ".txt": read_edgelist,
    ".edgelist": read_edgelist,
    # Real RFC-4180 parsing (via read_edgelist's csv-module path), not str.split(). header=True and
    # columns=(0, 1) (read_edgelist's own default) match the overwhelmingly common shape of a
    # hand-authored or pandas-exported edge-list CSV: a header row, source in the first column,
    # target in the second. A file with a different column order/weight column/no header needs
    # `read_edgelist(path, delimiter=",", ...)` called directly with the right kwargs.
    ".csv": partial(read_edgelist, delimiter=",", header=True),
    ".tsv": partial(read_edgelist, delimiter="\t", header=True),
}


def read_graph(path: str | Path) -> Graph:
    """Read `path` into a Graph using the native reader its extension names (case-insensitive).

    `.json` is read as a plexgraph graph/project file (`read_json`). `.txt`/`.edgelist` are read as
    a plain whitespace-delimited edgelist with every other `read_edgelist` option left at its
    default. `.csv`/`.tsv` are read as a comma-/tab-delimited edgelist with a header row and
    source/target in the first two columns (`read_edgelist(path, delimiter=..., header=True)`).
    Raises `UnknownGraphFormatError` for any other extension."""
    suffix = Path(path).suffix.lower()
    reader = _READERS.get(suffix)
    if reader is None:
        raise UnknownGraphFormatError(
            f"no native reader for {suffix!r} files (path: {path}); known extensions: "
            f"{', '.join(sorted(_READERS))}"
        )
    return reader(path)
