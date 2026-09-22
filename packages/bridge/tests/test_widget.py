import json
import sys
import threading
import time
import types
from pathlib import Path

import msgpack
import pytest

pytest.importorskip("anywidget")

from plexgraph_bridge import launcher, widget as widget_module  # noqa: E402
from plexgraph_bridge.style import StyleController  # noqa: E402
from plexgraph_bridge.widget import GraphWidget, inline_app  # noqa: E402
from plexgraph_core.model.ir import Graph  # noqa: E402


def _graph(n: int = 8) -> Graph:
    g = Graph()
    for i in range(n):
        g.add_node(i)
    for i in range(n):
        g.add_edge(i, (i + 1) % n)
        g.add_edge(i, (i * 7 + 3) % n)  # chords: a plain ring is laid out analytically, in one step
    return g


class Page:
    """Stands in for the browser side of a widget: records what the kernel sends and puts chunked frames back together
    exactly as widget.js does."""

    def __init__(self, view: GraphWidget) -> None:
        self.raw: list[tuple[dict, bytes]] = []
        self.notices: list[dict] = []  # messages that are not frames (the kernel telling the page something)
        self.lock = threading.Lock()
        view.send = self._send  # type: ignore[method-assign]
        self.view = view

    def _send(self, content, buffers=None):
        with self.lock:
            if content.get("type") == "frame":
                self.raw.append((content, bytes(buffers[0])))
            else:
                self.notices.append(content)

    def hello(self) -> None:
        self.view._on_page_message(self.view, {"type": "hello"}, [])

    def frames(self) -> list[dict]:
        """Every complete frame received so far, decoded, in order."""
        with self.lock:
            raw = list(self.raw)
        pieces: dict[int, dict[int, bytes]] = {}
        done: list[dict] = []
        for content, data in raw:
            entry = pieces.setdefault(content["id"], {})
            entry[content["index"]] = data
            if len(entry) == content["count"]:
                done.append({"stream": content["stream"], "message": msgpack.unpackb(b"".join(entry[i] for i in range(content["count"])), raw=False)})
        return done

    def wait_for(self, predicate, timeout: float = 20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = predicate(self.frames())
            if found:
                return found
            time.sleep(0.02)
        raise AssertionError(f"timed out; got {[f['message']['type'] for f in self.frames()]}")


@pytest.fixture
def make_widget():
    made: list[GraphWidget] = []

    def build(graph=None, **kwargs):
        view = GraphWidget(graph or _graph(), **kwargs)
        made.append(view)
        return view

    yield build
    for view in made:
        view.close()


# ---- the page

def test_the_viewer_page_is_inlined_into_one_string(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('a </script> b')")
    (tmp_path / "assets" / "app.css").write_text("body{color:red}")
    (tmp_path / "index.html").write_text(
        '<html><head><link rel="icon" href="data:image/svg+xml,x"><link rel="modulepreload" href="/assets/app.js">'
        '<link rel="stylesheet" href="/assets/app.css"><script type="module" crossorigin src="/assets/app.js"></script></head><body></body></html>')
    html = inline_app(tmp_path)
    assert "console.log('a <\\/script> b')" in html and "body{color:red}" in html
    assert 'src="/assets' not in html and 'href="/assets' not in html and "modulepreload" not in html
    assert 'href="data:image/svg+xml,x"' in html  # an icon that is already inline is left alone
    assert html.count("<script") == 1 and "<style>body{color:red}</style>" in html


def test_a_reference_that_is_not_in_the_viewer_directory_is_refused(tmp_path):
    (tmp_path / "secret.js").write_text("x")
    viewer = tmp_path / "viewer"
    viewer.mkdir()
    (viewer / "index.html").write_text('<script src="/../secret.js"></script>')
    with pytest.raises(RuntimeError, match="not in"):
        inline_app(viewer)


def test_the_real_viewer_inlines_with_nothing_left_to_fetch():
    html = inline_app(launcher._static_app_dir())
    assert "<canvas" in html and "src=\"/" not in html and "href=\"/" not in html


def test_the_host_script_holds_the_viewer_exactly_once():
    template = (Path(widget_module.__file__).parent / "widget.js").read_text()
    assert template.count("__APP_HTML__") == 1  # a second mention (in a comment, say) would be replaced too
    script = widget_module._host_script()
    assert "__APP_HTML__" not in script and script.count("const APP_HTML = ") == 1
    literal = script.split("const APP_HTML = ", 1)[1].split(";\n", 1)[0]
    assert "<canvas" in json.loads(literal)


# ---- streaming

def test_a_hello_starts_the_stream_with_the_graph_then_the_layout(make_widget):
    page = Page(make_widget(layout_iterations=4, seed=1))
    page.hello()
    frames = page.wait_for(lambda fs: fs if any(f["message"]["type"] == "layout_step" for f in fs) else None)
    kinds = [f["message"]["type"] for f in frames]
    assert kinds[0] == "graph" and "layout_step" in kinds
    assert len(frames[0]["message"]["nodes"]) == 8 and len({f["stream"] for f in frames}) == 1


def test_nothing_is_sent_until_the_viewer_says_hello(make_widget):
    page = Page(make_widget())
    time.sleep(0.3)
    assert page.raw == []


def test_a_frame_bigger_than_a_chunk_is_split_and_reassembles(make_widget, monkeypatch):
    monkeypatch.setattr(widget_module, "CHUNK_BYTES", 200)
    page = Page(make_widget(_graph(60), layout_iterations=2))
    page.hello()
    page.wait_for(lambda fs: fs if any(f["message"]["type"] == "layout_step" for f in fs) else None)
    graph_pieces = [c for c, _ in page.raw if c["id"] == 1]
    assert len(graph_pieces) > 3 and all(c["count"] == len(graph_pieces) for c in graph_pieces)
    assert [c["index"] for c in graph_pieces] == list(range(len(graph_pieces)))
    assert all(len(d) <= 200 for _, d in page.raw)
    assert len(page.frames()[0]["message"]["nodes"]) == 60  # and it decodes to the whole graph


def test_a_second_hello_restarts_the_stream_and_the_old_one_stops(make_widget):
    view = make_widget(_graph(3000), layout_iterations=300, seed=1)
    page = Page(view)
    page.hello()
    page.wait_for(lambda fs: fs if len(fs) >= 3 else None)
    page.hello()  # the viewer reloaded
    fresh = page.wait_for(lambda fs: [f for f in fs if f["stream"] == 2 and f["message"]["type"] == "graph"])
    assert len(fresh) == 1
    time.sleep(0.4)
    late = [c["stream"] for c, _ in page.raw[-5:]]
    assert set(late) == {2}, "frames of the replaced stream must not be mixed into the new one"


def test_a_style_change_reaches_the_open_viewer(make_widget):
    g = _graph()
    controller = StyleController(g)
    page = Page(make_widget(g, controller=controller, layout_iterations=2))
    page.hello()
    page.wait_for(lambda fs: fs if any(f["message"]["type"] == "layout_step" for f in fs) else None)
    controller.update(node_color="crimson")
    styles = page.wait_for(lambda fs: [f for f in fs if f["message"]["type"] == "style"])
    assert styles[-1]["message"]["op"] == "set"


def test_the_style_given_up_front_arrives_before_the_layout(make_widget):
    g = _graph()
    controller = StyleController(g)
    controller.update(node_color="crimson")
    page = Page(make_widget(g, controller=controller, layout_iterations=2))
    page.hello()
    frames = page.wait_for(lambda fs: fs if any(f["message"]["type"] == "layout_step" for f in fs) else None)
    kinds = [f["message"]["type"] for f in frames]
    assert kinds.index("style") < kinds.index("layout_step")


def test_close_stops_streaming_and_is_safe_twice(make_widget):
    view = make_widget(_graph(3000), layout_iterations=300, seed=1)
    page = Page(view)
    page.hello()
    page.wait_for(lambda fs: fs if len(fs) >= 2 else None)
    view.close()
    view.close()
    time.sleep(0.3)
    count = len(page.raw)
    time.sleep(0.5)
    assert len(page.raw) == count
    page.hello()  # a viewer that says hello after close is ignored
    time.sleep(0.3)
    assert len(page.raw) == count


def test_close_leaves_the_viewer_on_screen_and_tells_it_once(make_widget):
    # Closing an ipywidgets widget normally destroys the connection and blanks every view of it. A notebook that closes
    # the previous viewer when it shows the next (as the tour does) must not lose the earlier pictures.
    view = make_widget(_graph(40), layout_iterations=300, seed=1)
    page = Page(view)
    page.hello()
    page.wait_for(lambda fs: fs if len(fs) >= 2 else None)
    view.close()
    view.close()
    assert view.comm is not None, "the connection must stay open so the page keeps its picture"
    assert page.notices == [{"type": "closed"}]


def test_viewer_settings_travel_to_the_page(make_widget):
    assert make_widget().viewer_query == "?ws=parent"
    query = make_widget(viewer_style={"nodeRadiusPx": 9}).viewer_query
    assert query.startswith("?ws=parent&style=") and "nodeRadiusPx" in query


# ---- show()

class _Kernel:
    """A plain Jupyter kernel's shell."""


_Kernel.__name__ = "ZMQInteractiveShell"


@pytest.fixture
def notebook(monkeypatch):
    import IPython
    import IPython.display

    monkeypatch.setattr(IPython, "get_ipython", lambda: _Kernel())
    shown: list[object] = []
    monkeypatch.setattr(IPython.display, "display", lambda obj, *a, **k: shown.append(obj))
    return shown


def test_show_in_a_notebook_displays_a_widget_and_uses_no_port(notebook):
    handle = launcher.show(_graph(), layout_iterations=2, return_handle=True)
    try:
        assert notebook == [handle.widget] and isinstance(handle.widget, GraphWidget)
        assert handle.ws_port is None and handle.http_port is None and handle.url is None and handle.token is None
        with pytest.raises(RuntimeError, match="notebook widget"):
            handle.ws_url
    finally:
        handle.close()


def test_the_handle_restyles_the_widget_live(notebook):
    handle = launcher.show(_graph(), layout_iterations=2, return_handle=True, node_color="tomato")
    page = Page(handle.widget)
    try:
        page.hello()
        page.wait_for(lambda fs: fs if any(f["message"]["type"] == "layout_step" for f in fs) else None)
        handle.color_nodes([0, 1], "navy")
        page.wait_for(lambda fs: [f for f in fs if f["message"]["type"] == "style" and f["message"]["op"] == "paint"])
        handle.reset_style()
        assert handle.get_style() == {}
    finally:
        handle.close()


def test_show_returns_nothing_by_default_in_a_notebook(notebook):
    assert launcher.show(_graph(), layout_iterations=1) is None
    notebook[0].close()


def test_the_widget_carries_the_options_that_were_not_style_messages(notebook):
    launcher.show(_graph(), layout_iterations=1, height=333, node_radius_px=9)
    view = notebook[0]
    try:
        assert view.height == 333 and "nodeRadiusPx" in view.viewer_query
    finally:
        view.close()


def test_widget_false_uses_the_older_server_and_iframe(notebook, monkeypatch):
    inline = []
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: inline.append(url))
    handle = launcher.show(_graph(), layout_iterations=1, widget=False, return_handle=True)
    try:
        assert handle.widget is None and handle.ws_port and inline == [handle.url]
    finally:
        handle.close()


def test_asking_for_the_widget_without_anywidget_says_how_to_get_it(notebook, monkeypatch):
    monkeypatch.setattr(launcher, "_widget_available", lambda: False)
    with pytest.raises(ImportError, match=r"plexgraph\[jupyter\]"):
        launcher.show(_graph(), widget=True)


def test_without_anywidget_a_notebook_falls_back_to_the_iframe(notebook, monkeypatch):
    monkeypatch.setattr(launcher, "_widget_available", lambda: False)
    inline = []
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: inline.append(url))
    handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    try:
        assert handle.widget is None and len(inline) == 1
    finally:
        handle.close()


