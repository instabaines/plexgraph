// Runs in the notebook page. It hosts the viewer (the same app the browser tab shows) in a sandboxed iframe and relays
// between the kernel and that iframe: frames arrive over the notebook's own connection and are handed to the viewer with
// postMessage. Nothing is served on a port, so this works wherever a notebook widget does.
//
// The next line is completed with the viewer's page, in one piece, when the widget class is built.
const APP_HTML = __APP_HTML__;
// If the embedded viewer has not said hello within this long, something kept its script from ever running (seen causes:
// a host page whose Content-Security-Policy has no 'unsafe-inline' for scripts, or a browser with no usable WebGL) and
// nothing else in this file would ever tell Python that. This is a report of last resort, not the everyday path: a big
// graph still says hello immediately (loading it happens after) and does not risk hitting this timeout.
const STARTUP_TIMEOUT_MS = 8000;

export default {
  render({ model, el }) {
    // Visible outside the sandboxed iframe, so it renders even if the iframe's script never runs at all.
    const loading = document.createElement("div");
    loading.textContent = "Loading viewer\u2026";
    loading.style.cssText = "font:13px system-ui,sans-serif;color:#64748b;padding:8px 2px";
    el.appendChild(loading);

    const iframe = document.createElement("iframe");
    // Scripts and file downloads (the viewer exports images); nothing else, and no access to this page.
    iframe.setAttribute("sandbox", "allow-scripts allow-downloads");
    iframe.style.cssText = "width:100%;border:0;display:block";
    const fit = () => { iframe.style.height = model.get("height") + "px"; };
    fit();
    model.on("change:height", fit);

    // A srcdoc page has no address to carry settings, so they are handed over as a global the viewer reads first.
    // "<" is escaped so no setting can close the script element.
    const settings = JSON.stringify(model.get("viewer_query")).replace(/</g, "\\u003c");
    iframe.srcdoc = APP_HTML.replace("<head>", "<head><script>window.__PLEXGRAPH_PARAMS__ = " + settings + ";</script>");

    const toViewer = (message, transfer) => {
      if (iframe.contentWindow) iframe.contentWindow.postMessage(message, "*", transfer || []);
    };

    // A frame larger than one message travels in pieces (see widget.py); they are put back together here.
    let stream = -1;
    const partial = new Map(); // frame id -> { pieces, received }
    const frame = (bytes) => toViewer({ plexgraph: "frame", data: bytes }, [bytes]);

    const onKernel = (msg, buffers) => {
      if (msg.type === "closed") { // the kernel stopped this viewer; what it shows stays on screen
        toViewer({ plexgraph: "closed" });
        return;
      }
      if (msg.type !== "frame" || !buffers.length) return;
      if (msg.stream !== stream) { // the kernel started over (the viewer reloaded): drop anything half-received
        stream = msg.stream;
        partial.clear();
      }
      const piece = new Uint8Array(buffers[0].buffer, buffers[0].byteOffset, buffers[0].byteLength);
      if (msg.count === 1) {
        frame(piece.slice().buffer);
        return;
      }
      let entry = partial.get(msg.id);
      if (!entry) {
        entry = { pieces: new Array(msg.count), received: 0 };
        partial.set(msg.id, entry);
      }
      if (!entry.pieces[msg.index]) {
        entry.pieces[msg.index] = piece.slice();
        entry.received += 1;
      }
      if (entry.received === msg.count) {
        partial.delete(msg.id);
        const whole = new Uint8Array(entry.pieces.reduce((n, p) => n + p.length, 0));
        let at = 0;
        for (const p of entry.pieces) { whole.set(p, at); at += p.length; }
        frame(whole.buffer);
      }
    };
    model.on("msg:custom", onKernel);

    // The viewer says hello once it is listening, and again if it is reloaded; each time the kernel starts the stream.
    let started = false;
    const onViewer = (event) => {
      if (event.source !== iframe.contentWindow || !event.data) return;
      if (event.data.plexgraph === "hello") {
        started = true;
        loading.remove();
        model.send({ type: "hello" });
      } else if (event.data.plexgraph === "report") {
        model.send({ type: "report", kind: event.data.kind, data: event.data.data });
      }
    };
    window.addEventListener("message", onViewer);

    const startupTimer = setTimeout(() => {
      if (started) return;
      loading.textContent = "The viewer did not start. This notebook's page may block the script it needs to run "
        + "(a strict Content-Security-Policy), or its browser may have no usable WebGL. Open the browser console for "
        + "the reason; show(widget=False) uses a different route that does not need an embedded script.";
      loading.style.color = "#b91c1c";
      model.send({
        type: "report", kind: "error",
        data: { message: "the embedded viewer did not say hello within " + STARTUP_TIMEOUT_MS + "ms; its script may "
          + "have been blocked (Content-Security-Policy) or it has no usable WebGL" },
      });
    }, STARTUP_TIMEOUT_MS);

    el.appendChild(iframe);
    return () => {
      clearTimeout(startupTimer);
      window.removeEventListener("message", onViewer);
      model.off("msg:custom", onKernel);
      model.off("change:height", fit);
      toViewer({ plexgraph: "closed" });
    };
  },
};
