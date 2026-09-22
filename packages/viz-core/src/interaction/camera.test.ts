import { describe, expect, it, vi } from "vitest";
import { Camera } from "./camera";

// No jsdom in this project (see transport/parent.test.ts) -- a fake canvas, not a real DOM, matching that pattern.
function fakeCanvas(clientHeight = 500) {
  const listeners = new Map<string, Set<(e: any) => void>>();
  const on = (type: string, fn: (e: any) => void) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type)!.add(fn);
  };
  const off = (type: string, fn: (e: any) => void) => listeners.get(type)?.delete(fn);
  const dispatch = (type: string, event: any) => {
    for (const fn of listeners.get(type) ?? []) fn(event);
  };
  const captured: number[] = [];
  const canvas = {
    clientHeight,
    addEventListener: on,
    removeEventListener: off,
    setPointerCapture: (id: number) => captured.push(id),
  };
  const hasListener = (type: string, fn: (e: any) => void) => listeners.get(type)?.has(fn) ?? false;
  const listenerCount = (type: string) => listeners.get(type)?.size ?? 0;
  return { canvas: canvas as unknown as HTMLCanvasElement, dispatch, captured, hasListener, listenerCount };
}

function wheelEvent(deltaY: number) {
  return { deltaY, preventDefault: vi.fn() };
}

function pointerEvent(clientX: number, clientY: number, pointerId = 1) {
  return { clientX, clientY, pointerId };
}

