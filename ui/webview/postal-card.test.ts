// T302 (the user 2026-09-10, after seeing old and new postal cards side by side): the kind is coloured TEXT in the
// old chip colours, the delivery state is an icon at the head's right edge (sent / delivered / read / parked /
// bounced / recalled, from what the kernel files), both ENDS wear their sessions' colours (the peer's chip, then
// this session's own), no card wears a background wash, and a sent card whose message has not landed wears the
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

test("the kind is coloured text in the meta slot, never a chip, in the old chip colours", () => {
  assert.match(RENDER, /import \{ kindLabel, deliveryOf, deliveryTitle[^}]*\} from "\.\/postal-state";/);
  assert.match(CARD, /const kind = kindLabel\(intent \? intent\.cls : null\);/);
  assert.match(CARD, /meta = el\("span", "postal-kind postal-kind-" \+ intent\.cls\); meta\.textContent = kind;/);
  assert.doesNotMatch(CARD, /postal-service-intent|notice-chip/, "no chip");
  // the two raw colours are TOKENS with light-theme values (2.2:1 and 2.1:1 on the cream page otherwise; review):
  // 5.4:1 and 5.3:1 there, 6.4:1 and 6.7:1 on the dark page (WCAG text contrast 4.5:1)
  assert.match(CSS, /\.postal-kind-delegate \{ color: var\(--postal-delegate, #b08cff\); \}/);
  assert.match(CSS, /\.postal-kind-coordinate \{ color: var\(--postal-coordinate, #14b8a6\); \}/);
  assert.match(CSS, /\.postal-kind-question \{ color: var\(--postal-question, var\(--st-working-bg\)\); \}/);
  assert.match(CSS, /\n  --postal-delegate: #b08cff;\s+--postal-coordinate: #14b8a6;\s+--postal-question: var\(--st-working-bg\);/, "the dark tokens (the old chip colours)");
  const light = CSS.slice(CSS.indexOf("body.theme-light {"), CSS.indexOf("\n}\n", CSS.indexOf("body.theme-light {")));
  assert.match(light, /--postal-delegate: #6e3fd0;\s+--postal-coordinate: #0d6b64;\s+--postal-question: #7d5600;/, "the light tokens re-ink the three kinds (the working amber alone measured 4.26:1 on cream)");
  // under the narrow container query the GIST yields, never the kind word (the ruling: the kind never truncates)
  assert.match(CSS, /@container \(max-width: 360px\) \{\n  \.turn-postal-service \.notice-gist \{ min-width: min\(100%, 4ch\); \}\n\}/);
  assert.doesNotMatch(CSS, /@container \(max-width: 360px\) \{\n  \.turn-postal-service \.notice-meta/, "the meta no longer shrinks under it");
  // never truncated: the postal meta stays rigid
  assert.match(CSS, /\.turn-postal-service \.notice-meta \{ flex: 0 0 auto; \}/);
});

test("the delivery state is one icon per state at the head's right edge, each with a worded title", () => {
  for (const st of ["sent", "delivered", "read", "parked", "bounced", "recalled"]) {
    assert.match(RENDER, new RegExp("^  " + st + ": '<", "m"), st + " has a glyph");
  }
  assert.match(RENDER, /const DELIVERY_GLYPHS: Record<PostalDeliveryState, string>/);
  assert.match(fn("deliveryIcon"), /span\.dataset\.state = d\.state;/);
  assert.match(fn("deliveryIcon"), /span\.setAttribute\("role", "img"\);/, "a labelled span is announced only with an image role");
  assert.match(fn("deliveryIcon"), /const title = deliveryTitle\(d, clockOf\);[^\n]*\n\s*setTip\(span, title\);[^\n]*\n\s*span\.setAttribute\("role", "img"\);[^\n]*\n\s*span\.setAttribute\("aria-label", title\);/);
  assert.match(CARD, /const delivery = deliveryOf\(ev\);/);
  assert.match(CARD, /turn\.querySelector\("\.notice-head"\)\?\.appendChild\(deliveryIcon\(delivery\)\);/);
  assert.match(CSS, /\.postal-delivery \{ flex: 0 0 auto; margin-left: auto;/, "the right edge");
  // on a fold-less card whose one line wraps, the icon sits on the FIRST line like the glyph, not centred over the block
  assert.match(CSS, /\.turn-postal-service \.notice:not\(\.notice-collapsible\) \.postal-delivery \{ align-self: flex-start; margin-top: 4px; \}/);
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

test("no card wears a background wash in a session's colour; incoming keeps border + rail, sent stays slim", () => {
  assert.doesNotMatch(CSS, /\.notice-peer > \.notice:not\(\.notice-slim\)/, "the 6% peer wash is gone");
  assert.doesNotMatch(CSS, /color-mix\(in srgb, var\(--notice-rail, transparent\) 6%/);
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
  // the pending bubble's rule reaches the notice by a selector on the SAME declaration block — no lookalike copy
  assert.match(CSS, /\.queued-bubble, \.notice\.notice-slim\.queued-bubble \{/);
  assert.match(CSS, /\.notice\.notice-slim\.queued-bubble \{ max-width: none; display: block; \}/, "the row keeps its width (same specificity as the shared rule, later)");
  // the bubble's border + padding move the head line down: the rail dot follows, as it does for a boxed card
  assert.match(CARD, /turn\.classList\.add\("postal-provisional"\)/);
  assert.match(CSS, /\.turn-postal-service\.postal-provisional > \.dot, \.turn-postal-service\.postal-provisional > \.time-marker \{ top: 18px; \}/);
  assert.equal((CSS.match(/border: 1px dashed color-mix\(in srgb, var\(--you\) 65%, transparent\)/g) || []).length, 1,
               "one dashed --you border rule in the file: the bubble's");
});
