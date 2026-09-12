#!/usr/bin/env python3
"""Chat columns, the SERVED leg (the user 2026-09-08, who wanted several sessions open side by side instead of
tabbing between them; the partition 2026-09-11: the columns hold DIFFERENT sessions). The dashboard shell holds N
chat columns: column 1 is #chat-pane / #f-chat, every later one a client-made twin (_LANDING_SPLIT_JS) around an
iframe at /chat?col=N&skeleton=1 with its own state blob, socket and grow weight, slotted before #gv-a behind a
chat|chat gutter, and showing ONLY the sessions the shell's store says it holds; the first column shows the rest.

The node-side test (tests/test_chat_split.py) drives the split script against a DOM stub; this one drives the
REAL page: a hermetic kernel serves the dashboard with EIGHT synthetic sessions (a board wide enough that a
column served whole is told apart from one served as a view), a headless browser opens it once, and ONE
driver run walks the whole story in order, each step landing in its own assertion here:
  1. __rompMoveTab(B, "new") opens a second column that holds B — B's tab leaves column 1's strip, column 2's
     strip lists B alone — both columns near half the old width (the honest half, __rompSplitGrow), the v2 store
     persisted, the frame at /chat?col=2&skeleton=1; the column is served as a VIEW of B (the kernel's send
     counters: exactly one full frame, a status per other tab, never the board; and the column's socket asked for
     nothing) and showed the pane loader, never the no-sessions copy or an "Opening session" line, between the
     call and B's paint;
  2. routing by the owner: with C moved into column 2 as well and active there, the feed's click echo for C
     (a romp:focus-echo storage write) and a jumpSession for B posted into COLUMN 1 both land in column 2 —
     column 1 keeps A throughout — and __rompChatTarget names the owner for every session;
  3. __rompMoveTab(B, 1) with B alone in column 2 (C moved home first) closes column 2, column 1 lists B again
     and is back at the width it had before the column opened (the closing column's pixels go to the column on its
     left, the halving's twin), and the draft typed for B in column 2's box is in column 1's box when B is picked there;
  4. a new column on B again, then dragging the chat|chat gutter moves width between the two columns and
     persists column 2's grow;
  5. column 2's new-session picker lifts ITS iframe and pane (.lifted), never column 1's, and unlifts on toggle;
  6. a reload restores the v2 store: column 2 on B at the dragged width, column 1 without B;
  7. the cross on the last later column returns B to column 1 and leaves {v:2, cols:[]};
  8. the whole story runs well under half a minute (the driver waits on conditions, never on fixed sleeps);
  9. THE DRAG (the user 2026-09-11): a real pointer drag of B's tab in column 1 into the shell's edge zone — the
     zone mounted on the page's tabDrag message, the rectangle shown at the pane's right half with B's name while
     the pointer is over the zone, hidden after the drop — opens column 2 on B and column 1 lists no B; B's tab
     dragged from column 2 onto column 1's pane wears the cue there and, dropped, comes home and column 2 closes;
 10. a column blob from BEFORE the partition (a v1 store, its column-2 blob naming B and holding a draft for A, as a
     whole chat page's blob could) reloads into column 2 on B, and A's draft reaches column 1's box: the page offers
     state for a session it no longer shows to the shell, which hands it to the column that does.
Skips LOUDLY when the extension deps or a playwright browser are absent (CI installs none). Synthetic only:
placeholder sids, invented notes-api prompt text, no real session data."""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
# the kernel refuses to boot with a retired key variable or a 1Password name in its environment (kernel/credentials.py
# check_boot_environment): the lab's kernel env is scrubbed by the kernel's own rule, read from the module itself
from romp_load import load_source
from tests.dist_copy import copy_dist
_cred = load_source("romp_credentials_served", os.path.join(ROOT, "kernel", "credentials.py"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

SID_A = "11111111-2222-4333-8444-000000000301"   # "web": column 1's session
SID_B = "11111111-2222-4333-8444-000000000302"   # "api": the session the move opens a column on
SID_C = "11111111-2222-4333-8444-000000000303"   # "tests": moved into column 2 beside B for the routing step
SID_X = "11111111-2222-4333-8444-000000000999"   # a session no column lists (not on the board)
# six more tabs, so the board is EIGHT sessions: a column served whole takes eight full frames per push, a column served
# as a view of B takes exactly one (plus a status frame per other tab), and the /perf deltas in step 1 tell the two apart;
# the column's idle walk visits only its members (tabInView), and its one member is on screen, so no prefetch loads a tab
FILLERS = [("11111111-2222-4333-8444-00000000030%d" % k, name, k)
           for k, name in ((3, "tests"), (4, "docs"), (5, "lint"), (6, "deploy"), (7, "search"), (8, "auth"))]
BOARD = 2 + len(FILLERS)
DRAG_PX = 200
SLACK_PX = 40
DRAFT = "a half-typed note for the api session, kept across the move"
ORPHAN_DRAFT = "a note for the web session, left in a column blob from before the partition"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(sid, tag, cwd, pairs):
    """`pairs` CLOSED user/assistant turns for `sid` (an OPEN turn would invite the boot reconcile to resume
    it); `tag` keeps the two sessions' message uuids apart."""
    out, parent, t = [], None, 1_700_000_000
    filler = ["The ranking pass reads its weights from the notes-api config now.",
              "Tokenizer edge cases (hyphens, quotes) are covered by the new fixture set.",
              "Index rebuild time is dominated by the stemmer; caching its table halves it.",
              "The pagination cursor survives a re-sort because it encodes the sort key too."]
    for i in range(pairs):
        u = "11111111-2222-4333-8444-%02x00000a%04x" % (tag, i)
        a = "11111111-2222-4333-8444-%02x00000b%04x" % (tag, i)
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": sid, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the search module notes (part %d)" % (i + 1)}})
        body = "\n\n".join(["Note %d." % (i + 1)] + [filler[(i + k) % len(filler)] for k in range(3)])
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": sid, "cwd": cwd,
                    "message": {"id": "msg_lab_%d_%04d" % (tag, i), "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": body}], "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


