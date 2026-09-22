"""The viewer as a notebook widget.

`show()` in a notebook normally used a small web server and pointed an iframe at it. That only works where the
browser can reach the machine running Python, which is not true of Colab, Kaggle, JupyterHub and other hosted
notebooks. A widget needs no server and no port: the frames go over the notebook's own connection (the comm channel
every notebook already has) to a script in the page, which feeds the same viewer app the browser tab uses. It is built
on anywidget, so it works in JupyterLab, Jupyter Notebook, VS Code, Colab and the other frontends that run widgets.

The kernel side is `_WidgetBridge`: the same hub the WebSocket server uses, with the comm channel as its transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anywidget
import traitlets

from plexgraph_bridge.hub import ClientHub
from plexgraph_core.model.ir import Graph

if TYPE_CHECKING:
    from plexgraph_bridge.style import StyleController

logger = logging.getLogger("plexgraph_bridge.widget")

# A frame bigger than this travels as several messages and is put back together in the page. Notebook servers and the
# hosts in front of them limit the size of one message and the number per second; a few big pieces stay well inside both.
CHUNK_BYTES = 1 << 20
# Errors a viewer reports are kept, most recent last, up to this many.
_MAX_REPORTED_ERRORS = 50
# See the comment in _send_frame.
_SEND_PACING_SECONDS = 0.015

_SCRIPT = re.compile(r'<script\b[^>]*\bsrc="(?P<src>[^"]+)"[^>]*>\s*</script>')
_LINK = re.compile(r'<link\b(?P<attrs>[^>]*)>')
_HREF = re.compile(r'\bhref="([^"]+)"')


def inline_app(app_dir: Path) -> str:
    """The viewer's page with its scripts and styles written into it, so it can be handed over as one string."""
    def read(reference: str) -> str:
        target = (app_dir / reference.lstrip("/")).resolve()
        if not target.is_relative_to(app_dir.resolve()) or not target.is_file():
            raise RuntimeError(f"the viewer page refers to {reference}, which is not in {app_dir}")
        return target.read_text(encoding="utf-8")

    def script(match: re.Match[str]) -> str:
        tag = match.group(0)
        kind = ' type="module"' if 'type="module"' in tag else ""
        # a script must not contain the text that would end its own element
        return f"<script{kind}>{read(match.group('src')).replace('</script', '<' + chr(92) + '/script')}</script>"

    def link(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        href = _HREF.search(attrs)
        if href is None or href.group(1).startswith("data:"):
            return match.group(0)  # an icon that is already inline
        if 'rel="stylesheet"' in attrs:
            return f"<style>{read(href.group(1))}</style>"
        if 'rel="modulepreload"' in attrs:
            return ""  # only a hint to fetch early; the script is now inline
        return match.group(0)

    html = (app_dir / "index.html").read_text(encoding="utf-8")
    html = _SCRIPT.sub(script, html)
    html = _LINK.sub(link, html)
    if re.search(r'(src|href)="/(?!/)', html):
        raise RuntimeError("the viewer page still refers to files that were not inlined")
    return html


def _host_script() -> str:
    """The script that runs in the notebook page, with the viewer page written into it."""
    from plexgraph_bridge.launcher import _static_app_dir

    template = (Path(__file__).parent / "widget.js").read_text(encoding="utf-8")
    app_dir = _static_app_dir()
    try:
        html = inline_app(app_dir)
    except (OSError, RuntimeError) as error:
        logger.warning("the viewer is not available to the widget: %s", error)
        html = "<body>The plexgraph viewer is not built. In a checkout run: pnpm --filter @plexgraph/app build</body>"
    return template.replace("__APP_HTML__", json.dumps(html))


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _shared_loop() -> asyncio.AbstractEventLoop:
    """One background event loop serves every widget in the process; a notebook can hold many of them."""
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="plexgraph-widgets", daemon=True).start()
            _loop = loop
    return _loop


