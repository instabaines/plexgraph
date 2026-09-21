import { parseColor, type RGBA } from "./colors";

// Continuous colormaps, defined by evenly spaced control colors and interpolated linearly. Names follow
// matplotlib/ColorBrewer, and the control colors are their published values, so plots look familiar.
const CONTINUOUS: Record<string, string[]> = {
  viridis: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#fde725"],
  plasma: ["#0d0887", "#47039f", "#7301a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fa9e3b", "#fdc328", "#f0f921"],
  inferno: ["#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06", "#f7d13d", "#fcffa4"],
  magma: ["#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9567", "#fec98d", "#fcfdbf"],
  cividis: ["#00204d", "#31446b", "#666970", "#958f78", "#cbba69", "#ffea46"],
  coolwarm: ["#3b4cc0", "#6788ee", "#9abbff", "#c9d7f0", "#edd1c2", "#f7a889", "#e26952", "#b40426"],
  RdBu: ["#67001f", "#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#f7f7f7", "#d1e5f0", "#92c5de", "#4393c3", "#2166ac", "#053061"],
  Spectral: ["#9e0142", "#d53e4f", "#f46d43", "#fdae61", "#fee08b", "#ffffbf", "#e6f598", "#abdda4", "#66c2a5", "#3288bd", "#5e4fa2"],
  Blues: ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"],
  Greens: ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#006d2c", "#00441b"],
  Reds: ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#a50f15", "#67000d"],
  Oranges: ["#fff5eb", "#fee6ce", "#fdd0a2", "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#a63603", "#7f2704"],
  Purples: ["#fcfbfd", "#efedf5", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#54278f", "#3f007d"],
  Greys: ["#ffffff", "#f0f0f0", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525", "#000000"],
  YlOrRd: ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026", "#800026"],
};

/** The hyperloom default categorical palette (also used for layers); slightly translucent like the original. */
export const DEFAULT_PALETTE: RGBA[] = [
  [0.85, 0.33, 0.1, 0.85],
  [0.2, 0.65, 0.32, 0.85],
  [0.55, 0.35, 0.85, 0.85],
  [0.9, 0.65, 0.13, 0.85],
  [0.13, 0.59, 0.75, 0.85],
  [0.8, 0.2, 0.45, 0.85],
  [0.4, 0.4, 0.4, 0.85],
  [0.65, 0.75, 0.15, 0.85],
];

const CATEGORICAL: Record<string, string[]> = {
  tab10: ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"],
  Set1: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999"],
  Set2: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
  Dark2: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"],
  Paired: ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f", "#ff7f00", "#cab2d6", "#6a3d9a", "#ffff99", "#b15928"],
  Pastel1: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6", "#ffffcc", "#e5d8bd", "#fddaec", "#f2f2f2"],
};

export const colormapNames = (): string[] => Object.keys(CONTINUOUS);
export const paletteNames = (): string[] => ["default", ...Object.keys(CATEGORICAL)];
export const isColormap = (name: string): boolean => name in CONTINUOUS;
export const isPalette = (name: string): boolean => name === "default" || name in CATEGORICAL;

const LUT_SIZE = 256;
const luts = new Map<string, Float32Array>();

function lutFor(name: string): Float32Array {
  let lut = luts.get(name);
  if (lut) return lut;
  const stops = CONTINUOUS[name];
  if (!stops) throw new RangeError(`unknown colormap ${JSON.stringify(name)}; choose one of ${colormapNames().join(", ")}`);
  const colors = stops.map(s => parseColor(s));
  lut = new Float32Array(LUT_SIZE * 3);
  for (let i = 0; i < LUT_SIZE; i++) {
    const x = (i / (LUT_SIZE - 1)) * (colors.length - 1);
    const lo = Math.min(colors.length - 2, Math.floor(x)), f = x - lo;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = colors[lo][c] * (1 - f) + colors[lo + 1][c] * f;
  }
  luts.set(name, lut);
  return lut;
}

/** Color at position t in [0, 1] of a named colormap (values outside are clamped). */
export function sampleColormap(name: string, t: number, reverse = false): RGBA {
  const lut = lutFor(name);
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  const i = Math.round((reverse ? 1 - x : x) * (LUT_SIZE - 1)) * 3;
  return [lut[i], lut[i + 1], lut[i + 2], 1];
}

/** The colors of a named categorical palette. */
export function paletteColors(name: string): RGBA[] {
  if (name === "default") return DEFAULT_PALETTE;
  const stops = CATEGORICAL[name];
  if (!stops) throw new RangeError(`unknown palette ${JSON.stringify(name)}; choose one of ${paletteNames().join(", ")}`);
  return stops.map(s => parseColor(s));
}