class _ColabShell:
    pass


_ColabShell.__module__ = "google.colab._shell"


def test_colab_switches_on_its_widget_manager_and_gets_the_widget(monkeypatch):
    import IPython
    import IPython.display

    monkeypatch.setattr(IPython, "get_ipython", lambda: _ColabShell())
    shown = []
    monkeypatch.setattr(IPython.display, "display", lambda obj, *a, **k: shown.append(obj))
    enabled = []
    module = types.ModuleType("google.colab")
    module.output = types.SimpleNamespace(enable_custom_widget_manager=lambda: enabled.append(True))
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.colab", module)
    handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    try:
        assert enabled == [True] and isinstance(handle.widget, GraphWidget) and handle.ws_port is None
    finally:
        handle.close()


def test_colab_still_gets_a_widget_if_the_widget_manager_cannot_be_switched_on(monkeypatch):
    import IPython
    import IPython.display

    monkeypatch.setattr(IPython, "get_ipython", lambda: _ColabShell())
    monkeypatch.setattr(IPython.display, "display", lambda obj, *a, **k: None)
    module = types.ModuleType("google.colab")

    def broken():
        raise RuntimeError("not available")
    module.output = types.SimpleNamespace(enable_custom_widget_manager=broken)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.colab", module)
    handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    try:
        assert isinstance(handle.widget, GraphWidget)
    finally:
        handle.close()


