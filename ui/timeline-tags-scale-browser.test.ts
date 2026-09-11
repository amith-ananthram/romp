// THE SESSIONS & TAGS DIALOG'S LAYOUT WITH MANY TAGS, MEASURED (the user 2026-09-09: with ten tags the tag
// rows and the pane-filter matrix filled the dialog and "the sessions" showed two rows under the fold).
// timeline-tags-scale.test.ts executes the behaviour over the fake DOM; nothing there measures a pixel. This
// module mounts the real view file in a browser with thirty (or ten) tags and forty sessions, opens the
// dialog and measures, in three legs per browser:
//  1. the layout across pages: at 1300, 800 and 700px tall every tag row is one line (at most 32px), the tag
//     table scrolls within itself under its 30vh cap, [+ New tag] sits under the table (never under its fold),
//     the card never scrolls or clips as a whole, and the working area (the search box, "tag all", the session
//     rows) stays on screen both folded and with the filter matrix open, whose cell keeps to 25vh, scrolls within
//     itself when the page is short and clips the chips past its box (elementFromPoint finds no chip there, so
//     none is printed over the sessions); at 800 a real pointer drag past the table's edge cues the last row with room for the
//     cue, inside the box, and drops there, and a wheel mid-drag moves the cue and the drop to the rows it
//     brings under the pointer, scrolled to the end a stepped drag below the table cues the last row, and a top
//     cue on the first row scrolled 1.5px under the clip moves to the next row with the scroll unmoved and one
//     scroll event (overflow-anchor:none); the floors are the rows' rendered height (one and three tags show no blank and no scroll, at 420 three tags
//     keep three whole rows, the sessions box holds min(live, 4) rows and no blank, and four of the SMALLEST
//     rows when the first session's chips wrap at a 390px page); on a phone held sideways (844x390, touch) the
//     card scrolls as a whole (its rendered overflow-y is auto) instead of clipping;
//  2. the colour popover and the table at 1300px: the pill of the tag the dialog was opened for keeps its ring
//     inside the table's clip, Enter on a dot opens the popover with the current swatch focused (the browser's
//     focus ring; the inner ring says "current"), Tab closes it back onto the dot, a table scroll that moves the
//     dot closes it, a window resize closes it, the table's scroll survives a repaint (and clamps to 0 when the
//     table no longer scrolls), and a repaint that pushes the rows down re-places the popover on its dot;
//  3. framed shells: with the view in an iframe (offset into the page, or a 200px band at the page's foot) the
//     dialog is adopted into the top document and the popover is placed by the dot's rect there, on screen,
//     untranslated.
// Skips LOUDLY per leg without a playwright browser (`npx playwright install chromium firefox` under
// vscode-extension puts them on a machine). Synthetic names and ids only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
const requireCjs = createRequire(__filename);
const EXT = process.cwd();                                        // npm test runs in vscode-extension
const VIEW = fs.readFileSync(path.resolve(EXT, "..", "ui", "romp-timeline-view.js"), "utf8");
let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

// the kernel's timeline page, reduced to the view: the three Obsidian DOM helpers the panel expects on every
// element (timeline-boot.ts installDomHelpers) and the module shim the page wraps the file in (kernel.py _timeline_body)
const HELPERS = `var P=HTMLElement.prototype;
if(!P.createEl)P.createEl=function(tag,o){var e=document.createElement(tag);if(o&&o.cls)e.className=o.cls;if(o&&o.text)e.textContent=o.text;this.appendChild(e);return e;};
if(!P.createDiv)P.createDiv=function(o){return this.createEl('div',o);};if(!P.createSpan)P.createSpan=function(o){return this.createEl('span',o);};`;
const PAGE = `<!DOCTYPE html><html><head><meta charset=utf-8><style>html,body{margin:0;background:#1e1e1e;color:#ccc;font:13px sans-serif}</style></head>
<body><div id=host></div><script>${HELPERS}</script><script>var module={exports:{}};(function(module,exports){\n${VIEW}\n})(module,module.exports);
var TimelinePanel=module.exports.TimelinePanel; window.panel = new TimelinePanel(document.getElementById('host'));</script></body></html>`;
// two web-shell stand-ins hosting the view as an iframe: one offset into the page, one a 200px band at the
// foot of a 1300px page (the shell's f-timeline band, where a popover translated twice fell off the bottom)
const SHELL_OFFSET = `<!DOCTYPE html><html><body style="margin:0;background:#111"><iframe id=f-timeline src="/timeline" style="position:absolute;left:300px;top:400px;width:1000px;height:600px;border:0"></iframe></body></html>`;
const SHELL_BAND = `<!DOCTYPE html><html><body style="margin:0;background:#111"><div style="height:1100px"></div><iframe id=f-timeline src="/timeline" style="display:block;width:100%;height:200px;border:0"></iframe></body></html>`;

const PALETTE = ["#1EA1EB", "#54B204", "#4EA8A9", "#DD42FF", "#E87221", "#4EC9B0", "#E0AF68", "#F7768E", "#7AA2F7", "#9ECE6A", "#BB9AF7", "#FF9E64"];
const now = 1_781_000_000;
// the panel's data with `nTags` tags (tag-01, tag-02, ...) and `nSessions` sessions, the first `liveN` of them live;
// `tagsOnS1` puts the first session in that many tags (its row's chips wrap on a narrow card)
const dataFor = (nTags: number, nSessions = 40, liveN = nSessions, tagsOnS1 = 0) => {
  const names = Array.from({ length: nTags }, (_, i) => "tag-" + String(i + 1).padStart(2, "0"));
  return {
    now, turns: {}, messages: [], judging: [], palette: PALETTE,
    sessions: Array.from({ length: nSessions }, (_, i) => ({
      id: "s" + (i + 1), name: "job-" + String(i + 1).padStart(2, "0"), color: PALETTE[i % 12], state: "working", live: i < liveN, model: "Opus", effort: "high",
      context: 40, since: now - 60, awaiting: [], compacting: [], pendingMail: 0, compactions: [], faded: false, stale: false,
    })),
    views: {
      active: "all", at: 100, seq: 1000,
      actives: { chat: { tags: names.slice(0, 12) }, timeline: { all: true }, outline: { none: true } },
      tags: names.map((name, i) => ({ id: "g" + (i + 1), name, color: PALETTE[i % 12], members: (i < 20 ? ["s" + (i + 1)] : []).concat(i > 0 && i < tagsOnS1 ? ["s1"] : []), mtime: 100 })),
    },
  };
};
const DATA = dataFor(30);

// a scroll event is delivered asynchronously: two frames let it land before the next read
const twoFrames = (page: any) => page.evaluate(() => new Promise<void>((res) => requestAnimationFrame(() => requestAnimationFrame(() => res()))));
// the popover open with focus on its current swatch (the open focuses it a tick later), and the popover closed:
// waited for as conditions, never as a fixed delay (a page under load delivers a tick or a resize late). `inTop`
// reads the top document's focus, where a framed view's popover lives
const popFocused = (target: any, inTop = false) => target.waitForFunction((inTop: boolean) => {
  const p = (window as any).panel, doc = inTop ? (window.top as Window).document : document;
  return !!p._tagColorPop && doc.activeElement === p._tagColorPop.querySelector("[aria-checked=true]");
}, inTop);
const popClosed = (page: any) => page.waitForFunction(() => (window as any).panel._tagColorPop === null);

type MountOpts = { width?: number; height?: number; mobile?: boolean; nTags?: number; nSessions?: number; liveN?: number; tagsOnS1?: number };
async function mount(browser: any, opts: MountOpts = {}) {
  const { width = 1600, height = 1300, mobile = false, nTags = 30, nSessions = 40, liveN = nSessions, tagsOnS1 = 0 } = opts;
  const errors: string[] = [];
  // `mobile`: a phone held sideways. Touch, and the mobile layout viewport: both browsers lay a page without a
  // viewport meta out at 980px wide and scale it to the screen, the way a phone renders it
  const page = await browser.newPage(Object.assign({ viewport: { width, height } }, mobile ? { hasTouch: true, isMobile: true } : {}));
  page.on("pageerror", (e: Error) => { errors.push(e.message); });
  await page.route("http://romp.test/**", (route: any) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE }));
  await page.goto("http://romp.test/timeline");
  await page.evaluate((data: any) => {
    const p = (window as any).panel;
    p.update(data);
    p.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 1000 });
    p._openViewsDialog(null);
  }, dataFor(nTags, nSessions, liveN, tagsOnS1));
  return { page, errors };
}

