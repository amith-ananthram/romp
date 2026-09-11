// The incoming postal card's tint: the peer's hue at the card ground's OWN lightness (styles.css, T337b). The
// lightness token, --postal-wash-l, ships with the two themes' values as fallbacks, but the page under the card is
// host-derived in the VS Code webview (--bg reads --vscode-editor-background), so a dark host theme other than the
// default would leave the tint lighter or darker than its ground. This module MEASURES the ground: the page colour
// and the box overlay, resolved through a one-pixel canvas (the one way to turn any CSS colour into channels), composited,
// converted to OKLab lightness, and written on the body as the token's inline value, which outranks every theme block.
// It runs at boot and again whenever the body's classes or the root's inline style change (a theme toggle, a host
// theme change: VS Code rewrites the root's custom properties). A page where the canvas cannot resolve a colour (a
// test document without a canvas) leaves the token to the sheet's fallback (T337c, the review of 2026-09-11).

export type RGBA = [number, number, number, number];

function lin(c: number): number { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }

/** OKLab lightness of an opaque sRGB colour (channels 0..255). */
export function oklabLightness(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((v) => lin(v / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
}

/** The card ground: the (opaque) page with the box overlay composited over it. */
export function groundOf(page: RGBA, box: RGBA): [number, number, number] {
  const a = box[3];
  return [0, 1, 2].map((i) => page[i] * (1 - a) + box[i] * a) as [number, number, number];
}

/** Any CSS colour as channels, through a one-pixel canvas; null when the document cannot draw (no canvas, an unparsable value). */
export function resolveColour(doc: Document, css: string): RGBA | null {
  const v = (css || "").trim();
  if (!v) return null;
  try {
    const cv = doc.createElement("canvas"); cv.width = 1; cv.height = 1;
    const ctx = cv.getContext("2d");
    if (!ctx) return null;
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000000";                     // a value the canvas refuses leaves the previous fill: make it detectable
    ctx.fillStyle = v;
    if (ctx.fillStyle === "#000000" && !/^(#000000|#000|black|rgb\(0,\s*0,\s*0\)|rgba\(0,\s*0,\s*0,\s*1\))$/i.test(v)) return null;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  } catch {
    return null;
  }
}

/** Measure the ground under an incoming card and write its lightness on the body; false when nothing could be measured. */
export function syncPostalWash(doc: Document): boolean {
  const cs = doc.defaultView ? doc.defaultView.getComputedStyle(doc.body) : null;
  if (!cs) return false;
  const page = resolveColour(doc, cs.getPropertyValue("--bg"));
  const box = resolveColour(doc, cs.getPropertyValue("--box-bg"));
  if (!page || page[3] < 1) { doc.body.style.removeProperty("--postal-wash-l"); return false; }
  const L = oklabLightness(groundOf(page, box || [0, 0, 0, 0]));
  doc.body.style.setProperty("--postal-wash-l", L.toFixed(3));
  return true;
}

/** Sync now and on every theme or host change (the body's classes, the root's inline style). */
export function installPostalWash(doc: Document): void {
  syncPostalWash(doc);
  if (typeof MutationObserver === "undefined") return;
  if (doc.body.dataset.postalWash === "1") return;   // idempotent: a second call (a theme applied again) re-measured above
  doc.body.dataset.postalWash = "1";
  let queued = false;
  const later = () => {
    if (queued) return;
    queued = true;
    (doc.defaultView || window).requestAnimationFrame(() => { queued = false; syncPostalWash(doc); });
  };
  new MutationObserver(later).observe(doc.body, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(later).observe(doc.documentElement, { attributes: true, attributeFilter: ["style", "class"] });
}
