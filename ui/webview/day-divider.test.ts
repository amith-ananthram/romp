// The day divider (the user 2026-08-01). A day boundary used to stack its date on its own row
// INSIDE the 47px-wide rail marker; "Yesterday" measures 52.6px bold at the default 13px, so the
// pane's overflow:hidden ate its leading "Y". Sizing the label to fit that gutter is not a fix —
// `--fs` follows --vscode-chat-font-size, so a larger chat font re-clips whatever just fit at 13px.
// The date moved to a full-width divider in the prose column, where no date string can be cut off.
//
// Two invariants worth pinning, both of which a refactor could quietly break:
//   - the divider is a SIBLING before the turn, never a child of it. .dot and .time-marker are
//     absolutely positioned against the TURN's top edge, so a divider inside the turn would push
//     the message down and strand the dot up beside the rule.
//   - every append path emits it. The windowed rebuild and the incremental tail append are separate
//     code paths; when only one had it, scrolling back rebuilt dividers that live appends had dropped.
// The chat renderer has no jsdom harness, so — like render-rail.test.ts — pin at the source.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "styles.css"), "utf8");

test("dayDividerFor returns a divider only on the first turn of a past day, reached FORWARD (T339)", () => {
  const fn = RENDER.slice(RENDER.indexOf("function dayDividerFor"));
  assert.match(fn.slice(0, 400), /function dayDividerFor\(epoch: number, walk: DayWalk\)[^\n]*\n\s*const date = walk\.open\(epoch, Date\.now\(\)\);\s*\n\s*if \(!date\) return null/, "the marker's day rule plus the forward condition against the walk's high-water mark (time-marker.ts DayWalk, executed in time-marker.test.ts)");
  // the date centered between two hairlines: a rule, the label, a rule
  assert.match(fn.slice(0, 600), /d\.append\(el\("span", "day-divider-rule"\), lbl, el\("span", "day-divider-rule"\)\);/);
});

test("a collapsed run is placed and timed by its ANCHOR member, the latest, on both unit paths (T339)", () => {
  // the chat's windowed path (appendItem) and the comment popover's parity loop both pick the anchor with the one pure
  // rule (compact.ts itemAnchor, executed in compact.test.ts) and hand the day walk, the head and the walk's exit to it
  const ai = RENDER.slice(RENDER.indexOf("function appendItem("), RENDER.indexOf("function renderWindowItems("));
  assert.match(ai, /const anchor = itemAnchor\(it, \(i\) => eventEpoch\(s\.events\[i\]\)\);\s*\n\s*const dayOpen = eventEpoch\(s\.events\[anchor\]\);/, "the day walk reads the anchor");
  assert.match(ai, /const dv = dayDividerFor\(dayOpen, walk\);/, "…against the walk's mark, never the raw previous row");
  assert.match(ai, /renderNoticeGroup\(notes, s\.events\[anchor\], prevEpoch, key, open\)/, "the head is timed by the anchor");
  assert.match(ai, /renderNoticeGroup\([^\n]*\n\s*adv\(anchor\);/, "the walk leaves a closed run on its anchor");
  const ng = ai.slice(ai.indexOf('it.kind === "noticegroup"'), ai.lastIndexOf("} else {"));
  assert.doesNotMatch(ng, /adv\(it\.indices\[0\]\)/, "never the first member of a notice run");
  assert.match(ng, /\}\);\s*\n\s*adv\(anchor\);/, "…nor the last child of an open one: the anchor");
  // a tool run is untouched: its members are in transcript order, its head is timed by its first, its walk exits as before
  assert.match(ai, /const head = tag\(renderToolGroup\(tools, prevEpoch, key, open\)\);\s*\n\s*v\.el\.appendChild\(head\);\s*\n\s*adv\(it\.indices\[0\]\);/);
  const pop = RENDER.slice(RENDER.indexOf("const dayOpen = eventEpoch(evs[anchor]);") - 200, RENDER.indexOf("if (!relayNoted) list.appendChild(cmtRelayedNote"));
  assert.match(pop, /const anchor = itemAnchor\(it, \(i\) => eventEpoch\(evs\[i\]\)\);/);
  assert.match(pop, /renderNoticeGroup\(run, evs\[anchor\], prev, key, open\)/);
  assert.match(pop, /exit = it\.kind === "noticegroup" \? eventEpoch\(evs\[anchor\]\) : prev;[^\n]*\n\s*if \(it\.kind === "noticegroup" && exit != null\) prev = exit;/, "the popover's walk leaves an open notice run on its anchor too");
  assert.match(pop, /exit = eventEpoch\(it\.kind === "noticegroup" \? evs\[anchor\] : run\[run\.length - 1\]\); if \(exit != null\) prev = exit;/, "…and a closed one; a closed tool run exits on its last member, as before");
  assert.match(pop, /walk\.pass\(exit\);\s*\n\s*continue;/, "the day mark passes the run's exit");
  assert.doesNotMatch(RENDER, /eventEpoch\(evs\[itemFirstEvent\(it\)\]\)|eventEpoch\(s\.events\[itemFirstEvent\(it\)\]\)/, "no day walk reads a run's first member");
  // the head: rail time, data-t, uuid and hover from the anchor; the words from the first member
  const rng = RENDER.slice(RENDER.indexOf("function renderNoticeGroup("), RENDER.indexOf("function toggleToolGroup("));
  assert.match(rng, /function renderNoticeGroup\(evs: ChatEvent\[\], anchor: ChatEvent, prevEpoch: number \| null, key: string, open: boolean\)/);
  assert.match(rng, /const epoch = eventEpoch\(anchor\);\s*\n\s*const anchorUuid = anchor\.uuid \?\? null;/);
  assert.match(rng, /const b = noticeBrief\(evs\[0\]\);/);
});

