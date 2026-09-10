// Sticky rail stamp (the user 2026-07-22). restampMarkers can only stamp at a turn boundary, so a single
// message taller than the viewport leaves the rail blank while you scroll through it. paintRailSticky keeps a
// stamp in the top slot at a fixed buffer line (cTop + 6). A real marker LEADS while its top is at or below
// that line; the instant it crosses ABOVE the line the sticky takes the slot — same position, same time — and
// the crossed marker is hidden. So the hand-off is seamless: no gap where the slot goes empty, and no clipped
// duplicate sliding past (the user 2026-07-23). Exactly one stamp sits at the line at all times.
//
// The chat renderer has no jsdom harness, so — like the other render.ts tests — pin at the source, plus an
// executed replica of the pure selection + hand-off decision (the property most worth protecting).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("the sticky stamp is a fixed overlay on <body>, not a child of the swapped #content", () => {
  const fn = SRC.slice(SRC.indexOf("function ensureRailSticky"), SRC.indexOf("function paintRailSticky"));
  assert.match(fn, /el\("div", "time-marker rail-sticky"\)/, "carries the marker's type styling plus its own");
  assert.match(fn, /document\.body\.appendChild\(railSticky\)/, "lives on body so a #content rebuild can't destroy it");
  assert.match(fn, /railSticky\.isConnected/, "re-creates itself if it ever gets detached");
});

test("the hand-off happens at the buffer line, with the crossed marker hidden", () => {
  const end = SRC.indexOf("// Scroll is the sticky stamp's primary driver");
  assert.ok(end > 0, "the slice end anchor still exists (else every assertion below matches the whole file)");
  const fn = SRC.slice(SRC.indexOf("function paintRailSticky"), end);
  // the buffer line doubles as the sticky's rest position AND the hand-off threshold. When a day
  // label shows (old-day history), both drop by the label's height — the slot line — so the label
  // riding above the stamp stays inside the pane; with no label, slotLine === line and nothing
  // moves (see rail-day.test.ts).
  assert.match(fn, /const BUFFER = 6;/);
  assert.match(fn, /const line = cTop \+ BUFFER;/);
  assert.match(fn, /const slotLine = line \+ \(label \? dayH \+ 1 : 0\);/);
  // the TRACKED turn's own stamp leads while it is at or below the line; the sticky takes over once it
  // crosses. Keyed on the tracked marker, NOT on any stamp anywhere — a later time change further down the
  // view must not blank the top slot, which would reintroduce the empty gap this exists to prevent.
  assert.match(fn, /marker = m; markerTop = r\.top; markerShown = !!m\.textContent;/);
  assert.match(fn, /const realLeads = markerShown && markerTop >= slotLine;/);
  assert.match(fn, /if \(!hm \|\| realLeads\) \{/);
  // when the sticky leads, every marker that crossed ABOVE the slot line — or INTO the sticky's own
  // box — is hidden, so no clipped duplicate shows and no incoming stamp superimposes the sticky
  assert.match(fn, /for \(const \[m, top\] of all\) m\.style\.visibility = top < slotLine \+ g\.height \? "hidden" : "";/);
  // and it rests exactly at the slot line
  assert.match(fn, /stamp\.style\.top = slotLine \+ "px";/);
  // ...while a real stamp leading means nothing is suppressed
  assert.match(fn, /for \(const \[m\] of all\) m\.style\.visibility = "";/);
});

test("scroll drives it (passive, rAF-coalesced) and a resize re-measures it", () => {
  assert.match(SRC, /function scheduleRailSticky\(\): void \{[^}]*railStickyPending[^}]*requestAnimationFrame/s);
  // passive: it measures, never blocks the scroll it annotates; CAPTURE (T310): a scroll inside the pane — a notice body
  // at its max height, a wide formula — does not bubble, and the unread comment boxes on this scheduler must follow it
  assert.match(SRC, /addEventListener\("scroll", scheduleRailSticky, \{ passive: true, capture: true \}\)/,
    "passive: it measures, never blocks the scroll it annotates; capture: inner scrolls reach it too");
  assert.match(SRC, /window\.addEventListener\("resize", scheduleRailSticky\)/);
  // one scheduler, not two: with the spacing pass gone there is nothing to re-run on render but the sticky
  assert.doesNotMatch(SRC, /scheduleRestamp/, "the old restamp scheduler is gone");
  assert.doesNotMatch(SRC, /restampMarkers/, "and so is the spacing pass it drove");
  assert.doesNotMatch(SRC, /chooseStamps/, "render.ts no longer imports or calls it");
});

test("the CSS pins it to the viewport, above the rail, click-through", () => {
  assert.match(CSS, /\.rail-sticky \{[^}]*position: fixed/s);
  assert.match(CSS, /\.rail-sticky \{[^}]*pointer-events: none/s, "a passive annotation never eats a click behind it");
});

