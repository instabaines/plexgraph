"""Load temporal networks from contact sequences.

Temporal networks are usually published as one row per event, `u v t`: nodes u and v interacted at
time t. That is an *instantaneous contact*. Interval data (`u v start end`) is also supported. Both
become connectors with `t_start`/`t_end`; a contact at time t has `t_start == t_end == t` unless a
`duration` is given.

Do not pass `(u, v, t)` rows to `from_edgelist`: its third value is a *weight*.

Accepted timestamps, the same in every loader here:

- **Datetimes** (`datetime`, `date`, `numpy.datetime64`, pandas `Timestamp`) and **ISO 8601 text**
  ("2013-12-31 16:39:18", "2020-01-01T10:00:00Z", "2020-01-01"). Naive times are taken as UTC.
- **Text in another layout**: give `time_format`, a `strptime` pattern such as "%m/%d/%Y".
- **Plain numbers**: kept as they are, and the time axis shows the raw values. If they are Unix time,
  say so with `time_unit` ("epoch_seconds", "epoch_milliseconds", "epoch_microseconds" or
  "epoch_nanoseconds"): they are converted to seconds and the viewer shows dates.

Calendar times are stored as Unix seconds, and the graph is marked `time_unit = "epoch_seconds"`.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
import warnings
from pathlib import Path
from typing import Any, Hashable, Iterable, Mapping, Sequence

import numpy as np

from plexgraph_core.model.ir import Graph

# Seconds per unit of each declared numeric epoch.
_EPOCH_SECONDS = {"epoch_seconds": 1.0, "epoch_milliseconds": 1e-3, "epoch_microseconds": 1e-6, "epoch_nanoseconds": 1e-9}
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


class _Clock:
    """Turns whatever a row holds into seconds, according to the loader's `time_unit` / `time_format`."""

    def __init__(self, time_unit: str | None, time_format: str | None) -> None:
        if time_unit is not None and time_unit not in _EPOCH_SECONDS:
            raise ValueError(f"time_unit must be one of {sorted(_EPOCH_SECONDS)} or None, got {time_unit!r}")
        self.time_unit = time_unit
        self.time_format = time_format
        self.scale = _EPOCH_SECONDS.get(time_unit or "", 1.0)
        self.calendar = False  # saw a datetime or date text
        self.smallest_plain: float | None = None  # smallest undeclared number, to spot Unix time

    def seconds(self, value: Any, where: str) -> float:
        if isinstance(value, np.datetime64):
            self.calendar = True
            return self._finite(float(value.astype("datetime64[us]").astype("int64")) / 1e6, value, where)
        if isinstance(value, _dt.datetime):
            self.calendar = True
            if value.tzinfo is None:
                value = value.replace(tzinfo=_dt.timezone.utc)
            return self._finite(value.timestamp(), value, where)
        if isinstance(value, _dt.date):
            self.calendar = True
            return self._finite(_dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc).timestamp(), value, where)
        if isinstance(value, str):
            text = value.strip()
            if self.time_format is not None:
                try:
                    return self.seconds(_dt.datetime.strptime(text, self.time_format), where)
                except ValueError:
                    raise ValueError(f"{where}: {text!r} does not match time_format {self.time_format!r}") from None
            if _NUMBER.match(text):
                return self._number(float(text), where)
            try:
                return self.seconds(_dt.datetime.fromisoformat(text), where)
            except ValueError:
                raise ValueError(f"{where}: cannot read timestamp {text!r}; use ISO 8601 text or pass time_format") from None
        if isinstance(value, (bool, bytes)) or value is None:
            raise TypeError(f"{where}: timestamp must be a number, datetime or text, got {value!r}")
        return self._number(float(value), where)

    def _number(self, number: float, where: str) -> float:
        if self.time_unit is None and math.isfinite(number):
            self.smallest_plain = number if self.smallest_plain is None else min(self.smallest_plain, number)
        return self._finite(number * self.scale, number, where)

    @staticmethod
    def _finite(seconds: float, original: Any, where: str) -> float:
        if not math.isfinite(seconds):
            raise ValueError(f"{where}: timestamp must be finite, got {original!r}")
        return seconds

    def finish(self, graph: Graph) -> None:
        """Mark calendar/epoch graphs so the viewer shows dates, and flag numbers that look like Unix time."""
        if self.calendar or self.time_unit is not None:
            graph.time_unit = "epoch_seconds"
        elif self.smallest_plain is not None and self.smallest_plain >= 1e9:
            guess = ("epoch_seconds" if self.smallest_plain < 1e11 else "epoch_milliseconds" if self.smallest_plain < 1e14
                     else "epoch_microseconds" if self.smallest_plain < 1e17 else "epoch_nanoseconds")
            warnings.warn(f"every timestamp is at least {self.smallest_plain:g}, which looks like Unix time; "
                          f"pass time_unit={guess!r} to convert them and show dates in the viewer", UserWarning, stacklevel=4)


