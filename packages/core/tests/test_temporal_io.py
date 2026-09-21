import datetime as dt

import numpy as np
import pytest

from plexgraph_core import (
    from_pandas_temporal_edgelist,
    from_temporal_edgelist,
    read_temporal_edgelist,
)


def _times(g):
    return [(c.endpoints, c.t_start, c.t_end) for c in g.connectors()]


def test_contact_sequence_becomes_instantaneous_connectors():
    g = from_temporal_edgelist([("a", "b", 1), ("b", "c", 5), ("a", "b", 9)])
    assert g.num_nodes == 3
    assert [(s, e) for _, s, e in _times(g)] == [(1.0, 1.0), (5.0, 5.0), (9.0, 9.0)]
    assert all(c.is_event for c in g.connectors())


def test_repeated_pairs_stay_separate_events():
    g = from_temporal_edgelist([(0, 1, 1), (0, 1, 2), (1, 0, 3)])
    assert len(list(g.connectors())) == 3


def test_duration_gives_contacts_a_lifetime():
    g = from_temporal_edgelist([(0, 1, 10)], duration=5)
    (c,) = g.connectors()
    assert (c.t_start, c.t_end) == (10.0, 15.0)


def test_interval_rows_and_trailing_attributes():
    g = from_temporal_edgelist([(0, 1, 2, 8, {"kind": "call"})], intervals=True, directed=True)
    (c,) = g.connectors()
    assert (c.t_start, c.t_end, c.directed, c.attrs["kind"]) == (2.0, 8.0, True, "call")


def test_datetimes_become_epoch_seconds():
    naive = dt.datetime(2024, 1, 1, 0, 0, 1)
    g = from_temporal_edgelist([(0, 1, naive), (1, 2, np.datetime64("2024-01-01T00:00:03")),
                                (2, 3, dt.datetime(2024, 1, 1, 0, 0, 5, tzinfo=dt.timezone.utc))])
    starts = [c.t_start for c in g.connectors()]
    base = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()
    assert starts == [base + 1, base + 3, base + 5]


@pytest.mark.parametrize("bad", [("a", "b", "not a time"), ("a", "b", None), ("a", "b", float("nan")), ("a", "b", float("inf"))])
def test_bad_timestamps_are_rejected_with_the_row(bad):
    with pytest.raises((TypeError, ValueError), match="event 1"):
        from_temporal_edgelist([("x", "y", 0), bad])


def test_malformed_rows_and_ranges_are_rejected():
    with pytest.raises(ValueError, match="expected \\(u, v, t"):
        from_temporal_edgelist([("a", "b")])
    with pytest.raises(ValueError, match="before start"):
        from_temporal_edgelist([("a", "b", 5, 2)], intervals=True)
    with pytest.raises(ValueError, match="duration"):
        from_temporal_edgelist([("a", "b", 1)], duration=-1)
    with pytest.raises(ValueError, match="intervals"):
        from_temporal_edgelist([("a", "b", 1, 2)], intervals=True, duration=3)
    with pytest.raises(TypeError, match="dict"):
        from_temporal_edgelist([("a", "b", 1, "oops")])


def test_pandas_columns_attrs_and_intervals():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"src": [1, 2], "dst": [2, 3], "when": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                       "weightish": [0.5, 2.0], "until": pd.to_datetime(["2024-01-03", "2024-01-04"])})
    g = from_pandas_temporal_edgelist(df, "src", "dst", "when", edge_attr=["weightish"])
    assert [c.attrs["weightish"] for c in g.connectors()] == [0.5, 2.0]
    assert all(c.is_event for c in g.connectors())
    gi = from_pandas_temporal_edgelist(df, "src", "dst", "when", end="until")
    day = 86400.0
    assert [c.t_end - c.t_start for c in gi.connectors()] == [2 * day, 2 * day]
    with pytest.raises(KeyError, match="nope"):
        from_pandas_temporal_edgelist(df, "src", "dst", "nope")


def test_iso_text_timestamps_are_parsed():
    g = from_temporal_edgelist([("a", "b", "2013-12-31 16:39:18"), ("b", "c", "2014-01-01T00:00:00")])
    first, second = (c.t_start for c in g.connectors())
    assert second - first == 7 * 3600 + 20 * 60 + 42
    assert first == dt.datetime(2013, 12, 31, 16, 39, 18, tzinfo=dt.timezone.utc).timestamp()