# The chat iframes are SAME-ORIGIN with the shell, so every probe reads a column's document from the shell
# context (document.getElementById(fid).contentDocument): no frame handles that a navigation could tear down.
DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const out = { t0: Date.now() };
// Column 2's asks, read at the socket layer (the browser reports every frame's sockets): a needFull from a column opened
// as a view of one session is the fingerprint of the board loading behind the view. 2026-09-11: seven `nobase` asks per
// open, one per withheld tab, when the shim's FIFO delivered the status frames ahead of the strip that named their set.
const col2Asks = [];
page.on("websocket", (ws) => {
  if (!/[?&]col=2(?:&|$)/.test(ws.url())) return;
  ws.on("framesent", (f) => { try { const m = JSON.parse(f.payload); if (m && m.type === "needFull") col2Asks.push([m.id, m.why || ""]); } catch (e) { /* a non-JSON frame */ } });
});
const die = async (why) => {
  out.ms = Date.now() - out.t0;
  fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n");
  await browser.close();
  process.exit(0);
};
const T = 15000;
const waitFn = async (fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await die(why + " (" + String(e).split("\n")[0] + ")"); });
// a column's tab strip holds every one of `sids`
const waitTabs = (fid, sids) => waitFn(([fid, sids]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
  return sids.every((s) => ids.includes(s));
}, [fid, sids], fid + " never showed tabs " + sids.join(","));
// a column's tab strip lists NONE of `sids` (a moved tab is gone from its old column)
const waitNoTabs = (fid, sids) => waitFn(([fid, sids]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
  return ids.length > 0 && sids.every((s) => !ids.includes(s));
}, [fid, sids], fid + " still lists " + sids.join(","));
// a column's ACTIVE tab is `sid`
const waitActive = (fid, sid) => waitFn(([fid, sid]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid;
}, [fid, sid], fid + " never activated " + sid);
const waitBootGone = () => waitFn(() => !document.getElementById("romp-boot"), null, "boot splash never cleared");
const waitFocused = (fid) => waitFn((fid) => window.__rompFocusedChatId && window.__rompFocusedChatId() === fid, fid, fid + " never took the focus ring");
const waitGone = (id) => waitFn((id) => !document.getElementById(id), id, id + " never left the row");
const activeIn = (fid) => page.evaluate((fid) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null;
}, fid);
const tabsIn = (fid) => page.evaluate((fid) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  return d ? Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id) : null;
}, fid);
const targetOf = (sid) => page.evaluate((sid) => { const f = window.__rompChatTarget(sid); return f ? f.id : null; }, sid);
const width = (id) => page.evaluate((id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; }, id);
const composerIn = (fid) => page.evaluate((fid) => { const d = document.getElementById(fid).contentDocument; const ta = d && d.getElementById("composer-input"); return ta ? ta.value : null; }, fid);
const shell = () => page.evaluate(() => ({
  frameIds: window.__rompChatFrameIds(), cols: localStorage.getItem("romp-chat-cols"), sets: window.__rompChatSets(),
  rowKids: Array.from(document.querySelector(".row").children).map((e) => e.id || e.className),
  grow: JSON.parse(localStorage.getItem("romp-pane-grow") || "null"),
  lifted: Array.from(document.querySelectorAll(".lifted")).map((e) => e.id), pickerOpen: document.body.classList.contains("picker-open"),
}));
// the page rect of an element INSIDE a column, for page.mouse (the iframe's offset plus the element's own)
const rectIn = (fid, sel) => page.evaluate(([fid, sel]) => {
  const f = document.getElementById(fid); const fr = f.getBoundingClientRect(); const el = f.contentDocument.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect(); return { x: fr.left + r.left, y: fr.top + r.top, w: r.width, h: r.height };
}, [fid, sel]);
const clickIn = async (fid, sel) => { const r = await rectIn(fid, sel); if (!r) await die("no " + sel + " in " + fid); await page.mouse.click(r.x + r.w / 2, r.y + Math.min(r.h / 2, 120)); };
const clickTab = async (fid, sid) => { const fr = await (await page.$("#" + fid)).contentFrame(); await fr.locator('#tabs .tab[data-id="' + sid + '"]').first().click(); };
// GET /perf from the shell's origin (the landing's cookie authorises it): the kernel's send counters by class and slot
const perf = () => page.evaluate(() => fetch("/perf").then((r) => r.json()));
const sends = (p, slot) => (((p || {}).sends || {}).full || {})[slot]?.count || 0;

// ---- load: every session is a tab in column 1; column 1 shows A ----
await page.goto(cfg.url);
await waitTabs("f-chat", [cfg.sidA, cfg.sidB, cfg.sidC]);
await waitBootGone();
if ((await activeIn("f-chat")) !== cfg.sidA) { await clickTab("f-chat", cfg.sidA); await waitActive("f-chat", cfg.sidA); }
out.col1Before = await activeIn("f-chat");