// the dialog's geometry, read off the live nodes: the card, the tag table, the open matrix's cell (null when
// folded), the sessions box, the search box, "tag all", [+ New tag], the tag rows' pill cells and the session
// rows' name cells. The boxes are found by their STRUCTURE (the sessions box is the sessions grid's parent, the
// matrix cell the parent of the "All surfaces" row, which only the open matrix draws), never by the flex
// declaration under test: a leg that found the box by "flex:1 1000 auto" crashed, rather than measured, when
// that declaration changed (review find, 2026-09-09); the declarations come back as strings to assert on
const measure = (page: any) => page.evaluate(() => {
  const p = (window as any).panel;
  const back = p._viewsDialog as HTMLElement;
  const card = back.firstElementChild as HTMLElement;
  const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
  const byStyle = (pre: string) => all.find((n) => (n.getAttribute("style") || "").startsWith(pre));
  const tgrid = byStyle("display:grid;grid-template-columns:max-content max-content max-content 1fr;")!;
  const allLabel = all.find((n) => n.textContent === "All surfaces" && (n.getAttribute("style") || "").includes("flex:0 0 88px"));
  const mbox = allLabel ? (allLabel.parentElement as HTMLElement).parentElement as HTMLElement : null;
  const grid = byStyle("display:grid;grid-template-columns:max-content max-content 1fr;")!;
  const gridBox = grid.parentElement as HTMLElement;
  const search = card.querySelector("input[placeholder]") as HTMLElement;
  const tagAll = all.find((n) => n.textContent === "tag all" && n.tagName === "SPAN")!;
  const newTag = all.find((n) => n.textContent === "+ New tag" && n.tagName === "SPAN")!;
  const r = (n: HTMLElement) => { const b = n.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, height: b.height, width: b.width }; };
  const inside = (n: HTMLElement, box: HTMLElement) => { const a = n.getBoundingClientRect(), b = box.getBoundingClientRect(); return a.top >= b.top - 0.5 && a.bottom <= b.bottom + 0.5 && a.height > 0; };
  const pillCells = all.filter((n) => (n as any)._tname);
  const nameCells = all.filter((n) => (n as any)._sid);
  const dots = all.filter((n) => n.dataset.tagDot);
  // a session row's TRACK: the extent of its cells (name, [+], chips), the kids from its name cell up to the next row's
  const gkids = (Array.from(grid.children) as HTMLElement[]).filter((n) => !(n.getAttribute("style") || "").startsWith("grid-column:1 / -1;"));
  const track = (i: number) => { const a = gkids.indexOf(nameCells[i]), b = nameCells[i + 1] ? gkids.indexOf(nameCells[i + 1]) : gkids.length; const rs = gkids.slice(a, b).map((n) => n.getBoundingClientRect()); return Math.max(...rs.map((r) => r.bottom)) - Math.min(...rs.map((r) => r.top)); };
  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    card: Object.assign(r(card), { scrollHeight: card.scrollHeight, clientHeight: card.clientHeight, scrollWidth: card.scrollWidth, clientWidth: card.clientWidth,
      overflowY: getComputedStyle(card).overflowY, overflowX: getComputedStyle(card).overflowX }),
    tgrid: Object.assign(r(tgrid), { scrollHeight: tgrid.scrollHeight, clientHeight: tgrid.clientHeight, overflowY: getComputedStyle(tgrid).overflowY }),
    mbox: mbox ? Object.assign(r(mbox), { scrollHeight: mbox.scrollHeight, clientHeight: mbox.clientHeight, overflowY: getComputedStyle(mbox).overflowY, style: mbox.getAttribute("style") || "" }) : null,
    gridBox: Object.assign(r(gridBox), { scrollHeight: gridBox.scrollHeight, clientHeight: gridBox.clientHeight, style: gridBox.getAttribute("style") || "",
      minHeight: (gridBox.getAttribute("style") || "").match(/min-height:([\d.]+)px/)![1] }),
    grid: r(grid),
    search: Object.assign(r(search), { visible: inside(search, card) }),
    tagAll: Object.assign(r(tagAll), { visible: inside(tagAll, card) }),
    newTag: Object.assign(r(newTag), { visible: inside(newTag, card), underTable: newTag.getBoundingClientRect().top >= tgrid.getBoundingClientRect().bottom - 0.5 }),
    tagRows: pillCells.length,
    tagRowHeights: pillCells.map((n) => Math.round(n.getBoundingClientRect().height)),
    tagRowsVisible: pillCells.filter((n) => inside(n, tgrid)).length,
    // the room under the last tag row inside the table's box: its 4px bottom padding when nothing is blank
    underLastTagRow: pillCells.length ? tgrid.getBoundingClientRect().bottom - Math.max(...pillCells.map((n) => n.getBoundingClientRect().bottom)) : null,
    tgridMinHeight: ((tgrid.getAttribute("style") || "").match(/min-height:([\d.]+)px/) || [null, null])[1],
    tgridFlex: ((tgrid.getAttribute("style") || "").match(/flex:([^;]+);/) || [null, ""])[1],
    dots: dots.length, swatches: card.querySelectorAll("[role=radio]").length,
    sessionRows: nameCells.length,
    sessionRowsVisible: nameCells.filter((n) => inside(n, gridBox)).length,
    sessionRowHeights: nameCells.slice(0, 3).map((n) => Math.round(n.getBoundingClientRect().height)),
    sessionTracks: nameCells.slice(0, 4).map((_, i) => track(i)),
    sessionPitch: nameCells.length > 1 ? nameCells[1].getBoundingClientRect().top - nameCells[0].getBoundingClientRect().top : 0,
  };
});

// click the "pane filters" caption (folded opens the matrix, open folds it; the choice holds for the page) and
// return the caret it shows afterwards
const toggleFilters = (page: any) => page.evaluate(() => {
  const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const cap = () => Array.from(card.querySelectorAll("span")).find((n) => (n.textContent || "").startsWith("pane filters"))!;
  cap().click();
  return (cap().textContent || "").slice(-1);
});
// the open matrix: its chips, whether every chip lies inside the card's width, the number of chip lines, and
// what the chips PAINT: a chip whose centre is inside the matrix cell's box is hit there (elementFromPoint
// finds it), one whose centre is past the box is clipped by the cell and hit nowhere. A cell without its
// bound and its own scroll let the chips print over the sessions table, and no geometry read caught it
// (review find, 2026-09-09: the assertions read the cell's scrollHeight, which overflow of any kind grows)
const matrix = (page: any) => page.evaluate(() => {
  const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const chips = Array.from(card.querySelectorAll("span")).filter((n) => { const s = n.getAttribute("style") || ""; return s.startsWith("display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:9px;font-size:0.82em;border:1px solid ") && s.includes("cursor:pointer;"); });   // the shared chip plus the pointer (T321)
  const cb = card.getBoundingClientRect();
  const rows = new Set(chips.map((n) => Math.round(n.getBoundingClientRect().top)));
  const allLabel = Array.from(card.querySelectorAll("span")).find((n) => n.textContent === "All surfaces" && (n.getAttribute("style") || "").includes("flex:0 0 88px"));
  const mb = allLabel ? ((allLabel.parentElement as HTMLElement).parentElement as HTMLElement).getBoundingClientRect() : null;
  let inBox = 0, inBoxHit = 0, outBox = 0, outBoxPainted = 0;
  for (const c of chips) {
    const b = c.getBoundingClientRect(), cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
    const hit = document.elementFromPoint(cx, cy);
    const painted = !!hit && c.contains(hit);
    if (mb && cy >= mb.top && cy <= mb.bottom && cx >= mb.left && cx <= mb.right) { inBox++; if (painted) inBoxHit++; }
    else { outBox++; if (painted) outBoxPainted++; }
  }
  return { chips: chips.length, withinWidth: chips.every((n) => { const b = n.getBoundingClientRect(); return b.left >= cb.left && b.right <= cb.right + 0.5; }), lines: rows.size,
    inBox, inBoxHit, outBox, outBoxPainted };
});

