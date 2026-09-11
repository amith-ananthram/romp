#!/usr/bin/env python3
"""T347 (the user 2026-09-11, who wanted the focused session's cards on top of the feed): THE FOCUSED SESSION SECTION.

When a session tab has focus in the chat pane, the feed shows that session's cards ABOVE a horizontal divider — the
board's three columns (Working / Blocked / Completed), a miniature of the feed for one session, headed by the session's
name — while the board below stays exactly as it is, so those cards appear twice. OFF by default; the View menu's
fourth row ("Show focused session") switches it, persisted in the feed's view state under `focused`; the kernel relays
the chat pane's active tab to the feed clients of the same window as {type:"activeChat", id}. EVENT-based: the section
rebuilds on that frame and on the feed's own frames, never on a timer; no card below moves because of it (a view, not a
move).

The served guard drives the real /feed page from a hermetic kernel and hands it a SYNTHETIC payload — the notes-api demo
world: `web` with a card in each column, `api` with one working card, `tests` with none — through the page's own frame
path (a MessageEvent, exactly what the socket shim dispatches), then walks the whole lab in one browser run:
  (a) the switch off (the default): no #feed-focus anywhere;
  (b) an activeChat frame for `web` with the switch off: still no section;
  (c) the View menu's "Show focused session" row clicked: the section is #feed-list's first child, right above
      #feed-cols, headed `web`, its three columns holding exactly web's cards (the same titles per column as web's
      cards on the board), the divider under it, every board card still where it was, the row aria-checked;
  (d) an activeChat frame for `api`: the section rebuilds with api's one card, read one animation frame after the
      dispatch (no timer to wait out);
  (e) an activeChat frame with a null id: the one quiet line "No session is focused in the chat"; a frame for `tests`,
      a session with no cards: "tests has no cards";
  (f) a reload keeps the switch on (romp:feedview carries `"focused":true`) — the section is back with the no-focus line
      (the sid is not persisted; a reloaded feed is told again) — and a fresh frame restores web's cards;
  (g) both themes: the divider and the quiet line take their colours from the theme's tokens, so the light theme
      (the classes the feed's theme switch sets) recolours them.
Screenshots with FEED_FOCUS_SHOTS=<path-prefix>: -off-dark, -off-light, -on-dark, -on-light (the "on" pair with web
focused). Skips LOUDLY without the extension deps or a Playwright browser (CI's Python jobs install none;
ROMP_SERVED_TESTS_REQUIRE=1 turns the skips red where the browser is installed). The CI-safe source pins ride
ui/webview/feed-focus-section.test.ts, the pure pick ui/webview/feed-focus-entries.test.ts, the persisted key
ui/webview/feed-view-state.test.ts, the kernel relay tests/test_kernel_active_chat_relay.py. All fixtures synthetic.
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

SID_WEB = "aaaaaaaa-1111-2222-3333-777777777777"
SID_API = "aaaaaaaa-1111-2222-3333-888888888888"
SID_TESTS = "aaaaaaaa-1111-2222-3333-999999999999"
WEB_BLOCKED = "cccccccc-1111-2222-3333-000000000001"
WEB_WORKING = "cccccccc-1111-2222-3333-000000000002"
WEB_DONE = "cccccccc-1111-2222-3333-000000000003"
API_WORKING = "cccccccc-1111-2222-3333-000000000011"
WEB_KEYS = {"f:a:" + WEB_BLOCKED, "f:a:" + WEB_WORKING, "f:a:" + WEB_DONE}

NO_FOCUS = "No session is focused in the chat"


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
// tall enough for the section AND the board under it: the screenshots show both
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
const errors = [];   // an exception mid-render aborts the view-state write: every page error is evidence
page.on("pageerror", (e) => errors.push(String(e && e.stack || e).slice(0, 400)));
page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text().slice(0, 300)); });
const ready = async () => {
  // the columns are built by the first render, which the first payload triggers: wait for the page's script, not the DOM
  await page.waitForFunction(() => document.readyState === "complete" && typeof window.acquireVsCodeApi === "function", null, { timeout: 20000 });
  await page.waitForTimeout(600);
};
await page.goto(cfg.feed);
await ready();
// the synthetic payload: web with a card in EACH column, api with one working card, tests listed with none
const now = Math.floor(Date.now() / 1000);
const ask = (itemId, sid, name, column, text, t) => ({ itemId, sid, name, color: cfg.colors[name], text, t, live: true,
  turnId: "turn-" + itemId.slice(-2), trgb: [30, 161, 235], column, tree: [] });   // tree: the card's (empty) sub-goal DAG — render iterates it
const payload = { type: "feed", asks: [
    ask(cfg.webBlocked, cfg.web, "web", "needs_input", "notes-api: pick the retry policy", now - 300),
    ask(cfg.webWorking, cfg.web, "web", "working", "notes-api: draft the search index", now - 60),
    ask(cfg.webDone, cfg.web, "web", "completed", "notes-api: write the index schema", now - 240),
    ask(cfg.apiWorking, cfg.api, "api", "working", "notes-api: wire the tag filter", now - 120)],
  sessions: [{ sid: cfg.web, name: "web" }, { sid: cfg.api, name: "api" }, { sid: cfg.tests, name: "tests" }],
  order: [cfg.web, cfg.api, cfg.tests] };
// the page's own frame path; then ONE animation frame — the handler renders synchronously, there is no timer to wait out
const deliver = (m) => page.evaluate((m) => new Promise((res) => {
  window.dispatchEvent(new MessageEvent("message", { data: m }));
  requestAnimationFrame(() => res(null));
}), m);
const frame = () => page.evaluate(() => new Promise((res) => requestAnimationFrame(() => res(null))));
// off every card: a hovered card holds the board (hover-freeze) and would defer the section's repaint to the release
const park = async () => { await page.mouse.move(2, 2); await page.waitForTimeout(120); };
await deliver(payload);
for (const id of [cfg.webBlocked, cfg.webWorking, cfg.webDone, cfg.apiWorking]) await page.waitForSelector(`#feed-cols [data-key="a:${id}"]`, { timeout: 10000 });
await park();
const survey = () => page.evaluate(() => {
  const COLS = ["asks", "needsInput", "completed"];
  const list = document.getElementById("feed-list");
  const board = document.getElementById("feed-cols");
  const sec = document.getElementById("feed-focus");
  const cards = (root) => root ? Array.from(root.querySelectorAll(".fitem[data-key]")).map((c) => ({
    key: c.dataset.key, col: (c.closest(".feed-col")?.className.match(/\bcol-(asks|needsInput|completed)\b/) || [])[1] || null,
    title: c.querySelector(".fcard-title")?.textContent || "", name: c.querySelector(".fname")?.textContent || "" })) : [];
  const shown = (e) => !!e && e.style.display !== "none";
  const q = (s) => sec ? sec.querySelector(s) : null;
  return {
    section: !!sec, label: sec ? sec.getAttribute("aria-label") : null,
    first: list && list.firstElementChild ? (list.firstElementChild.id || list.firstElementChild.className) : null,
    aboveBoard: sec ? sec.nextElementSibling === board : null,
    kids: sec ? Array.from(sec.children).map((c) => c.tagName.toLowerCase() + "." + c.className.split(" ").join(".")) : [],
    headShown: shown(q(".feed-focus-head")), headName: q(".feed-focus-head .fname")?.textContent ?? null, cap: q(".feed-focus-cap")?.textContent ?? null,
    emptyShown: shown(q(".feed-focus-empty")), emptyText: q(".feed-focus-empty")?.textContent ?? null,
    colsShown: shown(q(".feed-focus-cols")),
    chips: sec ? Array.from(sec.querySelectorAll(".feed-focus-cols .feed-col-head .fcol-chip")).map((c) => c.textContent) : [],
    counts: sec ? Object.fromEntries(COLS.map((k) => [k, q(".feed-focus-cols .col-" + k + " .feed-col-count")?.textContent ?? null])) : {},
    folds: sec ? sec.querySelectorAll(".fcol-fold").length : 0,
    divider: !!q("hr.feed-focus-divider"),
    secCards: cards(sec), boardCards: cards(board),
    hovered: !!document.querySelector(".fitem:hover"),
    stored: localStorage.getItem("romp:feedview"),
  };
});
const light = async (on) => {   // LIGHT theme: the classes the feed's theme switch sets
  await page.evaluate((on) => { document.body.classList[on ? "add" : "remove"]("chat-theme-yatharth", "theme-light"); }, on);
  await page.waitForTimeout(250);
};
const theme = () => page.evaluate(() => {
  const d = document.querySelector("#feed-focus .feed-focus-divider"), e = document.querySelector("#feed-focus .feed-focus-empty"),
        n = document.querySelector("#feed-focus .feed-focus-head .fname"), c = document.querySelector("#feed-focus .feed-focus-cap");
  if (!d || !e || !n || !c) return null;
  return { divider: getComputedStyle(d).borderTopColor, empty: getComputedStyle(e).color, cap: getComputedStyle(c).color,
           emptySize: getComputedStyle(e).fontSize, headSize: getComputedStyle(n).fontSize, capSize: getComputedStyle(c).fontSize };
});
// (a) the default: the switch off, no section
const off = await survey();
// (b) the chat focuses web while the switch is off: nothing appears
await deliver({ type: "activeChat", id: cfg.web });
const offFocused = await survey();
if (cfg.shots) {
  await page.screenshot({ path: cfg.shots + "-off-dark.png" });
  await light(true);
  await page.screenshot({ path: cfg.shots + "-off-light.png" });
  await light(false);
}
// (c) the View menu's row
const menuRows = () => page.evaluate(() => Array.from(document.querySelectorAll(".feed-viewmenu .ctx-item")).map((r) => ({
  text: r.textContent, role: r.getAttribute("role"), checked: r.getAttribute("aria-checked") })));
await page.waitForSelector("#feed-viewbtn", { state: "visible", timeout: 10000 });
await page.click("#feed-viewbtn");
await page.waitForSelector(".feed-viewmenu .ctx-item", { timeout: 10000 });
const rowsBefore = await menuRows();
await page.locator(".feed-viewmenu .ctx-item", { hasText: "Show focused session" }).click();
await page.waitForSelector(".feed-viewmenu", { state: "detached", timeout: 10000 });   // a row click closes the menu
await park();
await frame();
const on = await survey();
await page.click("#feed-viewbtn");
await page.waitForSelector(".feed-viewmenu .ctx-item", { timeout: 10000 });
const rowsAfter = await menuRows();
await page.keyboard.press("Escape");
await page.waitForSelector(".feed-viewmenu", { state: "detached", timeout: 10000 });
await park();
// (g) both themes, with web focused
const dark = await theme();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-on-dark.png" });
await light(true);
const lit = await theme();
if (cfg.shots) await page.screenshot({ path: cfg.shots + "-on-light.png" });
await light(false);
// (d) the chat focuses api: the section rebuilds on the frame, read one animation frame later
await deliver({ type: "activeChat", id: cfg.api });
const api = await survey();
// (e) no tab has focus; then a session with no cards
await deliver({ type: "activeChat", id: null });
const none = await survey();
await deliver({ type: "activeChat", id: cfg.tests });
const bare = await survey();
// (f) RELOAD: the switch persists; the sid does not (the kernel tells a registering feed again — here nothing is recorded)
const storedBefore = await page.evaluate(() => localStorage.getItem("romp:feedview"));
await page.reload();
await ready();
await deliver(payload);
await page.waitForSelector(`#feed-cols [data-key="a:${cfg.webWorking}"]`, { timeout: 10000 });
await park();
const reloaded = await survey();
await deliver({ type: "activeChat", id: cfg.web });
const restored = await survey();
// (h) Clear on the SECTION's copy clears the card: both elements dismiss and leave together; Undo brings both back
// (the review of T347: the copy ran the board builder's finish, which guarded on the board's element, so the copy
// stayed and the card below never left)
const keyState = () => page.evaluate(([k, sid]) => {
  const f = document.querySelector(`#feed-focus [data-key="f:a:${k}"]`), a = document.querySelector(`#feed-cols [data-key="a:${k}"]`);
  // the board's run header for this session in the column the card leaves (Completed): a Clear from the copy must
  // start its one-motion exit as a Clear on the card below does (the follow-up after the review)
  const head = document.querySelector(`#feed-cols .col-completed .feed-sess-head[data-fsid="${sid}"]`);
  return { copy: !!f, board: !!a, copyDismissing: !!(f && f.classList.contains("dismissing")), boardDismissing: !!(a && a.classList.contains("dismissing")),
           headExiting: !!(head && head.classList.contains("sess-exit")) };
}, [cfg.webDone, cfg.web]);
// (i) Tab from a HOVERED copy lands inside the copy, where the pointer is, not on the board's twin below
await page.hover(`#feed-focus [data-key="f:a:${cfg.webWorking}"]`);
await page.keyboard.press("Tab");
const tabbed = await page.evaluate(() => { const ae = document.activeElement; return { inCopy: !!(ae && ae.closest("#feed-focus")), inBoard: !!(ae && ae.closest("#feed-cols")), tag: ae ? ae.tagName : null }; });
await page.keyboard.press("Escape");
await park();
await page.locator(`#feed-focus [data-key="f:a:${cfg.webDone}"] .fdismiss`, { hasText: /^Clear$/ }).click();   // the card's Clear (its Continue wears the same chrome)
const clearing = await keyState();                    // right after the click: both copies wear .dismissing
await page.waitForTimeout(300);                       // the 180 ms finish, with room
const cleared = await keyState();
await page.waitForSelector("#feed-undoclear", { state: "visible", timeout: 10000 });
await page.click("#feed-undoclear");
await page.waitForSelector(`#feed-cols [data-key="a:${cfg.webDone}"]`, { timeout: 10000 });
await frame();
const undone = await keyState();
fs.writeSync(1, "RESULT:" + JSON.stringify({ off, offFocused, rowsBefore, on, rowsAfter, dark, light: lit, api, none, bare,
                                              storedBefore, reloaded, restored, tabbed, clearing, cleared, undone, errors }) + "\n");
await browser.close();
process.exit(0);
"""