def test_a_widget_that_cannot_be_created_falls_back_to_the_server_route_and_says_why(notebook, monkeypatch):
    def broken(*args, **kwargs):
        raise TypeError("this ipywidgets is too old")
    monkeypatch.setattr(launcher, "_show_widget", broken)
    inline = []
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: inline.append(url))
    with pytest.warns(RuntimeWarning, match=r"could not be created \(TypeError: this ipywidgets is too old\)"):
        handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    try:
        assert handle.widget is None and inline == [handle.url]
    finally:
        handle.close()


def test_asking_for_the_widget_explicitly_does_not_hide_a_failure(notebook, monkeypatch):
    def broken(*args, **kwargs):
        raise TypeError("this ipywidgets is too old")
    monkeypatch.setattr(launcher, "_show_widget", broken)
    with pytest.raises(TypeError, match="too old"):
        launcher.show(_graph(), layout_iterations=1, widget=True)


def test_an_installed_anywidget_that_fails_to_import_counts_as_unavailable(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "anywidget":
            raise RuntimeError("incompatible with the ipywidgets in this environment")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert launcher._widget_available() is False


def test_the_hosted_notebook_warning_says_how_to_get_the_widget(monkeypatch):
    import IPython

    class Shell:
        pass
    Shell.__name__ = "ZMQInteractiveShell"
    monkeypatch.setattr(IPython, "get_ipython", lambda: Shell())
    monkeypatch.setattr(launcher, "_display_inline", lambda url, **kw: None)
    monkeypatch.setattr(launcher, "_widget_available", lambda: False)
    monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "1")
    with pytest.warns(RuntimeWarning, match=r"plexgraph\[jupyter\]"):
        handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    handle.close()