test("the divider is a sibling of the turn, never a child (the dot anchors to the turn's top)", () => {
  // a turn.insertBefore/appendChild of the divider would displace the absolutely-positioned dot
  assert.doesNotMatch(RENDER, /turn\.(insertBefore|appendChild)\(\s*(dv|dayDivider)/);
  // it is appended to the thread/fold container instead
  assert.match(RENDER, /v\.el\.appendChild\(tag\(dv\)\)/, "windowed path appends to the thread");
  assert.match(RENDER, /wrap\.appendChild\(dv\)/, "cleared-episode fold appends to the fold body");
});

test("every append path emits the divider, so scrolling back can't disagree with the live tail", () => {
  // four call sites: windowed rebuild, incremental tail append, cleared-episode fold, and the
  // comment popover's chat-parity loop (the parity bundle, 2026-08-26)
  // (the `function dayDividerFor(` definition is excluded, hence the negative lookbehind)
  const calls = RENDER.match(/(?<!function )dayDividerFor\(/g) ?? [];
  assert.equal(calls.length, 4, `expected 4 dayDividerFor() call sites, found ${calls.length}`);
});

test("the windowed divider carries data-unit so the scroll-to-unit map still resolves it", () => {
  // appendItem tags via tag(); the tail path sets it explicitly
  assert.match(RENDER, /dv\.dataset\.unit = String\(i\)/);
});

test("the incremental tail trim goes by data-unit, not by child count", () => {
  // THE subtle break this feature could cause: the hot append path used to keep
  // `spacer + (from - winStart)` CHILDREN, one per unit. A day divider makes a unit own two
  // nodes, so that count trimmed one real turn off the tail per divider in the kept range —
  // and the re-render started at `from`, so those turns were gone until a full rebuild.
  assert.doesNotMatch(RENDER, /while \(v\.el\.childNodes\.length > keep\)/, "count-based trim is gone");
  assert.match(RENDER, /while \(v\.el\.lastChild && unitOf\(v\.el\.lastChild\) >= from\)/);
  // the spacer has no data-unit, so it must map to a sentinel BELOW any real unit and end the walk
  assert.match(RENDER, /n\.dataset\.unit != null \? Number\(n\.dataset\.unit\) : -1/);
});

test("the divider label is never constrained to a fixed width; two hairlines share the leftover room so it centers (T339)", () => {
  const rule = CSS.slice(CSS.indexOf(".day-divider-label"));
  assert.doesNotMatch(rule.slice(0, 200), /\bwidth:|max-width:/, "a fixed width is the bug being closed");
  // the hairlines fill the leftover room equally, so the label takes exactly what it needs and lands in the middle
  assert.match(CSS, /^\.day-divider-rule \{ flex: 1 1 auto; height: 1px; background: var\(--box-border\); \}/m);
  assert.match(CSS, /\.day-divider-label \{[^}]*flex: 0 0 auto/);
  assert.doesNotMatch(CSS, /\.day-divider::after/, "the one-sided rule is gone");
});

test("the rail runs through the divider: its own segment, the turn's line in the turn's colour, spanning its margins (T339)", () => {
  const turnRail = CSS.match(/^\.turn::before \{ content: ""; position: absolute; left: ([\d.]+px); top: 0; bottom: 0; width: (\d+px); background: ([^;]+); opacity: ([\d.]+); \}/m);
  assert.ok(turnRail, "the turn's rail rule, the one this segment mirrors");
  const seg = CSS.match(/^\.day-divider::before \{ content: ""; position: absolute; left: ([\d.]+px); top: (-?\d+px); bottom: (-?\d+px); width: (\d+px); background: ([^;]+); opacity: ([\d.]+); \}/m);
  assert.ok(seg, "the divider's rail segment");
  assert.deepEqual([seg![1], seg![4], seg![5], seg![6]], [turnRail![1], turnRail![2], turnRail![3], turnRail![4]], "same left, width, colour and opacity as the turn's line");
  const box = CSS.match(/^\.day-divider \{[^}]*margin: (\d+px) 0 (\d+px);[^}]*\}/m);
  assert.ok(box, "the divider's margins");
  assert.deepEqual([seg![2], seg![3]], ["-" + box![1], "-" + box![2]], "the segment spans the divider's own margins, so the line is continuous");
  assert.match(CSS, /^\.day-divider \{[^}]*position: relative;/m, "…anchored to the divider");
});

