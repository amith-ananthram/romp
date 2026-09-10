// How render.ts WIRES the skeleton-tab state machine (skeleton-tabs.ts; its policy is executable in
// skeleton-tabs.test.ts). After a redial the kernel sends only the active tab in full and lists the rest as
// `skeleton` on the tab strip, each carrying a status frame instead of a transcript; the page keeps the stale
// pre-outage sessions underneath and must never DISPLAY one as current. No jsdom for the webview, so these are
// source-level pins (the prebuild-wiring.test.ts convention). Synthetic ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const WEBVIEW = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(WEBVIEW, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(WEBVIEW, "styles.css"), "utf8");

const fn = (name: string): string => {
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i >= 0, `${name} not found`);
  return RENDER.slice(i, RENDER.indexOf("\n}\n", i) + 3);
};

test("render.ts holds ONE skeleton set, declared beside tabMeta, and reads the active session through liveSession", () => {
  assert.match(RENDER, /import \{ newSkeletonState, applyTabOrderSkeleton, onStatus, onFull, onDismiss, onSocketUp, nextPrefetch, renderKind \} from "\.\/skeleton-tabs";/);
  // beside tabMeta / closingTabs / pendingTabMeta (below them: tab-close-optimistic.test.ts wants closingTabs within
  // 900 characters of tabMeta) — renderTabs reads it and can run before the module finishes evaluating
  assert.match(RENDER, /const pendingTabMeta = new Map<string, PendingTabMeta>\(\);\n(?:\/\/[^\n]*\n)*const skeletonTabs = newSkeletonState\(\);/);
  assert.ok(RENDER.indexOf("const skeletonTabs = newSkeletonState();") < RENDER.indexOf("function renderTabs()"));
  assert.match(RENDER, /function liveSession\(id: string \| null \| undefined\): Session \| undefined \{\s*\n\s*return id && !skeletonTabs\.ids\.has\(id\) \? sessions\.get\(id\) : undefined;/);
});

test("the tabOrder frame applies the skeleton list BEFORE applyTabOrder, so its one renderTabs paints the final set", () => {
  // the dispatch's own tabOrder branch is pinned byte-for-byte by tab-meta.test.ts, so the pre-step is a
  // statement AHEAD of the chain — it runs first, and applyTabOrder's renderTabs sees the final state
  assert.match(RENDER, /if \(m\.type === "tabOrder"\) noteSkeletonTabOrder\(m\);[^\n]*\n\s*if \(m\.type === "session"\) upsert\(m\);/);
  const note = fn("noteSkeletonTabOrder");
  assert.match(note, /const kernelOrder: string\[\] = Array\.isArray\(m\.order\) \? m\.order\.filter\(\(x: any\) => typeof x === "string"\) : \[\];/,
    "the same string-only kernel order applyTabOrder adopts");
  assert.match(note, /const changed = applyTabOrderSkeleton\(skeletonTabs, m\.skeleton, kernelOrder\);/);
  // one client-diag row per reconnect that produced a set: armed by the socket opening, spent by the first strip
  assert.match(note, /if \(skeletonDiagArmed && Array\.isArray\(m\.skeleton\) && skeletonTabs\.ids\.size\) \{\s*\n\s*skeletonDiagArmed = false;\s*\n\s*vscodeApi\?\.postMessage\(\{ type: "clientDiag", surface: "chat", what: "skeleton", data: \{ n: skeletonTabs\.ids\.size, active: activeId \} \}\);/);
  assert.match(RENDER, /else if \(m\.type === "wsup"\) \{ onSocketUp\(skeletonTabs\); skeletonDiagArmed = true; \}/,
    "a new socket forgets which fulls the dead one delivered and re-arms the row");
  // the tab we are ON became a skeleton (a click in the redial gap, a stale active hint) → re-show, keyed on change
  assert.match(note, /if \(changed && activeId && skeletonTabs\.ids\.has\(activeId\)\) showActive\(\);/);
  // the pre-step must come before the message handler's chain, and the definition sits with applyTabOrder
  assert.ok(RENDER.indexOf("function noteSkeletonTabOrder(") > RENDER.indexOf("function applyTabOrder("));
});

test("renderTabs draws a skeleton tab BEFORE the placeholder branch; both draw the chip through ONE shared helper", () => {
  const rt = fn("renderTabs");
  // (a sectioned strip stamps the copy's group on the skeleton and the placeholder alike — T264b, flipTabs keys per copy)
  assert.match(rt, /const s = sessions\.get\(id\);\s*\n(?:\s*\/\/[^\n]*\n)?\s*if \(renderKind\(skeletonTabs, id, !!s\) === "skeleton"\) \{\s*\n\s*const sk = makeSkeletonTab\(id\);\s*\n\s*if \(copyGroup !== undefined\) sk\.dataset\.copy = copyGroup \?\? "";[^\n]*\n\s*bar\.appendChild\(sk\); continue;\s*\n\s*\}\s*\n\s*if \(!s\) \{\s*\n\s*const ph = makePlaceholderTab\(id\);/,
    "skeleton first: a stale session entry must not make the tab read as loaded");
  assert.match(rt, /const st = applyTabStatus\(tab, s\);/, "the loaded tab's chip comes from the shared helper");
  assert.match(rt, /appendTabCtxGauge\(tab, s\);/, "…and its gauge");
  assert.match(rt, /wireTabDrag\(tab, id\);/, "…and its drag listeners");
  // exactly ONE status→class/dot block in the file: the helper (the "MISSING state" ring included — since T262g the
  // dot's class is tab-state.ts's tabDotClass, one slot in every state, so the file has ONE call and no ring literal)
  assert.equal(RENDER.split("const dotCls = tabDotClass(st);").length - 1, 1, "one dot-slot site: applyTabStatus");
  assert.equal(RENDER.split('el("span", "tab-dot unknown")').length - 1, 0, "no hand-rolled unknown ring anywhere");
  // …and the state → class step inside it is tab-state.ts's shared rule (tab groups, 2026-09-04: the folded
  // section header's pip reads the same function), so the file has ONE such call and no hand-rolled class literal
  assert.equal(RENDER.split("const stateCls = tabStateClass(s.status);").length - 1, 1, "one state-class site: applyTabStatus wears the shared rule");
  assert.equal(RENDER.split('tab.classList.add("tab-working")').length - 1, 0, "no hand-rolled state class anywhere");
  const chip = fn("applyTabStatus");
  assert.match(chip, /^function applyTabStatus\(tab: HTMLElement, s: \{ status: Partial<Status> \}\): ChipState \| undefined \{\s*\n\s*const st = s\.status\.state;/);
  assert.match(chip, /const dotCls = tabDotClass\(st\);\s*\n\s*if \(dotCls\) tab\.appendChild\(el\("span", dotCls\)\);/, "no state → the honest unknown ring (tabDotClass: a missing state is the gray ring)");
  assert.match(chip, /return st;\s*\n\}/);
});

test("makeSkeletonTab: the loaded-tab chrome minus what it does not know — no swirl, honest chip, click-safe, draggable, closable", () => {
  const sk = fn("makeSkeletonTab");
  assert.match(sk, /el\("div", "tab tab-skeleton" \+ \(id === activeId \? " active" : ""\)\)/);
  assert.match(sk, /const meta = tabMeta\.get\(id\);/, "name + color from the pushed tab meta (fresh)");
  assert.match(sk, /const name = meta\?\.name \|\| stale\?\.name \|\| "";/, "…falling back to the stale session's name — a name is still the name");
  assert.match(sk, /tab\.tabIndex = 0;/);
  assert.match(sk, /tab\.dataset\.id = id;/);
  assert.match(sk, /tab\.dataset\.act = "select";/, "the stable #tabs delegate — click-safe like every tab");
  assert.match(sk, /tab\.addEventListener\("keydown", onTabKey\);/);
  assert.match(sk, /tab\.draggable = true;\s*\n\s*wireTabDrag\(tab, id\);/, "a real live session: reordering is legitimate");
  assert.match(sk, /tab\.classList\.add\("colored"\)/);
  assert.match(sk, /const status = skeletonTabs\.status\.get\(id\) as Status \| undefined;\s*\n\s*applyTabStatus\(tab, \{ status: status \?\? \{\} \}\);/,
    "the chip reads ONLY the kernel's status frames; none yet → an empty status → the unknown ring");
  assert.match(sk, /if \(status\) appendTabCtxGauge\(tab, \{ status \}\);/, "the gauge only from a kernel-sent status");
  assert.match(sk, /const dead = status\?\.state === "closed";\s*\n\s*closeBtn\.title = dead \? "Close tab" : "End session";\s*\n\s*if \(dead\) closeBtn\.dataset\.dead = "1";/,
    "a dead session drawn as a skeleton drops like a dead loaded tab (the delegate's dead branch), no End confirm (review find 2026-09-08)");
  assert.match(sk, /tab\.title = "Not loaded yet — click to load";/);
  assert.match(sk, /closeBtn\.dataset\.act = "close";\s*\n\s*closeBtn\.dataset\.id = id;/, "the ✕ is delegated too");
  assert.doesNotMatch(sk, /tab-ph-swirl/, "NO swirl: that means 'romp is generating this', a transient");
  assert.doesNotMatch(sk, /stale\.(events|status)/, "the stale session's events/status are never read");
  assert.doesNotMatch(sk, /showTabTip/, "no rich hover tip — it reads a session's dir/model");
  assert.match(fn("makePlaceholderTab"), /tab-ph-swirl/, "the placeholder keeps its swirl");
  // DOM order label → gauge → ✕ (the ✕ keeps the right edge), as on a loaded tab
  const label = sk.indexOf("tab.appendChild(label);"), gauge = sk.indexOf("appendTabCtxGauge(tab"), close = sk.indexOf('const closeBtn = el("span", "tab-close");');
  assert.ok(label >= 0 && label < gauge && gauge < close);
  // the builders sit ABOVE makePlaceholderTab: tabs-first.test.ts slices makePlaceholderTab→renderTabs and
  // forbids close/drag there, and tab-ctx-gauge.test.ts orders the file's first label < gauge < `const close`
  const ph = RENDER.indexOf("function makePlaceholderTab(");
  for (const f of ["applyTabStatus", "wireTabDrag", "makeSkeletonTab", "appendTabCtxGauge"]) assert.ok(RENDER.indexOf(`function ${f}(`) < ph, f + " above the placeholder builder");
});

test("statusOnly begins with the skeleton branch: store + scheduleRenderTabs (one frame for a burst), never renderTabs or the no-base ask", () => {
  const body = RENDER.split("function statusOnly(msg: any) {")[1].split("\n}")[0];
  const first = body.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("//"))[0];
  assert.equal(first, 'if (onStatus(skeletonTabs, msg.id, msg.status) === "skeleton") { scheduleRenderTabs(); return; }');
  assert.ok(body.indexOf("onStatus(skeletonTabs") < body.indexOf('if (!s) { requestFullSession(msg.id, "nobase"); return; }'),
    "the no-base repair still follows, unchanged, for every non-skeleton sid");
  const skel = body.slice(0, body.indexOf("const s = sessions.get(msg.id);"));
  assert.doesNotMatch(skel, /\brenderTabs\(\)/, "sixteen status frames land in one burst — one animation frame, not sixteen synchronous repaints");
});

test("chatTail and update ask for the full on a skeleton id BEFORE their no-base check (a delta with no trusted base)", () => {
  const tail = fn("chatTail");
  assert.match(tail, /^function chatTail\(msg: any\) \{\s*\n(?:\s*\/\/[^\n]*\n)*\s*if \(skeletonTabs\.ids\.has\(msg\.id\)\) \{ requestFullSession\(msg\.id, "skeleton-delta"\); return; \}\s*\n\s*const s = sessions\.get\(msg\.id\);\s*\n\s*if \(!s\) \{/);
  const upd = fn("update");
  assert.match(upd, /if \(skeletonTabs\.ids\.has\(msg\.id\)\) \{ requestFullSession\(msg\.id, "skeleton-delta"\); return; \}[^\n]*\n\s*const s = sessions\.get\(msg\.id\);\s*\n\s*if \(!s\) \{ requestFullSession\(msg\.id, "nobase"\); return; \}/);
});

test("showActive gates the active session on the set, shows the loader with LOADING copy, and asks after notifyActive", () => {
  const sa = fn("showActive");
  assert.match(sa, /const s = activeId \? liveSession\(activeId\) : null;\s*\n\s*if \(!s\) \{/, "a skeleton active takes the existing !s branch");
  assert.match(sa, /const skeleton = skeletonTabs\.ids\.has\(activeId\);\s*\n\s*skeletonLoading = skeleton \? activeId : null;[^\n]*\n\s*if \(skeleton\) wait\.appendChild\(rompLoaderInner\("loading " \+ what \+ "…"\)\);\s*\n\s*else wait\.appendChild\(rompLoaderInner\("opening " \+ what \+ "…"\)\);/,
    "a running session is LOADING; 'opening' would claim a start that is not happening (the placeholder keeps its line)");
  assert.match(sa, /if \(skeleton\) requestFullSession\(activeId, "skeleton-click"\);/);
  // activeTab (notifyActive) precedes needFull on the wire → the kernel builds the new active first
  assert.ok(sa.indexOf("notifyActive();") < sa.indexOf('requestFullSession(activeId, "skeleton-click")'));
  // the ask lives in showActive — the ONE place every path that lands on a skeleton active goes through (the
  // click via setActive, dismissSession's MRU fallback, the strip re-listing the tab we are on); the idle
  // prefetch skips the active tab by design, so nothing else would load it
  assert.equal(RENDER.split('"skeleton-click"').length - 1, 2, "one call site (+ the type's literal)");
  assert.match(fn("setActive"), /renderTabs\(\);\s*\n\s*showActive\(\);/, "the click path: strip repaint, then showActive → loader + ask");
  assert.match(fn("dismissSession"), /activeId = mru\.find\([\s\S]*?showActive\(\);/, "the fallback path lands in showActive too");
});

test("upsert computes wasSkeleton beside awaitingFull.delete and routes a just-loaded skeleton to showActive, not appendActive", () => {
  const up = fn("upsert");
  assert.match(up, /awaitingFull\.delete\(msg\.id\);[^\n]*\n\s*const wasSkeleton = onFull\(skeletonTabs, msg\.id\);/);
  assert.match(up, /if \(wasSkeleton \|\| skeletonLoading === msg\.id\) showActive\(\);[^\n]*\n\s*else if \(existed && !forked && !firstBuild && !adopted\) \{\s*\n\s*appendActive\(\);/,
    "the loader is up and the view hidden — appendActive would append onto a hidden view");
  assert.match(fn("appendActive"), /if \(!content \|\| !activeId \|\| skeletonTabs\.ids\.has\(activeId\)\) \{ showActive\(\); return; \}/,
    "appendActive itself refuses a skeleton active — the loader stays");
});

test("the idle prefetch: runPrebuild asks nextPrefetch (hidden = document.hidden || the pane display:none) for exactly one, and viewState is null for a skeleton", () => {
  const run = fn("runPrebuild");
  assert.match(run, /if \(pendingBuildRaf != null\) \{ schedulePrebuild\(\); return; \}[^\n]*\n(?:\s*\/\/[^\n]*\n)*\s*const next = nextPrefetch\(skeletonTabs, activeId, awaitingFull, document\.hidden \|\| paneHidden\(\), tabInView\);\s*\n\s*if \(next\) requestFullSession\(next, "prefetch"\);/);
  assert.match(run, /const viewState = \(id: string\): ViewState \| null => \{\s*\n\s*if \(skeletonTabs\.ids\.has\(id\)\) return null;/,
    "the pure planner never builds DOM for a stale session");
  assert.match(RENDER, /function paneHidden\(\): boolean \{\s*\n\s*try \{ return \(window\.parent !== window && \(window\.innerWidth === 0 \|\| window\.innerHeight === 0\)\) \|\| \(window as PaneHiddenHost\)\.__rompPaneHidden === true; \}/,
    "the shim's own display:none test, mirrored: the zero-viewport probe OR the pane's published word (paint-gate.ts states the rule; chat-visibility.ts publishes it on this page)");
  assert.match(RENDER, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(!document\.hidden\) schedulePrebuild\(\); \}\);/,
    "coming back to the tab is the event that re-arms the chain");
});

test("run: render.ts's paneHidden() over window stand-ins says hidden when the probe OR a published word of true does, never the word first", () => {
  // The function lifted from render.ts (esbuild at run time, the chat-exact-tail-exec.test.ts pattern) is the third
  // consumer of the question the pane shim and perf telemetry answer; the eight cases are the shim's
  // (tests/test_kernel_disconnect_banner.py runs the served function over the same stand-ins).
  const a = RENDER.indexOf("function paneHidden(): boolean {"), b = RENDER.indexOf("// The prefetch never runs while the browser tab is hidden", a);
  assert.ok(a > 0 && b > a, "paneHidden's anchors moved; re-anchor");
  const js = requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts" }).code;
  const verdict = (framed: boolean, iw: number, ih: number, word?: unknown): boolean => {
    const win: any = { innerWidth: iw, innerHeight: ih };
    win.parent = framed ? {} : win;
    if (word !== undefined) win.__rompPaneHidden = word;
    return (new Function("window", js + "\nreturn paneHidden();") as (w: unknown) => boolean)(win);
  };
  assert.equal(verdict(true, 0, 0), true, "a framed pane with a zero viewport: hidden since load, by the probe");
  assert.equal(verdict(true, 600, 400), false, "a framed pane with a viewport and no word: shown");
  assert.equal(verdict(true, 600, 400, true), true, "a viewport and a word of true: hidden after a first show (Chromium keeps the iframe's size)");
  assert.equal(verdict(true, 600, 400, false), false, "a viewport and a word of false: shown");
  assert.equal(verdict(true, 0, 0, false), true, "a zero viewport and a stale word of false: hidden (Firefox zeroes the viewport and stalls the observer)");
  assert.equal(verdict(false, 600, 400, true), true, "a standalone page's word is its tab's hiding; the probe never applies there");
  assert.equal(verdict(false, 0, 0), false, "a standalone page with no word: never hidden by the probe");
  assert.equal(verdict(true, 600, 400, "yes"), false, "only a boolean true is the word");
});

test("requestFullSession(id, why): every ask names its why, from the fixed vocabulary", () => {
  assert.match(RENDER, /type NeedFullWhy = "gap" \| "nobase" \| "skeleton-click" \| "prefetch" \| "skeleton-delta";/);
  assert.match(RENDER, /function requestFullSession\(id: string, why: NeedFullWhy\): void \{\s*\n\s*if \(!id \|\| awaitingFull\.has\(id\)\) return;\s*\n\s*awaitingFull\.add\(id\);\s*\n\s*vscodeApi\?\.postMessage\(\{ type: "needFull", id, why \}\);/);
  const calls = [...RENDER.matchAll(/requestFullSession\(([^()]*?)\)/g)].map((m) => m[1]).filter((a) => !a.startsWith("id: string"));
  assert.ok(calls.length >= 6, "the gap, no-base ×3, skeleton-delta ×2, skeleton-click and prefetch sites");
  for (const c of calls) assert.match(c, /, "(gap|nobase|skeleton-click|prefetch|skeleton-delta)"$/, `call site without a why: requestFullSession(${c})`);
  const why = (w: string) => RENDER.split(`, "${w}")`).length - 1;
  assert.equal(why("gap"), 1); assert.equal(why("nobase"), 3); assert.equal(why("skeleton-delta"), 2);
  assert.equal(why("skeleton-click"), 1); assert.equal(why("prefetch"), 1);
});

test("dismissSession is the one removal site: onDismiss right after the session map forgets the id", () => {
  assert.match(fn("dismissSession"), /sessions\.delete\(id\);\s*\n\s*onDismiss\(skeletonTabs, id\);/);
  // applyTabOrder's omission teardown goes through dismissSession (pinned in draft-teardown.test.ts), so it is covered
  assert.match(fn("applyTabOrder"), /dismissSession\(id, "omitted", omitted\);/);
});

test("every ACTIVE-tab display path reads through liveSession; only name reads and the fork ACTION gate keep sessions.get(activeId)", () => {
  const raw = [...RENDER.matchAll(/sessions\.get\(activeId[^\n]*/g)].map((m) => m[0]);
  assert.equal(raw.length, 4, "two name-only reads (a stale name is still the name) + the two fork-prompt gates (an action on a running session, not a display of its transcript):\n" + raw.join("\n"));
  for (const r of raw) assert.match(r, /\?\.name|showForkPrompt\(activeId/);
  const live = RENDER.split("liveSession(activeId)").length - 1;
  assert.ok(live >= 17, `the sweep covers the display paths (${live} sites)`);
  for (const f of ["updateStatusline", "renderBgTasks", "renderSubHead", "paintScrollMarks", "updateCommentRail", "landNearestMoment", "virtualizeToViewport"]) {
    assert.match(fn(f), /liveSession\(activeId\)/, f + " reads the gated session");
  }
  assert.match(fn("renderLiveAsk"), /if \(!activeId \|\| skeletonTabs\.ids\.has\(activeId\) \|\| !liveAsks\.has\(activeId\)\) \{/,
    "a skeleton's pre-outage picker is stale — hidden until the tab loads");
});

test("styles: the skeleton label wears the placeholder's own muted value, and nothing else is new", () => {
  assert.match(CSS, /\.tab\.tab-skeleton \.tab-label \{ opacity: 0\.75; \}/);
  assert.match(CSS, /\.tab\.tab-placeholder\.colored \.tab-label \{ opacity: 0\.75; \}/, "the same number — no new opacity vocabulary");
  assert.equal((CSS.match(/tab-skeleton/g) || []).length, 1, "one rule; the chip/dot/ring rules are the shared ones");
});

test("the click path's loader latch: showActive latches the skeleton it is loading; upsert re-shows on it; a real view clears it", () => {
  // on the click path the strip that RELEASES the id lands before its full, so onFull() reports no skeleton —
  // the latch is what routes that full to showActive (review find 2026-09-07)
  assert.match(RENDER, /let skeletonLoading: string \| null = null;/);
  const show = RENDER.slice(RENDER.indexOf("function showActive("), RENDER.indexOf("function showActive(") + 6000);
  assert.match(show, /const skeleton = skeletonTabs\.ids\.has\(activeId\);\s*\n\s*skeletonLoading = skeleton \? activeId : null;/);
  assert.match(show, /document\.getElementById\("tab-loading"\)\?\.remove\(\);[^\n]*\n\s*skeletonLoading = null;/);
  assert.doesNotMatch(RENDER, /window\.addEventListener\("romp:wsup", \(\) => \{ onSocketUp/, "the socket flip is a frame now, never the onopen event");
});

test("the statusline over a skeleton tab says Loading, the word its loader uses, not Opening", () => {
  const usl = fn("updateStatusline");
  assert.match(usl, /const loading = skeletonTabs\.ids\.has\(activeId\) \|\| skeletonLoading === activeId;\s*\n\s*sl\.replaceChildren\(openingLine\(loading \? "Loading session" : "Opening session"\)\);/);
  assert.match(RENDER, /function openingLine\(text = "Opening session"\): HTMLElement \{/);
});

test("the strip's repaint gate sees a skeleton: the signature reads renderKind and the stored status, not the stale session", () => {
  const rt = fn("renderTabs");
  const sig = rt.slice(rt.indexOf("const stripSig = JSON.stringify(["), rt.indexOf("const mslotEl = "));
  assert.ok(sig.length > 0, "the signature located");
  assert.match(sig, /renderKind\(skeletonTabs, id, !!s\) === "skeleton"/);
  assert.match(sig, /skeletonTabs\.status\.get\(id\)/);
});