// ── executed replica of the selection + hand-off decision ────────────────────────────────────────────────
// Faithful to paintRailSticky in the label-free case (today's history: slotLine === line; the day-label
// shift is pinned at the source above and in rail-day.test.ts): line = cTop + BUFFER; marker = the last turn whose top <= line (its time is
// what sits at the top), tracked along with WHERE its own marker is and whether that marker is stamped. That
// tracked marker leads while it is at or below the line; otherwise the sticky leads at the line and every
// marker with mTop < line + STAMP_H is hidden — the sticky's bottom edge, so a stamp can never superimpose
// it. A turn models {top} and its marker {id, hm, text, mTop, mBottom}.
const BUFFER = 6;
const STAMP_H = 13;   // the modeled marker box height (mBottom - mTop below)
type Marker = { id: string; hm: string; text: string; mTop: number; mBottom: number };
type Turn = { top: number; marker: Marker | null };
function decideSticky(turns: Turn[], cTop: number, _cBottom: number): { show: boolean; hm: string; hidden: string[] } {
  const line = cTop + BUFFER;
  let marker: Marker | null = null;
  let markerTop = 0, markerShown = false;
  const all: Marker[] = [];
  for (const t of turns) {
    const m = t.marker;
    if (!m) continue;
    all.push(m);
    if (t.top <= line) { marker = m; markerTop = m.mTop; markerShown = !!m.text; }
  }
  const hm = marker ? (marker.hm || "") : "";
  const realLeads = markerShown && markerTop >= line;
  if (!hm || realLeads) return { show: false, hm: "", hidden: [] };
  // the code hides EVERY marker with top < line (so a straggler resets when it scrolls back); only the TIMED
  // ones are visually meaningful, so the replica reports those — hiding an empty same-minute marker is a no-op
  return { show: true, hm, hidden: all.filter((m) => m.text && m.mTop < line + STAMP_H).map((m) => m.id) };
}

const CTOP = 100, CBOT = 700, LINE = CTOP + BUFFER;   // #content spans [100, 700]; the line sits at 106

