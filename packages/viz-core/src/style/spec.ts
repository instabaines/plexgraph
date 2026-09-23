import type { TimeSplit } from "../render/time";
import type { ColorInput } from "./colors";

export const NODE_SHAPES = ["circle", "square", "triangle", "diamond", "cross"] as const;
export type NodeShape = (typeof NODE_SHAPES)[number];

/** A number computed from the graph: a node's degree, a connector's weight, or a time. */
export type NumericSource = "degree" | "weight" | "time";

interface ColormapOptions {
  /** A named continuous colormap (see colormapNames()); default "viridis". */
  colormap?: string;
  reverse?: boolean;
  /** Values mapped to the ends of the colormap; default is the data's min and max. */
  domain?: [number, number];
  /** Color for elements without a value; default is the element's base color. */
  missing?: ColorInput;
}

/** How to color nodes or edges. A plain color is shorthand for a constant. */
export type ColorEncoding =
  | ColorInput
  | { kind: "constant"; color: ColorInput }
  /** One color per distinct value (categorical), or a colormap over a numeric attribute (continuous); "auto" decides. */
  | ({ kind: "attribute"; attribute: string; scale?: "auto" | "categorical" | "continuous"; palette?: string | ColorInput[] } & ColormapOptions)
  /** degree and time apply to nodes, weight to edges; time works for both (a node's first activity, an edge's start). */
  | ({ kind: NumericSource } & ColormapOptions)
  /** Split the graph's time span into buckets (like the time ribbon) and color by bucket. */
  | { kind: "timeBucket"; buckets?: number; split?: TimeSplit; colormap?: string; reverse?: boolean; nodeTime?: "first" | "last"; missing?: ColorInput }
  /** One number per element (node id order, or connector order), mapped through a colormap. */
  | ({ kind: "values"; values: ArrayLike<number> } & ColormapOptions)
  /** One color per element: a flat [r, g, b, a, ...] array of 0-1 numbers, or a list of colors. Where `present` is
   * given, elements with a 0 there are left on their base color (so a partial mapping does not need every element). */
  | { kind: "colors"; colors: ArrayLike<number> | ColorInput[]; present?: ArrayLike<number>; missing?: ColorInput };

/** Node diameter or edge width in pixels: a constant, or a range driven by a numeric source. */
export type SizeEncoding =
  | number
  | { kind: "constant"; value: number }
  /** Explicit pixel sizes, one per element (NaN leaves the base size). */
  | { kind: "pixels"; values: ArrayLike<number> }
  | {
      kind: NumericSource | "attribute" | "values";
      attribute?: string;
      values?: ArrayLike<number>;
      /** [smallest, largest] size in pixels. */
      range: [number, number];
      domain?: [number, number];
      scale?: "linear" | "sqrt" | "log";
      /** Size for elements without a value; default is the base size. */
      missing?: number;
    };

export type ShapeEncoding =
  | NodeShape
  | { kind: "constant"; shape: NodeShape }
  | { kind: "attribute"; attribute: string; shapes?: NodeShape[] }
  | { kind: "values"; values: ArrayLike<string | number> };

export interface LabelStyle {
  /** "hover" (default: only the hovered or selected node), "all" (every drawn node, capped), or "none". */
  mode?: "hover" | "all" | "none";
  fontSize?: number;
  color?: ColorInput;
  /** Draw a light outline behind the text so it stays readable over edges. */
  halo?: boolean;
  /** Node attribute to show instead of the node key. */
  attribute?: string | null;
}

export interface NodeStyle {
  color?: ColorEncoding | null;
  /** Diameter in pixels. */
  size?: SizeEncoding | null;
  shape?: ShapeEncoding | null;
  /** 0-1, multiplies each node's own alpha. */
  opacity?: number | null;
  outline?: { color?: ColorInput; width?: number } | null;
  label?: LabelStyle | null;
}

export interface EdgeStyle {
  color?: ColorEncoding | null;
  /** Width in pixels (edges are drawn one pixel wide above the thin-edge threshold). */
  width?: SizeEncoding | null;
  /** 0-1, multiplies each edge's own alpha. */
  opacity?: number | null;
  /** Bend edges into arcs: 0 is straight; about 0.2 is typical; negative bends the other way. */
  curvature?: number | null;
  /** Scale arrowheads (1 is the default size). */
  arrowScale?: number | null;
  /**
   * Line pattern, as `[on1, off1, on2, off2]` pixel lengths (matplotlib-style dash tuple). `null`/omitted
   * is solid. A repeating on/off pair with the second pair zeroed gives a simple dash or dot.
   */
  dash?: number[] | null;
}

/** A partial style. Fields you leave out are unchanged; `null` restores that field's default. */
export interface StyleSpec {
  node?: NodeStyle | null;
  edge?: EdgeStyle | null;
  background?: ColorInput | null;
}
