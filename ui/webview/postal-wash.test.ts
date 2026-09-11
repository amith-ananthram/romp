// The incoming card's tint lightness is MEASURED from the page under it (postal-wash.ts, T337c): the pure parts here,
// the sheet's fallback values checked against the measurement for the two shipped themes, and the hook pinned.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { oklabLightness, groundOf, syncPostalWash, resolveColour } from "./postal-wash";

const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("the shipped fallbacks are the measured grounds of the two themes", () => {
  // dark: #1e1e1e under a 3% white box; light: the cream #F1EAE2 under a 3% black box (styles.css)
  const dark = oklabLightness(groundOf([30, 30, 30, 1], [255, 255, 255, 0.03]));
  const light = oklabLightness(groundOf([241, 234, 226, 1], [0, 0, 0, 0.03]));
  assert.ok(Math.abs(dark - 0.263) < 0.002, "dark ground L " + dark.toFixed(3));
  assert.ok(Math.abs(light - 0.919) < 0.002, "light ground L " + light.toFixed(3));
  assert.match(CSS, /--postal-wash-l: 0\.263;/, "the dark fallback is the measurement");
  assert.match(CSS, /--postal-wash-l: 0\.919;/, "the light fallback is the measurement");
});

test("a document that cannot draw leaves the token to the sheet", () => {
  // a minimal document: no canvas context, so nothing is measured and no inline value is set
  const removed: string[] = [];
  const doc = {
    body: { style: { removeProperty: (k: string) => removed.push(k), setProperty: () => { throw new Error("must not set"); } } },
    createElement: () => ({ getContext: () => null }),
    defaultView: { getComputedStyle: () => ({ getPropertyValue: (k: string) => (k === "--bg" ? "#1e1e1e" : "rgba(255,255,255,0.03)") }) },
  } as unknown as Document;
  assert.equal(syncPostalWash(doc), false);
  assert.deepEqual(removed, ["--postal-wash-l"], "an unmeasurable page clears any stale inline value");
});

// a canvas as the probe sees it: a value it takes becomes the fill (reported as a colour string); one it refuses leaves
// the previous fill in place, which is exactly how a real 2d context behaves on an unparsable fillStyle
function fakeDoc(accepts: (v: string) => string | null): Document {
  let fill = "";
  const ctx = {
    set fillStyle(v: string) { const r = accepts(v); if (r !== null) fill = r; },
    get fillStyle() { return fill; },
    clearRect() {}, fillRect() {},
    getImageData() { const m = /rgb\((\d+), (\d+), (\d+)\)/.exec(fill)!; return { data: [+m[1], +m[2], +m[3], 255] }; },
  };
  return { createElement: () => ({ getContext: () => ctx }) } as unknown as Document;
}
const CANVAS_TAKES = (v: string) => {
  if (v === "#010203") return "rgb(1, 2, 3)";
  if (v === "#040506") return "rgb(4, 5, 6)";
  if (/^rgb\(0 0 0\)$|^#000000ff$|^black$/.test(v)) return "rgb(0, 0, 0)";
  if (v === "#1e1e1e") return "rgb(30, 30, 30)";
  return null;                                       // refused: the fill stays
};

test("the probe resolves every spelling of black and refuses garbage by two sentinels, not an enumeration", () => {
  for (const black of ["rgb(0 0 0)", "#000000ff", "black"]) {
    assert.deepEqual(resolveColour(fakeDoc(CANVAS_TAKES), black), [0, 0, 0, 1], black + " is the measured ground, not a fallback");
  }
  assert.deepEqual(resolveColour(fakeDoc(CANVAS_TAKES), "#1e1e1e"), [30, 30, 30, 1]);
  assert.equal(resolveColour(fakeDoc(CANVAS_TAKES), "not a colour"), null, "a refused value leaves each sentinel: null");
  assert.equal(resolveColour(fakeDoc(CANVAS_TAKES), ""), null);
});

test("the chat installs the measurement at boot, and the sheet's rule reads the token with the shipped fallback", () => {
  assert.match(RENDER, /import \{ installPostalWash \} from "\.\/postal-wash";/);
  assert.match(RENDER, /installPostalWash\(document\);/);
  assert.match(CSS, /background: oklch\(from var\(--notice-rail, var\(--box-bg\)\) var\(--postal-wash-l, 0\.263\) var\(--postal-wash-c, 0\.03\) h\); \}/,
               "the rule falls back to the dark values when a token is missing, so the card never loses its background");
});
