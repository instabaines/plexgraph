/** Shared slice geometry for WebGL and vector exports. Positions stay aligned
 * across panels so differences reflect connectivity, not layout drift. */
export type SliceLayout = "stack" | "atlas" | "ribbon";
export function sliceGeometry(positions: Float32Array, count: number, layout: SliceLayout,
  shearX = 0.22, shearY = 0.4, planeScale = 0.5) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < positions.length; i += 2) {
    minX = Math.min(minX, positions[i]); maxX = Math.max(maxX, positions[i]);
    minY = Math.min(minY, positions[i + 1]); maxY = Math.max(maxY, positions[i + 1]);
  }
  if (!Number.isFinite(minX)) { minX = minY = -0.1; maxX = maxY = 0.1; }
  const scale = layout === "stack" ? planeScale : 0.8 / Math.max(maxX - minX, maxY - minY, 0.2);
  const cols = layout === "ribbon" ? Math.max(1, count) : Math.ceil(Math.sqrt(Math.max(1, count)));
  const rows = Math.ceil(count / cols);
  const offset = (s: number): [number, number] => layout === "stack" ? [s * shearX, s * shearY] :
    [(s % cols - (cols - 1) / 2) * 1.15 - (minX + maxX) * scale / 2,
      ((rows - 1) / 2 - Math.floor(s / cols)) * 1.15 - (minY + maxY) * scale / 2];
  const point = (n: number, s: number): [number, number] => {
    const [x, y] = offset(s);
    return [(positions[n * 2] ?? 0) * scale + x, (positions[n * 2 + 1] ?? 0) * scale + y];
  };
  return { scale, offset, point, bounds: [minX * scale - 0.06, minY * scale - 0.06,
    maxX * scale + 0.06, maxY * scale + 0.06] as const };
}