class ServedFocusedSessionSection(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="feedfocus-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        cls.port = _free_port()
        cls.token = "testtok-feedfocus"
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

    @staticmethod
    def _by_col(cards, keys):
        """{column: sorted titles} of the cards whose data-key is in `keys`."""
        out = {}
        for c in cards:
            if c["key"] in keys:
                out.setdefault(c["col"], []).append(c["title"])
        return {k: sorted(v) for k, v in out.items()}

    def test_the_section_follows_the_chats_active_tab_switches_on_from_the_view_menu_and_persists(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"feed": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token),
                       "web": SID_WEB, "api": SID_API, "tests": SID_TESTS,
                       "webBlocked": WEB_BLOCKED, "webWorking": WEB_WORKING, "webDone": WEB_DONE, "apiWorking": API_WORKING,
                       "colors": {"web": {"bg": "#1EA1EB", "fg": "#ffffff"}, "api": {"bg": "#E0A526", "fg": "#000000"}},
                       "shots": os.environ.get("FEED_FOCUS_SHOTS", "")}, f)
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
        self.assertEqual(r.get("errors"), [], "the page threw nothing (an exception mid-render would skip the view-state write): %r" % r.get("errors"))
        off, on = r["off"], r["on"]
        board_keys = sorted(c["key"] for c in off["boardCards"])
        web_board_keys = {"a:" + WEB_BLOCKED, "a:" + WEB_WORKING, "a:" + WEB_DONE}
        # the world: four cards on the board, web's three in three different columns, api's one in Working
        self.assertEqual(board_keys, sorted(["a:" + WEB_BLOCKED, "a:" + WEB_WORKING, "a:" + WEB_DONE, "a:" + API_WORKING]), "four cards landed: %r" % off["boardCards"])
        self.assertEqual(self._by_col(off["boardCards"], web_board_keys),
                         {"needsInput": ["notes-api: pick the retry policy"], "asks": ["notes-api: draft the search index"], "completed": ["notes-api: write the index schema"]},
                         "web has a card in each column: %r" % off["boardCards"])
        # (a) OFF by default: no section, and nothing saved says otherwise
        self.assertFalse(off["section"], "the switch is off by default — no #feed-focus: %r" % off)
        self.assertNotIn('"focused":true', off["stored"] or "", "a fresh view state does not carry the switch on: %r" % (off["stored"] or "")[:300])
        # (b) the chat focuses web while the switch is off: still nothing (the sid is remembered, the section is not built)
        self.assertFalse(r["offFocused"]["section"], "an activeChat frame with the switch off builds no section: %r" % r["offFocused"])
        self.assertEqual(sorted(c["key"] for c in r["offFocused"]["boardCards"]), board_keys, "…and moves no card")
        # (c) the View menu: the fourth row, a ✓ row, unchecked before the click
        rows = r["rowsBefore"]
        self.assertEqual(len(rows), 4, "four rows: %r" % rows)
        self.assertEqual((rows[3]["text"], rows[3]["role"], rows[3]["checked"]), ("Show focused session", "menuitemcheckbox", "false"), "the row before the click: %r" % rows)
        self.assertEqual(r["rowsAfter"][3]["checked"], "true", "the row is aria-checked after the click: %r" % r["rowsAfter"])
        self.assertEqual([x["text"] for x in r["rowsAfter"][:3]], [x["text"] for x in rows[:3]], "the other rows are untouched")
        # the section: #feed-list's first child, directly above #feed-cols, its parts in order
        self.assertTrue(on["section"], "the click built the section: %r" % on)
        self.assertFalse(on["hovered"], "no card is hovered (a hovered card would hold the repaint): %r" % on)
        self.assertEqual(on["first"], "feed-focus", "the section is the first child of #feed-list: %r" % on["first"])
        self.assertTrue(on["aboveBoard"], "…directly before #feed-cols")
        self.assertEqual(on["label"], "the chat's focused session")
        self.assertEqual(on["kids"], ["div.feed-focus-head", "div.feed-focus-empty", "div.feed-cols.feed-focus-cols", "hr.feed-focus-divider"], "head, quiet line, columns, rule: %r" % on["kids"])
        self.assertTrue(on["divider"], "the rule under the section")
        # headed by the session's name, with the one word saying why it sits on top
        self.assertTrue(on["headShown"], "the head shows for a focused session")
        self.assertEqual(on["headName"], "web", "the head names the focused session: %r" % on["headName"])
        self.assertEqual(on["cap"], "focused")
        self.assertFalse(on["emptyShown"], "no quiet line while the session has cards: %r" % on["emptyText"])
        self.assertTrue(on["colsShown"])
        # the board's three columns, the board's chips, no fold caret; each column counts its one card
        self.assertEqual(on["chips"], ["Working", "Blocked", "Completed"], "the same column chips as the board: %r" % on["chips"])
        self.assertEqual(on["folds"], 0, "the section's heads carry no fold caret")
        self.assertEqual(on["counts"], {"asks": "1", "needsInput": "1", "completed": "1"}, "one card per column: %r" % on["counts"])
        # exactly web's cards, per column the same titles as web's cards on the board, under the section's own keys
        self.assertEqual(sorted(c["key"] for c in on["secCards"]), sorted(WEB_KEYS), "the section holds web's three cards under f: keys: %r" % on["secCards"])
        self.assertEqual(self._by_col(on["secCards"], WEB_KEYS), self._by_col(on["boardCards"], web_board_keys),
                         "per column, the section's titles are web's titles on the board: %r vs %r" % (on["secCards"], on["boardCards"]))
        self.assertTrue(all(c["name"] == "web" for c in on["secCards"]), "every copy names web: %r" % on["secCards"])
        # THE BOARD IS UNTOUCHED: every card below is still there, same count, same columns (a view, not a move)
        self.assertEqual(sorted(c["key"] for c in on["boardCards"]), board_keys, "every board card is still on the board: %r" % on["boardCards"])
        self.assertEqual({c["key"]: c["col"] for c in on["boardCards"]}, {c["key"]: c["col"] for c in off["boardCards"]}, "…in the column it had")
        self.assertIn('"focused":true', on["stored"] or "", "the switch is persisted in the view state: %r" % (on["stored"] or "")[:300])
        # (g) both themes: the rule and the quiet line read the theme's tokens, so the light theme recolours them
        dark, lit = r["dark"], r["light"]
        self.assertIsNotNone(dark, "the theme probe found the section's parts")
        self.assertEqual(dark["divider"], "rgba(255, 255, 255, 0.12)", "dark: the menus' hairline (--menu-border): %r" % dark)
        self.assertEqual(lit["divider"], "rgba(0, 0, 0, 0.12)", "light: the light theme's hairline: %r" % lit)
        self.assertNotEqual(dark["empty"], lit["empty"], "the quiet line's colour follows --dim across themes: %r vs %r" % (dark, lit))
        self.assertNotEqual(dark["cap"], lit["cap"], "…and so does the 'focused' word's")
        self.assertEqual(lit["headSize"], dark["headSize"], "geometry is not theme: the head's size holds across themes")
        # (d) the chat focuses api: the section rebuilt on the frame — api's one card in Working, web's copies gone
        api = r["api"]
        self.assertEqual(api["headName"], "api", "the head follows the focus: %r" % api["headName"])
        self.assertEqual([(c["key"], c["col"], c["title"]) for c in api["secCards"]], [("f:a:" + API_WORKING, "asks", "notes-api: wire the tag filter")], "exactly api's card: %r" % api["secCards"])
        self.assertEqual(api["counts"], {"asks": "1", "needsInput": "", "completed": ""}, "the counts follow: %r" % api["counts"])
        self.assertEqual(sorted(c["key"] for c in api["boardCards"]), board_keys, "the board below still holds all four")
        # (e) no tab has focus: the head steps aside for the one quiet line; then a focused session with no cards
        none = r["none"]
        self.assertTrue(none["section"] and none["emptyShown"], "the section stays, with the quiet line: %r" % none)
        self.assertEqual(none["emptyText"], NO_FOCUS)
        self.assertFalse(none["headShown"] or none["colsShown"], "no head, no columns without a focus: %r" % none)
        self.assertEqual(none["secCards"], [], "no copies without a focus")
        self.assertTrue(none["divider"], "the rule stays, so the section still reads as a section")
        bare = r["bare"]
        self.assertEqual((bare["headShown"], bare["headName"], bare["emptyShown"], bare["emptyText"], bare["colsShown"], bare["secCards"]),
                         (True, "tests", True, "tests has no cards", False, []), "a focused session with no cards keeps its head and says so: %r" % bare)
        # (f) RELOAD: the switch survives (the stored blob), the sid does not — the fresh page shows the no-focus line
        # until the kernel tells it again; a fresh frame restores web's cards
        self.assertIn('"focused":true', r["storedBefore"] or "", "stored before the reload: %r" % (r["storedBefore"] or "")[:300])
        rl, rs = r["reloaded"], r["restored"]
        self.assertTrue(rl["section"], "the reloaded page builds the section from the persisted switch: %r" % rl)
        self.assertEqual((rl["emptyShown"], rl["emptyText"], rl["headShown"]), (True, NO_FOCUS, False), "…with the no-focus line, the sid unpersisted: %r" % rl)
        self.assertEqual(rs["headName"], "web", "a fresh activeChat frame restores the head: %r" % rs["headName"])
        self.assertEqual(sorted(c["key"] for c in rs["secCards"]), sorted(WEB_KEYS), "…and web's three copies: %r" % rs["secCards"])
        self.assertEqual(self._by_col(rs["secCards"], WEB_KEYS), self._by_col(rs["boardCards"], web_board_keys), "per column the same titles as the board's")
        self.assertEqual(sorted(c["key"] for c in rs["boardCards"]), board_keys, "the board below is whole after the reload too")

        # (h) Clear on the section's copy: both elements dismiss, both leave, Undo restores both (review of T347)
        cg, cd, un = r["clearing"], r["cleared"], r["undone"]
        self.assertEqual((cg["copy"], cg["board"], cg["copyDismissing"], cg["boardDismissing"]), (True, True, True, True),
                         "right after the click both copies wear .dismissing: %r" % cg)
        self.assertTrue(cg["headExiting"], "the board run's header (web's only Completed card) starts its one-motion exit from a Clear on the copy: %r" % cg)
        tb = r["tabbed"]
        self.assertEqual((tb["inCopy"], tb["inBoard"]), (True, False), "Tab from a hovered copy lands inside the copy, where the pointer is: %r" % tb)
        self.assertEqual((cd["copy"], cd["board"]), (False, False), "after the finish neither element remains: %r" % cd)
        self.assertEqual((un["copy"], un["board"], un["copyDismissing"], un["boardDismissing"]), (True, True, False, False),
                         "Undo restores the card below and its copy above, neither still dismissing: %r" % un)


if __name__ == "__main__":
    unittest.main()
