// Effort-switch UX (the user 2026-07-06): switching an /effort level reconnects the SDK session to apply it
// (--effort is a connect-time flag). Two cues, mirroring the model badge's switching-dots: (1) the effort
// badge shows the pulsing accent dots while the reconnect is pending (st.effortPending), and (2) an animated
// "Reloading session…" element (kind:"reconnecting") renders in the chat flow until the new client connects.
// render.ts has no jsdom harness → source pins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the effort badge shows switching-dots while a reconnect is pending, like the model badge", () => {
  assert.match(RENDER, /effortPending\?: boolean;/);   // status carries it
  assert.match(RENDER, /\(kind === "effort" && !!st\.effortPending\)/);          // effort feeds `pending`
  assert.match(RENDER, /const showDots = pending && \(kind === "model" \|\| kind === "effort"\);/);   // dots for both reconnect-style badges (billing moved to the tab menu, 2026-08-09)
});

test("a live reconnect has its own ChatEvent kind, dispatched to renderReconnecting", () => {
  assert.match(RENDER, /kind: "reconnecting"; effort\?: string;/);
  assert.match(RENDER, /ev\.kind === "reconnecting"\) return renderReconnecting\(ev\)/);
  // checked BEFORE the compact-boundary case (a live signal, not a boundary)
  const recon = RENDER.indexOf('ev.kind === "reconnecting") return renderReconnecting(ev)');
  const compact = RENDER.indexOf('ev.kind === "compact") return renderCompact(ev)');
  assert.ok(recon > 0 && compact > 0 && recon < compact);
});

test("renderReconnecting is a slim live SESSION notice: the accent loader dots in its glyph slot, naming the effort", () => {
  // 2026-09-08 (the notice-vocabulary pass): the ONE builder, romp severity (accent) for an in-flight romp operation
  assert.match(RENDER, /function renderReconnecting\(ev: Extract<ChatEvent, \{ kind: "reconnecting" \}>\)/);
  assert.match(RENDER, /notice\(\{ src: "session", glyph: noticeLiveGlyph\(metaDots\(\)\), sev: "romp", gist: reconnectingGist\(ev\.effort\), live: true,/);
  assert.match(RENDER, /Reloading session — applying \$\{effort\} effort…/);
  assert.match(RENDER, /"Reloading session…"/);                // effort-less fallback
  assert.match(CSS, /\.notice-sev-romp\s+\{ --notice-rail: var\(--accent\)/);   // accent (loading), not a status color
});