// the working area on screen without scrolling the dialog: the card holds its content, the search box, "tag
// all" and [+ New tag] are inside it, and at least `sessions` session rows and `tagRows` tag rows show
function assertWorkingArea(m: any, label: string, want: { sessions: number; tagRows: number }) {
  assert.ok(m.card.scrollHeight <= m.card.clientHeight + 1, label + ": the card holds its content without scroll: " + m.card.scrollHeight + " vs " + m.card.clientHeight);
  assert.ok(m.search.visible, label + ": the search box is on screen (" + Math.round(m.search.top) + ".." + Math.round(m.search.bottom) + " in the card " + Math.round(m.card.top) + ".." + Math.round(m.card.bottom) + ")");
  assert.ok(m.tagAll.visible, label + ": tag all is on screen (" + Math.round(m.tagAll.top) + ".." + Math.round(m.tagAll.bottom) + ")");
  assert.ok(m.newTag.visible, label + ": New tag is on screen (" + Math.round(m.newTag.top) + ".." + Math.round(m.newTag.bottom) + ")");
  assert.ok(m.sessionRowsVisible >= want.sessions, label + ": at least " + want.sessions + " session rows show without scrolling the dialog: " + m.sessionRowsVisible + " (box " + Math.round(m.gridBox.height) + "px, rows " + m.sessionRowHeights.join("/") + "px)");
  assert.ok(m.tagRowsVisible >= want.tagRows, label + ": at least " + want.tagRows + " tag rows show before scrolling the table: " + m.tagRowsVisible + " (table " + Math.round(m.tgrid.height) + "px)");
}

// the tag table, re-found after a repaint (the build makes a new element)
const TGRID_PRE = "display:grid;grid-template-columns:max-content max-content max-content 1fr;";