class _WidgetBridge(ClientHub):
    """The hub, with the widget's comm channel as its transport.

    The page says hello when its viewer is listening (and again if the viewer reloads); each hello starts the stream
    afresh: the graph, the current style, then the layout as it converges."""

    def __init__(self, widget: "GraphWidget", graph: Graph, *, layout_iterations: int, seed: int | None,
                 style: "StyleController | None") -> None:
        super().__init__(graph, layout_iterations=layout_iterations, seed=seed, style=style)
        self._widget = widget
        self._background = self._loop = _shared_loop()
        self._task: asyncio.Task[None] | None = None
        self._stream = 0
        self._frame = 0
        self._stopped = False
        # Counters for diagnosing a viewer that never gets its graph: how far the round trip actually got, without
        # needing the browser's console. Counted regardless of what the code goes on to do with the event.
        self._hellos_received = 0
        self._frames_sent = 0
        self._bytes_sent = 0
        self._last_streaming_error: str | None = None

    def hello(self) -> None:
        """A viewer is listening. Safe to call from any thread."""
        self._hellos_received += 1
        if not self._stopped:
            self._background.call_soon_threadsafe(self._restart)

    def _restart(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._stopped:
            return
        self._stream += 1
        self._frame = 0
        self._task = self._background.create_task(self._serve(self._stream))

    async def _serve(self, stream: int) -> None:
        async def send(data: bytes) -> None:
            if stream == self._stream:  # a stream that has been replaced must not interleave with its successor
                await self._send_frame(stream, data)

        async def until_replaced() -> None:
            await asyncio.Event().wait()  # returns only by being cancelled

        try:
            await self.serve_client(send, until_replaced)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._last_streaming_error = f"{type(error).__name__}: {error}"
            logger.exception("streaming to the widget failed")

    async def _send_frame(self, stream: int, data: bytes) -> None:
        if getattr(self._widget, "comm", None) is None:
            return  # the widget was closed
        self._frame += 1
        self._frames_sent += 1
        self._bytes_sent += len(data)
        view = memoryview(data)
        count = max(1, -(-len(data) // CHUNK_BYTES))
        for index in range(count):
            piece = view[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]
            self._widget.send({"type": "frame", "stream": stream, "id": self._frame, "index": index, "count": count},
                              [bytes(piece)])
            # A pace, not a throttle: sending many messages back to back, with nothing awaited in between, is the one
            # thing every failure seen so far has in common (a small graph's ~200 layout_step messages, and a large
            # graph's single graph message once it needs many chunks) -- both sent in one uninterrupted burst, and
            # both went missing after the same small handful of messages. Nothing here proves what in the chain drops
            # them, but yielding between sends removes the one shared trait of every failure and costs little: at
            # CHUNK_BYTES-sized pieces this adds under a second even for a very large graph.
            await asyncio.sleep(_SEND_PACING_SECONDS)

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def counters(self) -> dict[str, Any]:
        """How far the round trip to the browser has actually got: hellosReceived is 0 if the browser's "the viewer is
        listening" message never reached the kernel at all (points at the comm channel itself, before any of our code
        runs); framesSent/bytesSent being 0 despite a hello means streaming did not start or failed immediately
        (lastStreamingError, if any, says why); framesSent growing but the browser reporting no graph points at
        something between the kernel's model.send() and the browser's postMessage relay."""
        return {"hellosReceived": self._hellos_received, "framesSent": self._frames_sent,
                "bytesSent": self._bytes_sent, "lastStreamingError": self._last_streaming_error}

    def stop(self) -> None:
        self._stopped = True
        if not self._background.is_closed():
            self._background.call_soon_threadsafe(lambda: self._task and self._task.cancel())


class GraphWidget(anywidget.AnyWidget):
    """A live, interactive view of a graph, as a notebook widget. `show()` creates one; it is also what
    `show(...).widget` returns, if you want to place it in a layout of your own."""

    _esm = _host_script()

    height = traitlets.Int(600).tag(sync=True)
    viewer_query = traitlets.Unicode("?ws=parent").tag(sync=True)

    def __init__(self, graph: Graph, *, controller: "StyleController | None" = None, layout_iterations: int = 200,
                 seed: int | None = None, height: int = 600, viewer_style: dict[str, Any] | None = None) -> None:
        query = "?ws=parent"
        if viewer_style:
            from urllib.parse import quote

            query += f"&style={quote(json.dumps(viewer_style))}"
        super().__init__(height=height, viewer_query=query)
        self._reports_lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._errors: list[dict[str, Any]] = []
        self._host: dict[str, Any] = {}
        self._plexgraph = _WidgetBridge(self, graph, layout_iterations=layout_iterations, seed=seed, style=controller)
        if controller is not None:
            controller.bind(self._plexgraph.push_style)
        self.on_msg(self._on_page_message)

    def _on_page_message(self, _widget: Any, content: Any, _buffers: Any) -> None:
        if not isinstance(content, dict):
            return
        if content.get("type") == "hello":
            self._plexgraph.hello()
        elif content.get("type") == "report":
            self._record_report(content.get("kind"), content.get("data"))

    def _record_report(self, kind: Any, data: Any) -> None:
        with self._reports_lock:
            if kind == "state" and isinstance(data, dict):
                self._state = data
            elif kind == "error" and isinstance(data, dict):
                self._errors.append(data)
                del self._errors[:-_MAX_REPORTED_ERRORS]
                logger.warning("the viewer reported an error: %s", data.get("message"))
            elif kind == "host" and isinstance(data, dict):
                # From the widget's own host script (outside the possibly-sandboxed iframe): how many messages from
                # the kernel it has actually seen, distinct from `kernel.framesSent` (what the kernel believes it
                # sent). The two disagreeing is itself the diagnosis of a delivery problem between them.
                self._host = data

    @property
    def diagnostics(self) -> dict[str, Any]:
        """What the viewer says it sees, for working out why a viewer is blank or drawn wrongly: the size of its canvas
        and window, the pixel ratio, the WebGL renderer and whether its context was lost, what level of detail it chose,
        and any errors it hit. `host` is what the widget's own host script (which runs even if the inner viewer's
        script never does) has itself received and relayed from the kernel -- comparing `host.hostReceived` against
        `kernel.framesSent` says whether messages the kernel believes it sent actually reached the browser at all.
        `state` is empty until the viewer has loaded and settled (a second or two after it appears)."""
        with self._reports_lock:
            page = {"state": dict(self._state), "errors": list(self._errors), "host": dict(self._host)}
        return {**page, "kernel": self._plexgraph.counters}

    def close(self) -> None:
        """Stop streaming to the viewer. Safe to call twice.

        The viewer keeps showing what it has, marked as disconnected: it can no longer be updated, but the picture stays
        in the cell's output. (Closing an ipywidgets widget normally destroys its connection and blanks every view of
        it, which is not what closing a graph viewer should do to a notebook. The widget itself is released with the
        kernel, or when it is no longer referenced.)"""
        already = self._plexgraph.stopped
        self._plexgraph.stop()
        if not already and getattr(self, "comm", None) is not None:
            self.send({"type": "closed"})