def test_reddit_style_tsv_with_text_timestamps_and_attributes(tmp_path):
    f = tmp_path / "reddit.tsv"
    f.write_text("SRC\tDST\tPOST\tTIMESTAMP\tSENT\tPROPS\n"
                 "gaming\txbox\tp1\t2013-12-31 16:39:18\t1\t0.1,0.2\n"
                 "xbox\tgaming\tp2\t2014-01-01 00:00:00\t-1\t0.3,0.4\n")
    g = read_temporal_edgelist(f, delimiter="\t", header=True, columns=(0, 1, 3), attrs={"sentiment": 4, "post": 2}, directed=True)
    assert [n.key for n in g.nodes()] == ["gaming", "xbox"]
    a, b = list(g.connectors())
    assert (a.directed, a.attrs["sentiment"], a.attrs["post"]) == (True, 1, "p1")
    assert b.attrs["sentiment"] == -1 and b.t_start > a.t_start


def test_custom_time_format_and_bad_format(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("a b 31/12/2013\n")
    g = read_temporal_edgelist(f, time_format="%d/%m/%Y")
    assert next(iter(g.connectors())).t_start == dt.datetime(2013, 12, 31, tzinfo=dt.timezone.utc).timestamp()
    with pytest.raises(ValueError, match="does not match time_format"):
        read_temporal_edgelist(f, time_format="%Y-%m-%d")
    with pytest.raises(ValueError, match="e.txt:1.*ISO 8601"):
        read_temporal_edgelist(f)


def test_int_nodes_can_be_disabled(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("007 7 1\n")
    g = read_temporal_edgelist(f, int_nodes=False)
    assert [n.key for n in g.nodes()] == ["007", "7"]


def test_read_whitespace_file_like_snap(tmp_path):
    f = tmp_path / "events.txt"
    f.write_text("# u v t\n1 2 100\n\n2 3 105\nx y 110\n")
    g = read_temporal_edgelist(f)
    assert g.num_nodes == 5
    assert [(c.t_start) for c in g.connectors()] == [100.0, 105.0, 110.0]
    assert [n.key for n in g.nodes()][:3] == [1, 2, 3]


def test_read_csv_with_header_and_column_selection(tmp_path):
    f = tmp_path / "events.csv"
    f.write_text("time,ignore,from,to\n7,-,a,b\n9,-,b,c\n")
    g = read_temporal_edgelist(f, delimiter=",", header=True, columns=(2, 3, 0))
    assert [(c.t_start) for c in g.connectors()] == [7.0, 9.0]
    bad = tmp_path / "bad.txt"
    bad.write_text("1 2 soon\n")
    with pytest.raises(ValueError, match="bad.txt:1"):
        read_temporal_edgelist(bad)
    short = tmp_path / "short.txt"
    short.write_text("1 2\n")
    with pytest.raises(ValueError, match="short.txt:1"):
        read_temporal_edgelist(short)


def test_calendar_times_mark_the_graph_so_the_viewer_shows_dates(tmp_path):
    assert from_temporal_edgelist([("a", "b", 1), ("b", "c", 2)]).time_unit is None
    assert from_temporal_edgelist([("a", "b", dt.datetime(2020, 1, 1))]).time_unit == "epoch_seconds"
    assert from_temporal_edgelist([("a", "b", "2020-01-01 10:00:00")]).time_unit == "epoch_seconds"
    assert from_temporal_edgelist([("a", "b", 1_600_000_000)], time_unit="epoch_seconds").time_unit == "epoch_seconds"
    numeric = tmp_path / "n.txt"
    numeric.write_text("a b 5\n")
    text = tmp_path / "t.txt"
    text.write_text("a b 2020-01-01T00:00:00\n")
    assert read_temporal_edgelist(numeric).time_unit is None
    assert read_temporal_edgelist(text).time_unit == "epoch_seconds"
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"s": [1], "t": [2], "when": pd.to_datetime(["2020-05-05"])})
    assert from_pandas_temporal_edgelist(df, "s", "t", "when").time_unit == "epoch_seconds"


UNIX = 1388507958  # 2013-12-31 16:39:18 UTC


