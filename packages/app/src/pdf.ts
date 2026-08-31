// A minimal single-page PDF writer that embeds one JPEG image, filling
// the page. Written by hand instead of pulling in a PDF library (jsPDF's
// default bundle drags in html2canvas + DOMPurify — ~700KB of unrelated
// weight — just to embed one raster image, which doesn't fit this
// project's "keep dependencies thin" pattern elsewhere: the custom WebGL
// layer, the hand-rolled convex-hull algorithm). Embedding a JPEG in a
// PDF is a well-defined, bounded format — see the PDF 1.4 spec's
// DCTDecode filter — not something that benefits from a general-purpose
// library.
//
// Structure: Catalog -> Pages -> one Page, with a content stream that
// draws one Image XObject scaled to the page's MediaBox, and the image
// XObject itself holding the raw JPEG bytes verbatim (DCTDecode means
// "the stream IS a JPEG file", no re-encoding needed).

class PdfObjects {
  private parts: Uint8Array[] = [];
  private offsets: number[] = [0]; // index 0 unused (object numbers are 1-based)
  private length = 0;

  private push(bytes: Uint8Array): void {
    this.parts.push(bytes);
    this.length += bytes.length;
  }

  addObject(bytesOrString: Uint8Array | string): number {
    const objNum = this.offsets.length;
    this.offsets.push(this.length);
    const header = new TextEncoder().encode(`${objNum} 0 obj\n`);
    this.push(header);
    this.push(typeof bytesOrString === "string" ? new TextEncoder().encode(bytesOrString) : bytesOrString);
    this.push(new TextEncoder().encode("\nendobj\n"));
    return objNum;
  }

  raw(text: string): void {
    this.push(new TextEncoder().encode(text));
  }

  build(rootObjNum: number): Uint8Array {
    const xrefOffset = this.length;
    let xref = `xref\n0 ${this.offsets.length}\n0000000000 65535 f \n`;
    for (let i = 1; i < this.offsets.length; i++) {
      xref += `${String(this.offsets[i]).padStart(10, "0")} 00000 n \n`;
    }
    xref += `trailer\n<< /Size ${this.offsets.length} /Root ${rootObjNum} 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
    this.raw(xref);

    const total = new Uint8Array(this.length);
    let offset = 0;
    for (const part of this.parts) {
      total.set(part, offset);
      offset += part.length;
    }
    return total;
  }
}

/** Build a single-page PDF (as bytes) containing `jpegBytes` scaled to
 * fill a page of `width` x `height` (PDF points — we use CSS pixels
 * 1:1, which prints/views at roughly 96 DPI, matching what's on screen
 * closely enough for a "save what I'm looking at" export). */
export function buildPdfFromJpeg(jpegBytes: Uint8Array, width: number, height: number): Uint8Array {
  const pdf = new PdfObjects();
  pdf.raw("%PDF-1.4\n");

  const catalogObj = pdf.addObject("<< /Type /Catalog /Pages 2 0 R >>");
  pdf.addObject("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"); // obj 2
  pdf.addObject(
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${width} ${height}] ` +
      `/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>`
  ); // obj 3

  const content = `q\n${width} 0 0 ${height} 0 0 cm\n/Im0 Do\nQ`;
  const contentBytes = new TextEncoder().encode(content);
  pdf.addObject(`<< /Length ${contentBytes.length} >>\nstream\n${content}\nendstream`); // obj 4

  const imageHeader = new TextEncoder().encode(
    `<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} ` +
      `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpegBytes.length} >>\nstream\n`
  );
  const imageFooter = new TextEncoder().encode("\nendstream");
  const imageObj = new Uint8Array(imageHeader.length + jpegBytes.length + imageFooter.length);
  imageObj.set(imageHeader, 0);
  imageObj.set(jpegBytes, imageHeader.length);
  imageObj.set(imageFooter, imageHeader.length + jpegBytes.length);
  pdf.addObject(imageObj); // obj 5

  return pdf.build(catalogObj);
}
