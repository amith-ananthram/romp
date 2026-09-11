#!/usr/bin/env python3
"""Chat split screen, the SERVED leg (the user 2026-09-08, who wanted several sessions open side by side
instead of tabbing between them). The dashboard shell holds N chat columns: column 1 is #chat-pane / #f-chat,
every later one a client-made twin (_LANDING_SPLIT_JS) around an iframe at /chat?col=N with its own tab
strip, state blob, socket and grow weight, slotted before #gv-a behind a chat|chat gutter.

The node-side test (tests/test_chat_split.py) drives the split script against a DOM stub; this one drives the
REAL page: a hermetic kernel serves the dashboard with EIGHT synthetic sessions (a board wide enough that a
column served whole is told apart from one served as a view), a headless browser opens it once, and ONE
driver run walks the whole story in order, each step landing in its own assertion here:
  1. a split on session B opens a second column that is WIDE (the review find: the first split opened 0px
     because the new column's missing grow averaged in as NaN), in the right row slot, with a finite
     --g-chat2 and the column set persisted, its frame at /chat?col=2&skeleton=1;
  2. the new column shows B — as a VIEW of it (2026-09-11): the shell seeded the column's state blob with B
     before the frame existed, the page's socket dialled as a skeleton client of B, and the kernel served B
     whole and every other tab as a skeleton (a status frame each, never a full: GET /perf's `sends` say so),
     so the wire carried one session, not the board; between the call and B's paint the column showed the pane
     loader and never the no-sessions copy or an "Opening session" line (polled per animation frame from the
     shell). Column 1 keeps its tab, and __rompChatTarget routes a session-focus to the column showing it —
     or, for a session nobody shows, to the column the user last clicked in;
  3. a split opened ON a session column 1 already shows still takes it (the page's wantActive never
     arbitrates), and closing that column drops it from the persisted set;
  4. dragging the chat|chat gutter moves width between the two columns and persists column 2's grow;
  5. column 2's new-session picker lifts ITS iframe and pane (.lifted), never column 1's, and unlifts on
     toggle;
  6. a reload brings both columns back, column 2 on B again, at the width the drag left it;
  7. closing column 2 leaves one column and an empty persisted set.
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
SID_B = "11111111-2222-4333-8444-000000000302"   # "api": the session the split opens on
SID_X = "11111111-2222-4333-8444-000000000999"   # a session no column shows
# six more tabs, so the board is EIGHT sessions: a column served whole takes eight full frames per push, a column served
# as a view of B takes one (plus a status frame per other tab), and the /perf deltas in step 2 tell the two apart with
# room for the page's idle prefetch to have loaded a tab or two by the paint
FILLERS = [("11111111-2222-4333-8444-00000000030%d" % k, name, k)
           for k, name in ((3, "tests"), (4, "docs"), (5, "lint"), (6, "deploy"), (7, "search"), (8, "auth"))]
BOARD = 2 + len(FILLERS)
DRAG_PX = 200
SLACK_PX = 40


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
// a column's ACTIVE tab is `sid`
const waitActive = (fid, sid) => waitFn(([fid, sid]) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid;
}, [fid, sid], fid + " never activated " + sid);
const waitBootGone = () => waitFn(() => !document.getElementById("romp-boot"), null, "boot splash never cleared");
const waitFocused = (fid) => waitFn((fid) => window.__rompFocusedChatId && window.__rompFocusedChatId() === fid, fid, fid + " never took the focus ring");
const activeIn = (fid) => page.evaluate((fid) => {
  const f = document.getElementById(fid); const d = f && f.contentDocument;
  const t = d && d.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null;
}, fid);
const targetOf = (sid) => page.evaluate((sid) => { const f = window.__rompChatTarget(sid); return f ? f.id : null; }, sid);
const width = (id) => page.evaluate((id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; }, id);
const shell = () => page.evaluate(() => ({
  frameIds: window.__rompChatFrameIds(), cols: localStorage.getItem("romp-chat-cols"),
  rowKids: Array.from(document.querySelector(".row").children).map((e) => e.id || e.className),
  grow: JSON.parse(localStorage.getItem("romp-pane-grow") || "null"),
  lifted: Array.from(document.querySelectorAll(".lifted")).map((e) => e.id), pickerOpen: document.body.classList.contains("picker-open"),
}));
// the page rect of an element INSIDE a column, for page.mouse (the iframe's offset plus the element's own)
const rectIn = (fid, sel) => page.evaluate(([fid, sel]) => {
  const f = document.getElementById(fid); const fr = f.getBoundingClientRect(); const el = f.contentDocument.querySelector(sel);
  const r = el.getBoundingClientRect(); return { x: fr.left + r.left, y: fr.top + r.top, w: r.width, h: r.height };
}, [fid, sel]);
const clickIn = async (fid, sel) => { const r = await rectIn(fid, sel); await page.mouse.click(r.x + r.w / 2, r.y + Math.min(r.h / 2, 120)); };
// GET /perf from the shell's origin (the landing's cookie authorises it): the kernel's send counters by class and slot
const perf = () => page.evaluate(() => fetch("/perf").then((r) => r.json()));
const sends = (p, slot) => (((p || {}).sends || {}).full || {})[slot]?.count || 0;

// ---- load: both sessions are tabs in column 1; column 1 shows A ----
await page.goto(cfg.url);
await waitTabs("f-chat", [cfg.sidA, cfg.sidB]);
await waitBootGone();
if ((await activeIn("f-chat")) !== cfg.sidA) {
  const fr = await (await page.$("#f-chat")).contentFrame();
  await fr.locator('#tabs .tab[data-id="' + cfg.sidA + '"]').click();
  await waitActive("f-chat", cfg.sidA);
}
out.col1Before = await activeIn("f-chat");

// ---- 1. split on B: a second column, wide, before gv-a, persisted ----
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
  const f = window.__rompSplitChat(sidB);
  const row = document.querySelector(".row"), kids = Array.from(row.children).map((e) => e.id);
  const w = (id) => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().width : null; };
  return { frameId: f && f.id, tag: f && f.tagName, src: f && f.getAttribute("src"), paneCol: (document.getElementById("chat-pane-2") || {}).getAttribute?.("data-col"),
           order: kids, gIdx: kids.indexOf("gv-chat-2"), pIdx: kids.indexOf("chat-pane-2"), aIdx: kids.indexOf("gv-a"),
           pane1W: w("chat-pane"), pane2W: w("chat-pane-2"), gChat2Inline: row.style.getPropertyValue("--g-chat2"),
           gChat2: getComputedStyle(row).getPropertyValue("--g-chat2"), cols: localStorage.getItem("romp-chat-cols") };
}, cfg.sidB);

// ---- 2. the new column shows B, as a view of it; targets route by the column showing the session, else the last-clicked ----
await waitTabs("f-chat-2", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitFn(() => window.__obs && (window.__obs.done || window.__obs.timedOut), null, "column 2 never painted B's transcript");
const perfAtPaint = await page.evaluate(() => window.__obs.perfAtPaint ? window.__obs.perfAtPaint : null);
out.s2 = { col2Active: await activeIn("f-chat-2"), col1After: await activeIn("f-chat"),
           targetB: await targetOf(cfg.sidB), targetA: await targetOf(cfg.sidA),
           obs: await page.evaluate(() => { const { perfAtPaint, ...rest } = window.__obs; return rest; }),
           // the kernel's send counters across the open: full chat frames and status frames, before the call and at the paint
           fullChatDelta: sends(perfAtPaint, "chat") - sends(perf0, "chat"), statusDelta: sends(perfAtPaint, "status") - sends(perf0, "status"),
           col2Tabs: await page.evaluate(() => { const d = document.getElementById("f-chat-2").contentDocument;
             return Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => ({ id: t.dataset.id, skeleton: t.classList.contains("tab-skeleton"), active: t.classList.contains("active") })); }) };
await clickIn("f-chat-2", "#content");
await waitFocused("f-chat-2");
out.s2.unknownAfterCol2 = await targetOf(cfg.sidX);
await clickIn("f-chat", "#content");
await waitFocused("f-chat");
out.s2.unknownAfterCol1 = await targetOf(cfg.sidX);
out.s2.col1AfterClicks = await activeIn("f-chat");

// ---- 3. a split ON the session column 1 shows: the third column takes it anyway; close it ----
out.s3 = await page.evaluate((sidA) => { const f = window.__rompSplitChat(sidA); return { frameId: f && f.id, cols: localStorage.getItem("romp-chat-cols") }; }, cfg.sidA);
await waitTabs("f-chat-3", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-3", cfg.sidA);
out.s3.col3Active = await activeIn("f-chat-3");
out.s3.col1Still = await activeIn("f-chat");
out.s3.targetAWithThree = await targetOf(cfg.sidA);   // column 1 shows A too and comes first in the row
await page.evaluate(() => window.__rompCloseSplit(3));
out.s3.after = await shell();
out.s3.pane3Gone = await page.evaluate(() => !document.getElementById("chat-pane-3") && !document.getElementById("gv-chat-3") && !document.getElementById("f-chat-3"));

// ---- 4. drag the chat|chat gutter right: column 1 grows, column 2 shrinks, the grow persists ----
out.s4 = { before1: await width("chat-pane"), before2: await width("chat-pane-2") };
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

// ---- 6. reload: both columns return, column 2 on B, at the dragged width ----
await page.reload();
await waitTabs("f-chat", [cfg.sidA, cfg.sidB]);
await waitTabs("f-chat-2", [cfg.sidA, cfg.sidB]);
await waitActive("f-chat-2", cfg.sidB);
await waitBootGone();
out.s6 = await shell();
out.s6.col2Active = await activeIn("f-chat-2"); out.s6.col1Active = await activeIn("f-chat");
out.s6.pane1W = await width("chat-pane"); out.s6.pane2W = await width("chat-pane-2");

// ---- 7. close column 2: one column left, nothing persisted ----
await page.evaluate(() => window.__rompCloseSplit(2));
out.s7 = await shell();
out.s7.pane2Gone = await page.evaluate(() => !document.getElementById("chat-pane-2") && !document.getElementById("gv-chat-2") && !document.getElementById("f-chat-2"));
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
                       ROMP_TMUX_SOCKET="romp-chatsplit-%d" % cls.port,
                       # a postal bus of its own that is never started (the trio kernel_env gives every lab kernel):
                       # the kernel's boot-time ensure must never take the machine's fixed bus port (tests/test_hermetic_kernel_postal.py)
                       ROMP_POSTAL_PORT=str(_free_port()), ROMP_POSTAL_PEERS="0", ROMP_POSTAL_CLIENT_ONLY="1")
        cls.env.pop("ROMP_STATE_DIR", None)
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
                       "sidA": SID_A, "sidB": SID_B, "sidX": SID_X, "dragPx": DRAG_PX}, f)
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
        subprocess.run(["tmux", "-L", getattr(cls, "env", {}).get("ROMP_TMUX_SOCKET", ""), "kill-server"], capture_output=True)
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

    def test_1_a_split_on_a_session_opens_a_wide_second_column_before_gv_a_and_persists(self):
        s = self._r()["s1"]
        self.assertEqual(s["tag"], "IFRAME", "__rompSplitChat returns the new column's iframe: %r" % s)
        self.assertEqual(s["frameId"], "f-chat-2")
        self.assertEqual(s["src"], "/chat?col=2&skeleton=1", "every later column is /chat?col=N, a skeleton client of its session (2026-09-11)")
        self.assertEqual(s["paneCol"], "2")
        self.assertEqual(s["cols"], "[2]", "the open column set persists per browser")
        # row order: … chat-pane, gv-chat-2, chat-pane-2, gv-a …
        self.assertGreaterEqual(s["gIdx"], 0, s["order"])
        self.assertEqual(s["pIdx"], s["gIdx"] + 1, "the gutter sits directly ahead of its column: %r" % s["order"])
        self.assertLess(s["pIdx"], s["aIdx"], "the new column slots before gv-a: %r" % s["order"])
        self.assertEqual(s["order"][s["gIdx"] - 1], "chat-pane", "…and right after the first column: %r" % s["order"])
        # the review find (2026-09-08): the first split opened 0px wide because the new column's missing grow
        # averaged in as NaN. Both columns must be real widths the moment the split opens.
        self.assertGreater(s["pane1W"], 150, "column 1 keeps a real width: %r" % s)
        self.assertGreater(s["pane2W"], 150, "the new column opens at a real width, never a sliver: %r" % s)
        g = float(s["gChat2Inline"] or s["gChat2"] or "nan")
        self.assertTrue(g == g and abs(g) != float("inf"), "--g-chat2 on .row is a finite number: %r" % s)
        self.assertGreater(g, 0)

    def test_2_the_new_column_shows_its_session_and_focus_targets_route_by_column(self):
        r = self._r()
        s = r["s2"]
        self.assertEqual(r["col1Before"], SID_A, "the story starts with column 1 on A")
        self.assertEqual(s["col2Active"], SID_B, "the seeded blob's activeId lands the new column on B: %r" % s)
        self.assertEqual(s["col1After"], SID_A, "column 1's tab is untouched by the split: %r" % s)
        self.assertEqual(s["targetB"], "f-chat-2", "a focus for B belongs to the column showing B: %r" % s)
        self.assertEqual(s["targetA"], "f-chat", "a focus for A belongs to column 1: %r" % s)
        # a session no column shows goes to the column the user last worked in (click-tracked by the shell)
        self.assertEqual(s["unknownAfterCol2"], "f-chat-2", "after a click in column 2, an unshown session's focus goes there: %r" % s)
        self.assertEqual(s["unknownAfterCol1"], "f-chat", "…and back to column 1 after a click there: %r" % s)
        self.assertEqual(s["col1AfterClicks"], SID_A, "the focus clicks changed no tab")

    def test_2b_the_new_column_is_served_as_a_view_of_its_session_and_shows_the_loader_until_the_transcript(self):
        """The open's cost and copy (the user 2026-09-11, who found a new column slow to open and its copy reading as
        a create). The kernel's send counters across the open tell a column served WHOLE (a full frame per tab: eight
        here, and no status frame — a client that declared nothing gets none) from one served as a VIEW of B (the
        strip with a skeleton list, B's one full, a status per other tab): the status delta is at least the other
        tabs, and the full delta is well under the board. The full delta is not pinned to exactly one: the pusher
        cycle the handshake wakes can land before the bundle's ready, into a document that cannot hear it, and the
        ready arm's connect push then re-sends the one full (two); and the page's idle prefetch may have loaded a
        skeleton tab or two by the time B's transcript is painted. Never the board."""
        s = self._r()["s2"]
        o = s["obs"]
        self.assertTrue(o["done"], "the observer saw B's transcript painted in column 2: %r" % o)
        self.assertGreaterEqual(s["statusDelta"], BOARD - 1,
                                "a status frame per other tab: the column was served as a skeleton client, not whole: %r" % s)
        self.assertGreaterEqual(s["fullChatDelta"], 1, "B's one full frame: %r" % s)
        self.assertLess(s["fullChatDelta"], BOARD, "never the board (eight full frames per push before 2026-09-11): %r" % s)
        # the copy between the call and the paint: the pane loader, never the create flow's words
        self.assertEqual(o["emptyState"], 0, "no 'No session open' copy in a column opened on a session: %r" % o)
        self.assertEqual(o["openingText"], 0, "no 'Opening session' or 'opening …' line — a view of a running session is not a create: %r" % o)
        self.assertGreaterEqual(o["loaderSeen"], 1, "the pane loader (the romp swirl) covered the column until the transcript: %r" % o)
        # the strip at the paint: B loaded and active, the other tabs listed (skeletons until clicked or prefetched)
        tabs = {t["id"]: t for t in s["col2Tabs"]}
        self.assertEqual(len(tabs), BOARD, "every session is a tab: %r" % s["col2Tabs"])
        self.assertTrue(tabs[SID_B]["active"] and not tabs[SID_B]["skeleton"], "B is the loaded, active tab: %r" % s["col2Tabs"])
        self.assertLess(o["msToPaint"], 15000, "the paint came within the driver's wait")

    def test_3_a_split_on_a_session_already_shown_takes_it_and_closing_it_drops_it_from_the_set(self):
        s = self._r()["s3"]
        self.assertEqual(s["frameId"], "f-chat-3")
        self.assertEqual(s["cols"], "[2,3]")
        # the seeded blob names A and the page's wantActive never arbitrates (the shell's own:true hand-over, which used
        # to beat the arbitration here, went on 2026-09-11)
        self.assertEqual(s["col3Active"], SID_A, "the third column shows A even though column 1 does: %r" % s)
        self.assertEqual(s["col1Still"], SID_A)
        self.assertEqual(s["targetAWithThree"], "f-chat", "with two columns on A, the first in row order wins the target")
        self.assertTrue(s["pane3Gone"], "__rompCloseSplit(3) removes the pane, its gutter and its iframe")
        self.assertEqual(s["after"]["cols"], "[2]", "the persisted set is back to column 2 alone: %r" % s["after"])
        self.assertEqual(s["after"]["frameIds"], ["f-chat", "f-chat-2"])
        self.assertNotIn("chat3", s["after"]["grow"] or {}, "a closed column's grow leaves the store: %r" % s["after"]["grow"])

    def test_4_dragging_the_chat_gutter_moves_width_between_the_columns_and_persists_it(self):
        """FAILS on 2026-09-08 (a product bug the served leg exposed; left asserting the right behaviour): the
        grab's normalisation writes each pane's grow and only THEN reads the next pane's offsetWidth, and the
        write forces a reflow at a mixed scale (chat at its px width, the others still at their small default
        numbers), so column 2 is recorded at about a fifth of its width and column 1 balloons before the pointer
        moves. Pre-existing in _LANDING_JS for the chat|feed gutter, but every fresh browser's FIRST drag after a
        split hits it, since the new column's fair grow sits next to the 60/40 defaults."""
        s = self._r()["s4"]
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

    def test_6_a_reload_brings_both_columns_back_on_their_sessions_at_the_dragged_width(self):
        r = self._r()
        s = r["s6"]
        self.assertEqual(s["frameIds"], ["f-chat", "f-chat-2"], "both columns return after a reload: %r" % s)
        self.assertEqual(s["cols"], "[2]")
        self.assertEqual(s["col2Active"], SID_B, "column 2 finds its tab where it left it (its own state blob): %r" % s)
        self.assertEqual(s["col1Active"], SID_A)
        self.assertLessEqual(abs(s["pane2W"] - r["s4"]["after2"]), SLACK_PX,
                             "column 2 comes back at the width the drag left it: %r vs %r" % (s["pane2W"], r["s4"]["after2"]))
        self.assertLessEqual(abs(s["pane1W"] - r["s4"]["after1"]), SLACK_PX,
                             "…and so does column 1: %r vs %r" % (s["pane1W"], r["s4"]["after1"]))
        self.assertFalse(s["pickerOpen"]); self.assertEqual(s["lifted"], [])

    def test_7_closing_the_last_split_leaves_one_column_and_an_empty_set(self):
        s = self._r()["s7"]
        self.assertTrue(s["pane2Gone"], "__rompCloseSplit(2) removes the pane, its gutter and its iframe")
        self.assertEqual(s["frameIds"], ["f-chat"])
        self.assertEqual(s["cols"], "[]")
        self.assertNotIn("chat2", s["grow"] or {}, "the closed column's grow leaves the store: %r" % s["grow"])

    def test_8_the_whole_story_runs_in_well_under_half_a_minute(self):
        r = self._r()
        self.assertLess(r["ms"], 25000, "the driver waits on conditions, never on fixed sleeps: %d ms" % r["ms"])


if __name__ == "__main__":
    unittest.main()