describe("Camera", () => {
  it("starts centered, at 1x zoom", () => {
    const { canvas } = fakeCanvas();
    const camera = new Camera(canvas);
    expect(camera.x).toBe(0);
    expect(camera.y).toBe(0);
    expect(camera.zoom).toBe(1);
  });

  // ---- wheel / zoom

  it("zooms in on a negative deltaY (scroll up) and out on a positive one (scroll down)", () => {
    const { canvas, dispatch } = fakeCanvas();
    const camera = new Camera(canvas);
    dispatch("wheel", wheelEvent(-100));
    expect(camera.zoom).toBeGreaterThan(1);
    const afterFirst = camera.zoom;
    dispatch("wheel", wheelEvent(100));
    expect(camera.zoom).toBeLessThan(afterFirst);
  });

  it("matches the documented factor exactly: zoom *= exp(-deltaY * 0.001)", () => {
    const { canvas, dispatch } = fakeCanvas();
    const camera = new Camera(canvas);
    dispatch("wheel", wheelEvent(-250));
    expect(camera.zoom).toBeCloseTo(Math.exp(0.25), 10);
  });

  it("prevents the page from scrolling on wheel", () => {
    const { canvas, dispatch } = fakeCanvas();
    new Camera(canvas);
    const event = wheelEvent(10);
    dispatch("wheel", event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });

  it("clamps zoom to [0.02, 50] however far the wheel goes", () => {
    const { canvas, dispatch } = fakeCanvas();
    const camera = new Camera(canvas);
    dispatch("wheel", wheelEvent(1_000_000)); // scroll out, far past the floor
    expect(camera.zoom).toBe(0.02);
    dispatch("wheel", wheelEvent(-1_000_000)); // scroll in, far past the ceiling
    expect(camera.zoom).toBe(50);
  });

  it("does not move x/y on wheel, only zoom", () => {
    const { canvas, dispatch } = fakeCanvas();
    const camera = new Camera(canvas);
    dispatch("wheel", wheelEvent(-50));
    expect(camera.x).toBe(0);
    expect(camera.y).toBe(0);
  });

  it("calls onChange on wheel, and does not require one to be supplied", () => {
    const { canvas, dispatch } = fakeCanvas();
    const onChange = vi.fn();
    new Camera(canvas, onChange);
    dispatch("wheel", wheelEvent(-10));
    expect(onChange).toHaveBeenCalledOnce();
    // no onChange supplied at all: must not throw
    const bare = fakeCanvas();
    new Camera(bare.canvas);
    expect(() => bare.dispatch("wheel", wheelEvent(-10))).not.toThrow();
  });

  // ---- pointer drag (pan)

  it("does nothing on pointermove before any pointerdown", () => {
    const { canvas, dispatch } = fakeCanvas();
    const onChange = vi.fn();
    const camera = new Camera(canvas, onChange);
    dispatch("pointermove", pointerEvent(50, 50));
    expect(camera.x).toBe(0);
    expect(camera.y).toBe(0);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("captures the pointer and starts listening for move/up on pointerdown", () => {
    const { canvas, dispatch, captured, listenerCount } = fakeCanvas();
    new Camera(canvas);
    expect(listenerCount("pointermove")).toBe(0);
    dispatch("pointerdown", pointerEvent(100, 100, 7));
    expect(captured).toEqual([7]);
    expect(listenerCount("pointermove")).toBe(1);
    expect(listenerCount("pointerup")).toBe(1);
  });

  it("pans x/y opposite the drag direction, scaled by clientHeight and zoom, and calls onChange", () => {
    const { canvas, dispatch } = fakeCanvas(500);
    const onChange = vi.fn();
    const camera = new Camera(canvas, onChange);
    dispatch("pointerdown", pointerEvent(100, 100));
    dispatch("pointermove", pointerEvent(120, 80)); // dx=+20 (right), dy=-20 (up)
    const scale = 2 / (500 * 1); // clientHeight=500, zoom=1
    expect(camera.x).toBeCloseTo(-20 * scale, 10); // x -= dx: dragging right (dx=+20) moves the world camera left
    expect(camera.y).toBeCloseTo(-20 * scale, 10); // y += dy: dragging up (dy=-20) moves the world camera up too
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("accumulates across several move events in one drag, each relative to the last position", () => {
    const { canvas, dispatch } = fakeCanvas(500);
    const camera = new Camera(canvas);
    dispatch("pointerdown", pointerEvent(0, 0));
    dispatch("pointermove", pointerEvent(10, 0));
    const afterFirst = camera.x;
    dispatch("pointermove", pointerEvent(20, 0)); // another +10, not +20 from the original start
    expect(camera.x - afterFirst).toBeCloseTo(afterFirst, 10); // second move added the same delta as the first
  });

  it("scales panning by the current zoom (more zoomed in -> a drag covers less world distance)", () => {
    const { canvas: c1, dispatch: d1 } = fakeCanvas(500);
    const zoomedOut = new Camera(c1);
    d1("pointerdown", pointerEvent(0, 0));
    d1("pointermove", pointerEvent(50, 0));

    const { canvas: c2, dispatch: d2 } = fakeCanvas(500);
    const zoomedIn = new Camera(c2);
    zoomedIn.zoom = 4;
    d2("pointerdown", pointerEvent(0, 0));
    d2("pointermove", pointerEvent(50, 0));

    expect(Math.abs(zoomedIn.x)).toBeCloseTo(Math.abs(zoomedOut.x) / 4, 10);
  });

  it("stops panning and stops listening on pointerup; further moves do nothing", () => {
    const { canvas, dispatch, listenerCount } = fakeCanvas();
    const onChange = vi.fn();
    const camera = new Camera(canvas, onChange);
    dispatch("pointerdown", pointerEvent(0, 0));
    dispatch("pointerup", {});
    expect(listenerCount("pointermove")).toBe(0);
    expect(listenerCount("pointerup")).toBe(0);
    onChange.mockClear();
    const before = { x: camera.x, y: camera.y };
    dispatch("pointermove", pointerEvent(200, 200));
    expect(camera).toMatchObject(before);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("a second pointerdown/drag after releasing the first works normally", () => {
    const { canvas, dispatch } = fakeCanvas(500);
    const camera = new Camera(canvas);
    dispatch("pointerdown", pointerEvent(0, 0));
    dispatch("pointermove", pointerEvent(10, 0));
    dispatch("pointerup", {});
    const afterFirstDrag = camera.x;
    dispatch("pointerdown", pointerEvent(0, 0));
    dispatch("pointermove", pointerEvent(10, 0));
    expect(camera.x).toBeCloseTo(afterFirstDrag * 2, 10);
  });

  // ---- matrix()

  it("matrix() is the identity-shaped transform at the origin, 1x zoom, square aspect", () => {
    const camera = new Camera(fakeCanvas().canvas);
    // 0 * -x is -0 for x=0, which toEqual distinguishes from +0 (===-wise they are equal); +0 normalizes it away.
    expect(camera.matrix(1).map((n) => n + 0)).toEqual([1, 0, 0, 0, 1, 0, 0, 0, 1]);
  });

  it("matrix() divides the x scale (and x translation) by aspect, leaves y alone", () => {
    const camera = new Camera(fakeCanvas().canvas);
    camera.zoom = 2;
    expect(camera.matrix(4).map((n) => n + 0)).toEqual([0.5, 0, 0, 0, 2, 0, 0, 0, 1]);
  });

  it("matrix() encodes camera position as a negated, scaled translation", () => {
    const camera = new Camera(fakeCanvas().canvas);
    camera.x = 3;
    camera.y = -5;
    camera.zoom = 2;
    const m = camera.matrix(1);
    expect(m[6]).toBeCloseTo(-3 * 2, 10); // -x * (zoom/aspect)
    expect(m[7]).toBeCloseTo(5 * 2, 10); // -y * zoom
    expect(m[8]).toBe(1);
  });

  // ---- dispose()

  it("dispose() stops responding to wheel and pointerdown", () => {
    const { canvas, dispatch } = fakeCanvas();
    const onChange = vi.fn();
    const camera = new Camera(canvas, onChange);
    camera.dispose();
    dispatch("wheel", wheelEvent(-100));
    expect(camera.zoom).toBe(1);
    dispatch("pointerdown", pointerEvent(0, 0));
    dispatch("pointermove", pointerEvent(50, 50));
    expect(camera.x).toBe(0);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("dispose() mid-drag is safe and removes the drag listeners too", () => {
    const { canvas, dispatch, listenerCount } = fakeCanvas();
    const camera = new Camera(canvas);
    dispatch("pointerdown", pointerEvent(0, 0));
    expect(listenerCount("pointermove")).toBe(1);
    expect(() => camera.dispose()).not.toThrow();
    expect(listenerCount("pointermove")).toBe(0);
    expect(listenerCount("pointerup")).toBe(0);
  });

  it("dispose() without ever dragging is safe (removeEventListener on listeners never added)", () => {
    const { canvas } = fakeCanvas();
    const camera = new Camera(canvas);
    expect(() => camera.dispose()).not.toThrow();
  });
});
