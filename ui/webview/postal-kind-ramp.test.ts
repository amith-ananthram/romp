// T337 (the user 2026-09-10, who found the three kind colours of T320 too alike): each theme's three tokens sit at
// positions 0, 1/2 and 1 of ONE straight line in OKLCH, hue pinned to the accent's, from the deepest step that still reads
// on a boxed card to the far end of the tint the hue holds. The POSITIONS are the pin, not the hexes: a re-ink that keeps
// the even spacing passes, one that bunches two steps (T320's spanned a fifth of the line) fails. The comment beside the
// tokens in styles.css names the same knots.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
function block(opener: string): string {
  const at = CSS.indexOf(opener);
  assert.ok(at >= 0, opener + " present");
  return CSS.slice(at, CSS.indexOf("\n}", at)).replace(/\/\*[\s\S]*?\*\//g, "");   // comment-blind, like theme-parity
}
function token(blockText: string, name: string): string {
  const m = new RegExp(name + ":\\s*(#[0-9a-f]{6});", "i").exec(blockText);
  assert.ok(m, name + " declared as a hex");
  return m![1].toLowerCase();
}
// sRGB hex → OKLCH (Björn Ottosson's OKLab, the standard matrices)
function oklch(hex: string): { L: number; C: number; h: number } {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { L, C: Math.hypot(a, bb), h: ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360 };
}
const hueGap = (h: number, ref: number) => Math.abs(((h - ref + 540) % 360) - 180);

// the two lines: (L, C) at position 0 and at position 1, the hue held; the comment beside the tokens says the same
const MAPS = {
  dark: { block: ":root {", hue: 244, from: { L: 0.64, C: 0.085 }, to: { L: 0.9, C: 0.05 } },
  light: { block: "body.theme-light {", hue: 38, from: { L: 0.5, C: 0.11 }, to: { L: 0.25, C: 0.09 } },
};
const STEPS = ["--postal-coordinate", "--postal-delegate", "--postal-question"];

for (const [theme, map] of Object.entries(MAPS)) {
  test(theme + ": the three kind tokens sit at 0, 1/2 and 1 of the accent-hue line, evenly spaced", () => {
    const b = block(map.block);
    const steps = STEPS.map((n) => oklch(token(b, n)));
    steps.forEach((s, i) => {
      const t = i / 2;
      const wantL = map.from.L + (map.to.L - map.from.L) * t, wantC = map.from.C + (map.to.C - map.from.C) * t;
      assert.ok(Math.abs(s.L - wantL) <= 0.012, `${theme} ${STEPS[i]}: L ${s.L.toFixed(3)} is off the line (${wantL})`);
      assert.ok(Math.abs(s.C - wantC) <= 0.012, `${theme} ${STEPS[i]}: C ${s.C.toFixed(3)} is off the line (${wantC})`);
      assert.ok(hueGap(s.h, map.hue) <= 8, `${theme} ${STEPS[i]}: hue ${s.h.toFixed(1)} is not the accent's ${map.hue}`);
    });
    // even: the two lightness steps match; wide: the line is the whole map, not a fifth of it
    const d1 = steps[1].L - steps[0].L, d2 = steps[2].L - steps[1].L;
    assert.ok(Math.abs(d1 - d2) <= 0.012, `${theme}: uneven steps ${d1.toFixed(3)} vs ${d2.toFixed(3)}`);
    assert.ok(Math.abs(steps[2].L - steps[0].L) >= 0.24, `${theme}: the tokens span ${Math.abs(steps[2].L - steps[0].L).toFixed(3)} of lightness, the map is 0.25 or more`);
  });
}

test("the comment beside the tokens names the same knots, and the tokens hold their theme's accent hue", () => {
  assert.match(CSS, /L \.64, C \.085[\s\S]{0,300}L \.90, C \.05/, "the dark line's knots, in the :root comment");
  assert.match(CSS, /L \.50, C \.11[\s\S]{0,200}L \.25, C \.09/, "the light line's knots, in the light comment");
  assert.match(CSS, /hue pinned\s+at the accent's 244/);
  // the accent itself sits on each line's hue: the ramp is the accent's family, not a neighbour's
  assert.ok(hueGap(oklch(token(block(":root {"), "--accent")).h, MAPS.dark.hue) <= 8, "the dark accent's hue is the dark line's");
  assert.ok(hueGap(oklch(token(block("body.theme-light {"), "--accent")).h, MAPS.light.hue) <= 8, "the light accent's hue is the light line's");
});
