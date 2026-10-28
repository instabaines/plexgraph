"""Shared parsing for delimited text edge-list files (read_edgelist, read_temporal_edgelist): line reading (with the
comment/blank-line/header handling and BOM safety every one of them needs), the two value-coercion rules
("007" is a name, 24811812513198111524 is a name, 3 is a node id" / "3 is an int, 3.5 is a float, x is text") that
keep the readers behaving the same way, and two small helpers (node dedup, hyperedge-skip warnings) shared by every
loader/exporter in this package. Not part of the public API.
"""

from __future__ import annotations

import csv
import warnings
from pathlib import Path
from typing import Any, Hashable, Iterable, Iterator, Sequence

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


def select_columns(selector: bool | str | Sequence[str] | None, columns: Iterable[str], exclude: Iterable[str] = ()) -> list[str]:
    """Normalize a pandas `edge_attr=`/`attrs=`-style column selector against an actual DataFrame's `columns`:
    `True` means every column not in `exclude` (e.g. the source/target/key columns, which are never also an
    attribute); `None`/`False` means none; a single name means just that column; a list means exactly those,
    taken as given (not validated against `columns` -- the caller's own row access already raises a clear
    `KeyError` for a name that doesn't exist). Shared by every pandas-DataFrame loader in this package."""
    if selector is True:
        excluded = set(exclude)
        return [c for c in columns if c not in excluded]
    if selector is None or selector is False:
        return []
    if isinstance(selector, str):
        return [selector]
    return list(selector)


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
    exactly where a bad row came from). delimiter=None splits on any whitespace -- no quoting rules apply, since a
    real quoted field is exactly how you'd put whitespace inside a value in the first place, so there is no
    ambiguity to resolve. An explicit delimiter (e.g. "," or "\\t") is read as real CSV: a comma or tab inside a
    "quoted field" is part of the value, not a column break, and a doubled quote ("") inside one is a literal
    quote character -- the standard CSV quoting rules (RFC 4180), via the stdlib `csv` module rather than a plain
    str.split(), which would silently mis-split exactly this kind of field. Must be a single character; csv.reader
    itself enforces that. A trailing empty field ("a,b,") is a real, empty column either way.
    """
    if delimiter is not None and len(delimiter) != 1:
        raise ValueError(f"delimiter must be a single character for a real CSV-style delimiter, got {delimiter!r}")
    skipped_header = not header
    # utf-8-sig: a file saved by Excel starts with a byte-order mark, which would otherwise become part of the
    # first name. newline="": required by the csv module so it -- not universal-newline translation -- controls
    # line endings, including a newline embedded inside a quoted field.
    with open(path, encoding="utf-8-sig", newline="" if delimiter else None) as fh:
        # Comment/blank detection happens on the raw line, before any CSV unquoting -- a comment marker is a
        # line-level convention, not a field value, so a quoted field that happens to start with the marker
        # (e.g. a node literally named "#1") must not be mistaken for one.
        def kept_lines() -> Iterator[tuple[int, str]]:
            for number, raw in enumerate(fh, 1):
                if raw.strip() and not (comments and raw.lstrip().startswith(comments)):
                    yield number, raw

        if delimiter is None:
            rows: Iterable[tuple[int, list[str]]] = ((number, raw.split()) for number, raw in kept_lines())
        else:
            # csv.reader may consume more than one kept line per row it returns (a multi-line quoted field);
            # `start` tracks the first raw line that went into the row currently being assembled, so a reported
            # line number is still the row's true start even then.
            start: list[int | None] = [None]

            def text_lines() -> Iterator[str]:
                for number, raw in kept_lines():
                    if start[0] is None:
                        start[0] = number
                    yield raw

            def numbered_rows() -> Iterator[tuple[int, list[str]]]:
                for fields in csv.reader(text_lines(), delimiter=delimiter):
                    assert start[0] is not None
                    yield start[0], fields
                    start[0] = None

            rows = numbered_rows()
        for number, fields in rows:
            stripped = [f.strip() for f in fields]
            if not any(stripped):
                continue
            if not skipped_header:
                skipped_header = True
                continue
            yield stripped, number


def read_header(path: str | Path, *, delimiter: str | None, comments: str | None) -> list[str] | None:
    """The first data line's fields, for a caller that wants column names from a header row (e.g. a node-attribute
    table). None if the file has no data lines. Independent of whether the caller will itself skip that line as a
    header when it reads the file for real — call this first, then read_rows(..., header=True) to skip the same
    line during the real pass."""
    for parts, _ in read_rows(path, delimiter=delimiter, comments=comments, header=False):
        return parts
    return None