# ---- what the viewer reports about itself

def test_the_viewers_state_report_is_kept_for_diagnosis(make_widget):
    view = make_widget()
    assert view.diagnostics == {"state": {}, "errors": [], "kernel": {"hellosReceived": 0, "framesSent": 0, "bytesSent": 0, "lastStreamingError": None}}
    view._on_page_message(view, {"type": "report", "kind": "state", "data": {"canvas": {"pixels": [868, 348]}, "gl": {"lost": False}}}, [])
    view._on_page_message(view, {"type": "report", "kind": "state", "data": {"canvas": {"pixels": [900, 400]}}}, [])
    assert view.diagnostics["state"] == {"canvas": {"pixels": [900, 400]}}  # the latest


def test_errors_the_viewer_reports_are_kept_logged_and_capped(make_widget, caplog):
    view = make_widget()
    with caplog.at_level("WARNING", logger="plexgraph_bridge.widget"):
        for i in range(60):
            view._on_page_message(view, {"type": "report", "kind": "error", "data": {"message": f"boom {i}"}}, [])
    errors = view.diagnostics["errors"]
    assert len(errors) == widget_module._MAX_REPORTED_ERRORS and errors[-1]["message"] == "boom 59"
    assert "the viewer reported an error: boom 59" in caplog.text


def test_malformed_reports_are_ignored(make_widget):
    view = make_widget()
    for content in ("text", None, {"type": "report"}, {"type": "report", "kind": "state", "data": "x"},
                    {"type": "report", "kind": "error", "data": 5}, {"type": "report", "kind": "other", "data": {}}):
        view._on_page_message(view, content, [])
    assert view.diagnostics == {"state": {}, "errors": [], "kernel": {"hellosReceived": 0, "framesSent": 0, "bytesSent": 0, "lastStreamingError": None}}


def test_the_handle_exposes_the_diagnostics_and_a_server_viewer_has_none(notebook):
    handle = launcher.show(_graph(), layout_iterations=1, return_handle=True)
    try:
        handle.widget._on_page_message(handle.widget, {"type": "report", "kind": "state", "data": {"pixelRatio": 2}}, [])
        assert handle.diagnostics()["state"] == {"pixelRatio": 2}
    finally:
        handle.close()
    plain = launcher.ShowHandle(ws_port=1, http_port=None, thread=None, _stop=lambda: None)
    assert plain.diagnostics() == {}


def test_the_kernel_counters_show_how_far_the_round_trip_got(make_widget):
    view = make_widget(_graph(40), layout_iterations=200, seed=1)
    page = Page(view)
    assert view.diagnostics["kernel"]["hellosReceived"] == 0
    page.hello()
    page.wait_for(lambda fs: fs if len(fs) >= 2 else None)
    counters = view.diagnostics["kernel"]
    assert counters["hellosReceived"] == 1
    assert counters["framesSent"] >= 2 and counters["bytesSent"] > 0
    assert counters["lastStreamingError"] is None
    page.hello()  # counted even though it just restarts the same stream
    assert view.diagnostics["kernel"]["hellosReceived"] == 2


def test_a_hello_after_close_is_still_counted(make_widget):
    view = make_widget()
    view.close()
    view._on_page_message(view, {"type": "hello"}, [])
    assert view.diagnostics["kernel"]["hellosReceived"] == 1
    assert view.diagnostics["kernel"]["framesSent"] == 0  # stop() means it is not acted on


def test_a_streaming_failure_is_visible_in_the_counters(make_widget, monkeypatch):
    view = make_widget(layout_iterations=5)
    page = Page(view)

    def broken(*args, **kwargs):
        raise RuntimeError("layout blew up")
    monkeypatch.setattr(view._plexgraph, "serve_client", broken)
    page.hello()
    for _ in range(100):
        if view.diagnostics["kernel"]["lastStreamingError"] is not None:
            break
        time.sleep(0.02)
    assert view.diagnostics["kernel"]["lastStreamingError"] == "RuntimeError: layout blew up"