// THE REORDER DRAG, with real pointer events. `grabPill` records the order writes (the page has no host, so the
// hook is the write's only outlet), puts the mouse on a tag's pill and presses, and reads, BEFORE any cue is
// drawn, the place of the last row with room for a cue (its box plus 2px inside the table's box; a cue
// grows its cell 2px at the bottom and shifts the rows under it, so that place read with a cue drawn agreed with
// whatever the shift had produced); `cueOf` reads the cue: which pill cell wears it (its place in the table as
// drawn), on which edge, whether the cell, its 2px cue included, lies inside the table's box (a scroll container
// clips at its box, so a cue outside it is not drawn), and the table's scroll
const grabPill = async (page: any, name: string) => {
  const at = await page.evaluate(({ pre, name }: { pre: string; name: string }) => {
    const w = window as any;
    w.__writes = w.__writes || [];
    w.__rompTimelineSetViews = (v: any) => { w.__writes.push(JSON.parse(JSON.stringify(v))); };
    const card = (w.panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
    const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
    const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
    const cells = all.filter((n) => (n as any)._tname);
    const pill = cells.find((n) => (n as any)._tname === name)!.firstElementChild as HTMLElement;
    const b = pill.getBoundingClientRect(), t = tgrid.getBoundingClientRect();
    let lastWithRoom = -1;
    cells.forEach((c, k) => { const r = c.getBoundingClientRect(); if (r.top >= t.top - 0.01 && r.bottom + 2 <= t.bottom + 0.01) lastWithRoom = k; });
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, box: { top: t.top, bottom: t.bottom }, lastWithRoom, rows: cells.length, scrollTop: tgrid.scrollTop };
  }, { pre: TGRID_PRE, name });
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  return at;
};
const cueOf = (page: any) => page.evaluate((pre: string) => {
  const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
  const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
  const cells = all.filter((n) => (n as any)._tname);
  const t = tgrid.getBoundingClientRect();
  const i = cells.findIndex((c) => c.style.borderBottom || c.style.borderTop);
  const r = i >= 0 ? cells[i].getBoundingClientRect() : null;
  return { i, side: i < 0 ? "" : cells[i].style.borderBottom ? "bottom" : "top", cell: r ? { top: r.top, bottom: r.bottom } : null, box: { top: t.top, bottom: t.bottom },
    inside: !!r && r.top >= t.top - 0.01 && r.bottom <= t.bottom + 0.01, scrollTop: tgrid.scrollTop, rows: cells.length };
}, TGRID_PRE);
// the table scrolled to its end, and the first row whole there (the row to grab for a drag at the end)
const scrollTableToEnd = (page: any) => page.evaluate((pre: string) => {
  const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
  const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
  tgrid.scrollTop = tgrid.scrollHeight - tgrid.clientHeight;
  const t = tgrid.getBoundingClientRect();
  const whole = all.filter((n) => (n as any)._tname).find((c) => { const r = c.getBoundingClientRect(); return r.top >= t.top - 0.01 && r.bottom + 2 <= t.bottom + 0.01; });
  return { name: whole ? (whole as any)._tname as string : null, scrollTop: tgrid.scrollTop, max: tgrid.scrollHeight - tgrid.clientHeight };
}, TGRID_PRE);
// the table scrolled back to its start, and the rows with room for a cue there: the first (the row a top cue
// lands on) and the last (the row to grab for a drag above the table)
const scrollTableToStart = (page: any) => page.evaluate((pre: string) => {
  const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
  const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
  tgrid.scrollTop = 0;
  const t = tgrid.getBoundingClientRect();
  const room = all.filter((n) => (n as any)._tname).map((c, k) => ({ k, name: (c as any)._tname as string, r: c.getBoundingClientRect() }))
    .filter((p) => p.r.top >= t.top - 0.01 && p.r.bottom + 2 <= t.bottom + 0.01);
  const last = room[room.length - 1];
  return { firstWithRoom: room.length ? room[0].k : -1, lastWithRoom: last ? last.k : -1, name: last ? last.name : null, scrollTop: tgrid.scrollTop };
}, TGRID_PRE);
// with a cue drawn on row `i`, the table scrolled so that row's top edge sits 1.5px above the box (the row cut by
// the clip, its cue with it); a counting scroll listener goes on the table first, and the scrollTop the browser
// applied (an integer in both) comes back
const scrollCuedRowUnderClip = (page: any, i: number) => page.evaluate(({ pre, i }: { pre: string; i: number }) => {
  const w = window as any;
  const card = (w.panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
  const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
  const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
  const cell = all.filter((n) => (n as any)._tname)[i];
  w.__tgridScrolls = 0;
  tgrid.addEventListener("scroll", () => { w.__tgridScrolls++; }, { passive: true });
  tgrid.scrollTop = tgrid.scrollTop + (cell.getBoundingClientRect().top - tgrid.getBoundingClientRect().top) + 1.5;
  return tgrid.scrollTop as number;
}, { pre: TGRID_PRE, i });
const tgridScrolls = (page: any) => page.evaluate(() => (window as any).__tgridScrolls as number);
const lastOrder = (page: any) => page.evaluate(() => { const w = (window as any).__writes || []; return w.length ? w[w.length - 1].tagOrder as string[] : null; });

for (const name of ["chromium", "firefox"]) {
  const launch = async (t: any) => {
    if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser legs need it"); return null; }
    try { return await pw[name].launch(); }
    catch (e) { t.skip("no playwright " + name + " on this machine; this leg needs it (npx playwright install " + name + "): " + String((e as Error).message).split("\n")[0]); return null; }
  };

  test(`in ${name}, the dialog across pages: 1300, 800 and 700px tall and a phone held sideways, folded and with the matrix open`, async (t) => {
    const browser = await launch(t);
    if (!browser) return;
    try {
      // 1600x1300, thirty tags: the page the user asked about
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 1300, nTags: 30 });
        const m = await measure(page);
        assert.deepEqual(errors, [], "the view mounted and the dialog opened without a page error");
        assert.equal(m.viewport.h, 1300);
        // the tag rows: thirty, one line each
        assert.equal(m.tagRows, 30); assert.equal(m.dots, 30); assert.equal(m.swatches, 0, "no inline swatch");
        assert.ok(m.tagRowHeights.every((h: number) => h <= 32), "every tag row is one line (at most 32px): " + m.tagRowHeights.join(","));
        // the tag table scrolls within itself and stays within its viewport share
        assert.equal(m.tgrid.overflowY, "auto");
        assert.ok(m.tgrid.scrollHeight > m.tgrid.clientHeight + 100, "thirty rows overflow the capped table, which scrolls them: " + m.tgrid.scrollHeight + " in " + m.tgrid.clientHeight);
        // the cap bounds the content box; the 8px of vertical padding (room for the pills' rings inside the clip) sits outside it
        assert.ok(m.tgrid.height <= 0.30 * m.viewport.h + 8 + 1, "the table takes at most 30vh plus its 8px padding: " + m.tgrid.height + " vs " + (0.30 * m.viewport.h + 8));
        assert.ok(m.tagRowsVisible >= 8, "a useful number of tag rows shows before scrolling: " + m.tagRowsVisible);
        // the card never scrolls or clips as a whole: its content fits its box
        assert.ok(m.card.scrollHeight <= m.card.clientHeight + 1, "the card holds its content without scroll: " + m.card.scrollHeight + " vs " + m.card.clientHeight);
        assert.ok(m.card.scrollWidth <= m.card.clientWidth + 1, "…and nothing overflows it sideways: " + m.card.scrollWidth + " vs " + m.card.clientWidth);
        assert.ok(m.card.height <= 0.9 * m.viewport.h + 1, "the card keeps the 90vh ceiling: " + m.card.height);
        // the working area: search, tag all and at least eight session rows on screen without scrolling the dialog
        assert.ok(m.search.visible, "the search box is on screen: " + Math.round(m.search.top) + ".." + Math.round(m.search.bottom) + " in the card " + Math.round(m.card.top) + ".." + Math.round(m.card.bottom));
        assert.ok(m.tagAll.visible, "tag all is on screen: " + Math.round(m.tagAll.top) + ".." + Math.round(m.tagAll.bottom));
        assert.equal(m.sessionRows, 40);
        assert.ok(m.sessionRowsVisible >= 8, "at least eight session rows show without scrolling the dialog: " + m.sessionRowsVisible + " (box " + Math.round(m.gridBox.height) + "px, rows " + m.sessionRowHeights.join("/") + "px)");
        assert.ok(m.gridBox.bottom <= m.card.bottom + 0.5, "the sessions table ends inside the card; it scrolls the rest: " + m.gridBox.bottom + " vs " + m.card.bottom);
        assert.match(m.gridBox.style, /^flex:1 1000 auto;min-height:[\d.]+px;overflow-y:auto;$/, "the sessions box gives way first (the thousandfold shrink) down to its floor: " + m.gridBox.style);
        assert.equal(m.card.overflowY, "auto", "the card scrolls, never clips, past the floors");
        // [+ New tag]: on screen, and under the table rather than its last row (thirty rows would scroll it under the fold)
        assert.ok(m.newTag.visible, "New tag is on screen: " + Math.round(m.newTag.top) + ".." + Math.round(m.newTag.bottom) + " in the card " + Math.round(m.card.top) + ".." + Math.round(m.card.bottom));
        assert.ok(m.newTag.underTable, "New tag sits under the table, not inside it: its top " + m.newTag.top + " vs the table's bottom " + m.tgrid.bottom);
        // the filter matrix open: five rows of thirty-two chips wrap inside the card's width, and the card still fits
        assert.equal(await toggleFilters(page), "▾");
        const opened = await matrix(page);
        assert.equal(opened.chips, 5 * 32, "All, no tags and thirty tags on each of five rows");
        assert.ok(opened.withinWidth, "every chip lies inside the card's width (the rows wrap)");
        assert.ok(opened.lines > 5, "thirty-two chips per row wrap onto more than one line at this width: " + opened.lines);
        const after = await measure(page);
        assert.ok(after.mbox, "the open matrix lives in a cell of its own");
        assert.equal(after.mbox.style, "flex:0 1 auto;min-height:52px;max-height:25vh;overflow-y:auto;overflow-x:hidden;", "bounded to 25vh, scrolling within itself, giving way past the sessions' floor");
        assert.equal(after.mbox.overflowY, "auto", "the cell's rendered overflow is a scroll");
        assert.ok(after.mbox.bottom <= after.gridBox.top, "the cell ends above the sessions box: " + after.mbox.bottom + " vs " + after.gridBox.top);
        assert.ok(after.card.scrollHeight <= after.card.clientHeight + 1, "the card still holds its content without scroll with the matrix open: " + after.card.scrollHeight + " vs " + after.card.clientHeight);
        assert.ok(after.sessionRowsVisible >= 8, "the sessions keep at least eight rows on screen with the matrix open: " + after.sessionRowsVisible + " (box " + Math.round(after.gridBox.height) + "px, matrix " + Math.round(after.mbox.height) + "px)");
        assert.ok(after.newTag.visible, "New tag stays on screen with the matrix open");
        assert.equal(await toggleFilters(page), "▸");
        assert.equal((await measure(page)).mbox, null, "folded again: no matrix cell");
        assert.deepEqual(errors, []);
        await page.close();
      }
      // 1600x800, thirty tags: the sessions give way first, down to their floor of four rows; the matrix's cell scrolls
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 800, nTags: 30 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.viewport.h, 800);
        assertWorkingArea(m, "800px folded, thirty tags", { sessions: 4, tagRows: 5 });
        // THE REORDER DRAG past the table's edge (review find, 2026-09-09, with real pointer events: at this
        // height the row whose centre was inside the box but whose bottom edge was under the clip took the cue
        // and the drop, and the cue, a border on that edge, painted under the clip, invisible): the cue sits on
        // the last row with room for it, the cued cell lies inside the table's box cue included, and the drop
        // lands where the cue is
        const g1 = await grabPill(page, "tag-01");
        await page.mouse.move(g1.x, g1.box.bottom + 60, { steps: 4 });
        const c1 = await cueOf(page);
        assert.ok(c1.i > 0, "800px: a drag 60px below the table draws a cue: " + JSON.stringify(c1));
        assert.equal(c1.side, "bottom", "800px: the cue is the cell's bottom edge");
        assert.ok(c1.inside, "800px: the cued cell, its 2px cue included, lies inside the table's box: cell " + c1.cell!.top + ".." + c1.cell!.bottom + " in " + c1.box.top + ".." + c1.box.bottom);
        assert.equal(c1.i, g1.lastWithRoom, "800px: the cue sits on the LAST row with room for it, as read before any cue (a later row would be cut, or its cue would): row " + c1.i + " of " + c1.rows);
        assert.ok(g1.lastWithRoom < g1.rows - 1, "800px: thirty rows overflow the table, so the last row with room is not the last row: " + g1.lastWithRoom);
        await page.mouse.up();
        const o1 = await lastOrder(page);
        assert.ok(o1, "800px: the drop wrote the order");
        assert.equal(o1!.indexOf("tag-01"), c1.i, "800px: the drop landed where the cue was drawn");
        // a wheel mid-drag: no pointermove fires for a scroll, so the table's scroll re-ranks with the last
        // pointer y, and the cue and the drop follow the row now under the pointer
        const g2 = await grabPill(page, "tag-01");
        const midY = (g2.box.top + g2.box.bottom) / 2;
        await page.mouse.move(g2.x, midY, { steps: 3 });
        const c2 = await cueOf(page);
        assert.ok(c2.i >= 0 && c2.inside, "800px: the pointer at the table's middle cues a whole row: " + JSON.stringify(c2));
        await page.mouse.wheel(0, 200);
        await twoFrames(page);
        const c3 = await cueOf(page);
        assert.ok(c3.scrollTop > 100, "800px: the wheel scrolled the table under the held pill: " + c3.scrollTop);
        assert.notEqual(c3.i, c2.i, "800px: the cue moved with the rows: row " + c2.i + " before the wheel, " + c3.i + " after");
        assert.ok(c3.inside, "800px: the cued cell after the wheel lies inside the table's box: " + JSON.stringify(c3));
        assert.ok(c3.cell!.top <= midY && c3.cell!.bottom + 30 >= midY, "800px: the cue is on the row under the pointer or its neighbour: cell " + c3.cell!.top + ".." + c3.cell!.bottom + ", pointer " + midY);
        await page.mouse.up();
        const o2 = await lastOrder(page);
        assert.equal(o2!.indexOf("tag-01"), c3.i, "800px: the drop followed the rows the wheel brought under the pointer");
        // scrolled to the END (the last row has the 4px padding of room; a stepped drag carried the cue over
        // the rows above it, each cue pushing the last row 2px down, and measured with the cue drawn the last row
        // lost its room, so in Firefox the drop landed one row short at every height): a whole row dragged 60px
        // below the table in steps cues the LAST row and drops there
        const end = await scrollTableToEnd(page);
        await twoFrames(page);
        assert.ok(end.name && end.scrollTop > 100 && end.scrollTop === end.max, "800px at the end: the table scrolled to its end: " + JSON.stringify(end));
        const g4 = await grabPill(page, end.name!);
        assert.equal(g4.lastWithRoom, g4.rows - 1, "800px at the end: the last row has room for a cue (the padding under it): " + JSON.stringify(g4));
        await page.mouse.move(g4.x, g4.box.bottom + 60, { steps: 4 });
        const c4 = await cueOf(page);
        assert.equal(c4.i, c4.rows - 1, "800px at the end: a stepped drag past the table's edge cues the last row: " + JSON.stringify(c4));
        assert.equal(c4.side, "bottom");
        assert.ok(c4.inside, "800px at the end: the cued last row, its cue included, lies inside the box: " + JSON.stringify(c4));
        await page.mouse.up();
        const o4 = await lastOrder(page);
        assert.equal(o4!.indexOf(end.name!), c4.rows - 1, "800px at the end: the drop put the row last");
        // A CUED ROW SCROLLED UNDER THE TOP CLIP (the table's overflow-anchor:none had no test at first, and a view
        // without it passed every leg). With scroll anchoring on, a top cue leaving the first partly clipped row
        // moves that row's pill 2px, the browser shifts scrollTop by 2 to hold it, the lift's exact measurement
        // re-admits the row, and the cue and the scroll oscillate: Chromium kept the cue on a row whose top edge,
        // its cue with it, was under the clip, and Firefox fired a scroll event a frame at a standing scrollTop
        // before settling. From the top, the last whole row held with the pointer 60px above the table cues the
        // first row's top edge; the table then scrolled 1.5px past that row's top hands the cue to the next row,
        // with the scroll exactly where it was put and one scroll event, and the drop lands at the cue
        const start = await scrollTableToStart(page);
        await twoFrames(page);
        assert.ok(start.name && start.scrollTop === 0 && start.firstWithRoom >= 0 && start.lastWithRoom > start.firstWithRoom + 1, "800px from the top: the table at its start, a first whole row and a later one to grab: " + JSON.stringify(start));
        const g5 = await grabPill(page, start.name!);
        await page.mouse.move(g5.x, g5.box.top - 60, { steps: 3 });
        const c5 = await cueOf(page);
        assert.equal(c5.i, start.firstWithRoom, "800px from the top: a drag 60px above the table cues the first whole row: " + JSON.stringify(c5));
        assert.equal(c5.side, "top", "800px from the top: the cue is the cell's top edge");
        assert.ok(c5.inside, "800px from the top: the cued first row, its cue included, lies inside the box: " + JSON.stringify(c5));
        const applied = await scrollCuedRowUnderClip(page, c5.i);
        assert.ok(applied > 0, "800px from the top: the table scrolled the cued row under its clip: " + applied);
        await twoFrames(page);
        const c6 = await cueOf(page);
        const scrolls = await tgridScrolls(page);
        assert.equal(c6.i, c5.i + 1, "800px from the top, the cued row scrolled under the clip: the cue moves to the next row (with scroll anchoring on, the lift moved the row's pill and the browser moved the scroll to follow it, so the rows were read 2px off and the cue stayed on a row whose top edge, its cue with it, was under the clip): " + JSON.stringify(c6));
        assert.equal(c6.side, "top", "800px from the top: the cue keeps to the top edge");
        assert.ok(c6.inside, "800px from the top: the cued next row, its cue included, lies inside the box: " + JSON.stringify(c6));
        assert.equal(c6.scrollTop, applied, "800px from the top: the scroll stays exactly where it was put, no anchoring shift (overflow-anchor:none): " + c6.scrollTop + " vs " + applied);
        assert.equal(scrolls, 1, "800px from the top: one scroll event, the one the set fired (with anchoring on, Firefox fired one a frame at a standing scrollTop): " + scrolls);
        await page.mouse.up();
        const o5 = await lastOrder(page);
        assert.equal(o5!.indexOf(start.name!), c6.i, "800px from the top: the drop landed where the cue was drawn after the scroll");
        await page.evaluate((pre: string) => {
          const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
          (Array.from(card.querySelectorAll("*")) as HTMLElement[]).find((n) => (n.getAttribute("style") || "").startsWith(pre))!.scrollTop = 0;
        }, TGRID_PRE);
        await twoFrames(page);
        assert.deepEqual(errors, [], "800px: no page error across the drag legs");
        assert.equal(await toggleFilters(page), "▾");
        const o = await measure(page);
        assertWorkingArea(o, "800px open, thirty tags", { sessions: 4, tagRows: 3 });
        assert.ok(o.mbox, "800px open: the matrix cell exists");
        assert.ok(o.mbox.scrollHeight > o.mbox.clientHeight + 20, "800px open: the matrix's cell scrolls within itself: " + o.mbox.scrollHeight + " in " + o.mbox.clientHeight);
        assert.equal(o.mbox.overflowY, "auto", "800px open: the cell's rendered overflow is a scroll, not visible");
        assert.ok(o.mbox.clientHeight <= 0.25 * o.viewport.h + 1, "800px open: the cell keeps to 25vh: " + o.mbox.clientHeight + " vs " + 0.25 * o.viewport.h);
        assert.ok(o.mbox.bottom <= o.gridBox.top, "800px open: the cell ends above the sessions box: " + o.mbox.bottom + " vs " + o.gridBox.top);
        const painted = await matrix(page);
        assert.ok(painted.inBox > 0 && painted.inBoxHit === painted.inBox, "800px open: every chip inside the cell's box is painted there: " + painted.inBoxHit + " of " + painted.inBox);
        assert.ok(painted.outBox > 0, "800px open: chips past the cell's box, for the cell to clip: " + painted.outBox);
        assert.equal(painted.outBoxPainted, 0, "800px open: no chip past the cell's box is painted (the cell clips them; without its bound and scroll they printed over the sessions): " + painted.outBoxPainted + " of " + painted.outBox);
        assert.equal(await toggleFilters(page), "▸");
        assert.deepEqual(errors, []);
        await page.close();
      }
      // 1600x800, ten tags: the user's own count; the table's cap held exactly the ten rows and hid [+ New tag] as its last row
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 800, nTags: 10 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.tagRows, 10);
        assertWorkingArea(m, "800px folded, ten tags", { sessions: 4, tagRows: 5 });
        assert.ok(m.newTag.underTable, "800px, ten tags: New tag sits under the table: " + m.newTag.top + " vs " + m.tgrid.bottom);
        assert.deepEqual(errors, []);
        await page.close();
      }
      // 1600x700, thirty tags: the table and the open matrix give way down to their floors; nothing clips
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 700, nTags: 30 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.viewport.h, 700);
        assertWorkingArea(m, "700px folded, thirty tags", { sessions: 4, tagRows: 3 });
        assert.equal(await toggleFilters(page), "▾");
        const o = await measure(page);
        assertWorkingArea(o, "700px open, thirty tags", { sessions: 4, tagRows: 3 });
        assert.ok(o.mbox, "700px open: the matrix cell exists");
        assert.equal(o.mbox.overflowY, "auto", "700px open: the cell scrolls within itself");
        assert.equal((await matrix(page)).outBoxPainted, 0, "700px open: no chip past the cell's box is painted");
        assert.equal(await toggleFilters(page), "▸");
        assert.deepEqual(errors, []);
        await page.close();
      }
      // THE FLOORS, measured off the rendered rows (review find, 2026-09-09: a constant per row was not the
      // row height, which is the font's: one tag showed 6px of blank, three rows at the floor lost 7px, one live
      // session sat in a 96px box). At 1300 one tag and three tags show their rows and nothing else: no scroll,
      // and under the last row only the table's own 4px padding
      for (const nTags of [1, 3]) {
        const { page, errors } = await mount(browser, { width: 1600, height: 1300, nTags });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.tagRowsVisible, nTags, nTags + " tag(s): every row whole");
        assert.equal(m.tgridFlex, "0 0 auto", nTags + " tag(s): the table does not shrink; its rows are its height");
        assert.equal(m.tgridMinHeight, null, nTags + " tag(s): no floor to be off by");
        assert.ok(m.tgrid.scrollHeight <= m.tgrid.clientHeight + 0.5, nTags + " tag(s): no scroll: " + m.tgrid.scrollHeight + " in " + m.tgrid.clientHeight);
        assert.ok(m.underLastTagRow! <= 4.5 && m.underLastTagRow! >= 3.5, nTags + " tag(s): under the last row only the 4px padding, no blank: " + m.underLastTagRow);
        await page.close();
      }
      // at 420 with forty sessions the floors bind: three tags still show three whole rows and no scroll; thirty
      // tags keep three whole rows (the floor is three rows at their rendered height, not a constant)
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 420, nTags: 3 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.ok(m.card.scrollHeight > m.card.clientHeight, "420px, three tags: the page is short enough for the card to scroll (the floors bind): " + m.card.scrollHeight + " vs " + m.card.clientHeight);
        assert.equal(m.tagRowsVisible, 3, "420px, three tags: three whole rows (table " + Math.round(m.tgrid.height) + "px)");
        assert.ok(m.tgrid.scrollHeight <= m.tgrid.clientHeight + 0.5, "420px, three tags: no scroll: " + m.tgrid.scrollHeight + " in " + m.tgrid.clientHeight);
        await page.close();
      }
      {
        const { page, errors } = await mount(browser, { width: 1600, height: 420, nTags: 30 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.ok(m.tgrid.scrollHeight > m.tgrid.clientHeight, "420px, thirty tags: the table scrolls");
        assert.equal(m.tagRowsVisible, 3, "420px, thirty tags: the floor holds exactly three whole rows (table " + Math.round(m.tgrid.height) + "px, floor " + m.tgridMinHeight + "px)");
        assert.ok(Number(m.tgridMinHeight) >= 3 * 24 + 8 - 1 && Number(m.tgridMinHeight) <= 3 * 32 + 8, "420px, thirty tags: the floor is three rows at their rendered height: " + m.tgridMinHeight);
        await page.close();
      }
      // the sessions box holds its live rows and no blank: none when none is live, one row for one, four for four
      // or more (at 500 with thirty tags the floors bind, so the box sits at its floor)
      // (two live of six: the floor counts the live sessions, not every session; by every session the box held four
      // rows' worth over two rows of content)
      for (const [liveN, nSessions, label] of [[0, 3, "none live of three"], [1, 1, "one live"], [2, 6, "two live of six"], [3, 3, "three live"], [4, 4, "four live"], [40, 40, "forty live"]] as Array<[number, number, string]>) {
        const { page, errors } = await mount(browser, { width: 1600, height: 500, nTags: 30, nSessions, liveN });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.sessionRows, liveN, label + ": the live rows");
        const want = Math.min(liveN, 4);
        assert.equal(m.sessionRowsVisible, want, label + ": " + want + " whole rows in the box (box " + m.gridBox.height + "px, grid " + m.grid.height + "px)");
        if (liveN === 0) assert.ok(m.gridBox.height <= 0.5, label + ": no row, no box: " + m.gridBox.height);
        else if (liveN <= 4) assert.ok(Math.abs(m.gridBox.height - m.grid.height) <= 0.5, label + ": the box is exactly its rows, no blank and no clip: box " + m.gridBox.height + ", rows " + m.grid.height);
        else assert.ok(Math.abs(m.gridBox.height - (4 * m.sessionPitch - 3)) <= 0.5, label + ": at its floor the box is four rows and their three 3px gaps: box " + m.gridBox.height + ", pitch " + m.sessionPitch);
        assert.ok(m.search.visible && m.tagAll.visible, label + ": search and tag all inside the card");
        await page.close();
      }
      // a first session whose chips WRAP (the floor was four times the FIRST row's height at first, so a wrapped first
      // row put 74px of blank under four live sessions): at a 390px page the card is
      // 351px wide and six tags on the first session wrap its chips onto a second line. The floor is four of the
      // SMALLEST rows plus their gaps: on a tall page the box is its rows, and on a page short enough for the floors
      // to bind the box sits at that floor, holding three whole rows (the wrapped one costs a row, never blank)
      for (const [height, binds] of [[844, false], [400, true]] as Array<[number, boolean]>) {
        const label = "390x" + height + ", the first session in six tags";
        const { page, errors } = await mount(browser, { width: 390, height, nTags: 30, nSessions: 4, liveN: 4, tagsOnS1: 6 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.equal(m.sessionRows, 4);
        assert.ok(m.card.width <= 352 && m.card.width >= 350, label + ": the card is 90vw, 351px: " + m.card.width);
        assert.ok(m.sessionTracks[0] >= m.sessionTracks[1] + 10, label + ": the first row's chips wrapped, so its track is taller: " + m.sessionTracks.map((t: number) => Math.round(t * 10) / 10).join("/"));
        const small = Math.min(...m.sessionTracks);
        assert.ok(Math.abs(Number(m.gridBox.minHeight) - (4 * small + 9)) <= 0.1, label + ": the floor is four of the smallest rows and their three 3px gaps: " + m.gridBox.minHeight + " vs " + (4 * small + 9));
        assert.ok(m.gridBox.height <= m.grid.height + 0.5, label + ": no blank under the rows: box " + m.gridBox.height + ", rows " + m.grid.height);
        if (binds) {
          assert.ok(m.card.scrollHeight > m.card.clientHeight, label + ": the floors bind (the card scrolls): " + m.card.scrollHeight + " vs " + m.card.clientHeight);
          assert.ok(Math.abs(m.gridBox.height - Number(m.gridBox.minHeight)) <= 0.5, label + ": the box sits at its floor: " + m.gridBox.height + " vs " + m.gridBox.minHeight);
          assert.equal(m.sessionRowsVisible, 3, label + ": three whole rows in a box of four small rows' worth (the wrapped row costs one)");
        } else {
          assert.ok(Math.abs(m.gridBox.height - m.grid.height) <= 0.5, label + ": the box is exactly its rows: box " + m.gridBox.height + ", rows " + m.grid.height);
          assert.equal(m.sessionRowsVisible, 4, label + ": all four rows whole");
        }
        await page.close();
      }
      // 844x390, a phone held sideways: the floors outgrow the card, which scrolls as a whole instead of clipping
      {
        const { page, errors } = await mount(browser, { width: 844, height: 390, mobile: true, nTags: 30 });
        const m = await measure(page);
        assert.deepEqual(errors, []);
        assert.ok(m.card.scrollHeight > m.card.clientHeight + 20, "phone: the card scrolls as a whole: " + m.card.scrollHeight + " vs " + m.card.clientHeight + " (viewport " + m.viewport.w + "x" + m.viewport.h + ")");
        assert.ok(m.search.visible, "phone: the search box is inside the card's box: " + Math.round(m.search.top) + ".." + Math.round(m.search.bottom) + " in " + Math.round(m.card.top) + ".." + Math.round(m.card.bottom));
        assert.ok(m.tagAll.visible, "phone: tag all is inside the card's box: " + Math.round(m.tagAll.top) + ".." + Math.round(m.tagAll.bottom));
        assert.ok(m.newTag.visible, "phone: New tag is inside the card's box: " + Math.round(m.newTag.top) + ".." + Math.round(m.newTag.bottom));
        assert.ok(m.tagRowsVisible >= 2, "phone: the table keeps at least two rows: " + m.tagRowsVisible + " (table " + Math.round(m.tgrid.height) + "px)");
        // the card's overflow as rendered: a scroll (a card that clipped, overflow hidden, still takes a scrollTop
        // written by a script, so the write below cannot tell the two apart; the computed style can)
        assert.equal(m.card.overflowY, "auto", "phone: the card's rendered overflow-y is a scroll, not a clip");
        assert.equal(m.card.overflowX, "hidden", "phone: ...and nothing scrolls sideways");
        const scrolled = await page.evaluate((pre: string) => {
          const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
          card.scrollTop = card.scrollHeight;
          const gridBox = (Array.from(card.querySelectorAll("*")) as HTMLElement[]).find((n) => (n.getAttribute("style") || "").startsWith(pre))!.parentElement as HTMLElement;
          return { scrollTop: card.scrollTop, cardBottom: card.getBoundingClientRect().bottom, sessionsBottom: gridBox.getBoundingClientRect().bottom, sessionsHeight: gridBox.getBoundingClientRect().height };
        }, "display:grid;grid-template-columns:max-content max-content 1fr;");
        assert.ok(scrolled.scrollTop > 0, "phone: the card took the scroll: " + scrolled.scrollTop);
        assert.ok(scrolled.sessionsBottom <= scrolled.cardBottom + 0.5, "phone: scrolled to the end, the sessions box is reachable inside the card: " + scrolled.sessionsBottom + " vs " + scrolled.cardBottom + " (box " + Math.round(scrolled.sessionsHeight) + "px)");
        assert.deepEqual(errors, []);
        await page.close();
      }
    } finally { await browser.close(); }
  });

  test(`in ${name}, the colour popover and the tag table at 1300px: the ring inside the clip, keyboard open and Tab, scroll, resize, scroll memory, repaint`, async (t) => {
    const browser = await launch(t);
    if (!browser) return;
    try {
      const { page, errors } = await mount(browser, { width: 1600, height: 1300, nTags: 30 });
      // opened FOR a tag, its pill wears a ring 3px outside its box; the table's clip must spare it
      const ring = await page.evaluate((pre: string) => {
        const p = (window as any).panel;
        p._openViewsDialog("g1");
        const card = (p._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const all = Array.from(card.querySelectorAll("*")) as HTMLElement[];
        const tgrid = all.find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
        const cell = all.find((n) => (n as any)._tname === "tag-01")!;
        const pill = cell.firstElementChild as HTMLElement;
        const b = pill.getBoundingClientRect(), tb = tgrid.getBoundingClientRect();
        const out = { ringed: (pill.getAttribute("style") || "").indexOf("outline:1px solid") >= 0, offset: getComputedStyle(pill).outlineOffset, left: b.left - tb.left, top: b.top - tb.top };
        p._openViewsDialog(null);
        return out;
      }, TGRID_PRE);
      assert.ok(ring.ringed, "the pill of the tag the dialog was opened for wears the ring");
      assert.equal(ring.offset, "2px");
      assert.ok(ring.left >= 3, "the ringed pill sits at least 3px inside the table's left edge, so the clip spares its ring: " + ring.left);
      assert.ok(ring.top >= 3, "…and at least 3px under the table's top edge: " + ring.top);
      assert.deepEqual(errors, [], "the dialog opened for g1 and reopened plain without a page error");
      // keyboard: Enter on the focused dot opens the popover on the current swatch, which wears the inner ring
      // AND the browser's focus ring; Tab closes it and puts focus back on the dot
      await page.evaluate(() => {
        const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        (card.querySelector("[data-tag-dot='g1']") as HTMLElement).focus();
      });
      await page.keyboard.press("Enter");
      await popFocused(page);   // the popover focuses its current swatch a tick after opening
      const kb = await page.evaluate(() => {
        const p = (window as any).panel;
        const pop = p._tagColorPop as HTMLElement | null;
        if (!pop) return { open: false, swatches: 0, focused: false, boxShadow: "", outlineStyle: "", inlineOutline: "", othersPlain: false };
        const sws = Array.from(pop.querySelectorAll("[role=radio]")) as HTMLElement[];
        const cur = pop.querySelector("[aria-checked=true]") as HTMLElement;
        const cs = getComputedStyle(cur);
        return { open: true, swatches: sws.length, focused: document.activeElement === cur, boxShadow: cs.boxShadow, outlineStyle: cs.outlineStyle, inlineOutline: cur.style.outline,
          othersPlain: sws.filter((s) => s !== cur).every((s) => getComputedStyle(s).boxShadow === "none" && s.style.outline === "") };
      });
      assert.ok(kb.open, "Enter on the focused dot opens the popover");
      assert.equal(kb.swatches, 12);
      assert.ok(kb.focused, "the current swatch took focus");
      assert.notEqual(kb.boxShadow, "none", "the current swatch wears the inner ring (two inset shadows): " + kb.boxShadow);
      assert.equal(kb.inlineOutline, "", "…and no inline outline, which would replace the browser's focus ring");
      assert.notEqual(kb.outlineStyle, "none", "the keyboard-focused swatch shows the browser's focus ring: outline-style " + JSON.stringify(kb.outlineStyle));
      assert.ok(kb.othersPlain, "every other swatch: no box-shadow, no inline outline");
      await page.keyboard.press("Tab");
      const tabbed = await page.evaluate(() => {
        const p = (window as any).panel;
        const card = (p._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        return { open: p._tagColorPop !== null, onDot: document.activeElement === card.querySelector("[data-tag-dot='g1']") };
      });
      assert.equal(tabbed.open, false, "Tab closes the popover");
      assert.ok(tabbed.onDot, "…and puts focus back on the dot");
      // the last row's dot: the popover placed on screen, one layer above the backdrop, the current swatch focused
      const openOn = (key: string) => page.evaluate((k: string) => {
        const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const dot = card.querySelector("[data-tag-dot='" + k + "']") as HTMLElement;
        dot.scrollIntoView();
        dot.click();
      }, key);
      await openOn("g30");
      await popFocused(page);
      const popm = await page.evaluate(() => {
        const p = (window as any).panel;
        const card = (p._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const dot = card.querySelector("[data-tag-dot='g30']") as HTMLElement;
        const pop = p._tagColorPop as HTMLElement | null;
        if (!pop) return { open: false, swatches: 0, onScreen: false, nearDot: false, z: "", focused: false };
        const b = pop.getBoundingClientRect();
        const d = dot.getBoundingClientRect();
        return { open: true, swatches: pop.querySelectorAll("[role=radio]").length, onScreen: b.top >= 0 && b.bottom <= window.innerHeight && b.left >= 0 && b.right <= window.innerWidth,
          nearDot: Math.abs(b.top - d.bottom) <= 6 || Math.abs(d.top - b.bottom) <= 6, z: getComputedStyle(pop).zIndex, focused: document.activeElement === pop.querySelector("[aria-checked=true]") };
      });
      assert.ok(popm.open, "a click on the g30 dot opens the popover"); assert.equal(popm.swatches, 12);
      assert.ok(popm.onScreen, "the popover is on screen");
      assert.ok(popm.nearDot, "…anchored to its dot (below it, or above when below cannot hold it)");
      assert.equal(popm.z, "1003");
      assert.ok(popm.focused, "the current swatch took focus");
      // a table scroll that moves the dot closes it (the table sits at its end after scrolling the last row into
      // view, so the scroll goes up)
      const scrolled = await page.evaluate((pre: string) => {
        const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const tgrid = (Array.from(card.querySelectorAll("*")) as HTMLElement[]).find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
        const before = tgrid.scrollTop;
        tgrid.scrollTop = before - 120;
        return { before, after: tgrid.scrollTop };
      }, TGRID_PRE);
      await twoFrames(page);
      assert.notEqual(scrolled.after, scrolled.before, "the table scrolled: " + scrolled.before + " to " + scrolled.after);
      assert.equal(await page.evaluate(() => (window as any).panel._tagColorPop === null), true, "a table scroll that moved the dot closed the popover");
      // the host window's resize closes it
      await openOn("g30");
      await popFocused(page);
      assert.equal(await page.evaluate(() => (window as any).panel._tagColorPop !== null), true, "reopened on g30");
      await page.setViewportSize({ width: 1500, height: 1300 });
      await popClosed(page);
      assert.equal(await page.evaluate(() => (window as any).panel._tagColorPop === null), true, "the host window's resize closed the popover");
      await page.setViewportSize({ width: 1600, height: 1300 });
      // scroll memory: the table's scroll survives a repaint; a table that no longer scrolls clamps it to 0
      const mem = await page.evaluate(({ data, pre }: { data: any; pre: string }) => {
        const p = (window as any).panel;
        const card = () => (p._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const find = () => (Array.from(card().querySelectorAll("*")) as HTMLElement[]).find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
        const rows = () => (Array.from(card().querySelectorAll("*")) as HTMLElement[]).filter((n) => (n as any)._tname).length;
        const t1 = find();
        t1.scrollTop = 200;
        const got = t1.scrollTop;
        p._viewsDialogBuild();
        const t2 = find();
        const kept = { newTable: t2 !== t1, got, after: t2.scrollTop };
        p.update(Object.assign({}, data, { views: Object.assign({}, data.views, { seq: 1001, tags: data.views.tags.slice(0, 3) }) }));
        p._viewsDialogBuild();
        const t3 = find();
        const three = { rows: rows(), scrollTop: t3.scrollTop, scrollHeight: t3.scrollHeight, clientHeight: t3.clientHeight };
        p.update(Object.assign({}, data, { views: Object.assign({}, data.views, { seq: 1002 }) }));
        p._viewsDialogBuild();
        return { kept, three, rows30: rows() };
      }, { data: DATA, pre: TGRID_PRE });
      assert.ok(mem.kept.newTable, "the repaint built a new table");
      assert.ok(mem.kept.got > 0, "the table took the scroll: " + mem.kept.got);
      assert.ok(Math.abs(mem.kept.after - mem.kept.got) <= 1, "the table's scroll survives a repaint: " + mem.kept.got + " before, " + mem.kept.after + " after");
      assert.equal(mem.three.rows, 3, "the three-tag frame was adopted");
      assert.equal(mem.three.scrollTop, 0, "a table that no longer scrolls clamps its remembered scroll to 0: " + mem.three.scrollTop);
      assert.ok(mem.three.scrollHeight <= mem.three.clientHeight + 1, "three rows do not scroll: " + mem.three.scrollHeight + " in " + mem.three.clientHeight);
      assert.equal(mem.rows30, 30, "the thirty-tag frame was restored");
      // a repaint that pushes the rows down (a notice above the table) re-places the popover on its rebuilt dot
      await page.evaluate((pre: string) => {
        const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const tgrid = (Array.from(card.querySelectorAll("*")) as HTMLElement[]).find((n) => (n.getAttribute("style") || "").startsWith(pre))!;
        tgrid.scrollTop = 0;
      }, TGRID_PRE);
      await twoFrames(page);
      await page.evaluate(() => {
        const card = ((window as any).panel._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        (card.querySelector("[data-tag-dot='g3']") as HTMLElement).click();
      });
      await popFocused(page);
      const re = await page.evaluate(() => {
        const p = (window as any).panel;
        const pop = p._tagColorPop as HTMLElement | null;
        if (!pop) return { wasOpen: false, open: false, before: 0, after: 0, notice: false, gap: 0, sameNode: false };
        const before = pop.getBoundingClientRect().top;
        p._tagEditErr = { host: "", name: "", error: "synthetic refusal" };
        p._viewsDialogBuild();
        const card = (p._viewsDialog as HTMLElement).firstElementChild as HTMLElement;
        const notice = Array.from(card.querySelectorAll("span")).some((n) => (n.textContent || "").indexOf("synthetic refusal") >= 0);
        const pop2 = p._tagColorPop as HTMLElement | null;
        if (!pop2) return { wasOpen: true, open: false, before, after: 0, notice, gap: 0, sameNode: false };
        const dot = card.querySelector("[data-tag-dot='g3']") as HTMLElement;
        const b = pop2.getBoundingClientRect(), d = dot.getBoundingClientRect();
        return { wasOpen: true, open: true, before, after: b.top, notice, gap: Math.min(Math.abs(b.top - d.bottom), Math.abs(d.top - b.bottom)), sameNode: pop2 === pop };
      });
      assert.ok(re.wasOpen, "a click on the g3 dot opened the popover");
      assert.ok(re.notice, "the repaint drew the notice above the table");
      assert.ok(re.open, "the popover survives a repaint that rebuilt its row");
      assert.ok(re.sameNode, "…the same popover, re-placed");
      assert.notEqual(re.after, re.before, "…moved with its dot: top " + re.before + " before the notice, " + re.after + " after");
      assert.ok(re.gap <= 6, "…and hangs within 6px of the rebuilt dot: " + re.gap);
      await page.evaluate(() => { const p = (window as any).panel; p._tagEditErr = null; p._viewsDialogBuild(); });
      assert.deepEqual(errors, [], "no page error across the popover legs");
    } finally { await browser.close(); }
  });

  test(`in ${name}, framed shells: the dialog is adopted into the top document and the popover hangs on the dot's rect there`, async (t) => {
    const browser = await launch(t);
    if (!browser) return;
    try {
      const shells: Array<[string, string, string]> = [
        ["/shell-offset", SHELL_OFFSET, "the frame offset 300x400 into the page"],
        ["/shell-band", SHELL_BAND, "the frame a 200px band at the foot of a 1300px page"],
      ];
      for (const [shellPath, html, label] of shells) {
        const errors: string[] = [];
        const page = await browser.newPage({ viewport: { width: 1600, height: 1300 } });
        page.on("pageerror", (e: Error) => { errors.push(e.message); });
        await page.route("http://romp.test/**", (route: any) => {
          const url = String(route.request().url());
          const body = url.endsWith("/timeline") ? PAGE : url.endsWith(shellPath) ? html : null;
          if (body === null) return route.fulfill({ status: 404, contentType: "text/plain", body: "not here" });
          return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body });
        });
        await page.goto("http://romp.test" + shellPath);
        const frame = page.frame({ url: /\/timeline$/ });
        assert.ok(frame, label + ": the shell hosts the timeline frame");
        await frame.waitForFunction(() => !!(window as any).panel);
        const hosted = await frame.evaluate((data: any) => {
          const p = (window as any).panel;
          p.update(data);
          p.setCaps({ type: "caps", caps: ["tagEdit"], viewsSeq: 1000 });
          p._openViewsDialog(null);
          return { framed: window !== window.top, inTop: (p._viewsDialog as HTMLElement).ownerDocument === (window.top as Window).document, frameH: window.innerHeight, topH: (window.top as Window).innerHeight };
        }, DATA);
        assert.ok(hosted.framed, label + ": the view runs in a child frame");
        assert.ok(hosted.inTop, label + ": the dialog is adopted into the shell's document (frame " + hosted.frameH + "px tall in a " + hosted.topH + "px page)");
        await frame.evaluate(() => {
          const p = (window as any).panel;
          ((p._viewsDialog as HTMLElement).querySelector("[data-tag-dot='g1']") as HTMLElement).click();
        });
        await popFocused(frame, true);
        const fm = await frame.evaluate(() => {
          const p = (window as any).panel;
          const top = window.top as Window;
          const pop = p._tagColorPop as HTMLElement | null;
          const dot = (p._viewsDialog as HTMLElement).querySelector("[data-tag-dot='g1']") as HTMLElement;
          const d = dot.getBoundingClientRect();
          const rect = (b: DOMRect) => ({ top: b.top, bottom: b.bottom, left: b.left, right: b.right });
          if (!pop) return { open: false, swatches: 0, pop: rect(d), dot: rect(d), view: { w: top.innerWidth, h: top.innerHeight }, inTop: false, focused: false };
          return { open: true, swatches: pop.querySelectorAll("[role=radio]").length, pop: rect(pop.getBoundingClientRect()), dot: rect(d), view: { w: top.innerWidth, h: top.innerHeight },
            inTop: pop.ownerDocument === top.document, focused: top.document.activeElement === pop.querySelector("[aria-checked=true]") };
        });
        assert.ok(fm.open, label + ": a click on the g1 dot opens the popover");
        assert.equal(fm.swatches, 12);
        const gap = Math.min(Math.abs(fm.pop.top - fm.dot.bottom), Math.abs(fm.dot.top - fm.pop.bottom));
        assert.ok(gap <= 6, label + ": the popover hangs on the dot in the top document's coordinates: popover " + Math.round(fm.pop.top) + ".." + Math.round(fm.pop.bottom) + ", dot " + Math.round(fm.dot.top) + ".." + Math.round(fm.dot.bottom) + " (gap " + gap + ")");
        assert.ok(fm.pop.top >= 0 && fm.pop.left >= 0 && fm.pop.bottom <= fm.view.h && fm.pop.right <= fm.view.w,
          label + ": the popover is inside the top viewport " + fm.view.w + "x" + fm.view.h + ": " + Math.round(fm.pop.left) + "," + Math.round(fm.pop.top) + " to " + Math.round(fm.pop.right) + "," + Math.round(fm.pop.bottom));
        assert.ok(fm.inTop, label + ": the popover lives in the top document");
        assert.ok(fm.focused, label + ": the current swatch took focus in the top document");
        assert.deepEqual(errors, [], label + ": no page error");
        await page.close();
      }
    } finally { await browser.close(); }
  });
}
