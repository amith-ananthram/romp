// The session header's Clear (the user 2026-09-08): in grouped mode, each session header carries a Clear
// on the far right of its row that clears every clearable card of that session in the current view — every
// column, folded ones included — as one motion and ONE Undo batch, on the client (clearedStack) AND on the
// kernel (one askClearMany, one cleared.jsonl stamp). Only grouped mode renders headers, so the control
// exists only there. Source pins, in the style of the other feed tests, plus the router's handling of the
// batch message.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { routeOutbound } from "./federation";

const FEED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.ts"), "utf8");
const CSS = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "feed.css"), "utf8");
const FED = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "federation.ts"), "utf8");
const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");

test("the header's Clear IS the card's Clear: one builder, one class set, a layout-only position class", () => {
  // the user 2026-09-08 (twice): same size, the outline, blue on hover — so the card, the turn-group and the
  // header all build their Clear with clearButton(), which mints the .fdismiss button; the header adds only
  // a positional class and its behaviour
  assert.match(FEED, /function clearButton\(title: string, label = "Clear"\): HTMLElement \{\s*\n\s*const b = el\("button", "fdismiss"\);\s*\n\s*b\.textContent = label;[^\n]*\n\s*b\.title = title;/,
    "one builder; the label is the only knob (T271: the header's reads \"Clear all\")");
  assert.match(FEED, /const clr = clearButton\("clear this task"\);/, "the card");
  assert.match(FEED, /const clr = clearButton\("clear ALL sub-asks of this request \(inbox-zero\)"\);/, "the turn-group");
  assert.match(FEED, /const clr = clearButton\("clear every card for this session", "Clear all"\);[^\n]*\n\s*clr\.classList\.add\("feed-sess-clear"\); clr\.dataset\.act = "sess-clear";/, "the header");
  assert.doesNotMatch(FEED, /el\("button", "feed-sess-clear"\)/, "no lookalike element");
  // the tooltip keeps the sibling grammar: a lowercase verb phrase, like the card's "clear this task"
  assert.match(FEED, /sclr\.setAttribute\("aria-label", "clear every card for " \+ e\.name\);/);
  // after the caret, count and service chip; before the full-width process list that wraps below
  assert.match(FEED, /h\.append\(nm, fold, cnt, svc, clr, svcList\);/);
  assert.match(FEED, /sclr\.dataset\.fsid = e\.sid;\s*\n\s*const ncards = sessionCards\(e\.sid\)\.length;\s*\n\s*sclr\.style\.display = ncards \? "" : "none";/);
});

test("grouped mode only: headers (and so the control) are emitted under the grouped guard", () => {
  const guard = FEED.indexOf("if (feedPrefs().grouped) {\n    const rank = new Map(sessionOrder.map(");
  assert.ok(guard > 0, "the grouped-mode header build lives under the grouped guard");
  assert.match(FEED.slice(guard, guard + 2500), /head = \{ kind: "sess", t: e\.t, sid: s, col: k, name: src\.name/);
  assert.match(FEED, /function dressHeaderIfLast\(card: HTMLElement, sid: string\): void \{\s*\n\s*if \(!feedPrefs\(\)\.grouped\) return;/);
});

test("the click is delegated on the stable columns root, never bound to the re-rendered header", () => {
  assert.match(FEED, /import \{ delegate \} from "\.\/actions";/);
  const at = FEED.indexOf('const cols = el("div", "feed-cols"); cols.id = "feed-cols";');
  assert.ok(at > 0);
  assert.match(FEED.slice(at, at + 900), /delegate\(cols, \{\s*\n\s*"sess-clear": \(b, ev\) => \{ ev\.stopPropagation\(\); const sid = b\.dataset\.fsid; if \(sid\) clearSessionCards\(sid\); \},/);
  const head = FEED.slice(FEED.indexOf("function makeSessHead()"), FEED.indexOf("function updateSessHead("));
  assert.doesNotMatch(head, /clr\.(onclick|addEventListener)/, "no handler on the header's own node");
});

test("what it clears: the session's CLEARABLE cards in the current view — never a placeholder or a quarantine hold", () => {
  assert.match(FEED, /function clearable\(it: AskItem\): boolean \{\s*\n\s*return !it\.provisional && it\.blocked\?\.state !== "quarantine";/);
  assert.match(FEED, /function sessionCards\(sid: string\): AskItem\[\] \{\s*\n\s*return viewFiltered\(asks\)\.filter\(\(a\) => a\.sid === sid && clearable\(a\)\);/,
    "the current view's cards for the session — every column; folded cards are in the view too");
});

test("one click, one Undo batch on the client AND on the kernel, through the group-clear path", () => {
  const fn = FEED.slice(FEED.indexOf("function clearSessionCards(sid: string): void {"), FEED.indexOf("function reconcileCol("));
  assert.match(fn, /clearedStack\.push\(members\.slice\(\)\);/, "ONE client batch: one Undo restores the whole session");
  assert.match(fn, /for \(const m of members\) pendingCleared\.add\(m\.itemId\);/, "suppressed from incoming pushes until the kernel confirms");
  assert.match(fn, /vscodeApi\?\.postMessage\(\{ type: "askClearMany", itemIds: ids, sid \}\);/,
    "ONE kernel batch: N askClear posts stamped N batches and the kernel's Undo restored only the last");
  assert.doesNotMatch(fn, /type: "askClear",/, "no per-member posts");
  assert.match(fn, /c\.dispatchEvent\(new MouseEvent\("mouseleave"\)\); c\.classList\.add\("dismissing"\);/, "flush the hover highlight, animate out");
  assert.match(fn, /if \(head\.getAttribute\("data-fsid"\) === sid\) \{ head\.dispatchEvent\(new MouseEvent\("mouseleave"\)\); startSessHeadExit\(key, head\); \}/,
    "every column's header leaves with its run, one motion; the row's hover-freeze gate is released by the click, as a card's is");
  assert.match(fn, /setTimeout\(\(\) => \{[\s\S]*stillOurs\(\) && c\.classList\.contains\("dismissing"\)[\s\S]*dropDismissed\(ids\.filter\(\(id\) => pendingCleared\.has\(id\)\)\);\s*\n\s*\}, 180\);/,
    "finalize after the 180ms exit only what is still ours and still dismissing; drop only ids still pending (an Undo inside the window keeps its cards)");
  assert.doesNotMatch(fn, /confirm\(/, "no confirm dialog: Undo is the safety net");
  // the ask-group clear and the modal's group clear ride the same batch op — their Undo had the same hole
  assert.match(FEED, /vscodeApi\?\.postMessage\(\{ type: "askClearMany", itemIds: cur\.members\.map\(\(m\) => m\.itemId\), sid: cur\.sid \}\);/);
  assert.match(FEED, /type: "askClearMany", itemIds: grp\.members\.map\(\(mem\) => mem\.itemId\), sid: grp\.sid/);
});

test("the kernel takes the batch as one cleared.jsonl stamp and drops every member's citations", () => {
  const op = KERNEL.slice(KERNEL.indexOf('msg.get("type") == "askClearMany"'), KERNEL.indexOf('msg.get("type") == "quarantineDecision"'));
  assert.match(op, /_ids = \[str\(i\) for i in msg\["itemIds"\] if i\]/);
  assert.match(op, /_gesture_store_refusal\(client, "clear", _clear_all\(_ids\)\)/, "one _clear_all call = one batch stamp");
  assert.match(op, /_subtree_item_ids\(_i\)/, "the citation drop covers every member's subtree");
  assert.match(op, /_send_to_app\("chat", \{"type": "dropCitation", "itemId": _ids\[0\], "itemIds": _gone\}\)/);
  assert.match(op, /_mark_views_dirty\(\)/);
});

test("the router sends the batch to the session's kernel with bare ids, and Undo follows it there", () => {
  const r = routeOutbound({ type: "askClearMany", sid: "box2:11111111-2222-3333-4444-555555555555", itemIds: ["box2:11111111-2222-3333-4444-555555555555:g1", "box2:11111111-2222-3333-4444-555555555555:g2"] }, new Set(["box2"]));
  assert.equal(r.length, 1);
  assert.equal(r[0].host, "box2");
  assert.equal(r[0].msg.sid, "11111111-2222-3333-4444-555555555555");
  assert.deepEqual(r[0].msg.itemIds, ["11111111-2222-3333-4444-555555555555:g1", "11111111-2222-3333-4444-555555555555:g2"]);
  assert.match(FED, /if \(m && \(m\.type === "askClear" \|\| m\.type === "askClearMany" \|\| m\.type === "clearAll"\)\) \{\s*\n\s*this\.lastClearHosts = routes\.length \? routes\.map\(\(r\) => r\.host\) : \[LOCAL\];/,
    "undoClear follows the LAST clear, batched or single, to the kernel that took it (T286: the board-wide Clear all to every kernel it reached)");
});

test("the header Clear's own class carries layout only; size, outline and the accent hover come from .fdismiss", () => {
  assert.match(CSS, /\.feed-sess-clear \{ flex: none; margin-left: auto; \}/, "position only: far right of the row");
  // nothing of the button's look may live on the positional class (block-scoped negatives)
  assert.doesNotMatch(CSS, /\.feed-sess-clear[^{]*\{[^}]*(font|color|border|background|padding|line-height|opacity)/,
    "a header-only size or colour would be a second button that drifts");
  assert.doesNotMatch(CSS, /\.feed-sess-clear:hover|\.feed-sess-clear:focus/, "the hover is .fdismiss's accent hover, shared");
  // the shared button: outlined, 0.72em, accent on hover — and its weight pinned, since the header renders at 600
  assert.match(CSS, /\.fdismiss \{\s*\n\s*font: inherit; font-size: 0\.72em; font-weight: 400; cursor: pointer; white-space: nowrap;/);
  assert.match(CSS, /border: 1px solid var\(--card-border\); border-radius: 6px;/);
  assert.match(CSS, /\.fdismiss:hover \{ border-color: var\(--accent\); color: var\(--accent\); background: var\(--accent-wash\); \}/);
});

test("the header's button reads \"Clear all\" in the card Clear's exact chrome (T271, the user 2026-09-08)", () => {
  // the card's own Clear keeps its one word; the session-wide one says what it clears — same builder, same
  // .fdismiss class, the label the only difference
  assert.match(FEED, /function clearButton\(title: string, label = "Clear"\): HTMLElement \{\s*\n\s*const b = el\("button", "fdismiss"\);\s*\n\s*b\.textContent = label;/);
  assert.match(FEED, /clearButton\("clear every card for this session", "Clear all"\)/);
  assert.doesNotMatch(FEED, /clearButton\([^)]*, "Clear"\)/, "no caller spells the default; a card's Clear is the bare builder");
});
