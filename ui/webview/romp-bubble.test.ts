// The gray "romp-injected" bubble (the user 2026-06-19): a message romp pasted into the pane (a feed
// nudge / follow-up) renders as a GRAY right-aligned bubble with a "↯ romp" tag — same spot as the blue
// user bubble, but clearly romp, not you. The renderer has no jsdom harness, so pin the wiring at source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("a user ChatEvent can carry a romp flag", () => {
  assert.match(RENDER, /kind: "user";[^}]*romp\?: boolean/);
});

test("a romp event renders the gray romp-bubble + a romp tag, NOT the blue or the note box", () => {
  assert.match(RENDER, /const romp = kind === "romp";/, "derived from the ONE senderKind verdict (2026-08-18)");
  // 'injected' (the neutral left note) excludes romp, so romp gets its own branch
  assert.match(RENDER, /const injected = kind === "injected";/, "same verdict — the predicate itself lives in sender-identity.ts");
  // the tag shows the romp swirl-glyph LOGO (not the old ↯ symbol) + "romp" (the user 2026-06-19)
  assert.match(RENDER, /el\("img", "romp-tag-logo"\)/);
  assert.match(RENDER, /logo\.src = mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.match(RENDER, /createTextNode\("romp"\)/);
  assert.doesNotMatch(RENDER, /tag\.textContent = "↯ romp"/, "the ↯ placeholder is gone");
  // 2026-09-08 (the notice-vocabulary pass): a harness-injected line is a SYSTEM notice (its content div is the
  // notice body's markdown class); the .user-note box is retired
  assert.match(RENDER, /\(romp \? "romp-bubble" : tagged \? "romp-bubble tag-bubble" : injected \? "notice-md" : "user-bubble"\)/);
  // its own gray rail dot
  assert.match(RENDER, /dot\(romp \? "romp" : tagged \? "tag" : injected \? "ring" : "user"\)/);
  assert.match(RENDER, /"green" \| "ring" \| "user" \| "red" \| "romp"/, "the dot helper knows the romp variant");
});

test("the swirl LOGO is on EVERY romp bubble, next to the 'romp' tag (the user 2026-07-05; supersedes the 2026-06-23 auto-nudge-only gating)", () => {
  assert.match(RENDER, /kind: "user";[^}]*rompAuto\?: boolean/);
  // the <img> logo appends UNCONDITIONALLY inside the romp branch (no `if (ev.rompAuto)` gate), immediately
  // before the "romp" textnode — so any romp-tagged message (system notice, auto-nudge, or Nudge click) shows it
  assert.doesNotMatch(RENDER, /if \(ev\.rompAuto\) \{[\s\S]*?el\("img", "romp-tag-logo"\)/);
  assert.match(RENDER, /const tag = el\("div", "romp-tag"\);\s*const logo = el\("img", "romp-tag-logo"\)[\s\S]*?tag\.appendChild\(logo\);\s*tag\.appendChild\(document\.createTextNode\("romp"\)\)/);
});

test("a postal notice wears the peer's envelope glyph; the swirl is romp's OWN source glyph (2026-09-08)", () => {
  // the notice-vocabulary pass: one glyph per SOURCE — a peer's mail is from the peer (envelope + its session chip),
  // a romp notice is from romp (the swirl, the one non-stroke glyph)
  assert.match(RENDER, /notice\(\{ src, glyph: "peer", gist: summaryText, meta, body, open: owed,/);   // T302: the meta is the kind's coloured text element
  assert.match(RENDER, /if \(kind === "romp"\) \{\s*\n\s*const logo = el\("img"\) as HTMLImageElement;\s*\n\s*logo\.src = mediaSrc\("romp-swirl-glyph\.svg"\)/);
  assert.doesNotMatch(CSS, /\.postal-service-romp-logo/);
});

test("the romp bubble is a gray, right-aligned bubble (inherits the non-injected right-align)", () => {
  // the turn carries 'romp' (no 'injected'), so .turn-user:not(.injected) right-aligns it
  assert.match(RENDER, /"turn turn-user" \+ \(romp \? " romp" : injected \? " injected" : ""\)/);
  // 2026-09-08: on the shared overlay tokens — the white-alpha wash vanished on the light theme's cream
  assert.match(CSS, /\.romp-bubble \{[\s\S]*?background: var\(--overlay-05\); border: 1px solid var\(--overlay-10\);/);
  assert.match(CSS, /\.romp-tag \{/);
  // its rail dot is the swirl in a dark disc since 2026-07-23, matching the timeline's romp glyph —
  // the bubble stays gray, but the dot is no longer anonymous. Pinned in rail-line-hover.test.ts.
  assert.match(CSS, /\.dot\.romp \{ background: var\(--bg\); border: 1px solid var\(--fg\); \}/);   // tokenised 2026-09-08
});
