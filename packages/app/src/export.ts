// Export the current view as PNG/JPG (raster, straight canvas capture —
// requires preserveDrawingBuffer:true on the WebGL context, set in
// viz-core's Renderer), SVG (real vector geometry, from
// ViewerHandle.exportSVG()), HTML (the same SVG wrapped in a standalone
// page), or PDF (a hand-written single-page PDF embedding the JPEG raster
// — see pdf.ts for why this isn't a library; a true vector PDF would need
// re-deriving PDF drawing primitives from the same state exportSVG()
// reads, which is a reasonable future upgrade but isn't done here).

import { buildPdfFromJpeg } from "./pdf";

export type ExportFormat = "png" | "jpg" | "svg" | "html" | "pdf";

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on a delay rather than immediately — some browsers need the
  // object URL to still resolve when the download actually starts.
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error(`canvas.toBlob returned null for ${type}`))),
      type,
      quality
    );
  });
}

function wrapSvgAsHtml(svg: string): string {
  return `<!doctype html>\n<html><head><meta charset="utf-8"><title>plexgraph export</title></head>\n<body style="margin:0">\n${svg}\n</body></html>\n`;
}

export async function exportView(
  format: ExportFormat,
  canvas: HTMLCanvasElement,
  getSvg: () => string,
  filenameBase = "graph"
): Promise<void> {
  switch (format) {
    case "png": {
      const blob = await canvasToBlob(canvas, "image/png");
      downloadBlob(blob, `${filenameBase}.png`);
      return;
    }
    case "jpg": {
      const blob = await canvasToBlob(canvas, "image/jpeg", 0.92);
      downloadBlob(blob, `${filenameBase}.jpg`);
      return;
    }
    case "svg": {
      const svg = getSvg();
      downloadBlob(new Blob([svg], { type: "image/svg+xml" }), `${filenameBase}.svg`);
      return;
    }
    case "html": {
      const html = wrapSvgAsHtml(getSvg());
      downloadBlob(new Blob([html], { type: "text/html" }), `${filenameBase}.html`);
      return;
    }
    case "pdf": {
      const jpegBlob = await canvasToBlob(canvas, "image/jpeg", 0.92);
      const jpegBytes = new Uint8Array(await jpegBlob.arrayBuffer());
      const w = canvas.clientWidth || canvas.width;
      const h = canvas.clientHeight || canvas.height;
      const pdfBytes = buildPdfFromJpeg(jpegBytes, w, h);
      downloadBlob(new Blob([pdfBytes.buffer as ArrayBuffer], { type: "application/pdf" }), `${filenameBase}.pdf`);
      return;
    }
  }
}