test("the divider label matches the rail marker's type size", () => {
  // one size for one kind of label — no new font-size on this surface.
  // Anchor each rule at the START of a line: a bare indexOf(".time-marker {") hits the compound
  // `.turn-compacting .dot, .turn-compacting .time-marker {` rule first and reads the wrong size.
  const rule = (sel: string): string => {
    const m = CSS.match(new RegExp("^\\" + sel + " \\{[^}]*\\}", "m"));
    assert.ok(m, `no top-level ${sel} rule`);
    return m[0];
  };
  const size = (s: string): string | null => (s.match(/font-size: ([\d.]+em)/) ?? [])[1] ?? null;
  assert.equal(size(rule(".day-divider")), size(rule(".time-marker")));
  assert.equal(size(rule(".day-divider")), "0.72em");
});

// T339 review: the day walk's reference is a HIGH-WATER MARK (time-marker.ts DayWalk, executed in time-marker.test.ts),
// separate from the rail's raw previous-row chain (prevEpoch / prevTimedEpoch, the same-minute rule). Every path that
// emits a divider decides against the mark and passes its unit's exit; a window opening mid-transcript seeds the mark
// as a walk from the top would have (unitExit, the one rule for both).
test("every day walk decides against a DayWalk mark and never a raw epoch; windows and tails seed the mark from the top", () => {
  const calls = RENDER.match(/(?<!function )dayDividerFor\([^)]*\)/g) || [];
  assert.equal(calls.length, 4, "four call sites: " + calls.join(" | "));
  for (const c of calls) assert.match(c, /^dayDividerFor\(\w+, walk\)$/, "each hands the walk, not a number: " + c);
  const ai = RENDER.slice(RENDER.indexOf("function appendItem("), RENDER.indexOf("function renderWindowItems("));
  assert.match(ai, /^function appendItem\(v: View, s: Session, items: DisplayItem\[\], u: number, prevEpoch: number \| null, walk: DayWalk, working: boolean\): number \| null \{/m);
  assert.match(ai, /walk\.pass\(unitExit\(s, it\)\);[^\n]*\n\s*for \(const n of nodes\) if \(!stamped\.has\(n\)\) stampWalkDay\(n, walk\);\s*\n\s*return prevEpoch;\s*\n\}/, "the unit's exit passes the mark on the way out, and every node the unit appended is stamped with the walk's day unless a row was stamped in its own day mid-unit (T342)");
  const rw = RENDER.slice(RENDER.indexOf("function renderWindowItems("), RENDER.indexOf("function sizeSpacers("));
  assert.match(rw, /const walk = dayWalkBefore\(s, items, unitStart\);[^\n]*\n\s*for \(let u = unitStart; u < unitEnd; u\+\+\) prevEpoch = appendItem\(v, s, items, u, prevEpoch, walk, working\);/, "a window seeds the mark by walking the units before it");
  assert.match(RENDER, /function dayWalkBefore\(s: Session, items: DisplayItem\[\], unitStart: number\): DayWalk \{\s*\n\s*const w = new DayWalk\(\);\s*\n\s*for \(let u = 0; u < unitStart && u < items\.length; u\+\+\) w\.pass\(unitExit\(s, items\[u\]\)\);/);
  // unitExit: the one rule — a lone event its own epoch, a notice run its anchor, a tool run its first (collapsed) or last (expanded)
  const ue = RENDER.slice(RENDER.indexOf("function unitExit("), RENDER.indexOf("function dayWalkBefore("));
  assert.match(ue, /if \(it\.kind === "event"\) return eventEpoch\(s\.events\[it\.index\]\);/);
  assert.match(ue, /if \(it\.kind === "noticegroup"\) return eventEpoch\(s\.events\[itemAnchor\(it, \(i\) => eventEpoch\(s\.events\[i\]\)\)\]\);/);
  assert.match(ue, /const open = openFolds\.has\(toolGroupKey\(s\.events\[it\.indices\[0\]\]\)\);\s*\n\s*if \(!open\) return eventEpoch\(s\.events\[it\.indices\[0\]\]\);/, "a collapsed tool run: its first member");
  // an expanded run: its HIGH-WATER member (the walk passes every row and never rewinds), so the seed matches the walk for any row order
  assert.match(ue, /for \(const i of it\.indices\) \{ const ep = eventEpoch\(s\.events\[i\]\); if \(ep != null && \(mx == null \|\| ep > mx\)\) mx = ep; \}\s*\n\s*return mx;/);
  // the normal-mode tail: the mark seeded over the events before `from`, passed per row; the rail keeps its raw chain
  assert.match(RENDER, /const walk = dayWalkBeforeEvent\(s\.events, from\);[^\n]*\n\s*for \(let i = from; i < len; i\+\+\) \{\s*\n\s*const prev = prevTimedEpoch\(s\.events, i\);/);
  assert.match(RENDER, /v\.el\.appendChild\(node\);\s*\n\s*walk\.pass\(ep\);\s*\n\s*stampWalkDay\(node, walk\);\s*\n\s*\}/, "…and passes each row, stamping it with the walk's day (T342)");
  // the cleared-episode fold and the comment popover carry their own walk
  assert.match(RENDER, /const walk = new DayWalk\(\);   \/\/ the fold divides days/);
  assert.match(RENDER, /prevEp = ep; walk\.pass\(ep\);/);
  assert.match(RENDER, /let prev: number \| null = null;[^\n]*\n\s*const walk = new DayWalk\(\);/, "the popover");
  assert.match(RENDER, /if \(ep != null\) prev = ep;\s*\n\s*walk\.pass\(ep\);\s*\n\s*\}/, "…passes each lone row");
  assert.doesNotMatch(RENDER, /prevEpoch = prevEpoch == null \? ep : Math\.max/, "the rail's chain stays raw: the mark is the walk's, not the marker's");
});

test("a divider that leads the transcript draws no rail segment above the date, and its turn starts at its first dot (T339 review)", () => {
  // leading = the thread's first child, or right after the system-context card, which sits off the rail (its line suppressed)
  assert.match(CSS, /^\.turn-system::before \{ display: none; \}/m, "the card's own line is off, so a divider after it has nothing above");
  assert.match(CSS, /^\.day-divider:first-child::before, \.turn-system \+ \.day-divider::before \{ display: none; \}/m);
  const first = CSS.match(/^\.turn:first-child::before \{ top: (\d+px); \}/m);
  assert.ok(first, "the first turn's own rule, the one this mirrors");
  assert.match(CSS, new RegExp("^\\.day-divider:first-child \\+ \\.turn::before, \\.turn-system \\+ \\.day-divider \\+ \\.turn::before \\{ top: " + first![1] + "; \\}", "m"), "the turn after a leading divider starts its rail where a first turn does");
});
