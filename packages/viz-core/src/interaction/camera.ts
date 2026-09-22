// A minimal 2D pan/zoom camera. Exposes a 3x3 transform matrix (as a
// column-major mat3, flattened) for the renderer's vertex shader, and
// screen<->world coordinate conversion for future hit-testing/picking.

export class Camera {
  x = 0;
  y = 0;
  zoom = 1;

  // The renderer's frame loop only runs while something needs it (render-on-demand); a camera change from wheel/drag
  // is otherwise invisible to it (this class has no other link to the renderer), so it is told directly. Optional so
  // tests and other direct users of Camera do not need to supply one.
  constructor(private canvas: HTMLCanvasElement, private onChange?: () => void) {
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("pointerdown", this.onPointerDown);
  }

  private dragging = false;
  private lastClientX = 0;
  private lastClientY = 0;

  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.001);
    this.zoom = Math.min(50, Math.max(0.02, this.zoom * factor));
    this.onChange?.();
  };

  private onPointerDown = (e: PointerEvent) => {
    this.dragging = true;
    this.lastClientX = e.clientX;
    this.lastClientY = e.clientY;
    this.canvas.setPointerCapture(e.pointerId);
    this.canvas.addEventListener("pointermove", this.onPointerMove);
    this.canvas.addEventListener("pointerup", this.onPointerUp);
  };

  private onPointerMove = (e: PointerEvent) => {
    if (!this.dragging) return;
    const dx = e.clientX - this.lastClientX;
    const dy = e.clientY - this.lastClientY;
    this.lastClientX = e.clientX;
    this.lastClientY = e.clientY;
    const scale = 2 / (this.canvas.clientHeight * this.zoom);
    this.x -= dx * scale;
    this.y += dy * scale;
    this.onChange?.();
  };

  private onPointerUp = () => {
    this.dragging = false;
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
  };

  /** Column-major 3x3 matrix mapping world space to clip space ([-1, 1]). */
  matrix(aspect: number): number[] {
    const s = this.zoom;
    return [s / aspect, 0, 0, 0, s, 0, -this.x * (s / aspect), -this.y * s, 1];
  }

  dispose(): void {
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    this.canvas.removeEventListener("pointermove", this.onPointerMove);
    this.canvas.removeEventListener("pointerup", this.onPointerUp);
  }
}
