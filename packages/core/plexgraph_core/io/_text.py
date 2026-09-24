"""Shared parsing for delimited text edge-list files (read_edgelist, read_temporal_edgelist): line reading (with the
comment/blank-line/header handling and BOM safety every one of them needs), the two value-coercion rules
("007" is a name, 24811812513198111524 is a name, 3 is a node id" / "3 is an int, 3.5 is a float, x is text") that
keep the readers behaving the same way, and two small helpers (node dedup, hyperedge-skip warnings) shared by every
loader/exporter in this package. Not part of the public API.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Hashable, Iterator

from plexgraph_core.model.ir import Graph


def node_key(text: str, int_nodes: bool) -> Hashable:
    """A plain integer (no leading zeros, within 64 bits) becomes an int node id unless int_nodes=False; anything
    else — including a number too large for a 64-bit int, which cannot be a real id — stays text."""
    if int_nodes:
        # Strip at most one leading '-' before checking digits: text.lstrip("-") would strip every leading '-',
        # so "--5" would pass an isdigit() check and then crash int("--5") with an uncaught ValueError.
        body = text[1:] if text.startswith("-") else text
        if body.isdigit():
            value = int(text)
            # A signed 64-bit int's range is [-2**63, 2**63 - 1] -- asymmetric, so abs(value) < 2**63 would wrongly
            # reject the valid minimum -2**63 (abs(-2**63) == 2**63, which fails a strict '<').
            if str(value) == text and -(2**63) <= value < 2**63:
                return value
    return text


def ensure_node(graph: Graph, known: set[Hashable], key: Hashable) -> None:
    """Add `key` to `graph` the first time it's seen, tracking membership in the caller's `known` set so repeated
    keys (the common case: every edge mentions nodes already added by an earlier edge) don't hit `add_node`'s
    duplicate-key check. Shared by every loader that builds nodes from edge endpoints."""
    if key not in known:
        graph.add_node(key)
        known.add(key)


def warn_skipped_hyperedges(caller: str, count: int, reason: str) -> None:
    """Warn that `count` hyperedges (more than 2 endpoints) were skipped by `caller`, because `reason` -- shared by
    every exporter to a plain-edge format, none of which can represent a hyperedge."""
    if count:
        warnings.warn(f"{caller}: skipped {count} hyperedge(s) (more than 2 endpoints); {reason}", UserWarning, stacklevel=3)


def attr_value(text: str) -> Any:
    """An attribute column's text, read as an int, else a float, else left as text."""
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def read_rows(
    path: str | Path,
    *,
    delimiter: str | None,
    comments: str | None,
    header: bool,
) -> Iterator[tuple[list[str], int]]:
    """Yield (fields, line_number) for every data line of a delimited text file: comment and blank lines skipped,
    the first data line skipped too if header=True, 1-indexed line numbers as in the file (so a caller can report
    exactly where a bad row came from). delimiter=None splits on any whitespace; an explicit delimiter (e.g. "," or
    "\\t") only strips the line ending, since a trailing empty field ("a,b,") is then a real, empty column.
    """
    skipped_header = not header
    # utf-8-sig: a file saved by Excel starts with a byte-order mark, which would otherwise become part of the
    # first name.
    with open(path, encoding="utf-8-sig") as fh:
        for number, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n") if delimiter else raw.strip()
            if not line.strip() or (comments and line.lstrip().startswith(comments)):
                continue
            if not skipped_header:
                skipped_header = True
                continue
            yield [p.strip() for p in (line.split(delimiter) if delimiter else line.split())], number


def read_header(path: str | Path, *, delimiter: str | None, comments: str | None) -> list[str] | None:
    """The first data line's fields, for a caller that wants column names from a header row (e.g. a node-attribute
    table). None if the file has no data lines. Independent of whether the caller will itself skip that line as a
    header when it reads the file for real — call this first, then read_rows(..., header=True) to skip the same
    line during the real pass."""
    for parts, _ in read_rows(path, delimiter=delimiter, comments=comments, header=False):
        return parts
    return None