test("executed: tall turn, no stamp on screen → the sticky leads, showing its time", () => {
  const turns: Turn[] = [{ top: 40, marker: { id: "a", hm: "09:12", text: "", mTop: 55, mBottom: 68 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: true, hm: "09:12", hidden: [] });
});

test("executed: the tracked turn's own stamp resting BELOW the line leads — no sticky, no double", () => {
  const turns: Turn[] = [{ top: 96, marker: { id: "own", hm: "12:02", text: "12:02", mTop: 111, mBottom: 124 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: false, hm: "", hidden: [] });
});

test("executed: a LATER time change further down must not blank the top slot", () => {
  // The top turn is mid-minute (its own marker suppressed) and the next minute change sits 200px below.
  // Deferring to that stamp would leave the top with no time at all — the exact gap this exists to prevent.
  const turns: Turn[] = [
    { top: 90, marker: { id: "top", hm: "12:02", text: "", mTop: 105, mBottom: 118 } },      // tracked, suppressed
    { top: 300, marker: { id: "next", hm: "12:03", text: "12:03", mTop: 315, mBottom: 328 } }, // a different time
  ];
  const r = decideSticky(turns, CTOP, CBOT);
  assert.equal(r.show, true, "the sticky still names the time at the top");
  assert.equal(r.hm, "12:02");
  assert.deepEqual(r.hidden, [], "and the later change keeps its own stamp — it is not a duplicate");
});

test("executed: the moment a stamp crosses ABOVE the line, the sticky takes over and hides it (eager)", () => {
  // mTop 102 is above the line (106) but still on screen — the previous rule left the slot empty here; now
  // the sticky pins at the line with the same time and the crossed marker is hidden. No gap, no clipped sliver.
  const turns: Turn[] = [{ top: 90, marker: { id: "x", hm: "09:12", text: "09:12", mTop: 102, mBottom: 115 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: true, hm: "09:12", hidden: ["x"] });
});

test("executed: at the line exactly, the real stamp still leads — hand-off is one-sided", () => {
  const turns: Turn[] = [{ top: 92, marker: { id: "x", hm: "09:12", text: "09:12", mTop: LINE, mBottom: LINE + 13 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: false, hm: "", hidden: [] });
});

test("executed: a partially-clipped stamp at the very top is hidden and covered by the sticky", () => {
  // mTop 96 < cTop 100: half off the top, clipped — exactly the ugly sliver. The sticky covers it.
  const turns: Turn[] = [{ top: 82, marker: { id: "x", hm: "09:12", text: "09:12", mTop: 96, mBottom: 109 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: true, hm: "09:12", hidden: ["x"] });
});

test("executed: sticky leads at top, a genuine lower stamp stays visible — only the crossed one hides", () => {
  const turns: Turn[] = [
    { top: 88, marker: { id: "crossed", hm: "09:12", text: "09:12", mTop: 100, mBottom: 113 } }, // above line → hidden
    { top: 300, marker: { id: "lower", hm: "09:15", text: "09:15", mTop: 315, mBottom: 328 } },   // below → stays
  ];
  const r = decideSticky(turns, CTOP, CBOT);
  assert.equal(r.show, true);
  assert.deepEqual(r.hidden, ["crossed"], "the lower real stamp is not a double, so it is not hidden");
});

test("executed: it pins the LAST turn whose top is at/above the line when the sticky leads", () => {
  const turns: Turn[] = [
    { top: 20, marker: { id: "a", hm: "09:10", text: "", mTop: 35, mBottom: 48 } },
    { top: 60, marker: { id: "b", hm: "09:11", text: "", mTop: 75, mBottom: 88 } },   // last with top <= line
    { top: 900, marker: { id: "c", hm: "09:20", text: "09:20", mTop: 915, mBottom: 928 } }, // off-screen below
  ];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT).hm, "09:11");
});

test("executed: nothing scrolled to the line yet → no marker chosen, sticky hidden", () => {
  const turns: Turn[] = [{ top: 400, marker: { id: "a", hm: "09:20", text: "09:20", mTop: 415, mBottom: 428 } }];
  assert.deepEqual(decideSticky(turns, CTOP, CBOT), { show: false, hm: "", hidden: [] });
});

test("executed property: the top slot always holds exactly one time — never a gap, never a stacked pair", () => {
  // Walk one stamp up through the whole scroll, plus a same-minute run. At EVERY position the slot is filled
  // exactly once: either the tracked turn's own stamp sits at/below the line (sticky off), or the sticky holds
  // the line and every marker that crossed above it is hidden.
  const at = (mTop: number, text = "09:12"): Turn[] =>
    [{ top: mTop - 15, marker: { id: "a", hm: "09:12", text, mTop, mBottom: mTop + 13 } }];
  // A marker sits ~15px into its turn, so it belongs to the turn spanning the line only while
  // mTop <= LINE + 15; above that the turn has not reached the line yet and an earlier one is tracked.
  const states: Turn[][] = [
    at(LINE + 15), at(LINE + 9), at(LINE + 1), at(LINE),      // scrolling up toward the line: the real stamp leads
    at(LINE - 1), at(102), at(96), at(90),                    // crossed above it: the sticky leads, marker hidden
    at(55, ""),                                               // mid same-minute run, nothing stamped → sticky
    [                                                         // a same-minute run: only the first turn stamped
      { top: 60, marker: { id: "first", hm: "12:02", text: "12:02", mTop: 75, mBottom: 88 } },
      { top: 96, marker: { id: "m2", hm: "12:02", text: "", mTop: 111, mBottom: 124 } },
    ],
  ];
  for (const turns of states) {
    const r = decideSticky(turns, CTOP, CBOT);
    const visibleAboveLine = turns.some((t) => t.marker && t.marker.text && !r.hidden.includes(t.marker.id)
                                          && t.marker.mTop < LINE && t.marker.mBottom > CTOP);
    assert.equal(r.show && visibleAboveLine, false, "never the sticky AND a visible real stamp above the line");
    const trackedLeads = turns.filter((t) => t.marker && t.top <= LINE).slice(-1)
      .some((t) => !!t.marker!.text && t.marker!.mTop >= LINE);
    assert.equal(r.show || trackedLeads, true, "and never nothing: something always holds the slot");
  }
});

test("executed: a stamp intruding into the sticky's box is hidden, never superimposed", () => {
  // The tracked turn's stamp crossed above (sticky leads, suppressed same-minute marker), and the next
  // turn — a tool turn, marker only 10px below its top — has scrolled its stamped minute-change into the
  // sticky's own band without its turn reaching the line yet. Under a top-edge-only threshold the two
  // HH:MM texts superimpose in the gutter; the bottom-edge threshold hides the intruder until it leads.
  const turns: Turn[] = [
    { top: 90, marker: { id: "tracked", hm: "10:05", text: "", mTop: 100, mBottom: 113 } },
    { top: 107, marker: { id: "incoming", hm: "10:06", text: "10:06", mTop: 117, mBottom: 130 } },
  ];
  const r = decideSticky(turns, CTOP, CBOT);
  assert.equal(r.show, true, "the sticky still holds the slot");
  assert.equal(r.hm, "10:05", "showing the tracked turn's time");
  assert.deepEqual(r.hidden, ["incoming"], "the intruding stamp is hidden until it takes the lead");
});