// ---- 1. move B to a new column: column 2 holds B alone, column 1 no longer lists B, both near half the old width ----
const perf0 = await perf();
out.s1 = await page.evaluate((sidB) => {
  // What column 2 shows between the call and B's paint, read once per animation frame from the shell (the frames
  // are same-origin): the no-sessions copy, an "Opening session" / "opening …" line, the pane loader. Bounded: stops
  // at the paint, or after 1200 frames. Started BEFORE the call so no frame is missed.
  const o = { frames: 0, emptyState: 0, openingText: 0, loaderSeen: 0, done: false, timedOut: false, t0: performance.now() };
  window.__obs = o;
  const tick = () => {
    o.frames++;
    const f = document.getElementById("f-chat-2"); const d = f && f.contentDocument;
    if (d) {
      const es = d.getElementById("empty-state"); if (es && es.style.display !== "none") o.emptyState++;
      const ns = d.getElementById("no-sessions"); if (ns) o.emptyState++;
      const sl = d.getElementById("statusline"); if (sl && /Opening session/.test(sl.textContent || "")) o.openingText++;
      const tl = d.getElementById("tab-loading"); if (tl && /opening/.test(tl.textContent || "")) o.openingText++;
      const spin = d.getElementById("pane-spin"); if (spin && !spin.classList.contains("gone")) o.loaderSeen++;
      const t = d.querySelector("#tabs .tab.active[data-id]");
      const painted = Array.from(d.querySelectorAll("#content .thread")).some((el) => el.style.display !== "none" && el.children.length > 0);
      if (t && t.dataset.id === sidB && painted) { o.done = true; o.msToPaint = performance.now() - o.t0; o.perfAtPaint = fetch("/perf").then((r) => r.json()); return; }
    }
    if (o.frames < 1200) requestAnimationFrame(tick); else o.timedOut = true;
  };
  requestAnimationFrame(tick);
  const w = (id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; };
  const pane1Before = w("chat-pane");
  const f = window.__rompMoveTab(sidB, "new");
  const row = document.querySelector(".row"), kids = Array.from(row.children).map((e) => e.id);
  return { frameId: f && f.id, tag: f && f.tagName, src: f && f.getAttribute("src"), paneCol: (document.getElementById("chat-pane-2") || {}).getAttribute?.("data-col"),
           order: kids, gIdx: kids.indexOf("gv-chat-2"), pIdx: kids.indexOf("chat-pane-2"), aIdx: kids.indexOf("gv-a"),
           pane1Before, pane1W: w("chat-pane"), pane2W: w("chat-pane-2"), gChat2Inline: row.style.getPropertyValue("--g-chat2"),
           gChat2: getComputedStyle(row).getPropertyValue("--g-chat2"), cols: localStorage.getItem("romp-chat-cols"), sets: window.__rompChatSets() };
}, cfg.sidB);
await waitTabs("f-chat-2", [cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitNoTabs("f-chat", [cfg.sidB]);
await waitFn(() => window.__obs && (window.__obs.done || window.__obs.timedOut), null, "column 2 never painted B's transcript");
const perfAtPaint = await page.evaluate(() => window.__obs.perfAtPaint ? window.__obs.perfAtPaint : null);
out.s1.col2Active = await activeIn("f-chat-2"); out.s1.col1After = await activeIn("f-chat");
out.s1.col2Tabs = await tabsIn("f-chat-2"); out.s1.col1Tabs = await tabsIn("f-chat");
out.s1.obs = await page.evaluate(() => { const { perfAtPaint, ...rest } = window.__obs; return rest; });
out.s1.fullChatDelta = sends(perfAtPaint, "chat") - sends(perf0, "chat"); out.s1.statusDelta = sends(perfAtPaint, "status") - sends(perf0, "status");
out.s1.col2Asks = col2Asks.slice();
out.s1.targetB = await targetOf(cfg.sidB); out.s1.targetA = await targetOf(cfg.sidA); out.s1.targetX = await targetOf(cfg.sidX);

// ---- 2. routing by the owner: C joins column 2 and is active there; an echo for C and a jump for B posted into column 1 both land in column 2 ----
out.s2 = await page.evaluate((sidC) => { const f = window.__rompMoveTab(sidC, 2); return { target: f && f.id, cols: localStorage.getItem("romp-chat-cols") }; }, cfg.sidC);
await waitTabs("f-chat-2", [cfg.sidB, cfg.sidC]);
await waitNoTabs("f-chat", [cfg.sidB, cfg.sidC]);
await waitActive("f-chat-2", cfg.sidC);   // the move's focus shows the moved tab in its new column
out.s2.col2AfterMove = await activeIn("f-chat-2");
await clickTab("f-chat-2", cfg.sidB);
await waitActive("f-chat-2", cfg.sidB);
// the feed's click echo for C: a storage write every column hears; only the owner acts
await page.evaluate((sidC) => localStorage.setItem("romp:focus-echo", JSON.stringify({ sid: sidC, t: Date.now() })), cfg.sidC);
await waitActive("f-chat-2", cfg.sidC);
out.s2.col2AfterEcho = await activeIn("f-chat-2"); out.s2.col1AfterEcho = await activeIn("f-chat");
// the shell's switcher picks B while column 1 is the column last worked in: column 1 hands the pick to the owner
await clickIn("f-chat", "#content");
await waitFocused("f-chat");
await page.evaluate((sidB) => document.getElementById("f-chat").contentWindow.postMessage({ type: "jumpSession", id: sidB }, "*"), cfg.sidB);
await waitActive("f-chat-2", cfg.sidB);
out.s2.col2AfterJump = await activeIn("f-chat-2"); out.s2.col1AfterJump = await activeIn("f-chat");
out.s2.col1Tabs = await tabsIn("f-chat");
out.s2.targetB = await targetOf(cfg.sidB); out.s2.targetC = await targetOf(cfg.sidC); out.s2.targetA = await targetOf(cfg.sidA);

// ---- 3. B home again, with its draft: C goes home first, a draft is typed for B in column 2, then B moves to column 1 and column 2 closes ----
await page.evaluate((sidC) => window.__rompMoveTab(sidC, 1), cfg.sidC);
await waitTabs("f-chat", [cfg.sidA, cfg.sidC]);
await waitNoTabs("f-chat-2", [cfg.sidC]);
await waitActive("f-chat-2", cfg.sidB);
await clickIn("f-chat-2", "#composer-input");
await page.keyboard.type(cfg.draft);
out.s3 = { typed: await composerIn("f-chat-2"), col1BeforeMove: await activeIn("f-chat") };
out.s3.moveTarget = await page.evaluate((sidB) => { const f = window.__rompMoveTab(sidB, 1); return f && f.id; }, cfg.sidB);
await waitGone("chat-pane-2");
await waitTabs("f-chat", [cfg.sidA, cfg.sidB, cfg.sidC]);
out.s3.after = await shell();
out.s3.pane1W = await width("chat-pane");   // the closed column's pixels came back to column 1 (the halving's twin)
out.s3.col1Tabs = await tabsIn("f-chat");
await clickTab("f-chat", cfg.sidB);
await waitActive("f-chat", cfg.sidB);
await waitFn(([fid, draft]) => { const d = document.getElementById(fid).contentDocument; const ta = d && d.getElementById("composer-input"); return !!ta && ta.value === draft; }, ["f-chat", cfg.draft], "column 1's box never showed B's draft");
out.s3.draftInCol1 = await composerIn("f-chat");
out.s3.targetB = await targetOf(cfg.sidB);

// ---- 4. a new column on B again, then drag the chat|chat gutter right: column 1 grows, column 2 shrinks, the grow persists ----
out.s4 = await page.evaluate((sidB) => { const f = window.__rompMoveTab(sidB, "new"); return { frameId: f && f.id, cols: localStorage.getItem("romp-chat-cols") }; }, cfg.sidB);
await waitTabs("f-chat-2", [cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitNoTabs("f-chat", [cfg.sidB]);
out.s4.before1 = await width("chat-pane"); out.s4.before2 = await width("chat-pane-2");
const g = await (await page.$("#gv-chat-2")).boundingBox();
await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
await page.mouse.down();
out.s4.dragClass = await page.evaluate(() => document.body.classList.contains("drag"));
// the grab normalises every shown pane's grow to its px width: what the store holds the instant after mousedown
out.s4.growAtGrab = await page.evaluate(() => { const st = document.querySelector(".row").style; return { chat: parseFloat(st.getPropertyValue("--g-chat")), chat2: parseFloat(st.getPropertyValue("--g-chat2")), feed: parseFloat(st.getPropertyValue("--g-feed")) }; });
await page.mouse.move(g.x + g.width / 2 + cfg.dragPx, g.y + g.height / 2, { steps: 10 });
await page.mouse.up();
out.s4.after1 = await width("chat-pane"); out.s4.after2 = await width("chat-pane-2");
out.s4.dragClassAfter = await page.evaluate(() => document.body.classList.contains("drag"));
out.s4.grow = (await shell()).grow;

// ---- 5. the picker in column 2 lifts THAT column only; toggling it closed unlifts ----
await page.evaluate(() => document.getElementById("f-chat-2").contentWindow.postMessage({ type: "openPicker" }, "*"));
await waitFn(() => document.body.classList.contains("picker-open"), null, "the shell never lifted for column 2's picker");
out.s5 = { open: await shell() };
out.s5.open.pane2Lifted = await page.evaluate(() => document.getElementById("chat-pane-2").classList.contains("lifted"));
out.s5.open.frame2Lifted = await page.evaluate(() => document.getElementById("f-chat-2").classList.contains("lifted"));
out.s5.open.frame1Lifted = await page.evaluate(() => document.getElementById("f-chat").classList.contains("lifted"));
out.s5.open.pickerShown = await page.evaluate(() => { const d = document.getElementById("f-chat-2").contentDocument; const p = d && d.getElementById("picker"); return !!p && p.style.display !== "none"; });
await page.evaluate(() => document.getElementById("f-chat-2").contentWindow.postMessage({ type: "openPicker", toggle: true }, "*"));
await waitFn(() => !document.body.classList.contains("picker-open"), null, "the shell never released the lift");
out.s5.closed = await shell();

// ---- 6. reload: the v2 store restores column 2 on B at the dragged width, column 1 without B ----
await page.reload();
await waitTabs("f-chat", [cfg.sidA, cfg.sidC]);
await waitTabs("f-chat-2", [cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitBootGone();
await waitNoTabs("f-chat", [cfg.sidB]);
out.s6 = await shell();
out.s6.col2Active = await activeIn("f-chat-2"); out.s6.col1Active = await activeIn("f-chat");
out.s6.col1Tabs = await tabsIn("f-chat"); out.s6.col2Tabs = await tabsIn("f-chat-2");
out.s6.pane1W = await width("chat-pane"); out.s6.pane2W = await width("chat-pane-2");

// ---- 7. the cross on the last later column: B returns to column 1, the store is empty ----
await page.evaluate(() => document.querySelector("#chat-pane-2 .col-x").click());
await waitGone("chat-pane-2");
await waitTabs("f-chat", [cfg.sidA, cfg.sidB, cfg.sidC]);
out.s7 = await shell();
out.s7.pane2Gone = await page.evaluate(() => !document.getElementById("chat-pane-2") && !document.getElementById("gv-chat-2") && !document.getElementById("f-chat-2"));
out.s7.col1Tabs = await tabsIn("f-chat");
// ---- 9. THE DRAG: B's tab from column 1 into the right edge opens column 2 on B; from column 2 onto column 1's pane it comes home ----
// A real pointer drag: mouse down on the tab, a move past the drag threshold starts the page's dragstart (its tabDrag
// message mounts the shell's zones), moves carry the drag over the zone (Chromium's intercepted drag dispatches
// dragenter/dragover there), the release drops. The shell reads nothing from dataTransfer, so the zones see exactly
// what a hand drag gives them.
const rectOf = (sel) => page.evaluate((sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return { left: r.left, top: r.top, width: r.width, height: r.height }; }, sel);
const dragStart = async (fid, sid) => {
  const t = await rectIn(fid, '#tabs .tab[data-id="' + sid + '"]');
  if (!t) await die("no tab for " + sid + " in " + fid);
  await page.mouse.move(t.x + t.w / 2, t.y + t.h / 2);
  await page.mouse.down();
  await page.mouse.move(t.x + t.w / 2 + 24, t.y + t.h / 2 + 6, { steps: 4 });   // past the drag threshold: dragstart fires in the page
};
const zones = () => page.evaluate(() => Array.from(document.querySelectorAll(".col-drop")).map((z) => {
  const r = z.getBoundingClientRect();
  return { cls: z.className, pane: z.parentElement.id, col: z.getAttribute("data-col"), refused: z.getAttribute("data-refused"), top: z.style.top, left: r.left, right: r.right, rtop: r.top, width: r.width, height: r.height };
}));
if ((await activeIn("f-chat")) !== cfg.sidA) { await clickTab("f-chat", cfg.sidA); await waitActive("f-chat", cfg.sidA); }
out.s9 = { pane1: await rectOf("#chat-pane"), row: await rectOf(".row"), stripBottom: (await rectIn("f-chat", "#tabbar")) };
await dragStart("f-chat", cfg.sidB);
await waitFn(() => !!document.querySelector(".col-drop.col-drop-edge"), null, "the edge zone never mounted for B's drag");
out.s9.zones = await zones();
const edge = out.s9.zones.find((z) => z.cls.includes("col-drop-edge"));
if (!edge) await die("no edge zone among " + JSON.stringify(out.s9.zones));
await page.mouse.move(edge.left + edge.width / 2, edge.rtop + edge.height / 2, { steps: 8 });
await waitFn(() => document.getElementById("col-ghost").classList.contains("on"), null, "the rectangle never showed over the edge zone");
out.s9.ghost = await page.evaluate(() => { const g = document.getElementById("col-ghost"); const r = g.getBoundingClientRect(); const cs = getComputedStyle(g);
  return { cls: g.className, text: g.textContent, left: r.left, top: r.top, width: r.width, height: r.height, display: cs.display, bg: cs.backgroundColor, shadow: cs.boxShadow, font: cs.fontSize + "/" + cs.fontWeight, color: cs.color }; });
// the light theme's twin, read while the rectangle is up: the ring follows --accent, the wash and the line's colour are the light values
out.s9.light = await page.evaluate(() => { document.body.classList.add("theme-light"); const cs = getComputedStyle(document.getElementById("col-ghost")); const o = { bg: cs.backgroundColor, shadow: cs.boxShadow, color: cs.color }; document.body.classList.remove("theme-light"); return o; });
await page.mouse.up();
await waitTabs("f-chat-2", [cfg.sidB]); await waitActive("f-chat-2", cfg.sidB); await waitNoTabs("f-chat", [cfg.sidB]);
out.s9.after = await shell();
out.s9.afterDrop = await page.evaluate(() => { const g = document.getElementById("col-ghost"); return { cls: g.className, display: getComputedStyle(g).display, zones: document.querySelectorAll(".col-drop").length }; });
out.s9.col1Tabs = await tabsIn("f-chat"); out.s9.col2Tabs = await tabsIn("f-chat-2"); out.s9.col1Active = await activeIn("f-chat");
out.s9.pane1W = await width("chat-pane"); out.s9.pane2W = await width("chat-pane-2");
// …and back: B's tab from column 2 (its only member: no edge zone) onto column 1's pane
await dragStart("f-chat-2", cfg.sidB);
await waitFn(() => !!document.querySelector('#chat-pane > .col-drop'), null, "column 1's zone never mounted for the drag back");
out.s9.backZones = await zones();
const z1 = out.s9.backZones.find((z) => z.pane === "chat-pane");
await page.mouse.move(z1.left + z1.width / 2, z1.rtop + z1.height / 2, { steps: 8 });
await waitFn(() => { const z = document.querySelector("#chat-pane > .col-drop"); return !!z && z.classList.contains("over"); }, null, "column 1's zone never wore the cue");
out.s9.overBack = await page.evaluate(() => Array.from(document.querySelectorAll(".col-drop.over")).map((z) => z.parentElement.id));
out.s9.ghostBack = await page.evaluate(() => document.getElementById("col-ghost").className);
await page.mouse.up();
await waitGone("chat-pane-2"); await waitTabs("f-chat", [cfg.sidA, cfg.sidB, cfg.sidC]); await waitActive("f-chat", cfg.sidB);
out.s9.home = await shell(); out.s9.homeTabs = await tabsIn("f-chat"); out.s9.homeActive = await activeIn("f-chat");
out.s9.homeZones = await page.evaluate(() => document.querySelectorAll(".col-drop").length);

// ---- 10. a column blob from BEFORE the partition holds a draft for a session the column no longer shows: it reaches the column that does ----
// a v1 store ([2]) whose column-2 blob names B and holds a draft for A (a whole chat page's blob could): the migration
// keeps B for column 2; column 2's page, once it has heard the board, offers A's draft to the shell, which takes it and
// hands it to column 1, where A is shown
await page.evaluate(([sidA, sidB, draft]) => {
  localStorage.setItem("romp-chat-cols", "[2]");
  localStorage.setItem("romp-vscode-state-chat:2", JSON.stringify({ activeId: sidB, drafts: { [sidA]: draft } }));
}, [cfg.sidA, cfg.sidB, cfg.orphan]);
await page.reload();
await waitTabs("f-chat", [cfg.sidA, cfg.sidC]);
await waitTabs("f-chat-2", [cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitBootGone();
await waitNoTabs("f-chat", [cfg.sidB]);
out.s10 = { cols: await page.evaluate(() => localStorage.getItem("romp-chat-cols")), col2Tabs: await tabsIn("f-chat-2") };
await clickTab("f-chat", cfg.sidA);
await waitActive("f-chat", cfg.sidA);
await waitFn(([fid, draft]) => { const d = document.getElementById(fid).contentDocument; const ta = d && d.getElementById("composer-input"); return !!ta && ta.value === draft; }, ["f-chat", cfg.orphan], "column 1's box never showed the draft column 2's blob held for A");
out.s10.draftInCol1 = await composerIn("f-chat");
out.s10.blob2 = await page.evaluate(() => JSON.parse(localStorage.getItem("romp-vscode-state-chat:2") || "null"));
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedChatSplit(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one step of the shared result."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="chat-split-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # skips a concurrent build's staging files (tests/dist_copy.py)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # eight synthetic SDK sessions so the chat page has eight tabs (the test_dashboard_reload_served lab shape,
        # multiplied); their transcripts hold only CLOSED turns, so the boot reconcile never resumes any and no
        # CLI is ever spawned
        for sid, name, tag in [(SID_A, "web", 1), (SID_B, "api", 2)] + FILLERS:
            Path(state, "names", sid).write_text("%s\t%s\t\t\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, tag, cwd, 20))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.port = _free_port()
        cls.token = "testtok-chatsplit"
        cls.env = dict(os.environ,
                       XDG_STATE_HOME=os.path.join(cls.lab, "xdg"),
                       CLAUDE_CONFIG_DIR=claude,
                       ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
                       ROMP_SERVE_TOKEN=cls.token, ROMP_KERNEL_PORT=str(cls.port),
                       ROMP_DIST_DIR=dist, ROMP_MODEL_CATALOG="off",
                       # a postal bus of its own that is never started (the trio kernel_env gives every lab kernel):
                       # the kernel's boot-time ensure must never take the machine's fixed bus port (tests/test_hermetic_kernel_postal.py)
                       ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        cls.env.pop("ROMP_STATE_DIR", None)
        # a romp session's tool shell carries its own kernel's ROMP_MANAGER_PID and friends; inherited, a stale one has the
        # lab kernel's parent watch drain it seconds after boot (the run reads "never served /healthz"). The lab is nobody's child.
        for k in ("ROMP_MANAGER_PID", "ROMP_SUPERVISED", "ROMP_SID", "ROMP_SESSION_NAME"):
            cls.env.pop(k, None)
        for k in [k for k in cls.env if k in _cred.RETIRED_VARS or _cred.is_op_env_name(k)]:   # the kernel's own boot rule (module top)
            cls.env.pop(k, None)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=cls.env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        cls.result, cls.driver_error = None, None
        cls._drive()

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token),
                       "sidA": SID_A, "sidB": SID_B, "sidC": SID_C, "sidX": SID_X, "dragPx": DRAG_PX, "draft": DRAFT, "orphan": ORPHAN_DRAFT}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        t0 = time.monotonic()
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            cls.driver_error = "driver timed out; partial output:\n%s" % so
            return
        cls.driver_s = time.monotonic() - t0
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served leg needs one (CI installs none)")
        if p.returncode != 0:
            cls.driver_error = "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            cls.driver_error = "driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        r = json.loads(line[len("RESULT:"):])
        if "died" in r:
            cls.driver_error = "driver aborted early: %s\n%s" % (r["died"], json.dumps(r, indent=1)[-3000:])
            return
        cls.result = r

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            try:
                os.kill(k.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _r(self):
        if self.driver_error:
            tail = ""
            try:
                with open(self.klog) as fh:
                    tail = "\nkernel log tail:\n" + fh.read()[-2000:]
            except OSError:
                pass
            self.fail(self.driver_error + tail)
        return self.result

    def test_1_a_move_to_a_new_column_opens_it_on_the_session_alone_at_half_the_width_and_persists_the_partition(self):
        r = self._r()
        s = r["s1"]
        self.assertEqual(r["col1Before"], SID_A, "the story starts with column 1 on A")
        self.assertEqual(s["tag"], "IFRAME", "__rompMoveTab returns the new column's iframe: %r" % s)
        self.assertEqual(s["frameId"], "f-chat-2")
        self.assertEqual(s["src"], "/chat?col=2&skeleton=1", "every later column is /chat?col=N, a skeleton client of its session (2026-09-11)")
        self.assertEqual(s["paneCol"], "2")
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B]}]}, "the v2 store: column 2 holds B")
        self.assertEqual(s["sets"], {"2": [SID_B]}, "what every column page filters by")
        # row order: … chat-pane, gv-chat-2, chat-pane-2, gv-a …
        self.assertGreaterEqual(s["gIdx"], 0, s["order"])
        self.assertEqual(s["pIdx"], s["gIdx"] + 1, "the gutter sits directly ahead of its column: %r" % s["order"])
        self.assertLess(s["pIdx"], s["aIdx"], "the new column slots before gv-a: %r" % s["order"])
        self.assertEqual(s["order"][s["gIdx"] - 1], "chat-pane", "…and right after the first column: %r" % s["order"])
        # the honest half (__rompSplitGrow): the two columns share the old column's width, less the new 7px gutter
        half = (s["pane1Before"] - 7) / 2
        self.assertLessEqual(abs(s["pane1W"] - half), SLACK_PX, "column 1 is about half its old width: %r" % s)
        self.assertLessEqual(abs(s["pane2W"] - half), SLACK_PX, "…and so is the new column: %r" % s)
        self.assertGreater(s["pane2W"], 150, "the new column opens at a real width, never a sliver: %r" % s)
        g = float(s["gChat2Inline"] or s["gChat2"] or "nan")
        self.assertTrue(g == g and abs(g) != float("inf"), "--g-chat2 on .row is a finite number: %r" % s)
        self.assertGreater(g, 0)
        # the partition on the pages: column 2's strip lists B alone; column 1's no longer lists B
        self.assertEqual(s["col2Active"], SID_B, "the seeded blob's activeId lands the new column on B: %r" % s)
        self.assertEqual(s["col2Tabs"], [SID_B], "column 2 shows its one member and nothing else: %r" % s["col2Tabs"])
        self.assertNotIn(SID_B, s["col1Tabs"], "B left column 1's strip: %r" % s["col1Tabs"])
        self.assertEqual(len(s["col1Tabs"]), BOARD - 1, "column 1 keeps the rest: %r" % s["col1Tabs"])
        self.assertEqual(s["col1After"], SID_A, "column 1's tab is untouched by the move: %r" % s)
        self.assertEqual(s["targetB"], "f-chat-2", "a focus for B belongs to the column holding B: %r" % s)
        self.assertEqual(s["targetA"], "f-chat", "a focus for A belongs to column 1: %r" % s)
        self.assertEqual(s["targetX"], "f-chat", "a session no entry lists is the first column's: %r" % s)

    def test_1b_the_new_column_is_served_as_a_view_of_its_session_and_shows_the_loader_until_the_transcript(self):
        """The open's cost and copy (the user 2026-09-11, who found a new column slow to open and its copy reading as
        a create). The kernel's send counters across the open tell a column served WHOLE (a full frame per tab: eight
        here, and no status frame — a client that declared nothing gets none) from one served as a VIEW of B (the
        strip with a skeleton list, B's one full, a status per other tab): the status delta is at least the other
        tabs, and the full delta is the mechanism's fingerprint: exactly ONE full by the open (the pusher cycle the
        handshake wakes withholds the session frames from a pre-ready skeleton client, so the ready arm's connect push is
        the one full; review find 2026-09-11, when it crossed the wire twice), and no more: the column's idle walk visits
        only its members and its one member is on screen, so nothing is prefetched, and the column ASKS for nothing. The
        board loaded behind the view all the same before 2026-09-11: the pusher's strip and the ready arm's strip land in
        one burst, the shim's FIFO carries the newer strip to the END of that burst, so the status frames between them
        reached the page ahead of the strip that named their set, and each took the no-base ask meant for a lost first
        frame: seven asks, seven fulls, the board. A status ahead of its strip is now held for it (skeleton-tabs.ts
        holdStatus). Never the board."""
        s = self._r()["s1"]
        o = s["obs"]
        self.assertTrue(o["done"], "the observer saw B's transcript painted in column 2: %r" % o)
        self.assertGreaterEqual(s["statusDelta"], BOARD - 1,
                                "a status frame per other tab: the column was served as a skeleton client, not whole: %r" % s)
        self.assertEqual(s["fullChatDelta"], 1, "exactly one full per open, B's: never the board (eight per push before 2026-09-11), never the open's full twice, no prefetch (the column's one member is on screen): %r" % s)
        self.assertEqual(s["col2Asks"], [], "the column asked for nothing: a status delivered ahead of its strip is held for the strip, never the no-base ask that loaded the board behind the view, one ask per withheld tab (2026-09-11): %r" % s)
        # the copy between the call and the paint: the pane loader, never the create flow's words or the no-sessions copy
        self.assertEqual(o["emptyState"], 0, "no 'No session open' / no-sessions copy in a column opened on a session: %r" % o)
        self.assertEqual(o["openingText"], 0, "no 'Opening session' or 'opening …' line — a view of a running session is not a create: %r" % o)
        self.assertGreaterEqual(o["loaderSeen"], 1, "the pane loader (the romp swirl) covered the column until the transcript: %r" % o)
        self.assertLess(o["msToPaint"], 15000, "the paint came within the driver's wait")

    def test_2_a_focus_for_a_session_lands_in_the_column_that_holds_it_wherever_it_was_made(self):
        s = self._r()["s2"]
        self.assertEqual(s["target"], "f-chat-2", "a move into an open column returns that column's iframe: %r" % s)
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B, SID_C]}]}, "C joined column 2's entry")
        self.assertEqual(s["col2AfterMove"], SID_C, "the move's focus shows the moved tab in its new column: %r" % s)
        # the feed's click echo for C reaches every column's listener; only the owner acts
        self.assertEqual(s["col2AfterEcho"], SID_C, "the echo activated C in the column that holds it: %r" % s)
        self.assertEqual(s["col1AfterEcho"], SID_A, "…and column 1, which does not, stood down: %r" % s)
        # the shell's switcher, aimed at column 1, picked B: column 1 handed the pick to the owner
        self.assertEqual(s["col2AfterJump"], SID_B, "the jump landed in column 2, B's column: %r" % s)
        self.assertEqual(s["col1AfterJump"], SID_A, "column 1 keeps A: a pick of a session living elsewhere never changes its own tab: %r" % s)
        self.assertNotIn(SID_B, s["col1Tabs"]); self.assertNotIn(SID_C, s["col1Tabs"])
        self.assertEqual(s["targetB"], "f-chat-2"); self.assertEqual(s["targetC"], "f-chat-2"); self.assertEqual(s["targetA"], "f-chat")

    def test_3_a_move_home_closes_the_emptied_column_and_the_draft_travels_with_the_tab(self):
        r = self._r()
        s = r["s3"]
        self.assertEqual(s["typed"], DRAFT, "the draft was typed into column 2's box for B: %r" % s)
        self.assertEqual(s["col1BeforeMove"], SID_C, "C's move home showed C in column 1 (a move into an open column focuses the moved tab there): %r" % s)
        self.assertEqual(s["moveTarget"], "f-chat", "a move to the first column returns its iframe")
        a = s["after"]
        self.assertEqual(json.loads(a["cols"]), {"v": 2, "cols": []}, "column 2 held only B: its entry went with it: %r" % a)
        self.assertEqual(a["frameIds"], ["f-chat"], "…and its pane closed")
        self.assertEqual(a["sets"], {})
        self.assertNotIn("chat2", a["grow"] or {}, "a closed column's grow leaves the store: %r" % a["grow"])
        self.assertIn(SID_B, s["col1Tabs"], "column 1 lists B again: %r" % s["col1Tabs"])
        self.assertEqual(len(s["col1Tabs"]), BOARD)
        self.assertEqual(s["draftInCol1"], DRAFT, "B's draft, typed in column 2, is in column 1's box once B is picked there: %r" % s)
        self.assertEqual(s["targetB"], "f-chat")
        # the closed column's pixels came back to column 1 (the halving's twin; review find 2026-09-11: they went to every
        # pane by weight, and a tab dragged out and back narrowed the chat by a third each round trip)
        self.assertLessEqual(abs(s["pane1W"] - r["s1"]["pane1Before"]), SLACK_PX,
                             "column 1 is back at the width it had before the column opened: %r vs %r" % (s["pane1W"], r["s1"]["pane1Before"]))

    def test_4_dragging_the_chat_gutter_moves_width_between_the_columns_and_persists_it(self):
        """The grab's normalisation writes each pane's grow only after reading every pane's offsetWidth (a 2026-09-08
        product bug the served leg exposed: a write between reads forced a reflow at a mixed scale, so column 2 was
        recorded at about a fifth of its width and column 1 ballooned before the pointer moved)."""
        s = self._r()["s4"]
        self.assertEqual(s["frameId"], "f-chat-2", "the column on B again, the lowest free number reused")
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B]}]})
        self.assertTrue(s["dragClass"], "the grab arms the drag (body.drag makes the iframes let the pointer through)")
        self.assertFalse(s["dragClassAfter"], "…and the release disarms it")
        g0 = s["growAtGrab"]
        self.assertLessEqual(abs(g0["chat"] - s["before1"]), 2, "the grab records column 1 at its real width: %r" % s)
        self.assertLessEqual(abs(g0["chat2"] - s["before2"]), 2, "the grab records column 2 at its real width, not a half-relaid one: %r" % s)
        d1, d2 = s["after1"] - s["before1"], s["after2"] - s["before2"]
        self.assertLessEqual(abs(d1 - DRAG_PX), SLACK_PX, "column 1 grows by about the drag: %r" % s)
        self.assertLessEqual(abs(d2 + DRAG_PX), SLACK_PX, "column 2 shrinks by about the drag: %r" % s)
        g = (s["grow"] or {}).get("chat2")
        self.assertIsInstance(g, (int, float), "romp-pane-grow carries column 2's weight: %r" % s["grow"])
        self.assertTrue(g == g and abs(g) != float("inf"))
        self.assertIsInstance((s["grow"] or {}).get("chat"), (int, float))

    def test_5_the_picker_in_column_2_lifts_that_column_only_and_unlifts_on_toggle(self):
        s = self._r()["s5"]
        o = s["open"]
        self.assertTrue(o["pickerOpen"], "body.picker-open while column 2's picker is up: %r" % o)
        self.assertTrue(o["pickerShown"], "the picker overlay is visible in column 2")
        self.assertTrue(o["frame2Lifted"], "the asking iframe wears .lifted: %r" % o)
        self.assertTrue(o["pane2Lifted"], "…and its pane: %r" % o)
        self.assertFalse(o["frame1Lifted"], "column 1 is NOT lifted: %r" % o)
        self.assertEqual(sorted(o["lifted"]), ["chat-pane-2", "f-chat-2"], o["lifted"])
        c = s["closed"]
        self.assertFalse(c["pickerOpen"], "toggle closes the picker and releases the lift: %r" % c)
        self.assertEqual(c["lifted"], [], "no .lifted remains: %r" % c)

    def test_6_a_reload_restores_the_partition_column_2_on_its_session_at_the_dragged_width(self):
        r = self._r()
        s = r["s6"]
        self.assertEqual(s["frameIds"], ["f-chat", "f-chat-2"], "both columns return after a reload: %r" % s)
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B]}]}, "the v2 store, as written")
        self.assertEqual(s["sets"], {"2": [SID_B]})
        self.assertEqual(s["col2Active"], SID_B, "column 2 comes back on B (its blob's tab, still a member): %r" % s)
        self.assertEqual(s["col2Tabs"], [SID_B], "…and lists B alone")
        self.assertNotIn(SID_B, s["col1Tabs"], "column 1 comes back without B: %r" % s["col1Tabs"])
        # column 1 was ON B when B moved away (step 4): the re-point fell to its first visible member and the blob
        # persisted that, so the reload restores a member — never the tab that lives in column 2
        self.assertNotEqual(s["col1Active"], SID_B, "a reload never re-activates a session another column holds: %r" % s)
        self.assertIn(s["col1Active"], s["col1Tabs"], "column 1 comes back on one of its own: %r" % s)
        self.assertLessEqual(abs(s["pane2W"] - r["s4"]["after2"]), SLACK_PX,
                             "column 2 comes back at the width the drag left it: %r vs %r" % (s["pane2W"], r["s4"]["after2"]))
        self.assertLessEqual(abs(s["pane1W"] - r["s4"]["after1"]), SLACK_PX,
                             "…and so does column 1: %r vs %r" % (s["pane1W"], r["s4"]["after1"]))
        self.assertFalse(s["pickerOpen"]); self.assertEqual(s["lifted"], [])

    def test_7_the_cross_on_the_last_later_column_returns_its_session_to_the_first_column(self):
        s = self._r()["s7"]
        self.assertTrue(s["pane2Gone"], "the cross removes the pane, its gutter and its iframe")
        self.assertEqual(s["frameIds"], ["f-chat"])
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": []}, "an empty v2 store, never the v1 array")
        self.assertEqual(s["sets"], {})
        self.assertIn(SID_B, s["col1Tabs"], "B is column 1's again: %r" % s["col1Tabs"])
        self.assertNotIn("chat2", s["grow"] or {}, "the closed column's grow leaves the store: %r" % s["grow"])

    def test_8_the_whole_story_runs_in_well_under_half_a_minute(self):
        r = self._r()
        self.assertLess(r["ms"], 30000, "the driver waits on conditions, never on fixed sleeps: %d ms" % r["ms"])

    def test_9_a_tab_dragged_into_the_right_edge_opens_a_column_and_dragged_onto_another_column_moves_there(self):
        """The drag (the user 2026-09-11): a real pointer drag, the shell's zones mounted on the page's tabDrag message,
        the rectangle honest to the drop's geometry, the drops through __rompMoveTab, everything unmounted after."""
        s = self._r()["s9"]
        p1, row = s["pane1"], s["row"]
        # one column: the first is the source AND the rightmost, so the edge zone alone, under the strip, a fifth of the pane
        self.assertEqual(len(s["zones"]), 1, "no column zone on the source pane, none on the other panes: %r" % s["zones"])
        e = s["zones"][0]
        self.assertEqual(e["cls"], "col-drop col-drop-edge"); self.assertEqual(e["pane"], "chat-pane"); self.assertIsNone(e["refused"])
        want_w = max(72, min(180, 0.2 * p1["width"]))
        self.assertLessEqual(abs(e["width"] - want_w), 1, "the edge is a fifth of the pane, 72 to 180 px: %r for a pane %r wide" % (e["width"], p1["width"]))
        self.assertLessEqual(abs(e["right"] - (p1["left"] + p1["width"])), 1, "flush with the pane's right edge: %r" % e)
        strip_bottom = s["stripBottom"]["y"] + s["stripBottom"]["h"]
        self.assertLessEqual(abs(e["rtop"] - strip_bottom), 2, "the source pane's edge starts under its strip (stripH from the page): %r vs %r" % (e["rtop"], strip_bottom))
        self.assertTrue(e["top"].endswith("px"), "the top is set inline, in px: %r" % e["top"])
        self.assertLessEqual(abs(float(e["top"][:-2]) - (strip_bottom - p1["top"])), 2, "…to the strip's bottom in the pane's own pixels: %r" % e["top"])
        # the rectangle over the edge: the pane's right half at the row's height, B's name as its line, the accent dress
        g = s["ghost"]
        self.assertEqual(g["cls"], "on"); self.assertEqual(g["display"], "flex")
        self.assertEqual(g["text"], "api", "the dragged session's name, no verb")
        self.assertLessEqual(abs(g["left"] - (p1["left"] + p1["width"] / 2)), 1, "left = the pane's middle: %r vs %r" % (g, p1))
        self.assertLessEqual(abs(g["width"] - p1["width"] / 2), 1, "width = half the pane: %r vs %r" % (g, p1))
        self.assertLessEqual(abs(g["top"] - row["top"]), 1); self.assertLessEqual(abs(g["height"] - row["height"]), 1)
        self.assertEqual(g["bg"], "rgba(156, 210, 255, 0.12)", "the accent wash")
        self.assertIn("rgb(156, 210, 255)", g["shadow"]); self.assertIn("2px", g["shadow"]); self.assertIn("inset", g["shadow"])
        self.assertEqual(g["font"], "11px/600", "the rail's label dress")
        self.assertEqual(g["color"], "rgb(138, 138, 138)", "…in the rail's label colour")
        self.assertEqual(s["light"]["bg"], "rgba(194, 65, 12, 0.1)", "the light twin's wash")
        self.assertIn("rgb(194, 65, 12)", s["light"]["shadow"], "the ring resolves through --accent under the light theme: %r" % s["light"])
        self.assertEqual(s["light"]["color"], "rgb(93, 87, 78)", "the line takes the rail's light label colour, never the dark grey on the cream wash: %r" % s["light"])
        # the drop: column 2 on B, column 1 without B, the honest half; everything unmounted, the rectangle hidden
        a = s["after"]
        self.assertEqual(a["frameIds"], ["f-chat", "f-chat-2"]); self.assertEqual(json.loads(a["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B]}]})
        self.assertEqual(s["col2Tabs"], [SID_B]); self.assertNotIn(SID_B, s["col1Tabs"]); self.assertEqual(s["col1Active"], SID_A, "column 1 keeps A")
        self.assertLessEqual(abs(s["pane2W"] - (p1["width"] - 7) / 2), SLACK_PX, "the new column is the half the rectangle promised: %r vs %r" % (s["pane2W"], p1))
        d = s["afterDrop"]
        self.assertEqual(d["cls"], "", "the rectangle hidden after the drop"); self.assertEqual(d["display"], "none")
        self.assertEqual(d["zones"], 0, "every zone unmounted")
        # back: from column 2, alone, onto column 1's pane — one zone (no edge for a twin), the cue on it, the drop brings B home and closes column 2
        self.assertEqual([(z["pane"], z["col"], z["cls"]) for z in s["backZones"]], [("chat-pane", "", "col-drop")], "column 1's whole-pane zone alone: %r" % s["backZones"])
        z1 = s["backZones"][0]
        self.assertLessEqual(abs(z1["width"] - s["pane1W"]), 1, "the zone covers the whole pane"); self.assertEqual(z1["top"], "")
        self.assertEqual(s["overBack"], ["chat-pane"], "the cue on the zone under the pointer"); self.assertEqual(s["ghostBack"], "", "no rectangle for a column zone")
        h = s["home"]
        self.assertEqual(h["frameIds"], ["f-chat"]); self.assertEqual(json.loads(h["cols"]), {"v": 2, "cols": []}); self.assertEqual(h["sets"], {})
        self.assertIn(SID_B, s["homeTabs"]); self.assertEqual(s["homeActive"], SID_B, "the move's focus shows B where it landed")
        self.assertEqual(s["homeZones"], 0)

    def test_10_a_blob_from_before_the_partition_hands_its_draft_for_a_session_shown_elsewhere_to_that_column(self):
        """A v1 column was a whole chat page, so its blob may hold drafts for many sessions while the migration keeps one
        (review find 2026-09-11): the page offers state for a session it does not show to the shell (orphanState), which
        takes it and hands it to the column that shows the session — here A's draft from column 2's blob into column 1."""
        s = self._r()["s10"]
        self.assertEqual(json.loads(s["cols"]), {"v": 2, "cols": [{"n": 2, "ids": [SID_B]}]}, "the v1 store migrated to B's column: %r" % s)
        self.assertEqual(s["col2Tabs"], [SID_B])
        self.assertEqual(s["draftInCol1"], ORPHAN_DRAFT, "A's draft, held in column 2's blob, is in column 1's box once A is picked there: %r" % s)
        self.assertNotIn(SID_A, ((s["blob2"] or {}).get("drafts") or {}), "…and left column 2's blob: %r" % s["blob2"])


if __name__ == "__main__":
    unittest.main()