@pytest.mark.parametrize("unit, value", [("epoch_seconds", UNIX), ("epoch_milliseconds", UNIX * 1000),
                                         ("epoch_microseconds", UNIX * 10**6), ("epoch_nanoseconds", UNIX * 10**9)])
def test_declared_unix_units_become_seconds_and_dates(unit, value):
    g = from_temporal_edgelist([("a", "b", value)], time_unit=unit)
    assert next(iter(g.connectors())).t_start == pytest.approx(UNIX)
    assert g.time_unit == "epoch_seconds"


def test_unix_units_work_for_files_and_dataframes(tmp_path):
    f = tmp_path / "unix.txt"
    f.write_text(f"a b {UNIX * 1000}\nb c {UNIX * 1000 + 60_000}\n")
    g = read_temporal_edgelist(f, time_unit="epoch_milliseconds")
    assert [c.t_start for c in g.connectors()] == [UNIX, UNIX + 60]
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"s": ["a"], "t": ["b"], "w": [UNIX]})
    gd = from_pandas_temporal_edgelist(df, "s", "t", "w", time_unit="epoch_seconds")
    assert (next(iter(gd.connectors())).t_start, gd.time_unit) == (UNIX, "epoch_seconds")
    with pytest.raises(ValueError, match="time_unit must be one of"):
        from_temporal_edgelist([("a", "b", 1)], time_unit="hours")


def test_unix_looking_numbers_without_a_unit_warn_instead_of_silently_showing_raw_numbers():
    with pytest.warns(UserWarning, match=r"epoch_seconds"):
        g = from_temporal_edgelist([("a", "b", UNIX), ("b", "c", UNIX + 5)])
    assert g.time_unit is None and next(iter(g.connectors())).t_start == UNIX
    with pytest.warns(UserWarning, match=r"epoch_milliseconds"):
        from_temporal_edgelist([("a", "b", UNIX * 1000)])


def test_ordinary_numbers_do_not_warn(recwarn):
    from_temporal_edgelist([("a", "b", 3), ("b", "c", 1_000_000)])
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


def test_time_format_is_available_in_every_loader():
    g = from_temporal_edgelist([("a", "b", "12/31/2013"), ("b", "c", "01/02/2014")], time_format="%m/%d/%Y")
    first, second = (c.t_start for c in g.connectors())
    assert first == dt.datetime(2013, 12, 31, tzinfo=dt.timezone.utc).timestamp() and second - first == 2 * 86400
    with pytest.raises(ValueError, match="does not match time_format"):
        from_temporal_edgelist([("a", "b", "2013-12-31")], time_format="%m/%d/%Y")
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"s": ["a"], "t": ["b"], "w": ["31.12.2013 16:39"]})
    gd = from_pandas_temporal_edgelist(df, "s", "t", "w", time_format="%d.%m.%Y %H:%M")
    assert next(iter(gd.connectors())).t_start == dt.datetime(2013, 12, 31, 16, 39, tzinfo=dt.timezone.utc).timestamp()


def test_format_with_offset_and_iso_variants():
    g = from_temporal_edgelist([("a", "b", "2020-01-01T10:00:00Z"), ("a", "b", "2020-01-01 12:00:00+02:00"),
                                ("a", "b", "2020-01-01")])
    t = [c.t_start for c in g.connectors()]
    assert t[0] == t[1] == dt.datetime(2020, 1, 1, 10, tzinfo=dt.timezone.utc).timestamp()
    assert t[2] == dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp()


def test_numeric_text_in_tuples_is_a_number_not_a_failed_date():
    g = from_temporal_edgelist([("a", "b", "5"), ("b", "c", "7.5")])
    assert [c.t_start for c in g.connectors()] == [5.0, 7.5] and g.time_unit is None
    g2 = from_temporal_edgelist([("a", "b", "20131231")], time_format="%Y%m%d")
    assert next(iter(g2.connectors())).t_start == dt.datetime(2013, 12, 31, tzinfo=dt.timezone.utc).timestamp()


def test_error_location_survives_backslashes_in_the_path(tmp_path):
    # Windows paths contain backslashes; they must not be treated as regex escapes when the message is built.
    f = tmp_path / "C_Users_me.txt".replace("_", "\\")
    f.write_text("1 2 soon\n")
    with pytest.raises(ValueError, match="soon"):
        read_temporal_edgelist(f)
