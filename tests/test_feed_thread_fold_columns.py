#!/usr/bin/env python3
"""T263c (the user 2026-09-08): in the feed's group-by-session mode, a session's run folds INDEPENDENTLY per
column — collapse its Blocked (or Completed) cards while its Working cards stay open.

The fold used to be keyed by session alone, so the caret on any column's header folded the session across the
board. It is now keyed by (session, column). The served guard drives the real /feed page from a hermetic
kernel and hands it a SYNTHETIC payload (one session, one card blocked on the user and one working) through
the page's own frame path (a MessageEvent, exactly what the socket shim dispatches), then clicks the caret on
the session's header in the Blocked column and reads the DOM: that header folds and shows its count, the same
session's header in the Working column stays open with its card visible; a reload keeps the per-column fold.
Red on main: the Working header folds too (one key per session).

Screenshots with FEED_FOLD_SHOTS=<path-prefix> (dark + light). Skips LOUDLY without the extension deps or a
Playwright browser (CI installs none); the CI-safe pins ride ui/webview/feed-thread-fold.test.ts and the
executed key rules ui/webview/feed-view-state.test.ts. All fixtures synthetic (the notes-api demo world).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)

SID = "aaaaaaaa-1111-2222-3333-777777777777"
BLOCKED = "cccccccc-1111-2222-3333-000000000001"
WORKING = "cccccccc-1111-2222-3333-000000000002"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 520 }, deviceScaleFactor: 2 });
const errors = [];   // an exception mid-render aborts the view-state write: every page error is evidence
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 300)); });
await page.goto(cfg.feed);
// the columns are built by the first render, which the first payload triggers: wait for the page's script, not the DOM
await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
await page.waitForTimeout(600);
// the synthetic payload: ONE session with a card blocked on the user and a card working, grouped mode (the default)
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, column, text, t) => ({ itemId, sid: cfg.sid, name: "web", color: { bg: "#1EA1EB", fg: "#ffffff" },
  text, t, live: true, turnId: "turn-" + itemId.slice(-1), trgb: [30, 161, 235], column, tree: [] });   // tree: the card's (empty) sub-goal DAG — render iterates it
const payload = { type: "feed", asks: [ask(cfg.blocked, "needs_input", "notes-api: pick the retry policy", now - 300),
                                        ask(cfg.working, "working", "notes-api: draft the search index", now - 60)],
                  sessions: [{ sid: cfg.sid, name: "web" }], order: [cfg.sid] };
const deliver = (m) => page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: m })); }, m);
await deliver(payload);
await page.waitForSelector(`[data-key="a:${cfg.blocked}"]`, { timeout: 10000 });
await page.waitForSelector(`[data-key="a:${cfg.working}"]`, { timeout: 10000 });
await page.waitForTimeout(300);
// where each card sits: its column container and that container's header for the session
const survey = () => page.evaluate((cfg) => {
  const colOf = (el) => el ? el.closest(".feed-col") : null;
  const headIn = (col) => col ? col.querySelector(`.feed-sess-head[data-fsid="${cfg.sid}"]`) : null;
  const card = (id) => document.querySelector(`[data-key="a:${id}"]`);
  const read = (col) => {
    const h = headIn(col);
    return { present: !!col, head: !!h, folded: !!h && h.classList.contains("folded"), caret: h ? h.querySelector(".feed-sess-fold")?.textContent : null,
             count: h ? (h.querySelector(".feed-sess-foldn")?.style.display === "none" ? null : h.querySelector(".feed-sess-foldn")?.textContent) : null,
             cards: col ? col.querySelectorAll("[data-key^='a:']").length : 0, id: col ? (col.querySelector(".feed-col-list")?.id || col.id) : null };
  };
  const heads = Array.from(document.querySelectorAll(".feed-sess-head")).map((h) => ({ col: h.closest(".feed-col")?.querySelector(".feed-col-list")?.id || null, folded: h.classList.contains("folded") }));
  const cols = Array.from(document.querySelectorAll(".feed-col-list")).map((c) => c.id);
  return { blocked: read(colOf(card(cfg.blocked)) || document.getElementById(cfg.blockedCol || "")), working: read(colOf(card(cfg.working)) || document.getElementById(cfg.workingCol || "")),
           heads, cols, stored: localStorage.getItem("romp:feedview"), keys: Object.keys(localStorage),
           probe: (() => { try { localStorage.setItem("romp:probe", "x"); const v = localStorage.getItem("romp:probe"); localStorage.removeItem("romp:probe"); return v; } catch (e) { return "ERR " + e; } })(),
           hidden: document.hidden, empty: !!document.querySelector(".feed-empty"), listKids: document.querySelectorAll(".feed-col-list > *").length };
}, cfg);
const before = await survey();
if (!before.blocked.present || !before.working.present) { console.error("cards did not land in two columns: " + JSON.stringify(before)); process.exit(1); }
cfg.blockedCol = before.blocked.id; cfg.workingCol = before.working.id;   // remembered: a folded column hides its cards
// CLICK the caret on the session's header in the BLOCKED column
await page.click(`#${cfg.blockedCol} .feed-sess-head[data-fsid="${cfg.sid}"] .feed-sess-fold`);
await page.waitForTimeout(400);
const folded = await survey();
await page.mouse.move(5, 500);
await page.waitForTimeout(150);
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-dark.png" });
// LIGHT theme: the classes the feed's theme switch sets
await page.evaluate(() => document.body.classList.add("chat-theme-yatharth", "theme-light"));
await page.waitForTimeout(250);
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-light.png" });
await page.evaluate(() => document.body.classList.remove("chat-theme-yatharth", "theme-light"));
// RELOAD: the per-column fold persists (the state is written at the end of every render)
await page.reload();
await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
await page.waitForTimeout(600);
await page.waitForTimeout(300);
await deliver(payload);
await page.waitForSelector(`[data-key="a:${cfg.working}"]`, { timeout: 10000 });
await page.waitForTimeout(300);
const reloaded = await survey();
fs.writeSync(1, "RESULT:" + JSON.stringify({ before, folded, reloaded, errors }) + "\n");
await browser.close();
process.exit(0);
"""


