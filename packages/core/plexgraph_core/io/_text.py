"""Shared parsing for delimited text edge-list files (read_edgelist, read_temporal_edgelist): line reading (with the
comment/blank-line/header handling and BOM safety every one of them needs), and the two value-coercion rules
("007" is a name, 24811812513198111524 is a name, 3 is a node id" / "3 is an int, 3.5 is a float, x is text") that
keep the readers behaving the same way. Not part of the public API.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Hashable, Iterator

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def node_key(text: str, int_nodes: bool) -> Hashable:
    """A plain integer (no leading zeros, within 64 bits) becomes an int node id unless int_nodes=False; anything
    else — including a number too large for a 64-bit int, which cannot be a real id — stays text."""
    if int_nodes and text.lstrip("-").isdigit() and str(int(text)) == text and abs(int(text)) < 2**63:
        return int(text)
    return text


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
