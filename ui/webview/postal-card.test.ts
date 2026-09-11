// T302 (the user 2026-09-10, after seeing old and new postal cards side by side): the kind is coloured TEXT in the
// old chip colours, the delivery state is an icon at the head's right edge (sent / delivered / read / parked /
// bounced / recalled, from what the kernel files), both ENDS wear their sessions' colours (the peer's chip, then
// this session's own), the incoming card wears the peer's hue as a tint on its own ground (back since 2026-09-11), and a sent card whose message has not landed wears the
// pending send's own provisional dress (the queued bubble's class and rule). Source pins (render.ts has
// import-time DOM side effects); the pure module is executed in postal-state.test.ts.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");
function fn(name: string): string {
  const i = RENDER.indexOf("function " + name + "(");
  return RENDER.slice(i, RENDER.indexOf("\n}\n", i));
}
const CARD = fn("renderPostalService");

test("the kind is coloured text in the meta slot, never a chip, at prose weight, on one ranked ramp", () => {
  assert.match(RENDER, /import \{ kindLabel, deliveryOf, deliveryTitle[^}]*\} from "\.\/postal-state";/);
  assert.match(CARD, /const kind = kindLabel\(intent \? intent\.cls : null\);/);
  assert.match(CARD, /meta = el\("span", "postal-kind postal-kind-" \+ intent\.cls\); meta\.textContent = kind;/);
  assert.doesNotMatch(CARD, /postal-service-intent|notice-chip/, "no chip");
  // T320 (the user 2026-09-10): the word is NOT bold (the prose weight), and the three colours are TOKENS on ONE
  // sequential ramp in the accent's hue, ranked coordination < delegation < question by how much each asks of the
  // reader, each with a light-theme re-ink (the parity test holds every one at 4.5:1 on its page)
  assert.match(CSS, /\.postal-kind \{ font-weight: 400; \}/, "prose weight, not bold");
  assert.doesNotMatch(CSS, /\.postal-kind \{ font-weight: (600|700|bold)/);
  // (T337: the three are re-sampled evenly along the line; postal-kind-ramp.test.ts holds the positions, these the values)
  assert.match(CSS, /\.postal-kind-delegate \{ color: var\(--postal-delegate, #7cb5e3\); \}/);
  assert.match(CSS, /\.postal-kind-coordinate \{ color: var\(--postal-coordinate, #5696c8\); \}/);
  assert.match(CSS, /\.postal-kind-question \{ color: var\(--postal-question, #a2d4fe\); \}/);
  assert.match(CSS, /\n  --postal-coordinate: #5696c8;\s+--postal-delegate: #7cb5e3;\s+--postal-question: #a2d4fe;/, "the dark ramp, low to high");
  const light = CSS.slice(CSS.indexOf("body.theme-light {"), CSS.indexOf("\n}\n", CSS.indexOf("body.theme-light {")));
  assert.match(light, /--postal-coordinate: #974a32;\s+--postal-delegate: #752f18;\s+--postal-question: #551400;/, "the light ramp, low to high, deepening");
  // the ramp IS a ramp: in each theme the three steps are monotone in luminance in rank order (brighter with rank on
  // the dark page, darker with rank on the light one), so the eye reads one scale, not three tags
  const lumOf = (hex: string) => { const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  const dark = ["#5696c8", "#7cb5e3", "#a2d4fe"].map(lumOf), lightRamp = ["#974a32", "#752f18", "#551400"].map(lumOf);
  assert.ok(dark[0] < dark[1] && dark[1] < dark[2], "dark: coordination < delegation < question in luminance");
  assert.ok(lightRamp[0] > lightRamp[1] && lightRamp[1] > lightRamp[2], "light: coordination > delegation > question in luminance");
  assert.match(CSS, /coordination lowest \(an FYI\), delegation\s+next \(work handed over\), question highest \(an answer owed\)/, "the ranking sits beside the tokens");
  // under the narrow container query the head WRAPS: the gist takes its own full-width line, word-wise, and the kind
  // word keeps the first line whole and inside the card (T313; the 4ch floor that squeezed the gist into a letter
  // column beside the ends is gone)
  assert.match(CSS, /@container \(max-width: 360px\) \{\n  \.turn-postal-service \.notice-head \{ flex-wrap: wrap; \}\n  \.turn-postal-service \.notice-head > \.notice-gist \{ flex: 1 0 100%; order: 1; min-width: 0;\n    white-space: normal; overflow: visible; text-overflow: clip; overflow-wrap: break-word; \}\n\}/);
  assert.doesNotMatch(CSS, /min-width: min\(100%, 4ch\)/, "no character-wide gist column anywhere");
  assert.doesNotMatch(CSS, /@container \(max-width: 360px\) \{\n  \.turn-postal-service \.notice-meta/, "the meta no longer shrinks under it");
  // never truncated: the postal meta stays rigid
  assert.match(CSS, /\.turn-postal-service \.notice-meta \{ flex: 1 0 auto; display: inline-flex; align-items: baseline; gap: 7px; min-width: 0; \}/,
    "the postal meta never shrinks (the kind word stays whole) and grows to the head's edge to carry the icon (T313)");
});

test("the incoming card wears the peer's hue at its ground's own lightness, in both themes; the sent card does not", () => {
  // the user 2026-09-11, who missed the tint T302 had removed the day before on their own side-by-side ruling. A hue at
  // the ground's lightness, not a mix: a mix lifts the ground and the dimmest kind word fell under the ramp's 4.5:1 floor
  assert.match(CSS, /\.turn-postal-service\.postal-service-in \.notice:not\(\.notice-slim\) \{\n  background: oklch\(from var\(--notice-rail, var\(--box-bg\)\) var\(--postal-wash-l, 0\.263\) var\(--postal-wash-c, 0\.03\) h\); \}/,
               "one declaration: the rail's hue (the peer's colour) at the ground's lightness and a gentle chroma, each token with a fallback");
  assert.match(CSS, /\n  --postal-wash-l: 0\.263;  --postal-wash-c: 0\.03;/, "the dark ground's lightness and the chroma, beside the kind tokens");
  assert.match(CSS, /\n  --postal-wash-l: 0\.919;  --postal-wash-c: 0\.045;/, "the light ground's lightness and a stronger chroma for the cream's warm hue, in the light block");
  assert.doesNotMatch(CSS, /theme-light[^{}]*postal-service-in[^{}]*\{[^}]*background/, "no theme takes it back");
  assert.doesNotMatch(CSS, /postal-service-out[^{}]*\{[^}]*oklch\(from var\(--notice-rail/, "the sent card is untinted");
  assert.match(fn("renderPostalService"), /rail: ev\.color \? ev\.color\.bg : undefined/, "the rail is the peer's colour, so the hue is the peer's");
});

const STATE = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "postal-state.ts"), "utf8");

test("the delivery state is one icon per state at the head's right edge, each with a worded title", () => {
  // the glyph map lives in the pure module (postal-state.test.ts executes it: the T337 ladder); the renderer imports it
  for (const st of ["sent", "delivered", "read", "parked", "bounced", "recalled"]) {
    assert.match(STATE, new RegExp("^  " + st + ": '<", "m"), st + " has a glyph");
  }
  assert.match(STATE, /export const DELIVERY_GLYPHS: Record<PostalDeliveryState, string>/);
  assert.doesNotMatch(RENDER, /const DELIVERY_GLYPHS/, "one map, in the module");
  assert.match(RENDER, /import \{ kindLabel, deliveryOf, deliveryTitle, DELIVERY_GLYPHS, type PostalDelivery, type PostalReceipt \} from "\.\/postal-state";/);
  assert.doesNotMatch(RENDER, /type PostalDeliveryState/, "the state type left the renderer with the map");
  // the mark's wrapper (T337, the user 2026-09-10 wanting the circled check): a 16-unit box drawn at 14 px, a 1.5 stroke,
  // round caps and joins, no fill unless a rung says so; the read rung's check is knocked out in the page colour by the
  // sheet (a presentation attribute cannot carry a var())
  assert.match(fn("deliveryIcon"), /'<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" '\s*\+ 'stroke-width="1\.5" stroke-linecap="round" stroke-linejoin="round">' \+ DELIVERY_GLYPHS\[d\.state\] \+ "<\/svg>"/);
  assert.match(CSS, /\.postal-delivery-read \.postal-mark-check \{ stroke: var\(--bg\); \}/, "the read rung's check is cut out of the filled circle");
  assert.match(STATE, /export const MARK_CHECK_CLASS = "postal-mark-check";/);
  assert.match(fn("deliveryIcon"), /span\.dataset\.state = d\.state;/);
  assert.match(fn("deliveryIcon"), /span\.setAttribute\("role", "img"\);/, "a labelled span is announced only with an image role");
  assert.match(fn("deliveryIcon"), /const title = deliveryTitle\(d, clockOf\);[^\n]*\n\s*setTip\(span, title\);[^\n]*\n\s*span\.setAttribute\("role", "img"\);[^\n]*\n\s*span\.setAttribute\("aria-label", title\);/);
  assert.match(CARD, /const delivery = deliveryOf\(ev\);/);
  assert.match(CARD, /turn\.querySelector\("\.notice-meta"\)\?\.appendChild\(deliveryIcon\(delivery\)\);/);
  assert.match(CSS, /\.postal-delivery \{ flex: 0 0 auto; margin-left: auto;/, "the right edge");
  // on a fold-less card whose one line wraps, the icon sits on the FIRST line like the glyph, not centred over the block
  // the icon rides inside the META slot (T313): a one-line flex box the head aligns by baseline, so it sits on the kind
  // word's line, the first, however the gist wraps — and on a phone-width head the two wrap as one unit
  assert.match(RENDER, /turn\.querySelector\("\.notice-meta"\)\?\.appendChild\(deliveryIcon\(delivery\)\);/);
  // …and the meta always exists when there is an icon: a kind-less legacy card (no declared kind, no leading token) with
  // a delivery state gets an EMPTY meta slot, so no icon is ever appended straight to the head (T313 review find)
  assert.match(RENDER, /if \(!meta && delivery\) meta = el\("span", "postal-meta-empty"\);/);
  assert.doesNotMatch(RENDER, /notice-head"\)\?\.appendChild\(deliveryIcon/, "no second path for the icon");
  assert.doesNotMatch(CSS, /\.postal-delivery \{ align-self: flex-start; margin-top: 4px; \}/, "no first-line nudge left: the meta's line IS the first line");
  assert.match(CSS, /\.postal-delivery-read \{ color: var\(--accent\); \}/);
  assert.match(CSS, /\.postal-delivery-parked \{ color: var\(--warn\); \}/);
  assert.match(CSS, /\.postal-delivery-bounced \{ color: var\(--st-blocked-bg\); \}/);
  // the words "delivered" / "parked" left the meta text: no meta words are pushed any more
  assert.doesNotMatch(CARD, /meta\.push\(/);
  // the event carries the ledger's receipt
  assert.match(RENDER, /receipt\?: PostalReceipt;/);
});

test("both ends wear their sessions' colours: the peer's chip, then this session's own; a narrow head keeps its dot", () => {
  assert.match(CARD, /const src = el\("span", "notice-src-ends"\);/);
  // the own end is the session that OWNS the transcript being built (a comment popover's parent, a subagent
  // viewer's session), the same chain every other owner lookup in the file uses (review, 2026-09-10)
  assert.match(CARD, /const ownId = renderingOwnerSid \?\? renderingSid \?\? activeId;/);
  assert.match(CARD, /const own = ownId && sessions\.has\(ownId\) \? sessions\.get\(ownId\) : undefined;/);
  // the working dot is the peer chip's; the own chip never gains one from the next working frame (no flap)
  assert.match(fn("refreshPostalDots"), /querySelectorAll\("\.notice-src-chip:not\(\.notice-src-self\)"\)/);
  assert.match(CARD, /ev\.direction === "in" \? "from " : "to "/);
  assert.match(CARD, /ev\.direction === "in" \? " to " : " from "/);
  assert.match(CARD, /const self = el\("span", "notice-src-chip notice-src-self"\);/);
  assert.match(CARD, /nm\.append\(\.\.\.hostNameNodes\(own\.name, ownId\)\);/, "the host label muted, as the tab wears it");
  assert.match(CARD, /self\.style\.setProperty\("--peer-bg", own\.color\.bg\); self\.style\.setProperty\("--peer-fg", own\.color\.fg\);/);
  assert.match(CSS, /@container \(max-width: 520px\) \{\n  \.turn-postal-service \.notice-src-self \{ width: 10px; height: 10px; padding: 0; border-radius: 50%;/);
  assert.match(CSS, /\.turn-postal-service \.notice-src-self \.notice-src-name \{ display: none; \}/);
});

test("the old 6% mix and its hook stay gone; incoming keeps border + rail, sent stays slim", () => {
  assert.doesNotMatch(CSS, /\.notice-peer > \.notice:not\(\.notice-slim\)/, "the retired hook's rule stays gone");
  assert.doesNotMatch(CSS, /color-mix\(in srgb, var\(--notice-rail, transparent\) 6%/, "the mix that lifted the ground stays gone: the tint is a hue at the ground's lightness (below)");
  assert.match(CARD, /rail: ev\.color \? ev\.color\.bg : undefined,/, "the rail still names the peer");
  // the shared notice rule (a body → a card, head-only → slim) stands, and the postal card overrides it for ONE
  // direction: incoming keeps its box even with nothing to fold; sent stays slim
  assert.match(fn("notice"), /const boxed = collapsible \|\| acts\.length > 0;/);
  assert.match(CARD, /if \(ev\.direction === "in"\) \{ turn\.classList\.add\("notice-boxed"\); turn\.querySelector\("\.notice"\)\?\.classList\.remove\("notice-slim"\); \}/);
  // …and a boxed card with nothing to fold still WRAPS its one line (T294's rule, now keyed on the fold, not on
  // slimness): an ellipsis with nothing behind it to click is the dead end the rule forbids (review, 2026-09-10)
  assert.match(CSS, /\.turn-postal-service \.notice:not\(\.notice-collapsible\) \.notice-gist \{ white-space: normal; overflow: visible; text-overflow: clip; overflow-wrap: anywhere; \}/);
  assert.match(CSS, /\.turn-postal-service \.notice:not\(\.notice-collapsible\) \.notice-glyph \{ align-self: flex-start; margin-top: 4px; \}/);
  assert.doesNotMatch(CARD, /notice-peer/, "the wash hook is gone with the wash");
});

test("a sent card that has not landed wears the pending send's own provisional dress: the same class and rule", () => {
  assert.match(CARD, /if \(ev\.direction === "out" && \(delivery\.state === "sent" \|\| delivery\.state === "parked"\)\) \{\s*\n\s*turn\.querySelector\("\.notice"\)\?\.classList\.add\("queued-bubble"\);/);
  // the pending bubble's rule reaches the notice by selectors on the SAME declaration block, no lookalike copy: the
  // notice of EITHER density, since a sent card boxed by its fold is a .notice with no .notice-slim, and `.notice` alone
  // (0,1,0) later in the file used to win that card's border, background and radius back from `.queued-bubble` (0,1,0);
  // `.notice.queued-bubble` (0,2,0) outranks it. The slim selector (0,3,0) stays: `.notice.notice-slim` (0,2,0) is
  // later in the file too and would otherwise take the slim card's dress back from `.notice.queued-bubble`.
  assert.match(CSS, /\.queued-bubble, \.notice\.queued-bubble, \.notice\.notice-slim\.queued-bubble \{/);
  // the width reset reaches both densities the same way: the boxed card keeps the column too, so it never snaps from
  // the bubble's 72% to the full width when the receipt lands
  assert.match(CSS, /\.notice\.queued-bubble, \.notice\.notice-slim\.queued-bubble \{ max-width: none; display: block; \}/, "the row keeps its width at either density (the same specificities as the shared rule, later)");
  // the bubble's border + padding move the head line down: the rail dot follows, as it does for a boxed card
  assert.match(CARD, /turn\.classList\.add\("postal-provisional"\)/);
  assert.match(CSS, /\.turn-postal-service\.postal-provisional > \.dot, \.turn-postal-service\.postal-provisional > \.time-marker \{ top: 18px; \}/);
  assert.equal((CSS.match(/border: 1px dashed color-mix\(in srgb, var\(--you\) 55%, transparent\)/g) || []).length, 1,
               "one dashed --you border rule in the file: the bubble's");
  // T337 (the review of the kind colours): the dress FADES BY ITS COLOURS, not by an element opacity that dimmed every
  // colour inside (the kind word read below 4.5:1 on the wash): the old 10% / 65% / 0.85 are folded into 8.5% / 55% /
  // an 85% --fg ink, the ink is a custom property the notice's gist and body read (their own rules set --fg back), and
  // the head's own colours stay whole
  const bubble = CSS.slice(CSS.indexOf(".queued-bubble, .notice.queued-bubble, .notice.notice-slim.queued-bubble {"), CSS.indexOf("\n}\n", CSS.indexOf(".queued-bubble, .notice.queued-bubble, .notice.notice-slim.queued-bubble {")));
  assert.doesNotMatch(bubble, /opacity:/, "no element opacity on the provisional dress");
  assert.match(bubble, /--prov-ink: color-mix\(in srgb, var\(--fg\) 85%, transparent\);/);
  assert.match(bubble, /background: color-mix\(in srgb, var\(--you\) 8\.5%, transparent\);/);
  assert.match(bubble, /color: var\(--prov-ink\);/);
  assert.match(CSS, /\.notice\.queued-bubble \.notice-gist, \.notice\.queued-bubble \.notice-body \{ color: var\(--prov-ink\); \}/, "the words fade with the dress");
  assert.doesNotMatch(CSS, /queued-bubble[^\n]*\.postal-kind/, "nothing re-colours the kind word on the provisional card: the token reads there as it is");
  // the old opacity lifts are ink lifts now, and no queued-bubble rule carries an opacity at all: the hovered cancelable
  // bubble and the bubble being edited bring the words to full ink, and a notice romp itself queued (T243) keeps its
  // landed card's full ink under the wrapper
  assert.match(CSS, /\.queued-bubble\.cancelable:hover \{ --prov-ink: var\(--fg\); border-color: var\(--accent\); \}/);
  assert.match(CSS, /\.queued-bubble\.editing \{ --prov-ink: var\(--fg\);/);
  assert.match(CSS, /\.queued-bubble\.queued-romp \{ background: transparent; border: 0; padding: 0; --prov-ink: var\(--fg\); \}/);
  assert.doesNotMatch(CSS, /\.queued-bubble[^\n{]*\{[^}]*\bopacity:/, "no queued-bubble rule sets an opacity: the fade and its lifts are the ink");
  assert.match(CSS, /\.queued-bubble\.cancelable \{ position: relative; padding-right: 30px; transition: color \.1s, border-color \.1s, background \.1s; \}/, "the transition names what changes");
});