def from_temporal_edgelist(
    events: Iterable[Sequence[Any]],
    *,
    directed: bool = False,
    intervals: bool = False,
    duration: float | None = None,
    time_unit: str | None = None,
    time_format: str | None = None,
) -> Graph:
    """Build a Graph from temporal events.

    Each event is `(u, v, t)`, or `(u, v, start, end)` when `intervals=True`; either may end with a
    dict of connector attributes. `duration` gives contacts a lifetime `[t, t + duration]` (in seconds
    once times are converted; the default is instantaneous). Nodes are created the first time they
    appear, keyed by the value given. Repeated `(u, v)` pairs stay separate events.

    Times may be datetimes or ISO text (see the module docs). Declare plain Unix numbers with `time_unit`
    and non-ISO text with `time_format`.
    """
    if intervals and duration is not None:
        raise ValueError("duration applies to instantaneous contacts; with intervals=True give (u, v, start, end)")
    if duration is not None and (not math.isfinite(duration) or duration < 0):
        raise ValueError("duration must be a non-negative finite number")
    clock = _Clock(time_unit, time_format)
    g = Graph()
    known: set[Hashable] = set()
    width = 4 if intervals else 3
    for row, event in enumerate(events):
        where = f"event {row}"
        if len(event) not in (width, width + 1):
            shape = "(u, v, start, end[, attrs])" if intervals else "(u, v, t[, attrs])"
            raise ValueError(f"{where}: expected {shape}, got {len(event)} values: {event!r}")
        attrs: dict[str, Any] = {}
        if len(event) == width + 1:
            attrs = event[width]
            if not isinstance(attrs, dict):
                raise TypeError(f"{where}: the trailing value must be a dict of attributes, got {attrs!r}")
        u, v = event[0], event[1]
        start = clock.seconds(event[2], where)
        end = clock.seconds(event[3], where) if intervals else start + (duration or 0.0)
        if end < start:
            raise ValueError(f"{where}: end ({end}) is before start ({start})")
        for key in (u, v):
            if key not in known:
                g.add_node(key)
                known.add(key)
        g.add_edge(u, v, directed=directed, t_start=start, t_end=end, **attrs)
    clock.finish(g)
    return g


def from_pandas_temporal_edgelist(
    df: Any,
    source: str = "source",
    target: str = "target",
    time: str = "time",
    *,
    end: str | None = None,
    duration: float | None = None,
    edge_attr: str | list[str] | bool | None = None,
    directed: bool = False,
    time_unit: str | None = None,
    time_format: str | None = None,
) -> Graph:
    """Build a temporal Graph from a DataFrame with one row per event.

    `time` is the timestamp column (datetimes, ISO or formatted text, or numbers; see the module docs).
    Give `end` (a column) for interval data, or `duration` for a fixed lifetime after each contact.
    `edge_attr` selects extra columns to keep as connector attributes (a name, a list, or True for all
    remaining columns).
    """
    reserved = {source, target, time} | ({end} if end else set())
    if edge_attr is True:
        keep = [c for c in df.columns if c not in reserved]
    elif edge_attr is None or edge_attr is False:
        keep = []
    elif isinstance(edge_attr, str):
        keep = [edge_attr]
    else:
        keep = list(edge_attr)
    missing = [c for c in [source, target, time, *([end] if end else []), *keep] if c not in df.columns]
    if missing:
        raise KeyError(f"columns not found in the DataFrame: {missing}")

    def rows():
        cols = [source, target, time] + ([end] if end else []) + keep
        for values in df[cols].itertuples(index=False, name=None):
            head, extra = values[: 4 if end else 3], values[4 if end else 3:]
            yield (*head, dict(zip(keep, extra))) if keep else head

    return from_temporal_edgelist(rows(), directed=directed, intervals=end is not None, duration=duration,
                                  time_unit=time_unit, time_format=time_format)


