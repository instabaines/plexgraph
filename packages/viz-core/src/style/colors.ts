export type RGBA = [number, number, number, number];
/** A CSS color string ("#1f77b4", "crimson", "rgb(...)") or [r, g, b] / [r, g, b, a] with components in 0-1. */
export type ColorInput = string | ArrayLike<number>;

const HEX = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

function fromHex(text: string): RGBA | null {
  const m = HEX.exec(text.trim());
  if (!m) return null;
  let h = m[1];
  if (h.length <= 4) h = Array.from(h, c => c + c).join("");
  const n = (i: number) => parseInt(h.slice(i, i + 2), 16) / 255;
  return [n(0), n(2), n(4), h.length === 8 ? n(6) : 1];
}

let probe: CanvasRenderingContext2D | null | undefined;

/** Any CSS color, by asking the browser to normalise it (names, hsl(), rgb() ...). Null outside a browser or when invalid. */
function fromCss(text: string): RGBA | null {
  if (typeof document === "undefined") return null;
  probe ??= document.createElement("canvas").getContext("2d");
  if (!probe) return null;
  // An invalid string leaves fillStyle unchanged, so assigning it after two different sentinels tells them apart.
  probe.fillStyle = "#010203"; probe.fillStyle = text; const first = String(probe.fillStyle);
  probe.fillStyle = "#040506"; probe.fillStyle = text; const second = String(probe.fillStyle);
  if (first !== second) return null;
  const hex = fromHex(first);
  if (hex) return hex;
  const m = /^rgba?\(([^)]+)\)$/.exec(first);
  if (!m) return null;
  const [r, g, b, a = 1] = m[1].split(",").map(Number);
  return [r / 255, g / 255, b / 255, a];
}

/** Normalise a color to [r, g, b, a] in 0-1. Throws a descriptive error for anything it cannot read. */
export function parseColor(input: ColorInput): RGBA {
  if (typeof input === "string") {
    const rgba = fromHex(input) ?? fromCss(input);
    if (!rgba) throw new TypeError(`cannot read color ${JSON.stringify(input)}; use "#rrggbb", a CSS name, or [r, g, b, a] in 0-1`);
    return rgba;
  }
  if (input.length !== 3 && input.length !== 4) throw new TypeError(`a color array needs 3 or 4 numbers (r, g, b[, a] in 0-1), got ${input.length}`);
  const [r, g, b, a = 1] = Array.from(input);
  for (const v of [r, g, b, a]) {
    if (!Number.isFinite(v) || v < -1e-9 || v > 1 + 1e-9) throw new RangeError(`color components are 0-1, got ${JSON.stringify(Array.from(input))}; for 0-255 values use a hex string`);
  }
  return [r, g, b, a];
}

export function rgbaToCss([r, g, b, a]: RGBA): string {
  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${Number(a.toFixed(3))})`;
}

/** "#rrggbb" (alpha dropped), for HTML color inputs. */
export function rgbaToHex([r, g, b]: RGBA): string {
  const h = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0");
  return `#${h(r)}${h(g)}${h(b)}`;
}

export function withAlpha(color: RGBA, alpha: number): RGBA {
  return [color[0], color[1], color[2], alpha];
}