GROUP_DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 520 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
await page.goto(cfg.feed);
await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
await page.waitForTimeout(600);
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, column, text, t, extra) => Object.assign({ itemId, sid: cfg.sid, name: "web", color: { bg: "#1EA1EB", fg: "#ffffff" },
  text, t, live: true, turnId: "turn-solo-" + itemId.slice(-1), trgb: [30, 161, 235], column, tree: [] }, extra || {});
// m1 + m2 share a typed turn (turnId + groupTitle) → one turn-group card, in Blocked (its worst member)
const grp = { turnId: "turn-group-1", groupTitle: "notes-api: ship the search index" };
const payload = { type: "feed", asks: [ask(cfg.m1, "needs_input", "notes-api: pick the index backend", now - 300, grp),
                                        ask(cfg.m2, "completed", "notes-api: write the index schema", now - 240, grp),
                                        ask(cfg.c3, "completed", "notes-api: tune the retry curve", now - 60)],
                  sessions: [{ sid: cfg.sid, name: "web" }], order: [cfg.sid] };
const deliver = (m) => page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: m })); }, m);
await deliver(payload);
await page.waitForSelector(`[data-key="g:turn-group-1"]`, { timeout: 10000 });
await page.waitForSelector(`[data-key="a:${cfg.c3}"]`, { timeout: 10000 });
await page.waitForTimeout(300);
const survey = () => page.evaluate((cfg) => {
  const listOf = (el) => el ? el.closest(".feed-col")?.querySelector(".feed-col-list")?.id || null : null;
  const heads = Array.from(document.querySelectorAll(".feed-sess-head")).map((h) => ({
    col: h.closest(".feed-col")?.querySelector(".feed-col-list")?.id || null, folded: h.classList.contains("folded"),
    count: h.querySelector(".feed-sess-foldn")?.style.display === "none" ? null : h.querySelector(".feed-sess-foldn")?.textContent }));
  return { groupCol: listOf(document.querySelector('[data-key="g:turn-group-1"]')), soloCol: listOf(document.querySelector(`[data-key="a:${cfg.c3}"]`)),
           groupShown: !!document.querySelector('[data-key="g:turn-group-1"]'), soloShown: !!document.querySelector(`[data-key="a:${cfg.c3}"]`),
           heads, stored: localStorage.getItem("romp:feedview") };
}, cfg);
const before = await survey();
if (!before.groupCol || !before.soloCol) { console.error("cards did not land: " + JSON.stringify(before)); process.exit(1); }
// FOLD both runs (the group's Blocked run, the solo card's Completed run)
for (const col of [before.groupCol, before.soloCol]) {
  await page.click(`#${col} .feed-sess-head[data-fsid="${cfg.sid}"] .feed-sess-fold`);
  await page.waitForTimeout(250);
}
const folded = await survey();
// JUMP to the completed MEMBER of the group (a bell-entry click / notification tap): the run it RENDERS in — Blocked,
// the group's column — must open; the Completed run, an unrelated fold, must stay folded
await deliver({ romp: "revealCard", itemId: cfg.m2, sid: cfg.sid });
await page.waitForTimeout(500);
const jumped = await survey();
fs.writeSync(1, "RESULT:" + JSON.stringify({ before, folded, jumped, groupCol: before.groupCol, soloCol: before.soloCol, errors }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedFoldIsPerColumn(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="feedfold-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        cls.port = _free_port()
        cls.token = "testtok-feedfold"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(os.path.join(cls.lab, "kernel.log"), "w"), stderr=subprocess.STDOUT, env=env)
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

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_a_jump_into_a_folded_turn_group_member_opens_the_run_the_group_renders_in(self):
        # T263d (review of T263c): a turn-group's members render in the GROUP's column (its worst member), so the
        # jump must unfold that run — keying by the member's own column opened the unrelated Completed run and
        # left the group's Blocked run shut (red on the first cut)
        cfg = os.path.join(self.lab, "cfg-group.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token), "sid": SID,
                       "m1": "eeeeeeee-1111-2222-3333-000000000001", "m2": "eeeeeeee-1111-2222-3333-000000000002",
                       "c3": "eeeeeeee-1111-2222-3333-000000000003"}, f)
        driver = os.path.join(self.lab, "driver-group.mjs")
        with open(driver, "w") as f:
            f.write(GROUP_DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertEqual(r.get("errors"), [], "the page threw nothing: %r" % r.get("errors"))
        gcol, scol = r["groupCol"], r["soloCol"]
        self.assertEqual(gcol, "col-needsInput-list", "the mixed-state group renders in Blocked, its worst member's column: %r" % r["before"])
        self.assertEqual(scol, "col-completed-list", "the solo completed card renders in Completed: %r" % r["before"])
        f_ = r["folded"]
        self.assertFalse(f_["groupShown"] or f_["soloShown"], "both runs folded: nothing rendered: %r" % f_)
        self.assertEqual(sorted(h["folded"] for h in f_["heads"]), [True, True], "both headers folded: %r" % f_["heads"])
        j = r["jumped"]
        heads = {h["col"]: h for h in j["heads"]}
        self.assertTrue(j["groupShown"], "the jump opened the run the member RENDERS in — the group is back on screen: %r" % j)
        self.assertFalse(heads[gcol]["folded"], "the Blocked header (the group's run) is open: %r" % heads)
        self.assertTrue(heads[scol]["folded"], "the Completed run — an unrelated fold — stays folded: %r" % heads)
        self.assertFalse(j["soloShown"], "…its solo card still hidden: %r" % j)

    def test_folding_a_session_in_one_column_leaves_its_other_column_open_and_persists(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token), "sid": SID,
                       "blocked": BLOCKED, "working": WORKING, "shots": os.environ.get("FEED_FOLD_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        b, f_, rl = r["before"], r["folded"], r["reloaded"]
        self.assertEqual(r.get("errors"), [], "the page threw nothing (an exception mid-render would skip the view-state write): %r" % r.get("errors"))
        # the world: the two cards sit in different columns, each under a header for the one session
        self.assertNotEqual(b["blocked"]["id"], b["working"]["id"], "two columns: %r" % b)
        self.assertTrue(b["blocked"]["head"] and b["working"]["head"], "a session header heads each run: %r" % b)
        self.assertFalse(b["blocked"]["folded"] or b["working"]["folded"], "nothing folded to start: %r" % b)
        # THE FIX: the Blocked header folded (caret ▸, its one card counted onto it, no card rendered), the Working header
        # untouched (caret ▾, its card still on screen) — red on main, where one key folded the session in every column
        self.assertTrue(f_["blocked"]["folded"], "the clicked column folded: %r" % f_["blocked"])
        self.assertEqual((f_["blocked"]["caret"], f_["blocked"]["count"], f_["blocked"]["cards"]), ("▸", "1", 0), "folded header stands in for its one card: %r" % f_["blocked"])
        self.assertFalse(f_["working"]["folded"], "the other column stays open: %r" % f_["working"])
        self.assertEqual((f_["working"]["caret"], f_["working"]["cards"]), ("▾", 1), "…its card still visible: %r" % f_["working"])
        self.assertEqual(sorted(h["folded"] for h in f_["heads"]), [False, True], "exactly one of the session's two headers is folded: %r" % f_["heads"])
        # after a reload the same column is folded and the other open
        self.assertTrue(rl["blocked"]["folded"], "the fold survives a reload: %r (stored before reload: %r)" % (rl["blocked"], (f_["stored"] or "")[:300]))
        self.assertFalse(rl["working"]["folded"], "…and stays per column: %r" % rl["working"])
        # persisted per column: the stored key names the column, not the bare session
        self.assertIn(SID + "\\u0000needsInput", rl["stored"] or "", "the stored fold is (session, column): %r" % (rl["stored"] or "")[:300])
        self.assertNotIn('"%s"' % SID, rl["stored"] or "", "…never the bare sid")


if __name__ == "__main__":
    unittest.main()
