#!/usr/bin/env python3
"""The chat pane never jumps to another session on its own (T357, the user 2026-09-11). On the real chat page: two
sessions, the user on the first; its tab leaves the strip on its own (federation's synthetic `closed` with hostDrop, the
frame a host relay going down produces, and the merged strip without that host's tabs) and the pane goes UNFOCUSED: no
active tab, the body names the session that vanished, the composer disabled with no session name, no session colour on
the box. A tabOrder push that adds a NEW session changes nothing (no adoption). A tabOrder push listing the vanished
session again restores focus to it,
and its transcript comes back. Screenshots of the unfocused state, dark and light (PV_SHOTS names the folder).
The reload road (the review's HIGH, the user's actual trigger): the persisted state names a REMOTE tab this kernel never
lists; after a reload the pane stays unfocused naming it, the local sessions adopt nothing, the remote strip entry
restores focus to it, and a pick made before the relay wins.
The #only= filter (the review's probe): the persisted active tab hidden by the filter goes unfocused, its transcript off
screen, named as hidden by the view. Synthetic fixtures only: placeholder UUIDs, a hermetic state root, an invented
notes-api world."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID_A = "11111111-2222-3333-4444-555555555555"
SID_B = "aaaaaaaa-1111-2222-3333-444444444444"
SID_C = "bbbbbbbb-1111-2222-3333-444444444444"   # a session that appears while the user's tab is away: never adopted
REMOTE = "REMOTEBOX:cccccccc-1111-2222-3333-444444444444"   # a remote host's session this kernel never lists: the reload road's awaited tab
PROVISIONAL = "new-" + "docs"   # a provisional create's id (ui/webview/provisional.ts): a persisted choice that can never be listed again


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _transcript(sid, text):
    u, a = "u-" + sid[:8], "a-" + sid[:8]
    return (json.dumps({"type": "user", "uuid": u, "parentUuid": None, "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": sid,
                        "message": {"role": "user", "content": "Where do the notes-api docs start?"}}) + "\n"
            + json.dumps({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": sid,
                          "message": {"role": "assistant", "model": "claude-opus-5", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}) + "\n")


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
await page.goto(cfg.chat);
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, 2, { timeout: 20000 });
const state = () => page.evaluate(() => {
  const act = document.querySelector("#tabs .tab.active[data-id]");
  const empty = document.getElementById("empty-state");
  const ta = document.getElementById("composer-input");
  const box = document.getElementById("composer");
  const note = document.getElementById("composer-note");
  const views = Array.from(document.querAll ? [] : document.querySelectorAll("#content .view, #content [data-sid]"));
  return { active: act ? act.dataset.id : null, tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id),
           empty: empty && getComputedStyle(empty).display !== "none" ? { text: empty.textContent, unfocused: empty.classList.contains("unfocused"), vanished: empty.dataset.vanished || "" } : null,
           composer: ta ? { disabled: ta.disabled, placeholder: ta.placeholder, identity: box ? box.style.getPropertyValue("--composer-identity") : "",
                            ph: (() => { const ph = document.getElementById("composer-ph"); return ph && getComputedStyle(ph).display !== "none" ? ph.textContent : ""; })() } : null,
           note: note ? note.textContent : null,
           accent: document.body.style.getPropertyValue("--active-accent"),
           statusline: (document.getElementById("statusline") || {}).textContent || "",
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
});
const inject = (frame) => page.evaluate((f) => { window.postMessage(f, "*"); }, frame);
const out = {};
// the user picks A
await page.click('#tabs .tab[data-id="' + cfg.sidA + '"]');
await page.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.sidA, { timeout: 10000 });
await page.waitForFunction(() => !document.getElementById("composer-input").disabled, null, { timeout: 10000 });
out.before = await state();
// A's tab leaves on its own: the frames federation dispatches when a host drops off — a synthetic `closed` per session
// of that host, and the merged strip without them (the strip paints a tab for every id the last tabOrder carried)
const B_TAB = { id: cfg.sidB, name: "api", color: { bg: "#9cd2ff", fg: "#0c1a2e" } };
const A_TAB = { id: cfg.sidA, name: "web", color: { bg: "#f2b26b", fg: "#1a1206" } };
const C_TAB = { id: cfg.sidC, name: "docs", color: { bg: "#b5e3a1", fg: "#0f1f0a" } };
await inject({ type: "closed", id: cfg.sidA, hostDrop: true });
await inject({ type: "tabOrder", order: [cfg.sidB], tabs: [B_TAB], live: [cfg.sidB], skeleton: [] });
await page.waitForFunction((sid) => !document.querySelector("#tabs .tab.active[data-id]") && !document.querySelector('#tabs .tab[data-id="' + sid + '"]'), cfg.sidA, { timeout: 10000 });
await page.waitForTimeout(300);
out.unfocused = await state();
const shot = async (name) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); await page.screenshot({ path: cfg.shots + "/" + name + ".png" }); };   // the whole pane: the strip, the body's line, the disabled box
await shot("romp_chat-unfocused-pane-dark");
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(200);
await shot("romp_chat-unfocused-pane-light");
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(100);
// another session appearing meanwhile takes nothing: a NEW tab (docs) joins the strip and the pane stays unfocused
await inject({ type: "tabOrder", order: [cfg.sidB, cfg.sidC], tabs: [B_TAB, C_TAB], live: [cfg.sidB, cfg.sidC], skeleton: [cfg.sidC] });
await page.waitForFunction((sid) => !!document.querySelector('#tabs .tab[data-id="' + sid + '"]'), cfg.sidC, { timeout: 10000 });
await page.waitForTimeout(300);
out.otherArrived = await state();
// A's tab is re-listed (the host re-attached): the kernel's strip names it a skeleton (this page holds no frame for it
// any more), focus goes back to it, the skeleton branch asks for its frame, and its transcript returns
await inject({ type: "tabOrder", order: [cfg.sidA, cfg.sidB, cfg.sidC], tabs: [A_TAB, B_TAB, C_TAB], live: [cfg.sidA, cfg.sidB, cfg.sidC], skeleton: [cfg.sidA, cfg.sidC] });
await page.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.sidA, { timeout: 10000 });
// the restore lands in two steps: the strip's skeleton tab takes focus at once, and its session frame follows (the
// skeleton branch asks for it); the state is read once the transcript is on screen, which is when the window border
// wears the session's colour again
await page.waitForFunction(() => { const e = document.getElementById("empty-state"); const ta = document.getElementById("composer-input"); return (!e || getComputedStyle(e).display === "none") && ta && !ta.disabled && document.body.style.getPropertyValue("--active-accent") !== ""; }, null, { timeout: 20000 });
out.restored = await state();

// ---- the reload road (the review's HIGH, the user's actual trigger): a kernel restart RELOADS the page, so no dismissal
// runs; the page remembers the REMOTE tab it showed, the local kernel's sessions arrive first, and the remote host relays
// later. Seed the persisted state with a remote sid and reload: the pane must stay unfocused naming it, adopt nothing,
// and focus it when its strip entry arrives; a pick in between wins.
const seed = () => page.evaluate((remote) => {
  const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-"));
  const st = key ? JSON.parse(localStorage.getItem(key) || "{}") : {};
  // the name as the page persists it for a remote session: federation prefixes the host (host-prefix.ts hostPrefix)
  localStorage.setItem(key, JSON.stringify({ ...st, activeId: remote, activeName: "REMOTEBOX:web" }));
  return key;
}, cfg.remote);
out.seedKey = await seed();
await page.reload();
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, 2, { timeout: 20000 });
await page.waitForTimeout(600);   // the local sessions' frames land: nothing may adopt
out.reloadAwaiting = await state();
const R_TAB = { id: cfg.remote, name: "web", color: { bg: "#f2b26b", fg: "#1a1206" } };
await inject({ type: "tabOrder", order: [cfg.remote, cfg.sidA, cfg.sidB, cfg.sidC], tabs: [R_TAB, A_TAB, B_TAB, C_TAB], live: [cfg.remote, cfg.sidA, cfg.sidB, cfg.sidC], skeleton: [cfg.remote, cfg.sidC] });
await page.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.remote, { timeout: 10000 });
out.reloadRestored = await state();
// …and a pick in between wins: seed again, reload, click B before the remote host relays; the relay then changes nothing
await seed();
await page.reload();
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, 2, { timeout: 20000 });
await page.waitForTimeout(300);
await page.click('#tabs .tab[data-id="' + cfg.sidB + '"]');
await page.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.sidB, { timeout: 10000 });
await inject({ type: "tabOrder", order: [cfg.remote, cfg.sidA, cfg.sidB, cfg.sidC], tabs: [R_TAB, A_TAB, B_TAB, C_TAB], live: [cfg.remote, cfg.sidA, cfg.sidB, cfg.sidC], skeleton: [cfg.remote, cfg.sidC] });
await page.waitForTimeout(500);
out.pickWins = await state();
// ---- the follow-up's roads ----
// (e) an ADOPTED tab (the pane never clicked) is persisted like a pick: clear the choice, reload, the first session
// adopts the box, and the persisted state names it
await page.evaluate(() => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = key ? JSON.parse(localStorage.getItem(key) || "{}") : {}; delete st.activeId; delete st.activeName; localStorage.setItem(key, JSON.stringify(st)); });
await page.reload();
await page.waitForFunction(() => !!document.querySelector("#tabs .tab.active[data-id]"), null, { timeout: 20000 });
out.adopted = await page.evaluate(() => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = JSON.parse(localStorage.getItem(key) || "{}"); return { active: document.querySelector("#tabs .tab.active[data-id]").dataset.id, persisted: st.activeId || null, name: st.activeName || "" }; });
// (c) a persisted state that predates the name: the body never shows a raw sid
await page.evaluate((remote) => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = JSON.parse(localStorage.getItem(key) || "{}"); st.activeId = remote; delete st.activeName; localStorage.setItem(key, JSON.stringify(st)); }, cfg.remote);
await page.reload();
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, 2, { timeout: 20000 });
await page.waitForTimeout(500);
out.nameless = await state();
// (a) the strip EMPTIES under the unfocused pane: the body and the box's placeholder follow
await inject({ type: "tabOrder", order: [], tabs: [], live: [], skeleton: [] });
await page.waitForFunction(() => { const e = document.getElementById("empty-state"); return !!e && /No sessions yet/.test(e.textContent || ""); }, null, { timeout: 10000 });
out.emptied = await state();
// (b) a persisted id that can never be listed again (a provisional create): gone, not awaited
await page.evaluate((prov) => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = JSON.parse(localStorage.getItem(key) || "{}"); st.activeId = prov; st.activeName = "docs"; localStorage.setItem(key, JSON.stringify(st)); }, cfg.provisional);
await page.reload();
await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, 2, { timeout: 20000 });
await page.waitForTimeout(500);
out.gone = await state();
// the #only= filter (the review's probe): web persisted active, the page served at #only=api, a reload: the strip shows
// only api, and web's transcript must NOT be on screen; the pane is unfocused naming web as hidden by the view
await page.evaluate((sid) => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = JSON.parse(localStorage.getItem(key) || "{}"); st.activeId = sid; st.activeName = "web"; localStorage.setItem(key, JSON.stringify(st)); }, cfg.sidA);
await page.goto(cfg.chat + "#only=api");   // a hash-only change is no navigation: the page keeps running, so…
await page.reload();                        // …reload with the hash in place, the way a restart's reload would find it
await page.waitForFunction((sid) => { const tabs = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id); return tabs.length >= 1 && !tabs.includes(sid); }, cfg.sidA, { timeout: 20000 });
await page.waitForTimeout(800);
out.onlyFiltered = await page.evaluate(() => {
  const act = document.querySelector("#tabs .tab.active[data-id]");
  const empty = document.getElementById("empty-state");
  const turns = Array.from(document.querySelectorAll("#content .turn")).filter((t) => { const r = t.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(t).display !== "none"; });
  return { active: act ? act.dataset.id : null, tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id),
           empty: empty && getComputedStyle(empty).display !== "none" ? { text: empty.textContent, vanished: empty.dataset.vanished || "" } : null,
           visibleTurns: turns.length, composerDisabled: document.getElementById("composer-input").disabled };
});
// a ROUTINE push under the filter (the review's leak, pre-existing on main): applyTabOrder's restore re-focused the
// filtered-out session for one task per push, its CACHED transcript on screen, before renderTabs's deferred unfocus put
// the body back. The leak needs web's view cached, so web is focused live first (the filter lifted live restores it), the
// filter is set live again (unfocused, view cached), and a MutationObserver samples every DOM change across a routine
// tabOrder push listing web: the most transcript rows visible at once, and how often the body was down. A frame
// recorder saw nothing here (headless Chromium reverts the leak in the same task, before any paint); the observer sees
// the transient: 4 rows / 1 body-down per push with applyTabOrder's visibility predicate reverted, 0 / 0 with it.
await page.evaluate(() => { location.hash = ""; });
await page.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid && document.querySelectorAll("#content .turn").length > 0; }, cfg.sidA, { timeout: 10000 });
await page.evaluate(() => { location.hash = "#only=api"; });
await page.waitForFunction((sid) => !document.querySelector("#tabs .tab.active[data-id]") && !Array.from(document.querySelectorAll("#tabs .tab[data-id]")).some((t) => t.dataset.id === sid), cfg.sidA, { timeout: 10000 });
await page.waitForTimeout(300);
await page.evaluate(() => {
  const visibleRows = () => Array.from(document.querySelectorAll("#content .turn")).filter((t) => { const r = t.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(t).display !== "none"; }).length;
  const empty = document.getElementById("empty-state");
  window.__mo = { samples: 0, maxRows: 0, bodyDown: 0, focused: 0 };
  const sample = () => {
    window.__mo.samples++;
    window.__mo.maxRows = Math.max(window.__mo.maxRows, visibleRows());
    if (!empty || getComputedStyle(empty).display === "none") window.__mo.bodyDown++;
    if (document.querySelector("#tabs .tab.active[data-id]")) window.__mo.focused++;
  };
  const obs = new MutationObserver(sample);
  obs.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "style", "hidden"] });
  window.__moStop = () => obs.disconnect();
});
await inject({ type: "tabOrder", order: [cfg.sidA, cfg.sidB], tabs: [A_TAB, B_TAB], live: [cfg.sidA, cfg.sidB], skeleton: [] });
await page.waitForTimeout(600);
out.pushUnderFilter = await page.evaluate(() => { window.__moStop(); return window.__mo; });
// the hidden tab torn down while the pane is unfocused (the review's low): the body's line follows the reason
await inject({ type: "closed", id: cfg.sidA, hostDrop: true });
await inject({ type: "tabOrder", order: [cfg.sidB], tabs: [B_TAB], live: [cfg.sidB], skeleton: [] });
await page.waitForFunction((sid) => { const e = document.getElementById("empty-state"); return !!e && (e.textContent || "").includes("host disconnected") && !document.querySelector('#tabs .tab[data-id="' + sid + '"]'); }, cfg.sidA, { timeout: 10000 });
out.tornDownWhileHidden = await state();
// a FIRST arrival the filter hides is not adopted (the review's low: the adopt wrote activeId past the rule's visibility
// half): nothing persisted, the page served under a filter that hides the FIRST-arriving session (api arrives before web in
// this world) and shows a later one, the sessions arrive: the pane adopts the first VISIBLE one, never the hidden first
const clearState = () => page.evaluate(() => { for (const k of Object.keys(localStorage)) if (k.startsWith("romp-vscode-state-")) localStorage.removeItem(k); });
await clearState();
await page.goto(cfg.chat + "#only=web");
await page.reload();
await page.waitForFunction(() => document.querySelectorAll("#tabs .tab[data-id]").length >= 1, null, { timeout: 20000 });
await page.waitForTimeout(800);
out.firstArrivalUnderFilter = await state();
// …and the adoption ended the unfocused state (the review's high): with the filter lifted live, one routine tabOrder push
// must leave the pane on web; a declined record left standing would have handed api to applyTabOrder's restore
await page.evaluate(() => { location.hash = ""; });
await page.waitForTimeout(300);
await inject({ type: "tabOrder", order: [cfg.sidA, cfg.sidB], tabs: [A_TAB, B_TAB], live: [cfg.sidA, cfg.sidB], skeleton: [] });
await page.waitForTimeout(400);
out.afterLiftAndPush = await state();
// a filter matching NO live session: every adoption is declined, and the declined first arrival is RECORDED (the review's
// low: the body showed the generic line and lifting the filter restored nothing), so the body wears the view's line and
// lifting the filter live restores that session through renderTabs's schedule
await clearState();
await page.goto(cfg.chat + "#only=nomatch-zz");
await page.reload();
await page.waitForFunction(() => document.getElementById("empty-state") && getComputedStyle(document.getElementById("empty-state")).display !== "none" && (document.getElementById("empty-state").dataset.vanished || "") !== "", null, { timeout: 20000 });
await page.waitForTimeout(300);
out.noMatchFilter = await state();
await page.evaluate(() => { location.hash = ""; });
await page.waitForFunction(() => !!document.querySelector("#tabs .tab.active[data-id]"), null, { timeout: 10000 });
out.noMatchLifted = await state();
// a declined record's session torn down (its host dropped, the kernel stopped listing it): the record goes with it and the
// frame stays NAME-FREE (the review's medium: the teardown's reason painted the session's name in bold in its colour on a
// frame the filter is meant to keep clean)
await clearState();
await page.goto(cfg.chat + "#only=nomatch-zz");
await page.reload();
await page.waitForFunction(() => document.getElementById("empty-state") && (document.getElementById("empty-state").dataset.vanished || "") !== "", null, { timeout: 20000 });
const recorded = await page.evaluate(() => document.getElementById("empty-state").dataset.vanished);
const other = recorded === cfg.sidA ? cfg.sidB : cfg.sidA;
await inject({ type: "closed", id: recorded, hostDrop: true });
await inject({ type: "tabOrder", order: [other], tabs: [other === cfg.sidA ? A_TAB : B_TAB], live: [other], skeleton: [] });
await page.waitForTimeout(400);
out.declinedTornDown = Object.assign(await state(), { recorded });
// the SHELL road (the review's medium): on the dashboard the chat pane is a same-origin iframe of the shell and the
// filter lives on the SHELL's URL (only-filter.ts reads window.top), so a live edit of the shell's hash must reach the
// framed pane, whose own hash never changes: the pane unfocuses at once and restores when the filter shows the tab again
await page.evaluate((sid) => { const key = Object.keys(localStorage).find((k) => k.startsWith("romp-vscode-state-")); const st = JSON.parse(localStorage.getItem(key) || "{}"); st.activeId = sid; st.activeName = "web"; localStorage.setItem(key, JSON.stringify(st)); }, cfg.sidA);
await page.goto(cfg.shell);
await page.waitForSelector("#f-chat", { timeout: 20000 });
const fr = await (await page.$("#f-chat")).contentFrame();
await fr.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.sidA, { timeout: 20000 });
await fr.waitForFunction(() => document.querySelectorAll("#content .turn").length > 0, null, { timeout: 20000 });
const frameState = () => fr.evaluate(() => {
  const act = document.querySelector("#tabs .tab.active[data-id]");
  const empty = document.getElementById("empty-state");
  const turns = Array.from(document.querySelectorAll("#content .turn")).filter((t) => { const r = t.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(t).display !== "none"; });
  let topHash = null; try { topHash = window.top.location.hash; } catch (e) { topHash = "cross-origin"; }
  return { active: act ? act.dataset.id : null, tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id),
           empty: empty && getComputedStyle(empty).display !== "none" ? { text: empty.textContent, vanished: empty.dataset.vanished || "" } : null,
           visibleTurns: turns.length, paneHash: location.hash, topHash };
});
const t0 = Date.now();
await page.evaluate(() => { location.hash = "#only=api"; });   // the SHELL's hash; the pane's own URL is untouched
await fr.waitForFunction((sid) => !document.querySelector("#tabs .tab.active[data-id]") && !Array.from(document.querySelectorAll("#tabs .tab[data-id]")).some((t) => t.dataset.id === sid), cfg.sidA, { timeout: 5000 });
out.shellHidden = Object.assign(await frameState(), { ms: Date.now() - t0 });
await page.evaluate(() => { location.hash = "#only="; });      // the filter lifted, on the shell again
await fr.waitForFunction((sid) => { const a = document.querySelector("#tabs .tab.active[data-id]"); return !!a && a.dataset.id === sid; }, cfg.sidA, { timeout: 5000 });
await fr.waitForFunction(() => document.querySelectorAll("#content .turn").length > 0, null, { timeout: 5000 });
out.shellRestored = await frameState();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedUnfocusedPane(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served lab needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served lab needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="unfocused-pane-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own session-hosts off (the conftest rule), or a connect would spawn a real host
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        for sid, name, color, text in ((SID_A, "web", ("#f2b26b", "#1a1206"), "The web notes start in docs/guide.md."),
                                       (SID_B, "api", ("#9cd2ff", "#0c1a2e"), "The api notes start in docs/api.md.")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, color[0], color[1]))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, text))
        cls.port, cls.token = _free_port(), "testtok-unfocused"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def test_the_focused_tab_leaving_on_its_own_unfocuses_the_pane_and_its_return_restores_it(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "shell": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token), "sidA": SID_A, "sidB": SID_B, "sidC": SID_C, "remote": REMOTE, "provisional": PROVISIONAL,
                       "shots": os.environ.get("PV_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served lab needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertEqual(r["before"]["active"], SID_A); self.assertFalse(r["before"]["composer"]["disabled"])
        # the tab left on its own → UNFOCUSED: no active tab, the body names web and says its host disconnected, the
        # composer disabled with no session name, no session colour on the box or the window border
        u = r["unfocused"]
        self.assertIsNone(u["active"], "no tab is active: %r" % u)
        self.assertNotIn(SID_A, u["tabs"], "the tab is gone from the strip"); self.assertIn(SID_B, u["tabs"], "the other tab stays")
        self.assertIsNotNone(u["empty"], "the blank body is up")
        self.assertTrue(u["empty"]["unfocused"]); self.assertEqual(u["empty"]["vanished"], SID_A)
        self.assertIn("No session selected. Pick a tab to start.", u["empty"]["text"])
        self.assertIn("web", u["empty"]["text"]); self.assertIn("host disconnected", u["empty"]["text"])
        self.assertTrue(u["composer"]["disabled"], "the box takes no input")
        self.assertEqual(u["composer"]["placeholder"], "Pick a tab to start", "no session name in the placeholder")
        self.assertEqual(u["composer"]["ph"], "", "the name overlay is down: no session name anywhere on the box")
        self.assertEqual(u["composer"]["identity"], "", "no session colour on the focus ring")
        self.assertEqual(u["accent"], "", "no session colour on the window border")
        self.assertEqual(u["statusline"], "", "the statusline says nothing: not the vanished session's chips")
        self.assertIn("web", u["note"] or "", "the note above the box still says whose box went away (T236)")
        # another session's tab re-listed meanwhile: nothing changes hands
        o = r["otherArrived"]
        self.assertIsNone(o["active"], "a different session appearing does not take focus: %r" % o)
        self.assertIn(SID_C, o["tabs"], "the new tab is on the strip"); self.assertTrue(o["composer"]["disabled"])
        self.assertIsNotNone(o["empty"]); self.assertEqual(o["empty"]["vanished"], SID_A, "the body still names the session that vanished")
        # the same session's tab is back → focus restored to it, its transcript on screen, the box live again
        s = r["restored"]
        self.assertEqual(s["active"], SID_A, "focus went back to the session the user chose: %r" % s)
        self.assertIsNone(s["empty"], "the blank body is gone")
        self.assertFalse(s["composer"]["disabled"]); self.assertIn("web", s["composer"]["ph"] or "", "the box names its session again (the name overlay): %r" % s["composer"])
        self.assertEqual(s["accent"], r["before"]["accent"], "the window border wears what it wore before the tab vanished")
        self.assertNotEqual(s["statusline"], "", "the statusline names the restored session's state again")
        # the reload road: the persisted remote tab is awaited, the body names it, the local sessions adopt nothing
        self.assertTrue(r["seedKey"], "the shim's persisted state was found and seeded")
        a = r["reloadAwaiting"]
        self.assertIsNone(a["active"], "after the reload nothing adopts the box while the remembered tab is awaited: %r" % a)
        self.assertIsNotNone(a["empty"]); self.assertTrue(a["empty"]["unfocused"]); self.assertEqual(a["empty"]["vanished"], REMOTE)
        self.assertIn("web", a["empty"]["text"]); self.assertIn("REMOTEBOX", a["empty"]["text"], "the host the tab wore"); self.assertIn("not listed yet", a["empty"]["text"])
        self.assertTrue(a["composer"]["disabled"]); self.assertEqual(a["statusline"], "")
        self.assertEqual(r["reloadRestored"]["active"], REMOTE, "the remote host relays its strip: focus goes to the remembered tab")
        self.assertEqual(r["pickWins"]["active"], SID_B, "a pick before the relay wins; the relay changes nothing: %r" % r["pickWins"])
        # the follow-up's roads
        ad = r["adopted"]
        self.assertEqual(ad["persisted"], ad["active"], "an adopted tab is persisted like a pick: %r" % ad); self.assertIn(ad["name"], ("web", "api"))
        nl = r["nameless"]
        self.assertIsNone(nl["active"]); self.assertIn("a session is not listed yet", nl["empty"]["text"], "no raw sid in the body: %r" % nl["empty"])
        self.assertNotIn("cccccccc", nl["empty"]["text"])
        em = r["emptied"]
        self.assertIsNone(em["active"]); self.assertEqual(em["tabs"], [], "the strip emptied")
        self.assertIn("No sessions yet.", em["empty"]["text"]); self.assertEqual(em["composer"]["placeholder"], "Click + to add a session", "the box's placeholder followed the strip: %r" % em["composer"])
        g = r["gone"]
        self.assertIsNone(g["active"], "a provisional id is never awaited or adopted: %r" % g)
        self.assertIn("docs", g["empty"]["text"]); self.assertIn("is no longer on the strip. Pick a tab.", g["empty"]["text"])
        # the #only= filter: the hidden active tab's transcript leaves the screen; the pane is unfocused naming it
        of = r["onlyFiltered"]
        self.assertIsNone(of["active"], "no tab active under the filter: %r" % of); self.assertNotIn(SID_A, of["tabs"]); self.assertIn(SID_B, of["tabs"])
        self.assertEqual(of["visibleTurns"], 0, "the filtered session's transcript is NOT on screen (the demo leak): %r" % of)
        self.assertIsNotNone(of["empty"]); self.assertEqual(of["empty"]["vanished"], SID_A)
        self.assertEqual(of["empty"]["text"], "This tab view shows no session. Change the view, or pick a tab.", "name-free: a clean recording frame")
        self.assertTrue(of["composerDisabled"])
        # a routine push under the filter never re-focuses the hidden tab, not for one animation frame (the review's leak:
        # 1 frame of 70 with the body down and four transcript rows visible per push)
        pu = r["pushUnderFilter"]
        self.assertGreater(pu["samples"], 0, "the push mutated the strip, so the observer sampled: %r" % pu)
        self.assertEqual((pu["maxRows"], pu["bodyDown"], pu["focused"]), (0, 0, 0), "no transient with a transcript row visible, the body down or a tab focused (4 rows and 1 body-down per push with applyTabOrder's predicate reverted): %r" % pu)
        fa = r["firstArrivalUnderFilter"]
        self.assertNotIn(SID_B, fa["tabs"]); self.assertNotEqual(fa["active"], SID_B, "the first arrival (api), hidden by #only=web, is never adopted: %r" % fa)
        self.assertEqual(fa["active"], SID_A, "…the first VISIBLE arrival (web) is, over the declined record: %r" % fa)
        lp = r["afterLiftAndPush"]
        self.assertEqual(lp["active"], SID_A, "the adoption ended the unfocused state: the filter lifted and one routine push later the pane is still on web, never handed api (the review's high): %r" % lp)
        nm = r["noMatchFilter"]
        self.assertIsNone(nm["active"]); self.assertEqual(nm["tabs"], [], "no tab shows under a filter matching nothing: %r" % nm)
        self.assertEqual(nm["empty"]["vanished"], SID_B, "the declined FIRST arrival (api arrives first in this world) is recorded, and a later hidden arrival never overwrites it: %r" % nm)
        self.assertEqual(nm["empty"]["text"], "The first session to arrive is hidden by this view. Pick a tab, or change the view.", "the declined record's own head, name-free")
        nl = r["noMatchLifted"]
        self.assertEqual(nl["active"], nm["empty"]["vanished"], "lifting the filter restores the recorded session through the schedule: %r" % nl)
        dt = r["declinedTornDown"]
        # the durable claims (the surviving hidden session's next frame re-records it by design, so `vanished` may be empty
        # or the survivor): no active tab, and the line name-free with no bold name, whichever head stands
        self.assertIsNone(dt["active"])
        self.assertIn(dt["empty"]["text"], ("No session selected. Pick a tab to start.", "No session open — click + to add one.",
                                            "The first session to arrive is hidden by this view. Pick a tab, or change the view."), "a name-free head after a declined record's teardown: %r" % dt)
        for nm_ in ("web", "api"):
            self.assertNotIn(nm_, dt["empty"]["text"], "the frame stays name-free after a declined record's teardown (the review's medium): %r" % dt)
        # the hidden tab torn down while the pane is unfocused: the line follows the reason, naming the tab
        td = r["tornDownWhileHidden"]
        self.assertIsNone(td["active"]); self.assertEqual(td["empty"]["vanished"], SID_A); self.assertNotIn(SID_A, td["tabs"])
        self.assertIn("web", td["empty"]["text"]); self.assertIn("host disconnected", td["empty"]["text"], "no longer the view's name-free line once the tab is gone: %r" % td)
        # the SHELL road (the review's medium): the pane framed on the dashboard, the filter edited on the SHELL's URL
        # (where only-filter.ts reads it): the pane's own hash never changes, yet the framed pane unfocuses off the live
        # edit and restores when the filter lifts
        sh = r["shellHidden"]
        self.assertEqual((sh["paneHash"], sh["topHash"]), ("", "#only=api"), "the edit was the shell's; the pane's own URL carries no hash: %r" % sh)
        self.assertIsNone(sh["active"]); self.assertNotIn(SID_A, sh["tabs"]); self.assertIn(SID_B, sh["tabs"])
        self.assertEqual(sh["visibleTurns"], 0, "the filtered session's transcript left the framed pane: %r" % sh)
        self.assertEqual(sh["empty"]["text"], "This tab view shows no session. Change the view, or pick a tab.")
        self.assertLess(sh["ms"], 2000, "off the live edit, not a later kernel frame: %r" % sh)
        sr = r["shellRestored"]
        self.assertEqual(sr["active"], SID_A, "the filter lifted on the shell: the hidden tab takes focus back: %r" % sr)
        self.assertGreater(sr["visibleTurns"], 0); self.assertIsNone(sr["empty"])


if __name__ == "__main__":
    unittest.main()