def read_temporal_edgelist(
    path: str | Path,
    *,
    delimiter: str | None = None,
    comments: str = "#",
    header: bool = False,
    columns: Sequence[int] = (0, 1, 2),
    attrs: Mapping[str, int] | None = None,
    time_unit: str | None = None,
    time_format: str | None = None,
    int_nodes: bool = True,
    directed: bool = False,
    intervals: bool = False,
    duration: float | None = None,
) -> Graph:
    """Read a text file of temporal events, one per line (`u v t`, as in SNAP temporal datasets).

    `delimiter=None` splits on whitespace; pass "," or "\\t" for delimited files. `columns` selects the
    source, target and time columns (plus an end-time column with `intervals=True`). `attrs` maps an
    attribute name to a column to keep on each event, e.g. `{"sentiment": 4}`; numeric text becomes a
    number. Lines starting with `comments` and blank lines are skipped, and `header=True` skips the first
    data line. Times follow the module docs: ISO text works as is, Unix numbers need `time_unit`, other
    text needs `time_format`. Node ids that are plain integers (no leading zeros, within 64 bits) become
    integers unless `int_nodes=False`; everything else stays text.
    """
    need = 4 if intervals else 3
    if len(columns) != need:
        raise ValueError(f"columns must name {need} columns, got {tuple(columns)}")

    def node(text: str) -> Hashable:
        # Only plain integers that fit in 64 bits: "007" and 24811812513198111524 are names, not numbers.
        if int_nodes and text.lstrip("-").isdigit() and str(int(text)) == text and abs(int(text)) < 2**63:
            return int(text)
        return text

    def value(text: str) -> Any:
        for kind in (int, float):
            try:
                return kind(text)
            except ValueError:
                pass
        return text

    attr_columns = dict(attrs or {})
    current_line = 0

    def events():
        nonlocal current_line
        skipped_header = not header
        # utf-8-sig: a file saved by Excel starts with a byte-order mark, which would otherwise become part of the
        # first name. Only the line ending is removed, because with an explicit delimiter a trailing empty field
        # ("a<TAB>b<TAB>5<TAB>") is a real, empty column.
        with open(path, encoding="utf-8-sig") as fh:
            for number, raw in enumerate(fh, 1):
                line = raw.rstrip("\r\n") if delimiter else raw.strip()
                if not line.strip() or (comments and line.lstrip().startswith(comments)):
                    continue
                current_line = number
                if not skipped_header:
                    skipped_header = True
                    continue
                parts = [p.strip() for p in (line.split(delimiter) if delimiter else line.split())]
                try:
                    picked = [parts[i] for i in columns]
                except IndexError:
                    raise ValueError(f"{path}:{number}: expected at least {max(columns) + 1} columns, got {len(parts)}") from None
                head = (node(picked[0]), node(picked[1]), *picked[2:])   # times stay text; the clock reads them
                if attr_columns:
                    try:
                        extra = {name: value(parts[i]) for name, i in attr_columns.items()}
                    except IndexError:
                        raise ValueError(f"{path}:{number}: expected at least {max(attr_columns.values()) + 1} columns, got {len(parts)}") from None
                    yield (*head, extra)
                else:
                    yield head

    try:
        return from_temporal_edgelist(events(), directed=directed, intervals=intervals, duration=duration,
                                      time_unit=time_unit, time_format=time_format)
    except (ValueError, TypeError) as exc:
        # The shared parser numbers events; the reader knows the file line the failing event came from.
        if re.match(r"event \d+", str(exc)):
            where = f"{path}:{current_line}"
            # replaced through a function: a Windows path has backslashes, which re.sub would read as escapes
            raise type(exc)(re.sub(r"^event \d+", lambda _: where, str(exc), count=1)) from None
        raise
