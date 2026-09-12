// The light theme's two structural guarantees (2026-08-28), so it cannot rot as features land
// dark-first:
//  1. KEY PARITY — body.theme-light re-declares EVERY custom property the sheet's :root defines
//     (a new token added to :root without a light value fails here, in the same commit).
//  2. CONTRAST — the designated (fg, bg) token pairs clear WCAG in BOTH themes; a feature that
//     adds a pair adds it to PAIRS.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const read = (f: string) => fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", f), "utf8");

function block(css: string, opener: string): string {
  const at = css.indexOf(opener);
  assert.ok(at >= 0, opener + " present");
  // COMMENT-BLIND parsing swallowed tokens whose declarations follow a multi-line comment and
  // corrupted values that carry one inline (PR #763 item 6: --accent/--card-border/--err skipped
  // in silence and the suite stayed green) — strip comments FIRST, always
  return css.slice(at, css.indexOf("\n}", at)).replace(/\/\*[\s\S]*?\*\//g, "");
}
function props(blockText: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of blockText.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/gi)) out.set(m[1], m[2].trim());
  return out;
}

// resolve a declared value to solid RGB over a background: hex directly; var(x, fallback) via the
// fallback (the stand-ins are absent in this static read); rgba composited over the bg
function rgbOf(v: string, bg: [number, number, number]): [number, number, number] | null {
  const hex = v.match(/^#([0-9a-f]{6})$/i);
  if (hex) { const h = hex[1]; return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]; }
  const vr = v.match(/^var\([^,]+,\s*(.+)\)$/);
  if (vr) return rgbOf(vr[1].trim(), bg);
  const ra = v.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/);
  if (ra) {
    const a = ra[4] === undefined ? 1 : parseFloat(ra[4]);
    return [1, 2, 3].map((i) => Math.round(parseInt(ra[i], 10) * a + bg[i - 1] * (1 - a))) as [number, number, number];
  }
  return null;   // fonts, shadows, sizes — not a color
}
function lum(rgb: [number, number, number]): number {
  const ch = (c: number) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2]);
}
/** OKLCH (L 0..1, C, hue in degrees) to sRGB channels 0..255, clamped to the gamut: the tinted ground of an incoming card. */
function oklchToRgb(L: number, C: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b, m_ = L - 0.1055613458 * a - 0.0638541728 * b, s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const lin = [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
               -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
  return lin.map((c) => { c = Math.max(0, Math.min(1, c)); const v = c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; return Math.round(v * 255); }) as [number, number, number];
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// (fg token, ground token, floor) — 4.5 for reading text, 3 for large/secondary chrome
const PAIRS: Array<[string, string, number]> = [
  ["--fg", "--bg", 4.5],
  ["--dim", "--bg", 4.5],
  ["--fg", "--surface-raised", 4.5],
  ["--accent", "--bg", 3],
  ["--cmt-hl-outline", "--bg", 3],   // the comment notch (the rail tick's fill): a LINE, so it must read against the page (T310)
  ["--st-awaiting-bg", "--bg", 3],   // the unread passage's dashed box and ring, and the tick's halo (2026-09-12): a line in the needs-you red
  ["--accent-fg", "--accent", 3],
  ["--warn", "--bg", 3],
  ["--err", "--bg", 3],
  ["--green", "--bg", 3],
  ["--code-fg", "--bg", 4.5],
  ["--text-muted", "--surface-raised", 4.5],
  ["--postal-coordinate", "--bg", 4.5],   // the postal kind word's three-step ramp (T320): text, so 4.5:1 on the page in both themes...
  ["--postal-delegate", "--bg", 4.5],
  ["--postal-question", "--bg", 4.5],
  ["--postal-coordinate", "--box-bg", 4.5],   // ...and on a BOXED card (an incoming message paints --box-bg over --bg), where the
  ["--postal-delegate", "--box-bg", 4.5],     // review found the light coordination step at 4.33:1 (2026-09-10)
  ["--postal-question", "--box-bg", 4.5],
  ["--st-working-fg", "--st-working-bg", 3],
  ["--st-ready-fg", "--st-ready-bg", 3],
  ["--st-blocked-fg", "--st-blocked-bg", 3],
  ["--st-retrying-fg", "--st-retrying-bg", 3],       // 2026-09-08: the retrying amber tokenised (#e67e22/#2a1500 dark, #9C4A0C/#fff light)
  // (--st-compacting-fg on --st-compacting-bg is deliberately NOT paired: the dark teal + white pairing predates
  // this file and sits at 2.49:1, and decision 3 of the 2026-09-08 notice audit keeps dark byte-identical; the
  // light re-ink — #0F766E, 4.30:1 on the card, white on it 5.47:1 — is pinned by value in notice-vocab.test.ts)
  ["--link", "--bg", 4.5],          // hyperlink ink (2026-09-02: the light theme's first link ink sat on --err)
  ["--hl-fg", "--bg", 4.5],         // the hljs syntax palette (tokenized 2026-09-02; was dark-only raw hex)
  ["--hl-kw", "--bg", 4.5],
  ["--hl-str", "--bg", 4.5],
  ["--hl-num", "--bg", 4.5],
  ["--hl-cmt", "--bg", 3],          // comments are deliberately quiet
  ["--hl-cmt", "--box-bg", 4.5],    // ...but readable on the code block they sit in (the fence's fill is --box-bg over --bg)
  ["--hl-title", "--bg", 4.5],
  ["--hl-meta", "--bg", 4.5],
  ["--hl-attr", "--bg", 4.5],
];

test("styles.css: the provisional wash the kind pairs are computed against is the one the sheet paints", () => {
  const css = read("styles.css");
  const bubble = css.slice(css.indexOf(".queued-bubble, .notice.queued-bubble, .notice.notice-slim.queued-bubble {"), css.indexOf("\n}\n", css.indexOf(".queued-bubble, .notice.queued-bubble, .notice.notice-slim.queued-bubble {")));
  assert.match(bubble, /background: color-mix\(in srgb, var\(--you\) 8\.5%, transparent\);/);
  assert.doesNotMatch(bubble, /opacity:/, "the fade is in the colours: no element opacity dims the words on the card");
});

for (const sheet of ["styles.css", "feed.css"]) {
  const css = read(sheet);
  const dark = props(block(css, ":root {"));
  const light = props(block(css, "body.theme-light {"));

  test(sheet + ": key parity — the light block re-declares every :root token", () => {
    const missing = [...dark.keys()].filter((k) => !light.has(k));
    assert.deepEqual(missing, [], sheet + " light block is missing tokens");
    // a silent parse regression must fail LOUDLY (PR #763 item 6): both blocks hold dozens of
    // tokens — a parser that suddenly sees fewer is broken, not a tidier sheet
    assert.ok(dark.size >= 30, sheet + " parsed only " + dark.size + " dark tokens — parser broken?");
    assert.ok(light.size >= dark.size, sheet + " parsed fewer light tokens than dark");
  });

  test(sheet + ": the designated pairs clear WCAG in BOTH themes — and the evaluated COUNT is pinned", () => {
    for (const [name, theme] of [["dark", dark], ["light", light]] as const) {
      const bgv = theme.get("--bg"); assert.ok(bgv, name + " --bg");
      const page = rgbOf(bgv!, [30, 30, 30])!;
      let evaluated = 0;
      for (const [fgTok, bgTok, floor] of PAIRS) {
        const f = theme.get(fgTok), g = theme.get(bgTok);
        if (!f || !g) continue;   // feed.css :root deliberately holds a SUBSET of tokens
        const ground = rgbOf(g, page); const fore = ground && rgbOf(f, ground);
        if (!ground || !fore) continue;
        evaluated++;
        assert.ok(contrast(fore, ground) >= floor,
          `${sheet} ${name}: ${fgTok} on ${bgTok} = ${contrast(fore, ground).toFixed(2)} < ${floor}`);
      }
      // a skip must be loud (PR #763 item 6): pin how many pairs actually ran per sheet/theme —
      // grow these numbers when PAIRS grows, never let them silently shrink
      const expected = sheet === "styles.css" ? PAIRS.length : 20;   // feed's :root holds a deliberate subset (+ the retrying pair, 2026-09-08)
      // T337: the postal kind words also sit on the PROVISIONAL card (a sent card not yet landed wears the pending
      // bubble's dress: an 8.5% wash of --you over the page, styles.css .queued-bubble, no element opacity since the
      // fade moved into the dress's colours), the darkest ground they meet; each reads at 4.5:1 there too
      if (sheet === "styles.css") {
        const you = rgbOf(theme.get("--you")!, page)!;
        const wash = [0, 1, 2].map((i) => Math.round(you[i] * 0.085 + page[i] * 0.915)) as [number, number, number];
        for (const tok of ["--postal-coordinate", "--postal-delegate", "--postal-question"]) {
          const fore = rgbOf(theme.get(tok)!, wash)!;
          assert.ok(contrast(fore, wash) >= 4.5, `${sheet} ${name}: ${tok} on the provisional wash = ${contrast(fore, wash).toFixed(2)} < 4.5`);
        }
      }
      // T337c: the INCOMING card no longer wears --box-bg but the peer's hue at the ground's lightness (styles.css: an oklch
      // relative colour from the rail, the tokens --postal-wash-l and --postal-wash-c), a different ground for every peer;
      // each kind word reads at 4.5:1 there for EVERY hue (the sent boxed card still wears --box-bg: those pairs stand)
      if (sheet === "styles.css") {
        const L = parseFloat(theme.get("--postal-wash-l")!), C = parseFloat(theme.get("--postal-wash-c")!);
        assert.ok(L > 0 && L < 1 && C > 0, `${sheet} ${name}: the wash tokens parse (${L}, ${C})`);
        for (const tok of ["--postal-coordinate", "--postal-delegate", "--postal-question"]) {
          let worst = Infinity, worstHue = -1;
          for (let h = 0; h < 360; h++) {
            const ground = oklchToRgb(L, C, h);
            const fore = rgbOf(theme.get(tok)!, ground)!;
            const c = contrast(fore, ground);
            if (c < worst) { worst = c; worstHue = h; }
          }
          assert.ok(worst >= 4.5, `${sheet} ${name}: ${tok} on the tinted ground = ${worst.toFixed(2)} at hue ${worstHue} < 4.5`);
        }
      }
      assert.ok(evaluated >= expected,
        `${sheet} ${name}: only ${evaluated}/${expected} contrast pairs evaluated — silent skip`);
    }
  });
}
