// T337 (the user 2026-09-10, who found the three kind colours of T320 too alike): each theme's three tokens sit at
// positions 0, 1/2 and 1 of ONE straight line in OKLCH, hue pinned to the accent's. The line's two ends are two floors:
// the deep end is the deepest step that still reads at 4.5:1 on the PROVISIONAL card's wash (theme-parity.test.ts holds
// that contrast, and the box's and the page's), the far end stops short of the prose ink so a lone Question still reads
// as a colour and not as body text (the distance is pinned here). The POSITIONS are the pin, not the hexes: a re-ink that
// keeps the line and the even spacing passes; one that bunches two steps (T320's light steps were .05 then .09 apart),
// drifts off the hue or runs into the ink fails. The comment beside the tokens in styles.css names the same two endpoints.
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
  // a bare hex, or the hex fallback of a var() (the dark --fg is var(--vscode-foreground, #cccccc))
  const m = new RegExp(name + ":\\s*(?:var\\([^,]+,\\s*)?(#[0-9a-f]{6})\\)?;", "i").exec(blockText);
  assert.ok(m, name + " declared as a hex, bare or as a var() fallback");
  return m![1].toLowerCase();
}
// sRGB hex → OKLab (Björn Ottosson's matrices); OKLCH from it
function oklab(hex: string): { L: number; a: number; b: number } {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const [r, g, b] = [1, 3, 5].map((i) => lin(parseInt(hex.slice(i, i + 2), 16) / 255));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return { L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
           a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
           b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s };
}
function oklch(hex: string): { L: number; C: number; h: number } {
  const { L, a, b } = oklab(hex);
  return { L, C: Math.hypot(a, b), h: ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360 };
}
const hueGap = (h: number, ref: number) => Math.abs(((h - ref + 540) % 360) - 180);
const dist = (x: { L: number; a: number; b: number }, y: { L: number; a: number; b: number }) => Math.hypot(x.L - y.L, x.a - y.a, x.b - y.b);

// the two lines: (L, C) at position 0 and at position 1, the hue held, both ends inside sRGB on that hue; the comment
// beside the tokens says the same
const MAPS = {
  dark: { block: ":root {", hue: 244, from: { L: 0.65, C: 0.1 }, to: { L: 0.85, C: 0.078 } },
  light: { block: "body.theme-light {", hue: 38, from: { L: 0.5, C: 0.11 }, to: { L: 0.3, C: 0.1 } },
};
const STEPS = ["--postal-coordinate", "--postal-delegate", "--postal-question"];

for (const [theme, map] of Object.entries(MAPS)) {
  test(theme + ": the three kind tokens sit at 0, 1/2 and 1 of the accent-hue line, evenly spaced, the far end clear of the ink", () => {
    const b = block(map.block);
    const steps = STEPS.map((n) => oklch(token(b, n)));
    steps.forEach((s, i) => {
      const t = i / 2;
      const wantL = map.from.L + (map.to.L - map.from.L) * t, wantC = map.from.C + (map.to.C - map.from.C) * t;
      assert.ok(Math.abs(s.L - wantL) <= 0.012, `${theme} ${STEPS[i]}: L ${s.L.toFixed(3)} is off the line (${wantL})`);
      assert.ok(Math.abs(s.C - wantC) <= 0.012, `${theme} ${STEPS[i]}: C ${s.C.toFixed(3)} is off the line (${wantC})`);
      assert.ok(hueGap(s.h, map.hue) <= 4, `${theme} ${STEPS[i]}: hue ${s.h.toFixed(1)} is not the accent's ${map.hue} (an endpoint clipped by the gamut drifts)`);
    });
    // even: the two lightness steps match
    const d1 = steps[1].L - steps[0].L, d2 = steps[2].L - steps[1].L;
    assert.ok(Math.abs(d1 - d2) <= 0.012, `${theme}: uneven steps ${d1.toFixed(3)} vs ${d2.toFixed(3)}`);
    // the far end keeps its distance from the prose ink (--fg): a lone Question is a colour, not body text. The dark end
    // sits at the ink's own lightness, so its distance is all chroma, the hue's gamut edge there; .075 is just under it
    const gap = dist(oklab(token(b, "--postal-question")), oklab(token(b, "--fg")));
    assert.ok(gap >= 0.075, `${theme}: --postal-question is ${gap.toFixed(3)} from --fg in OKLab, too close to the ink`);
  });
}

test("the comment beside the tokens names the same two endpoints, and the tokens hold their theme's accent hue", () => {
  assert.match(CSS, /L \.65, C \.10[\s\S]{0,500}L \.85, C \.078/, "the dark line's two endpoints, in the :root comment");
  assert.match(CSS, /L \.50, C \.11[\s\S]{0,300}L \.30, C \.10/, "the light line's two endpoints, in the light comment");
  assert.match(CSS, /hue pinned\s+at the accent's 244/);
  assert.match(CSS, /The trade-off is span against distance from the ink/, "the comment states the trade-off the far end makes");
  assert.match(CSS, /reads at 4\.5:1 on the PROVISIONAL card, the darkest ground a kind word sits on/, "the comment names the wash as the floor, not the box");
  // the accent itself sits on each line's hue: the ramp is the accent's family, not a neighbour's
  assert.ok(hueGap(oklch(token(block(":root {"), "--accent")).h, MAPS.dark.hue) <= 8, "the dark accent's hue is the dark line's");
  assert.ok(hueGap(oklch(token(block("body.theme-light {"), "--accent")).h, MAPS.light.hue) <= 8, "the light accent's hue is the light line's");
});
