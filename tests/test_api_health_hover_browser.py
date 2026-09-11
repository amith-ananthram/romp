#!/usr/bin/env python3
"""The API cell's hover History section, driven in a real browser.

The shell page is kernel-served HTML with inline CSS and JS, so it has no jsdom harness: the source pins in
tests/test_api_health_hover.py hold the SHAPE and this module holds the BEHAVIOR. A scratch copy of
km._landing() is served from a temp directory over plain HTTP by a handler that also answers GET /api-health
with a synthetic signal (switchable between variants through /variant/<name>, so one page load exercises a
storm, a quiet tail across a restart, an empty signal, two buckets of one family and of two, an offline
window, a bucket the boot seeded, and tails longer than the cap); everything else 404s and the shell socket
is a shim that never opens, as in tests/test_api_health_browser.py. Playwright's chromium drives it: a frame
is handed to window.__rompApiHealth, the hover is opened by mouseenter / focus / Enter / click, and the driver
reports what the DOM did and how many reads the page made. Skips LOUDLY without the extension's node deps or
a browser (CI installs none).

Executed here, beyond the rendering: the newest read wins a race, a fresh show drops the last answer, a pin
after the hover reads once, the answer waits under a held pointer, the section's place between the sessions
the family-only bucket name, the dated stamp and the hour and day durations; Escape
dismisses the focus-shown hover, tooltip and dialog roles by mode, a window-refocus does not pop the hover,
and the geometry at 830 px wide and on a short window. The window-refocus trigger itself cannot be produced
headless (bringToFront fires no window focus in Playwright's chromium), so that case drives the mechanism with
synthetic window and cell focus events in one task. A real Tab out of an iframe onto the cell (the cell's
keyboard path on the dashboard, where every pane is an iframe; Chromium fires focus on the top window for the
frame change, in the cell's own task) shows the hover; the cell is described by a short summary, not the tip;
a transition filed at asOf (the hover's own read) closes the state before it; a bucket the boot seeded reads
one time in the head, the divider and the boot's row; a mixed window counts every attempt once and says how
many had no status; the previous kernel's last row filed in the same second as this kernel's start sits UNDER
the restart row; a row filed after the start sits under the clamped restart row with one restart mark, not a
row and a divider; the cell's description at focus time, in the focus's own task, carries the state word the
frame put on the cell, and the landed one its since; a hover after the pinned dialog closed is a tooltip again, not
a dialog left over; the light theme's head dot and failure line are read from the computed style; the answer under a
held pointer is waited for by the page's own count of parsed answers, never by a pause; the hover on a narrow window
is first anchored flush at the right edge, so the measurement after the rows land is against the edge (the reset
before it is what keeps the rows from wrapping).

Synthetic only: invented bucket labels, a fixed shape, times relative to the run so the same-day clock words
apply; no real data."""
import functools
import http.server
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from romp_load import load_source

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
EXT = os.path.join(os.path.dirname(HERE), "vscode-extension")
km = load_source("romp_kernel_apih_hover_browser", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_apih_hover_browser", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

NOW = int(time.time())
BOOT = NOW - 600
BOOT_M = BOOT - (BOOT % 60) - 2     # a boot at :58 of a minute: the second-straddling case two clocks would split on
RESTART = sb.API_HEALTH_RESTART_WHY
# invented labels in the digest form (12 hex characters), never real material: low-entropy digits, so nothing
# credential-shaped sits in this file, and every expected string below is built from these, never written out
KEY = "key:" + "0" * 11 + "1" + "|fable"
LOGIN = "login:" + "0" * 11 + "2" + "|fable"
OTHER = "key:" + "0" * 11 + "3" + "|opus"
AUTH, LAUTH = KEY.split("|")[0], LOGIN.split("|")[0]
WHY = "rate429 over 300 s = 0.40, n = 20 (attempts)"
FEW = "fewer than 10 attempts in every window"
SID = "11111111-2222-4333-8444-000000000001"      # a placeholder sid for the frame's one waiting session


def _win(requests, r429, r5xx, gave, sess, complete=True, no_status=0):
    # the counts follow the rates (T301: the reading counts the failures; the old fixture carried rates alone)
    n429 = int(round(requests * (r429 or 0))) if requests else 0
    n5xx = int(round(requests * (r5xx or 0))) if requests else 0
    return {"complete": complete, "requests": requests, "ok": requests - n429 - n5xx, "rateLimited": n429, "overloaded": 0,
            "serverErrors": n5xx, "otherErrors": 0, "noStatus": no_status, "gaveUp": gave, "retries": n429 + n5xx,
            "sessionsRetrying": sess, "turnsRetrying": sess, "rate429": r429, "rate5xx": r5xx}


def _spread(total, n, k):
    """`total` events over the newest `k` of `n` bins, evenly, deterministic (the oldest of the k gets the remainder)."""
    out = [0] * n
    for i in range(k):
        out[n - 1 - i] = total // k + (1 if i < total % k else 0)
    return out


def _tier(win, bin_s, n, k):
    return {"binS": bin_s, "from": NOW - NOW % bin_s - (n - 1) * bin_s,
            "ok": _spread(win["ok"], n, k), "rateLimited": _spread(win["rateLimited"], n, k),
            "serverErrors": _spread(win["serverErrors"] + win["overloaded"], n, k),
            "noStatus": _spread(win["noStatus"], n, k), "other": _spread(win["otherErrors"], n, k)}


def _ledger(windows):
    """T316: the ledger's three tiers (one-minute bins for the hour, five-minute for the day, hourly for the week) carrying
    the 15-minute window's counts, so the counts the lines read (the day) are the window's and the bars draw."""
    w = windows.get("900") or _win(0, None, None, 0, 0)
    return {"minute": _tier(w, 60, 60, 15), "fiveMin": _tier(w, 300, 288, 3), "hour": _tier(w, 3600, 168, 1)}


def _bucket(key, state, since, why, windows):
    auth, fam = key.split("|")
    return {"auth": auth, "family": fam, "state": state, "stateSince": since, "why": why, "windows": windows,
            "evidence": None, "transitions": [], "lastError": None, "ledger": _ledger(windows)}


def _tr(t, key, frm, to, why="x"):
    auth, fam = key.split("|")
    return {"t": t, "bucket": key, "auth": auth, "family": fam, "from": frm, "to": to, "why": why, "evidence": None}


def _base(**over):
    d = {"schema": 1, "asOf": NOW, "uptimeS": 600.0, "complete": False, "seq": 65, "lastEventAt": NOW - 3,
         "rate429Basis": "attempts", "coverage": {"sidechainExcluded": True}, "config": {"windows": [60, 300, 900]},
         "bootId": "4242.%d" % BOOT, "bootAt": BOOT, "overall": {"state": "unknown", "worstBucket": None},
         "buckets": {}, "transitions": []}
    d.update(over)
    return d


STORM_WINS = {"60": _win(4, 0.5, 0.0, 0, 1), "300": _win(20, 0.4, 0.0, 1, 2), "900": _win(45, 0.2, 0.02, 1, 2, complete=False)}
QUIET_WINS = {"60": _win(0, None, None, 0, 0), "300": _win(0, None, None, 0, 0), "900": _win(0, None, None, 0, 0, complete=False)}


def _chain(key, times_states):
    """Transitions for one bucket from a list of (t, state entered), each from the previous state."""
    out, prev = [], "unknown"
    for t, st in times_states:
        out.append(_tr(t, key, prev, st, FEW if st == "unknown" else "x"))
        prev = st
    return out


PAYLOADS = {
    # one bucket in a storm; the boot filed thrashing -> unknown, so the tail carries its own restart row
    "storm": _base(overall={"state": "thrashing", "worstBucket": KEY},
                   buckets={KEY: _bucket(KEY, "thrashing", NOW - 300, WHY, STORM_WINS)},
                   transitions=[_tr(NOW - 3000, KEY, "unknown", "healthy"), _tr(NOW - 1200, KEY, "healthy", "thrashing"),
                                _tr(BOOT + 1, KEY, "thrashing", "unknown", RESTART), _tr(NOW - 300, KEY, "unknown", "thrashing")]),
    # the bucket was already unknown when the previous kernel stopped: the boot filed nothing, the tail crosses
    # bootAt with no restart row, and the section must insert the divider
    "quiet": _base(overall={"state": "healthy", "worstBucket": KEY},
                   buckets={KEY: _bucket(KEY, "healthy", NOW - 100, None, {"60": _win(12, 0.0, 0.0, 0, 0), "300": _win(30, 0.0, 0.0, 0, 0), "900": _win(30, 0.0, 0.0, 0, 0, complete=False)})},
                   transitions=[_tr(NOW - 4000, KEY, "unknown", "healthy"), _tr(NOW - 3500, KEY, "healthy", "unknown", FEW),
                                _tr(NOW - 100, KEY, "unknown", "healthy")]),
    # no traffic ever: no bucket, no tail
    "empty": _base(),
    # two buckets of one family: the head names the worst one and counts them
    "two": _base(overall={"state": "thrashing", "worstBucket": KEY},
                 buckets={KEY: _bucket(KEY, "thrashing", NOW - 300, WHY, STORM_WINS),
                          LOGIN: _bucket(LOGIN, "healthy", NOW - 500, None, {"60": _win(0, None, None, 0, 0), "300": _win(11, 0.0, 0.0, 0, 0), "900": _win(11, 0.0, 0.0, 0, 0, complete=False)})},
                 transitions=[_tr(NOW - 500, LOGIN, "unknown", "healthy"), _tr(NOW - 300, KEY, "unknown", "thrashing")]),
    # two buckets of two families: named by family alone
    "twoFam": _base(overall={"state": "thrashing", "worstBucket": KEY},
                    buckets={KEY: _bucket(KEY, "thrashing", NOW - 300, WHY, STORM_WINS),
                             OTHER: _bucket(OTHER, "healthy", NOW - 500, None, {"60": _win(11, 0.0, 0.0, 0, 0), "300": _win(11, 0.0, 0.0, 0, 0), "900": _win(11, 0.0, 0.0, 0, 0, complete=False)})},
                    transitions=[_tr(NOW - 500, OTHER, "unknown", "healthy"), _tr(NOW - 300, KEY, "unknown", "thrashing")]),
    # this machine cannot reach the API: attempts fail at the connection level, so `requests` (attempts WITH a
    # status) is small while noStatus, gaveUp and sessionsRetrying are not; the windows NEST (the 15 min window
    # holds everything the 5 min one does), as a real document's do
    "offline": _base(overall={"state": "unknown", "worstBucket": KEY},
                     buckets={KEY: _bucket(KEY, "unknown", NOW - 200, FEW, {"60": _win(0, None, None, 1, 2, no_status=5),
                                                                          "300": _win(8, 0.25, 0.0, 1, 2, no_status=7),
                                                                          "900": _win(8, 0.25, 0.0, 1, 2, no_status=7, complete=False)})},
                     transitions=[_tr(NOW - 500, KEY, "unknown", "healthy"), _tr(NOW - 200, KEY, "healthy", "unknown", FEW)]),
    # a bucket unknown since before the boot with nothing since: the boot found it unknown, filed no row and seeded
    # its stateSince from the kernel's boot clock (bootAt itself; the backend is seeded with it); the read that
    # served this payload replaced the seed's reason with its own, as the live route does (an unchanged unknown
    # bucket carries why_unknown, never the restart reason); every hold in the tail is from a previous kernel life,
    # more than a day old, so the stamps carry their date and the durations reach hours and days
    "stale": _base(overall={"state": "unknown", "worstBucket": KEY},
                   buckets={KEY: _bucket(KEY, "unknown", BOOT, FEW, QUIET_WINS)},
                   transitions=_chain(KEY, [(NOW - 190000, "healthy"), (NOW - 183000, "degraded"), (NOW - 100000, "unknown")])),
    # the bucket had traffic when the previous kernel stopped, so the boot filed its restart row; a boot at :58 of a
    # minute, and nothing since: head since, the restart row's stamp and the pre-boot hold's end are one clock
    "minute": _base(bootAt=BOOT_M, uptimeS=float(NOW - BOOT_M), overall={"state": "unknown", "worstBucket": KEY},
                    buckets={KEY: _bucket(KEY, "unknown", BOOT_M, FEW, QUIET_WINS)},
                    transitions=[_tr(NOW - 3000, KEY, "unknown", "healthy"), _tr(NOW - 1500, KEY, "healthy", "thrashing"),
                                 _tr(BOOT_M, KEY, "thrashing", "unknown", RESTART)]),
    # the previous kernel's last read filed a transition 0.2 s before this kernel's start (it drains under SIGTERM
    # while still serving; the manager respawns at once), so the two sit in one second. bootAt is the start to the
    # millisecond and the restart row is seeded at it: the row is the newest, the old row's hold ends at it, no
    # divider (an int boot would sit before the old row, which would then read as the current state)
    "inversion": _base(bootAt=BOOT + 0.9, uptimeS=float(NOW - (BOOT + 0.9)), overall={"state": "unknown", "worstBucket": KEY},
                       buckets={KEY: _bucket(KEY, "unknown", BOOT + 0.9, FEW, QUIET_WINS)},
                       transitions=[_tr(NOW - 3000, KEY, "unknown", "healthy"), _tr(NOW - 1500, KEY, "healthy", "thrashing"),
                                    _tr(BOOT + 0.7, KEY, "thrashing", "healthy"), _tr(BOOT + 0.9, KEY, "healthy", "unknown", RESTART)]),
    # the previous kernel filed AFTER this one's start (the two overlapped, or the clock stepped): the backend seeds
    # the restart row one millisecond past that row and serves that stamp as bootAt, so the old row is the first
    # pre-boot row, its hold ending at the restart row; one restart, one mark: the restart row above suppresses the
    # divider at the crossing. uptimeS stays the kernel's own (_STARTED at BOOT + 0.9)
    "clamped": _base(bootAt=BOOT + 0.951, uptimeS=float(NOW - (BOOT + 0.9)), overall={"state": "unknown", "worstBucket": KEY},
                     buckets={KEY: _bucket(KEY, "unknown", BOOT + 0.951, FEW, QUIET_WINS)},
                     transitions=[_tr(NOW - 3000, KEY, "unknown", "healthy"), _tr(NOW - 1500, KEY, "healthy", "thrashing"),
                                  _tr(BOOT + 0.95, KEY, "thrashing", "healthy"), _tr(BOOT + 0.951, KEY, "healthy", "unknown", RESTART)]),
    # the hover's own read filed a transition, so its t is asOf: the state it closed reads its duration, closed, and
    # the new state is the one 'so far' (a flag, never a stamp comparison)
    "ownread": _base(overall={"state": "thrashing", "worstBucket": KEY},
                     buckets={KEY: _bucket(KEY, "thrashing", NOW, WHY, STORM_WINS)},
                     transitions=[_tr(NOW - 500, KEY, "unknown", "healthy"), _tr(NOW, KEY, "healthy", "thrashing", WHY)]),
    # eight transitions, six from before the boot (the bucket unknown at the stop: no restart row) and two after:
    # the cap shows six transitions, and the divider is extra
    "cap": _base(overall={"state": "degraded", "worstBucket": KEY},
                 buckets={KEY: _bucket(KEY, "degraded", NOW - 200, "x", STORM_WINS)},
                 transitions=_chain(KEY, [(NOW - 5000, "healthy"), (NOW - 4500, "degraded"), (NOW - 4000, "healthy"), (NOW - 3500, "thrashing"),
                                          (NOW - 3000, "recovering"), (NOW - 2500, "unknown"), (NOW - 400, "healthy"), (NOW - 200, "degraded")])),
    # eight transitions all after the boot: six rows and no divider
    "capPost": _base(overall={"state": "healthy", "worstBucket": KEY},
                     buckets={KEY: _bucket(KEY, "healthy", NOW - 70, "x", STORM_WINS)},
                     transitions=_chain(KEY, [(NOW - 560, "healthy"), (NOW - 490, "degraded"), (NOW - 420, "healthy"), (NOW - 350, "thrashing"),
                                              (NOW - 280, "recovering"), (NOW - 210, "healthy"), (NOW - 140, "degraded"), (NOW - 70, "healthy")])),
}


def _hm(t):
    return time.strftime("%H:%M", time.localtime(t))


def _min(s):
    """dur()'s minute form for 60 s <= s < 3600 s: Math.round, which rounds a half up (Python's round would not)."""
    return "%d min" % int(s / 60 + 0.5)


def _dur(s):
    """dur() under an hour: whole seconds first (Math.round), then '<n> s' under a minute, else Math.round minutes."""
    s = int(s + 0.5)
    return "%d s" % s if s < 60 else "%d min" % int(s / 60 + 0.5)


def _hmd(t):
    """The section's stamp: HH:MM today, else 'MM-DD HH:MM' (the browser runs on this machine's clock and zone)."""
    lt, today = time.localtime(t), time.localtime(NOW)
    hm = _hm(t)
    return hm if (lt.tm_year, lt.tm_yday) == (today.tm_year, today.tm_yday) else time.strftime("%m-%d ", lt) + hm


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
const R = { err: {}, pageErrors: [] };
page.on("pageerror", (e) => { R.pageErrors.push(String(e && e.stack || e && e.message || e)); });
// the WebSocket shim: the page's sockets never open (no kernel behind the scratch page), without the real
// socket's close-and-redial noise
await page.addInitScript(() => {
  function Fake(url) { this.url = String(url); this.readyState = 0; this.sent = []; this.onopen = this.onmessage = this.onclose = this.onerror = null; }
  Fake.prototype.send = function () { throw new Error("not open"); };
  Fake.prototype.close = function () {};
  Fake.CONNECTING = 0; Fake.OPEN = 1; Fake.CLOSING = 2; Fake.CLOSED = 3;
  window.WebSocket = Fake;
});
await page.goto(cfg.url);
await page.waitForFunction(() => typeof window.__rompApiHealth === "function", null, { timeout: 20000 });
const step = async (name, fn) => { try { await fn(); } catch (e) { R.err[name] = String(e && e.stack || e); } };
const ev = (fn, arg) => page.evaluate(fn, arg);
const rows = () => ev(() => Array.from(document.querySelectorAll("#ah-tip .ah-hist .ah-hrow")).map((n) => ({
  k: (n.querySelector(".ru-tip-k") || {}).textContent || "", w: n.querySelector(".ah-hword") ? n.querySelector(".ah-hword").textContent : null,
  v: n.querySelector(".ru-tip-v") ? n.querySelector(".ru-tip-v").textContent : null, boot: n.classList.contains("ah-boot") })));
// T316: the FIRST section is the title (with the window named once) and one line per machine (dot, name, coloured
// counts); `word`/`dot`/`name`/`since` read THIS machine's line. The History section holds the bars, the vertical legend
// (its rows), the age of the read, the machine names over their bars with several hosts, and this machine's State changes
const head = () => ev(() => { const h = document.querySelector("#ah-tip .ah-hist"), top = document.querySelector("#ah-tip > .ru-tip-win");
  const q = (s) => { const n = h && h.querySelector(s); return n ? n.textContent : null; };
  const qt = (s) => { const n = top && top.querySelector(s); return n ? n.textContent : null; };
  const me = top && top.querySelector('.ah-mline[data-host=""]');
  const mq = (s) => { const n = me && me.querySelector(s); return n ? n.textContent : null; };
  const bars = h ? Array.from(h.querySelectorAll(".ah-bars")) : [];
  return { word: mq(".ah-desc"), dot: me && me.querySelector(".ah-dot") ? me.querySelector(".ah-dot").getAttribute("data-dot") : null, name: mq(".ah-nm"),
           since: mq(".ah-since"), title: qt(".ru-tip-name span"), win: qt(".ah-win"), ago: q(".ah-ago"),
           legend: h ? Array.from(h.querySelectorAll(".ah-legend .ah-lrow")).map((n) => n.textContent) : null, graphs: bars.length,
           bars: bars.length ? +bars[0].getAttribute("data-bars") : 0, big: bars.some((b) => b.classList.contains("ah-big")),
           err: q(".ah-err"), wait: !!(h && h.querySelector(".ah-wait")),
           mlines: Array.from(document.querySelectorAll("#ah-tip .ah-mline")).map((n) => ((n.querySelector(".ah-nm") || {}).textContent || "") + ": " + ((n.querySelector(".ah-desc") || {}).textContent || "")),
           gnames: Array.from(h ? h.querySelectorAll(".ah-gname .ah-nm") : []).map((n) => n.textContent),
           unknown: /unknown/i.test(document.getElementById("ah-tip").innerText),
           names: Array.from(h ? h.querySelectorAll(".ru-tip-name span:first-child") : []).map((n) => n.textContent) }; });
const shown = () => ev(() => document.getElementById("ah-tip").style.display === "block");
// the colours the theme resolves to, read from the computed style of the head's dot and of the failure line
const dotColor = () => ev(() => getComputedStyle(document.querySelector('#ah-tip > .ru-tip-win .ah-mline[data-host=""] .ah-dot')).backgroundColor);
const errColor = () => ev(() => getComputedStyle(document.querySelector("#ah-tip .ah-hist .ah-err")).color);
const described = () => ev(() => document.getElementById("rail-api").getAttribute("aria-describedby"));
// the tip's role and modal flag, whether focus sits inside it, and whether it is the centered card
// #ah-summary is read null-safe so a page without it fails the description assertions alone, not every step
const mode = () => ev(() => { const t = document.getElementById("ah-tip"), d = document.getElementById("ah-summary"), b = d ? d.getBoundingClientRect() : null;
  return { role: t.getAttribute("role"), modal: t.getAttribute("aria-modal"),
  focusInside: t.contains(document.activeElement), modalClass: t.classList.contains("ru-modal"), shown: t.style.display === "block",
  described: document.getElementById("rail-api").getAttribute("aria-describedby"), activeIsCell: document.activeElement === document.getElementById("rail-api"),
  descText: d ? d.textContent : null, descBox: b ? [b.width, b.height] : null, descInTree: !!d && document.body.contains(d) }; });
const descOf = () => ev(() => { const d = document.getElementById("ah-summary"); return d ? d.textContent : null; });
// the mouseenter and the look at what it painted are ONE task: the read fires on the show, so its answer cannot land
// before this returns, and the loader state read here is what the user sees before the answer (a separate round trip
// lets the local server answer in between, and the dots are already rows)
const enter = () => ev(() => { const el = document.getElementById("rail-api"); el.dispatchEvent(new MouseEvent("mouseenter", { bubbles: false, clientX: el.getBoundingClientRect().left + 10 }));
  return { wait: !!document.querySelector("#ah-tip .ah-hist .ah-wait"), described: el.getAttribute("aria-describedby"), fetchN: window.__fetchN,
           rows: document.querySelectorAll("#ah-tip .ah-hist .ah-hrow").length, desc: (document.getElementById("ah-summary") || {}).textContent }; });
const leave = () => ev(() => { document.getElementById("rail-api").dispatchEvent(new MouseEvent("mouseleave")); });
const waitRows = () => page.waitForFunction(() => document.querySelectorAll("#ah-tip .ah-hist .ah-hrow").length > 0, null, { timeout: 8000 });
const waitSel = (s) => page.waitForFunction((s) => !!document.querySelector(s), s, { timeout: 8000 });
const variant = (v) => ev((v) => window.__realFetch("/variant/" + v).then((r) => r.text()), v);
const fetchN = () => ev(() => window.__fetchN);
const pause = (ms) => ev((ms) => new Promise((r) => setTimeout(r, ms)), ms);   // the driver's own wait, never the page's
const frame = (over) => Object.assign({ type: "apiHealth", state: "ok", cls: "", reason: "", text: "ok", waiting: 0, retrying: 0, blocked: 0, since: 0, sessions: [], seq: 1 }, over || {});
// where the tip sits against the viewport and the cell, and whether any row wrapped or the tip clips
const geo = () => ev(() => { const tip = document.getElementById("ah-tip"), t = tip.getBoundingClientRect(), c = document.getElementById("rail-api").getBoundingClientRect();
  return { l: t.left, r: t.right, t: t.top, b: t.bottom, h: t.height, cellTop: c.top, iw: window.innerWidth, ih: window.innerHeight,
           rowH: Array.from(document.querySelectorAll("#ah-tip .ah-hist .ah-hrow")).map((n) => n.getBoundingClientRect().height),
           clipped: tip.scrollHeight > tip.clientHeight, maxH: tip.style.maxHeight }; });
// the reads are counted through a wrapper on the page's fetch; the real fetch still runs. __fetchN counts reads at call
// time; __doneN counts answers the page has PARSED (its r.json() settled, one microtask before the script files the
// answer), so a step can wait for an answer to have landed in the script rather than for a fetch to have started.
// __restoreFetch puts the wrapper back after a step swapped fetch out.
await ev(() => { const real = window.fetch; window.__fetchN = 0; window.__doneN = 0; window.__realFetch = real;
  window.__lastUrls = [];
  window.__restoreFetch = function () { window.fetch = function (u) { const api = String(u).indexOf("/api-health") === 0; if (api) window.__fetchN++; window.__lastUrls.push(String(u));
    const p = real.apply(window, arguments);
    return api ? p.then((r) => { const j = r.json.bind(r); r.json = () => j().then((d) => { window.__doneN++; return d; }); return r; }) : p; }; };
  window.__restoreFetch(); });
// the frame's quiet and errs flags come from the kernel's own ring, the same events the document counts (T301 review:
// the dot follows the frame, a reading only words the lines), so the lab derives them from the served document with the
// page's own reading rule and pushes a frame that says what a kernel serving that document would say
const flagsFor = () => ev(() => window.__realFetch("/api-health").then((r) => r.json()).then((d) => {
  const r = window.__rompApiHealthMerge.readHistory(d); return { quiet: r.level === "quiet", errs: r.level === "errors" ? (r.errors || 1) : 0 }; }));
await ev((f) => { window.__rompApiHealth(f); }, frame(await flagsFor()));
await step("storm", async () => {
  // 1. the hover: the loader's dots first, the rows when the read lands; the cell is described by the tip
  const first = await enter();
  R.stormWaitFirst = first.wait; R.stormDescribed = first.described; R.stormFetchN0 = first.fetchN; R.stormDesc0 = first.desc;
  await waitRows();
  R.storm = { head: await head(), rows: await rows(), fetchN: await fetchN(), shown: await shown(), mode: await mode(), geo: await geo() };
  await leave();
  R.stormHidden = !(await shown()); R.stormDescribedAfter = await described();
});
// each later show is sampled the same way: a fresh show drops the last answer (the dots stand in again) and reads once
const show = async (name) => { await variant(name); await ev((f) => { window.__rompApiHealth(f); }, frame(await flagsFor())); const e = await enter(); R[name + "Wait"] = e.wait; R[name + "Rows0"] = e.rows; };
await step("quiet", async () => {
  // 2. the tail crosses bootAt with no restart row: the divider; the hold from before the boot ends at the boot
  await show("quiet"); await waitRows();
  R.quiet = { head: await head(), rows: await rows() };
  await leave();
});
await step("empty", async () => {
  // 3. no bucket: the head says so, no rows
  await show("empty"); await waitSel("#ah-tip .ah-hist .ah-legend");
  R.empty = { head: await head(), rows: await rows() };
  await leave();
});
await step("two", async () => {
  // 4. two buckets of one family: the head names the worst by family and auth and counts them
  await show("two"); await waitRows();
  R.two = { head: await head(), rows: await rows() };
  await leave();
});
await step("twoFam", async () => {
  // 5. two buckets of two families: named by family alone
  await show("twoFam"); await waitRows();
  R.twoFam = { head: await head(), rows: await rows() };
  await leave();
});
await step("offline", async () => {
  // 6. a window of no-status attempts names them with its give-ups and sessions; a mixed window names both; a
  //    quiet window says no attempts
  await show("offline"); await waitRows();
  R.offline = { head: await head(), rows: await rows() };
  await leave();
});
await step("stale", async () => {
  // 7. a bucket the boot seeded: since the boot in the head, the divider at the boot, the pre-boot hold closed at
  //    the boot and never 'so far'; dated stamps, hour and day durations
  await show("stale"); await waitRows();
  R.stale = { head: await head(), rows: await rows() };
  await leave();
  // 7b. the boot filed a restart row (traffic at the stop) at :58 of a minute: head since and the row's stamp agree
  await show("minute"); await waitRows();
  R.minute = { head: await head(), rows: await rows() };
  await leave();
});
await step("inversion", async () => {
  // 7d. the previous kernel's last row 0.2 s before the boot, the restart row at the boot: the restart row is the
  //     newest, the old row closes at it, no divider; 7e. that row AFTER the boot and the restart row a millisecond
  //     past it (the backend's clamp): still one restart mark
  await show("inversion"); await waitRows();
  R.inversion = { head: await head(), rows: await rows() };
  await leave();
  await show("clamped"); await waitRows();
  R.clamped = { head: await head(), rows: await rows() };
  await leave();
});
await step("ownread", async () => {
  // 7c. a transition at t === asOf, the hover's own read: the closed state reads its duration, the new one 'so far'
  await show("ownread"); await waitRows();
  R.ownread = { head: await head(), rows: await rows() };
  await leave();
});
await step("cap", async () => {
  // 8. the cap counts transitions: six of them with the divider extra, six alone when nothing crosses the boot
  await show("cap"); await waitRows();
  R.cap = { rows: await rows() };
  await leave();
  await show("capPost"); await waitRows();
  R.capPost = { rows: await rows() };
  await leave();
});
await step("fail", async () => {
  // 9. a failed read is one loud line in place of the rows: a non-2xx, a rejected fetch, a malformed answer
  await ev(() => { window.fetch = () => Promise.resolve({ ok: false, status: 503 }); });
  await enter(); await waitSel("#ah-tip .ah-err");
  R.fail503 = { head: await head(), rows: await rows(), desc: await descOf(), errColor: await errColor() };
  await leave();
  await ev(() => { window.fetch = () => Promise.reject(new Error("Failed to fetch")); });
  await enter(); await page.waitForFunction(() => /Failed to fetch/.test((document.querySelector("#ah-tip .ah-err") || {}).textContent || ""), null, { timeout: 8000 });
  R.failReject = { head: await head(), rows: await rows() };
  await leave();
  await ev(() => { window.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }); });
  await enter(); await page.waitForFunction(() => /malformed/.test((document.querySelector("#ah-tip .ah-err") || {}).textContent || ""), null, { timeout: 8000 });
  R.failMalformed = { head: await head(), rows: await rows() };
  await leave();
  // the real reads come back, and the next hover replaces the failure with the rows
  await ev(() => { window.__restoreFetch(); });
  await variant("storm"); await enter(); await waitRows();
  R.recovered = { head: await head(), rows: await rows() };
  await leave();
});
await step("theme", async () => {
  // 9b. the light theme (body.theme-light, which the shell's theme reader toggles): the head's dot for the signal's
  //     quiet states wears the light rail's dark gray, and the failure line the light error red, both read from the
  //     COMPUTED style, so a dropped rule shows here and not only in a source pin
  await ev(() => { document.body.classList.add("theme-light"); });
  await show("quiet"); await waitRows(); R.lightHealthyDot = { word: (await head()).word, color: await dotColor() }; await leave();
  await show("stale"); await waitRows(); R.lightUnknownDot = { word: (await head()).word, color: await dotColor() }; await leave();
  await ev(() => { window.fetch = () => Promise.resolve({ ok: false, status: 503 }); });
  await enter(); await waitSel("#ah-tip .ah-err"); R.lightErr = await errColor(); await leave();
  await ev(() => { window.__restoreFetch(); document.body.classList.remove("theme-light"); });
  await variant("storm");
});
await step("race", async () => {
  // 10. two reads in flight (enter, leave, enter): the older answer landing first is dropped, the dots stay until
  //     the newer one lands, and the newer one is what shows. The frame is a plain fine one (the last show pushed the
  //     stale variant's quiet flag): the head's word while the dots stand is the frame's, so it must be known here
  await ev((f) => { window.__rompApiHealth(f); }, frame({ quiet: false, errs: 0 }));
  await ev(async () => { const rf = window.__realFetch;
    await rf("/variant/storm"); window.__A = await (await rf("/api-health")).json();
    await rf("/variant/quiet"); window.__B = await (await rf("/api-health")).json();
    window.__pend = []; window.fetch = () => new Promise((res) => { window.__pend.push(res); }); });
  await enter(); await leave(); await enter();
  R.racePending = await ev(() => window.__pend.length);
  R.fineDesc0 = await descOf();   // a fine frame, its read in flight: the description keeps a state word (the dot's)
  await ev(() => { window.__pend[0]({ ok: true, status: 200, json: () => Promise.resolve(window.__A) }); });
  await pause(80);
  R.raceAfterOld = await head();
  await ev(() => { window.__pend[1]({ ok: true, status: 200, json: () => Promise.resolve(window.__B) }); });
  await waitRows();
  R.raceAfterNew = await head();
  await leave();
  await ev(() => { window.__restoreFetch(); });
  await variant("storm");
});
await step("order", async () => {
  // 11. with a waiting session (and a terminal count an older peer's frame may still carry, which the kernel's
  //     own frame lost in T332), the section sits after Sessions waiting, and no terminal-coverage line follows it (T331)
  await ev((f) => { window.__rompApiHealth(f); }, frame({ state: "degraded", cls: "429", text: "rate limited · 1 waiting", waiting: 1, retrying: 1, since: NOW_PLACEHOLDER, tmux: 1, seq: 1,
    sessions: [{ sid: "SID_PLACEHOLDER", name: "web", color: null, kind: "retrying", cls: "429", status: 429, since: NOW_PLACEHOLDER, suppressed: false }] }));
  await enter(); await waitRows();
  R.order = await ev(() => Array.from(document.querySelectorAll("#ah-tip > .ru-tip-win")).map((w) => { const n = w.querySelector(".ru-tip-name span"); return n ? n.textContent : w.textContent.slice(0, 24); }));
  await leave();
  await ev((f) => { window.__rompApiHealth(f); }, frame());
});
await step("hosts", async () => {
  // 11b. T301: a frame carrying an attached host's frame reads that host's document through the relay and names
  //      every machine: the section counts them, the dot is the worst state, the History carries a line per machine
  await ev((f) => { window.__rompApiHealth(f); }, frame({ host: "HUBHOST", hosts: { TESTHOST: { state: "degraded", cls: "429", text: "rate limited · 2 waiting", waiting: 2, retrying: 2, blocked: 0, since: NOW_PLACEHOLDER, reason: "", stale: false } } }));
  await variant("quiet");
  await enter(); await page.waitForFunction(() => document.querySelectorAll("#ah-tip .ah-hist .ah-bars").length >= 2, null, { timeout: 8000 });
  R.hosts = { head: await head(), rows: await rows(), fetched: await ev(() => window.__lastUrls || null) };
  await leave();
  await ev((f) => { window.__rompApiHealth(f); }, frame());
  await variant("storm");
});
await step("detail", async () => {
  // 11c. T316: the dot's click opens the detail: the same card with range chips (1 hour, 24 hours, 7 days) and one LARGE
  //      stacked histogram per machine; the day is the default and draws 96 quarter-hour bars from the 288 five-minute
  //      bins, the hour its 60 one-minute bins, the week its 168 hourly bins; Escape closes it
  await ev((f) => { window.__rompApiHealth(f); }, frame(await flagsFor()));
  await ev(() => { document.getElementById("rail-api").click(); });
  await waitSel("#ah-tip .ah-range"); await page.waitForFunction(() => !!document.querySelector("#ah-tip .ah-bars.ah-big"), null, { timeout: 8000 });
  const chips = () => ev(() => Array.from(document.querySelectorAll("#ah-tip .ah-range .rsp-btn")).map((b) => ({ t: b.textContent, on: b.classList.contains("on"), act: b.getAttribute("data-act") })));
  const barsOf = () => ev(() => { const b = document.querySelector("#ah-tip .ah-bars.ah-big"); return b ? +b.getAttribute("data-bars") : 0; });
  // the stack: within one bar, the successes segment sits at the bottom (the largest y), the 429 segment on it, the 5xx on that
  const stackOf = () => ev(() => { const b = document.querySelector("#ah-tip .ah-bars.ah-big"); if (!b) return null; const byX = {};
    b.querySelectorAll("rect").forEach((r) => { (byX[r.getAttribute("x")] = byX[r.getAttribute("x")] || []).push(r); });
    const col = Object.values(byX).find((a) => a.length >= 3) || Object.values(byX).find((a) => a.length >= 2); if (!col) return null;
    return col.map((r) => ({ c: r.getAttribute("class").replace("ah-seg ah-seg-", ""), y: +r.getAttribute("y"), h: +r.getAttribute("height"), fill: getComputedStyle(r).fill })); });
  R.detail = { mode: await mode(), chips: await chips(), dayBars: await barsOf(), head: await head(), stack: await stackOf(),
    legendAboveBars: await ev(() => { const l = document.querySelector("#ah-tip .ah-hist .ah-legend"), b = document.querySelector("#ah-tip .ah-hist .ah-bars"); return !!(l && b) && (l.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0; }),
    legendCount: await ev(() => document.querySelectorAll("#ah-tip .ah-legend").length) };
  await ev(() => { document.querySelector('#ah-tip [data-act="range:hour"]').click(); });
  R.detail.hourBars = await barsOf(); R.detail.hourOn = await ev(() => document.querySelector('#ah-tip [data-act="range:hour"]').classList.contains("on"));
  await ev(() => { document.querySelector('#ah-tip [data-act="range:week"]').click(); });
  R.detail.weekBars = await barsOf();
  await ev(() => { document.querySelector('#ah-tip [data-act="range:day"]').click(); });
  await page.keyboard.press("Escape");
  R.detail.closed = !(await shown());
  // the hover after the detail draws the day, small, with no chips
  await enter(); await waitRows();
  R.detail.hoverAfter = await head(); R.detail.hoverChips = await ev(() => document.querySelectorAll("#ah-tip .ah-range").length);
  await leave();
});
await step("focus", async () => {
  // 12. keyboard focus shows the hover as the pointer does; blur hides it. The focus and the look at the description
  //     are ONE task: assistive tech reads the description at focus time, and what is there then is what the user
  //     hears. A frame with a distinctive text stands on the cell first, so the word the description carries is
  //     provably the frame's; frame() is put back after.
  await ev((f) => { window.__rompApiHealth(f); }, frame({ state: "degraded", cls: "429", text: "rate limited · 1 waiting", waiting: 1, since: NOW_PLACEHOLDER }));
  R.focusDesc0 = await ev(() => { document.getElementById("rail-api").focus(); return (document.getElementById("ah-summary") || {}).textContent; });
  R.focusShown = await shown(); R.focusDescribed = await described(); R.focusMode = await mode();
  await waitRows();
  R.focusDesc = await descOf();
  await ev(() => { document.getElementById("rail-api").blur(); });
  R.blurHidden = !(await shown()); R.blurDescribed = await described();
  await ev((f) => { window.__rompApiHealth(f); }, frame());
  // 13. the pointer arriving on the tip focus already shows keeps its rows and does not re-read
  await ev(() => { document.getElementById("rail-api").focus(); });
  await waitRows();
  const n0 = await fetchN();
  const e = await enter();
  R.focusEnter = { wait: e.wait, rows: e.rows, read: e.fetchN - n0 };
  await pause(60);
  R.focusEnterAfter = { fetchN: (await fetchN()) - n0, rows: (await rows()).length };
  // 14. Escape dismisses the focus-shown hover without moving focus
  await page.keyboard.press("Escape");
  R.focusEsc = await mode();
  // 15. the window regaining focus re-dispatches focus on the active element in the same task: not a show, not a
  //     read (synthetic window and cell focus events, the trigger being unreachable headless); a focus that arrives
  //     after the next frame is the user's, and shows
  const n1 = await fetchN();
  R.refocus = await ev(() => { const el = document.getElementById("rail-api");
    window.dispatchEvent(new FocusEvent("focus")); el.dispatchEvent(new FocusEvent("focus"));
    return { shown: document.getElementById("ah-tip").style.display === "block", fetchN: window.__fetchN }; });
  R.refocus.read = R.refocus.fetchN - n1;
  await ev(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  R.refocusLater = await ev(() => { document.getElementById("rail-api").dispatchEvent(new FocusEvent("focus"));
    return { shown: document.getElementById("ah-tip").style.display === "block", fetchN: window.__fetchN }; });
  R.refocusLater.read = R.refocusLater.fetchN - n1;
  await ev(() => { document.getElementById("rail-api").blur(); });
});
await step("frameTab", async () => {
  // 15b. the cell's keyboard path on the dashboard, where every pane is an iframe: focus in a pane, Tab onto the cell.
  //      Chromium fires focus on the TOP window for the frame change, in the same task as the cell's focus, so the
  //      mark is set; the element it recorded is the body (the host is cleared before the event), not the cell, and
  //      the hover shows and reads once. A scratch iframe with an input stands in for the pane, inserted right before
  //      the cell so the Tab lands on it; removed after.
  await ev(() => new Promise((res) => { const el = document.getElementById("rail-api"), f = document.createElement("iframe");
    f.id = "lab-pane"; f.style.cssText = "width:1px;height:1px;border:0"; f.srcdoc = "<input id=lab-in>"; f.onload = () => res();
    el.parentNode.insertBefore(f, el); }));
  await ev(() => { window.__winLog = []; window.addEventListener("focus", () => { const a = document.activeElement; window.__winLog.push(a ? (a.id || a.tagName) : null); }); });
  await page.frameLocator("#lab-pane").locator("#lab-in").focus();
  R.frameTabFrom = await ev(() => document.activeElement ? document.activeElement.id : null);
  const n = await fetchN();
  await page.keyboard.press("Tab");
  R.frameTab = await ev(() => ({ active: document.activeElement ? document.activeElement.id : null, shown: document.getElementById("ah-tip").style.display === "block",
    described: document.getElementById("rail-api").getAttribute("aria-describedby"), winLog: window.__winLog.slice(), fetchN: window.__fetchN }));
  R.frameTab.read = R.frameTab.fetchN - n;
  await waitRows();
  R.frameTabRows = (await rows()).length;
  await ev(() => { document.getElementById("rail-api").blur(); document.getElementById("lab-pane").remove(); });
});
await step("keyboard", async () => {
  // 16. Enter pins the detail with the section in it, as the dialog with focus inside, and reads nothing new (the
  //     focus show's read is the same document); Escape closes, focus returns to the cell, and the hover does NOT
  //     pop back from that refocus
  await ev(() => { document.getElementById("rail-api").focus(); });
  await waitRows();
  R.kbFetchPre = await fetchN();
  await page.keyboard.press("Enter");
  R.kbFetchPost = await fetchN();
  R.kbPinned = await ev(() => { const t = document.getElementById("ah-tip"); return t.style.display === "block" && t.classList.contains("ru-modal"); });
  R.kbMode = await mode();
  R.kbRows = (await rows()).length; R.kbHead = await head();
  R.kbFetchBefore = await fetchN();
  // 17. a frame landing on the open detail re-reads the history (the world changed)
  await ev((f) => { window.__rompApiHealth(f); }, frame({ state: "degraded", cls: "529", text: "overloaded · 1 waiting", waiting: 1, blocked: 1, since: NOW_PLACEHOLDER, seq: 2 }));
  await page.waitForFunction((n) => window.__fetchN > n, R.kbFetchBefore, { timeout: 8000 });
  R.kbFetchAfter = await fetchN();
  await page.keyboard.press("Escape");
  R.escHidden = !(await shown());
  R.escFocusBack = await ev(() => document.activeElement === document.getElementById("rail-api"));
  await pause(120);    // a late answer or the refocus must not re-show it
  R.escStillHidden = !(await shown()); R.escDescribed = await described();
});
await step("pin", async () => {
  // 18. a click after the hover reads nothing new and keeps the hover's answer; a click with nothing showing reads
  await ev(() => { document.getElementById("rail-api").blur(); });
  await enter(); await waitRows();
  const n0 = await fetchN();
  await ev(() => { document.getElementById("rail-api").click(); });
  R.pinAfterHover = { read: (await fetchN()) - n0, mode: await mode(), rows: (await rows()).length, wait: (await head()).wait };
  await page.keyboard.press("Escape");
  R.pinClosed = await mode();
  // 18b. the hover after the dialog: close() leaves the hidden tip's role alone, so show() must put the tooltip role
  //      back and drop aria-modal, or a keyboard user's next hover is announced as a modal dialog their focus sits outside
  //      of. Focus is taken off whatever holds it first (the close's return to the body is a no-op focus() call, and the
  //      browser moves focus off the hidden tip only at its next rendering update), so the hover starts from the body.
  await ev(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); });
  await enter(); await waitRows();
  R.hoverAfterDialog = await mode();
  await leave();
  const n1 = await fetchN();
  await ev(() => { document.getElementById("rail-api").click(); });
  R.pinFromHidden = { read: (await fetchN()) - n1, mode: await mode(), wait: (await head()).wait };
  await waitRows();
  // 19. an answer landing under a held primary pointer waits for the release. The wait is for the answer to have been
  //     parsed by the page (__doneN), not for the fetch to have started: a slow answer would otherwise be read before
  //     it landed, and the release would have nothing to paint
  await ev(() => { document.getElementById("ah-tip").dispatchEvent(new PointerEvent("pointerdown", { button: 0, bubbles: true })); });
  await variant("quiet");
  const d0 = await ev(() => window.__doneN);
  await ev((f) => { window.__rompApiHealth(f); }, frame({ seq: 3 }));
  await page.waitForFunction((n) => window.__doneN > n, d0, { timeout: 8000 });
  R.heldWord = (await head()).word;
  await ev(() => { document.body.dispatchEvent(new PointerEvent("pointerup", { button: 0, bubbles: true })); });
  R.releasedWord = (await head()).word;
  await page.keyboard.press("Escape");
  await ev(() => { document.getElementById("rail-api").blur(); });
  await variant("storm");
});
await step("geometry", async () => {
  // 20. the hover measures after a reset and is capped to the room above the rail: at 830 px wide it keeps its
  //     margin and no row wraps; on a 280 px tall window it stays above the rail and clips instead of spilling.
  //     Each size is a FRESH page: the shell lays the rail out once at load, so a resized viewport does not move it.
  //     On the narrow page the pointer enters near the RIGHT edge, so the first anchor (the loader's narrow tip) sits
  //     flush at the right margin and the re-render after the rows land is measured against that edge: without the
  //     reset a fixed element's shrink-to-fit width is taken from where it last sat, and the rows wrap. The first
  //     anchor's rectangle is recorded so the test can check that premise.
  await enter(); await waitRows();
  R.geoWide = await geo();
  await leave();
  const fresh = async (width, height, atEdge) => {
    const p = await browser.newPage({ viewport: { width, height } });
    await p.addInitScript(() => {
      function Fake(url) { this.url = String(url); this.readyState = 0; this.onopen = this.onmessage = this.onclose = this.onerror = null; }
      Fake.prototype.send = function () { throw new Error("not open"); }; Fake.prototype.close = function () {};
      Fake.CONNECTING = 0; Fake.OPEN = 1; Fake.CLOSING = 2; Fake.CLOSED = 3; window.WebSocket = Fake; });
    await p.goto(cfg.url);
    await p.waitForFunction(() => typeof window.__rompApiHealth === "function", null, { timeout: 20000 });
    await p.evaluate((f) => { window.__rompApiHealth(f); }, frame());
    const first = await p.evaluate((atEdge) => { const el = document.getElementById("rail-api"), x = atEdge ? window.innerWidth - 10 : el.getBoundingClientRect().left + 10;
      el.dispatchEvent(new MouseEvent("mouseenter", { clientX: x })); const t = document.getElementById("ah-tip").getBoundingClientRect();
      return { x, l: t.left, r: t.right, w: t.width }; }, atEdge);
    await p.waitForFunction(() => document.querySelectorAll("#ah-tip .ah-hist .ah-hrow").length > 0, null, { timeout: 8000 });
    const g = await p.evaluate(() => { const tip = document.getElementById("ah-tip"), t = tip.getBoundingClientRect(), c = document.getElementById("rail-api").getBoundingClientRect();
      return { l: t.left, r: t.right, t: t.top, b: t.bottom, h: t.height, w: t.width, cellTop: c.top, iw: window.innerWidth, ih: window.innerHeight,
               rowH: Array.from(document.querySelectorAll("#ah-tip .ah-hist .ah-hrow")).map((n) => n.getBoundingClientRect().height),
               clipped: tip.scrollHeight > tip.clientHeight, maxH: tip.style.maxHeight }; });
    await p.close();
    g.first = first;
    return g;
  };
  R.geo830 = await fresh(830, 600, true);
  R.geoShort = await fresh(1200, 280, false);
});
if (cfg.shots) await page.screenshot({ path: cfg.shots });
fs.writeSync(1, "RESULT:" + JSON.stringify(R) + "\n");
await browser.close();
process.exit(0);
""".replace("NOW_PLACEHOLDER", str(NOW - 30)).replace("SID_PLACEHOLDER", SID)


class _Lab(http.server.SimpleHTTPRequestHandler):
    """The scratch page's server: index.html from the temp dir, GET /api-health from PAYLOADS, /variant/<name>
    switches which one; everything else the shell asks for 404s."""
    variant = ["storm"]

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api-health":
            return self._json(PAYLOADS[self.variant[0]])
        if path == "/remote/TESTHOST/api-health":            # T301: an attached host's document through the relay
            return self._json(PAYLOADS["storm"])
        if path == "/dist/api-health-global.js":            # the merge + reading rules the shell script reads
            body = open(os.path.join(EXT, "dist", "api-health-global.js"), "rb").read()
            self.send_response(200); self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if path.startswith("/variant/"):
            name = path.rsplit("/", 1)[1]
            if name in PAYLOADS:
                self.variant[0] = name
            return self._json({"variant": self.variant[0]})
        return super().do_GET()


class ServedHistory(unittest.TestCase):
    """One browser run over the scratch page; each test reads one facet of what it reported."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served hover needs a browser")
        cls.lab = tempfile.mkdtemp(prefix="apih-hover-browser-")
        if not os.path.exists(os.path.join(EXT, "dist", "api-health-global.js")):
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        html = km._landing()
        with open(os.path.join(cls.lab, "index.html"), "w") as f:
            f.write(html if isinstance(html, str) else html.decode("utf-8"))
        _Lab.variant[0] = "storm"
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Lab, directory=cls.lab))
        cls.thr = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thr.start()
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"url": "http://127.0.0.1:%d/" % cls.srv.server_address[1],
                       "shots": os.environ.get("APIH_HOVER_SHOT", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        cls.srv.shutdown()
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this machine: the served hover needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        cls.R = json.loads(line[len("RESULT:"):])

    def test_the_driver_hit_no_script_error(self):
        self.assertEqual(self.R["err"], {})
        # every page exception counts: a render-path TypeError names no 'api', and a step with no DOM wait after its
        # read would otherwise pass over it
        self.assertEqual(self.R["pageErrors"], [])

    def test_the_hover_reads_the_route_once_shows_the_loader_first_and_describes_the_cell(self):
        R = self.R
        self.assertTrue(R["stormWaitFirst"], "the loader's dots stand in until the answer lands")
        self.assertEqual(R["stormFetchN0"], 1, "one read per show, fired on the show")
        self.assertEqual(R["stormDescribed"], "ah-summary", "described by the short summary, not the tip")
        self.assertEqual(R["storm"]["fetchN"], 1)
        self.assertTrue(R["storm"]["shown"])
        self.assertFalse(R["storm"]["head"]["wait"], "the dots go when the rows arrive")
        self.assertTrue(R["stormHidden"], "mouseleave hides the hover")
        self.assertIsNone(R["stormDescribedAfter"], "and drops the description")

    def test_a_fresh_show_drops_the_last_answer_and_the_newest_read_wins_a_race(self):
        R = self.R
        for name in ("quiet", "empty", "two", "twoFam", "offline", "stale", "minute", "inversion", "clamped", "ownread", "cap", "capPost"):
            self.assertTrue(R[name + "Wait"], "%s: the dots stand in again, not the last hover's rows" % name)
            self.assertEqual(R[name + "Rows0"], 0, name)
        self.assertEqual(R["racePending"], 2, "enter, leave, enter: two reads in flight")
        self.assertTrue(R["raceAfterOld"]["wait"], "the older answer landing first is dropped: the dots stay")
        self.assertEqual(R["raceAfterOld"]["word"], "", "and this machine's line still says what the frame alone says: nothing yet (no verdict word)")
        self.assertEqual(R["fineDesc0"], "API health: Fine. Reading the details. Press Enter to open it.", "the description keeps the dot's state word meanwhile")
        self.assertEqual(R["raceAfterNew"]["word"], "30 successful requests", "the newer read's answer is what shows")
        self.assertFalse(R["raceAfterNew"]["wait"])

    def test_the_storm_reads_in_plain_words_with_the_legend_and_the_tail_with_its_restart_row(self):
        # T301: no per-window rows and no state-machine word: the head says what happened over the longest window
        # (the counts, the machine's verdict as a plain phrase), the dot is red for a window in errors even with
        # nothing waiting right now, the legend explains the codes, and the tail is capped at four in plain words
        h, rows = self.R["storm"]["head"], self.R["storm"]["rows"]
        # T316: this machine's line is its counts over the day in their colours, no verdict word; the window named once
        self.assertEqual((h["word"], h["dot"]), ('35 successful requests · 9 429s · 1 5xx', "errors"))
        self.assertEqual(h["name"], "this machine", "a frame without a host name")
        self.assertEqual(h["title"], "API health", "one machine: no count")
        self.assertEqual(h["win"], "last 24 hours", "the ledger's day, named once at the top")
        self.assertIsNone(h["since"], "the frame is ok: no since on the line")
        self.assertRegex(h["ago"], r"^read (now|\d+ minutes? ago)$", "the read's age in words on one clock, never a clock stamp")
        self.assertEqual(h["names"], ["History", "State changes"])
        self.assertEqual(h["legend"], ["429 rate limit: the API told us to slow down", "5xx server error: the API itself failed"],
                         "vertical: one line each, 429 then 5xx; no other line when the range holds no such failure")
        self.assertEqual((h["graphs"], h["bars"], h["big"]), (1, 96, False), "the day as 96 quarter-hour bars, small in the hover")
        self.assertFalse(h["unknown"], "the word never reaches the user")
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in rows],
                         [(_hmd(NOW - 300), "rate-limit storm", "5 min so far", False),
                          (_hmd(BOOT + 1), "quiet · kernel restarted", "5 min", False),
                          (_hmd(NOW - 1200), "rate-limit storm", "10 min", False),
                          (_hmd(NOW - 3000), "fine", "30 min", False)],
                         "newest first; each state held until the bucket's next change; the boot's own row names the restart, so no divider")

    def test_a_tail_crossing_boot_at_without_a_restart_row_gets_the_divider_and_the_hold_ends_at_the_boot(self):
        h, rows = self.R["quiet"]["head"], self.R["quiet"]["rows"]
        self.assertEqual((h["word"], h["dot"]), ("30 successful requests", "fine"))
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in rows],
                         [(_hmd(NOW - 100), "fine", "2 min so far", False),
                          (_hmd(BOOT), "kernel restarted", None, True),
                          (_hmd(NOW - 3500), "quiet", "48 min", False),
                          (_hmd(NOW - 4000), "fine", "8 min", False)],
                         "the unknown from before the boot ended at the boot (every bucket comes back unknown), not at the next change")

    def test_a_bucket_the_boot_seeded_reads_since_the_boot_in_the_head_and_the_tail_alike(self):
        h, rows = self.R["stale"]["head"], self.R["stale"]["rows"]
        self.assertEqual((h["word"], h["dot"]), ("no API traffic", "quiet"), "no traffic reads quiet and gray, never the machine's word")
        self.assertFalse(h["unknown"])
        tail = rows
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in tail],
                         [(_hmd(BOOT), "kernel restarted", None, True),
                          (_hmd(NOW - 100000), "quiet", "1 d 3 h", False),
                          (_hmd(NOW - 183000), "API failing", "23 h 3 min", False),
                          (_hmd(NOW - 190000), "fine", "1 h 57 min", False)],
                         "the divider at the boot; the pre-boot quiet is closed at the boot, never 'so far'")
        self.assertTrue(all(" so far" not in (r["v"] or "") for r in tail), "no open-ended state for a bucket the boot seeded")
        self.assertTrue(all(re.match(r"\d\d-\d\d \d\d:\d\d$", r["k"]) for r in tail[1:]), "stamps from another day carry their date")

    def test_a_boot_at_58_seconds_reads_one_time_in_the_head_and_on_its_restart_row(self):
        h, rows = self.R["minute"]["head"], self.R["minute"]["rows"]
        self.assertEqual((h["word"], h["dot"]), ("no API traffic", "quiet"))
        tail = rows
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in tail],
                         [(_hmd(BOOT_M), "quiet · kernel restarted", _min(NOW - BOOT_M) + " so far", False),
                          (_hmd(NOW - 1500), "rate-limit storm", _min(BOOT_M - (NOW - 1500)), False),
                          (_hmd(NOW - 3000), "fine", "25 min", False)],
                         "the boot's own row names the restart (no divider); the pre-boot hold ends at the boot")
        self.assertFalse(any(r["boot"] for r in tail))
        self.assertEqual(BOOT_M % 60, 58, "the payload's boot sits two seconds before a minute boundary")

    def test_the_restart_row_sorts_above_the_previous_kernel_s_last_row_filed_in_the_same_second(self):
        h, rows = self.R["inversion"]["head"], self.R["inversion"]["rows"]
        self.assertEqual(h["word"], "no API traffic")
        tail = rows
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in tail],
                         [(_hmd(BOOT + 0.9), "quiet · kernel restarted", _dur(NOW - (BOOT + 0.9)) + " so far", False),
                          (_hmd(BOOT + 0.7), "fine", "0 s", False),
                          (_hmd(NOW - 1500), "rate-limit storm", _dur(BOOT + 0.7 - (NOW - 1500)), False),
                          (_hmd(NOW - 3000), "fine", "25 min", False)],
                         "the restart row is the newest; the previous kernel's last state closed at the restart, never 'so far'; "
                         "the boot's own row names the restart, so no divider")
        self.assertEqual(sum(1 for r in tail if " so far" in (r["v"] or "")), 1)
        self.assertFalse(any(r["boot"] for r in tail))

    def test_a_row_the_previous_kernel_filed_after_this_start_sits_under_the_clamped_restart_row_with_one_mark(self):
        h, rows = self.R["clamped"]["head"], self.R["clamped"]["rows"]
        tail = rows
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in tail],
                         [(_hmd(BOOT + 0.951), "quiet · kernel restarted", _dur(NOW - (BOOT + 0.951)) + " so far", False),
                          (_hmd(BOOT + 0.95), "fine", "0 s", False),
                          (_hmd(NOW - 1500), "rate-limit storm", _dur(BOOT + 0.95 - (NOW - 1500)), False),
                          (_hmd(NOW - 3000), "fine", "25 min", False)],
                         "the old row is the first pre-boot row (its hold ends at the restart row, one millisecond on); the row "
                         "before it closed at the old row; the restart row above suppresses the divider: one restart, one mark")
        self.assertFalse(any(r["boot"] for r in tail), "no divider under a shown restart row")
        self.assertEqual(sum(1 for r in tail if "kernel restarted" in (r["w"] or "")), 1)

    def test_a_transition_the_hover_s_own_read_filed_closes_the_state_before_it(self):
        h, rows = self.R["ownread"]["head"], self.R["ownread"]["rows"]
        self.assertEqual(h["word"], '35 successful requests · 9 429s · 1 5xx')
        tail = rows
        self.assertEqual([(r["k"], r["w"], r["v"]) for r in tail],
                         [(_hmd(NOW), "rate-limit storm", "0 s so far"), (_hmd(NOW - 500), "fine", "8 min")],
                         "the closed state reads its duration with no 'so far' although its end is asOf; the new one is 'so far'")
        self.assertEqual(sum(1 for r in tail if " so far" in r["v"]), 1)

    def test_no_bucket_reads_quiet_and_gray_with_no_rows(self):
        h, rows = self.R["empty"]["head"], self.R["empty"]["rows"]
        self.assertEqual((h["word"], h["dot"]), ("no API traffic", "quiet"))
        self.assertEqual(rows, [])
        self.assertIsNone(h["since"])
        self.assertEqual(len(h["legend"]), 2, "the legend stands whatever the traffic")
        self.assertEqual(h["graphs"], 0, "no bucket: nothing to draw")
        self.assertFalse(h["unknown"])

    def test_two_buckets_of_one_family_read_as_one_machine_and_the_tail_tells_them_apart_by_auth(self):
        # T301: the head counts every bucket of this machine (one kernel's buckets share its clock); the "worst of N
        # buckets" caveat is gone; the tail still names a bucket by family and auth when two share the family
        h, rows = self.R["two"]["head"], self.R["two"]["rows"]
        self.assertEqual(h["word"], "46 successful requests · 9 429s · 1 5xx", "both buckets' counts, added (one kernel's clock)")
        tail = rows
        self.assertEqual([r["w"] for r in tail], ["fable · %s rate-limit storm" % AUTH, "fable · %s fine" % LAUTH])
        self.assertEqual([r["v"] for r in tail], ["5 min so far", "8 min so far"], "each bucket's current state is its own 'so far'")

    def test_two_buckets_of_two_families_are_named_by_family_alone(self):
        h, rows = self.R["twoFam"]["head"], self.R["twoFam"]["rows"]
        self.assertEqual(h["word"], "46 successful requests · 9 429s · 1 5xx")
        self.assertEqual([r["w"] for r in rows], ["fable rate-limit storm", "opus fine"])

    def test_an_offline_window_reads_its_no_connection_attempts_in_plain_words(self):
        h, rows = self.R["offline"]["head"], self.R["offline"]["rows"]
        self.assertEqual((h["word"], h["dot"]), ("6 successful requests · 2 429s · 7 no connection", "errors"),
                         "attempts without a status are counted as no connection; the machine's word (unknown) is never said")
        self.assertEqual(h["legend"][-1], "other no connection, or another error", "the other line joins the legend when the range holds such failures")
        self.assertEqual(len(h["legend"]), 3)
        self.assertFalse(h["unknown"])

    def test_the_cap_shows_four_transitions_with_the_divider_extra_and_four_without_one(self):
        # T301: the State changes list is a glance, not a log: four rows, in plain words
        rows = self.R["cap"]["rows"]
        self.assertEqual([(r["k"], r["w"], r["v"], r["boot"]) for r in rows],
                         [(_hmd(NOW - 200), "API failing", "3 min so far", False),
                          (_hmd(NOW - 400), "fine", "3 min", False),
                          (_hmd(BOOT), "kernel restarted", None, True),
                          (_hmd(NOW - 2500), "quiet", "32 min", False),
                          (_hmd(NOW - 3000), "recovering", "8 min", False)],
                         "four transitions and the divider, the four oldest changes cut")
        self.assertEqual(sum(1 for r in rows if not r["boot"]), 4)
        rows = self.R["capPost"]["rows"]
        self.assertEqual(len(rows), 4)
        self.assertFalse(any(r["boot"] for r in rows), "nothing crosses the boot: no divider")
        self.assertEqual([r["w"] for r in rows], ["fine", "API failing", "fine", "recovering"])
        self.assertEqual(rows[0]["v"], "1 min so far")

    def test_a_failed_read_is_one_loud_line_and_never_the_previous_numbers(self):
        R = self.R
        self.assertEqual(R["fail503"]["head"]["err"], "Could not read the API history: HTTP 503")
        self.assertEqual(R["fail503"]["rows"], [], "the rows the last hover showed are gone")
        self.assertEqual(R["failReject"]["head"]["err"], "Could not read the API history: Failed to fetch")
        self.assertEqual(R["failReject"]["rows"], [])
        self.assertEqual(R["failMalformed"]["head"]["err"], "Could not read the API history: malformed answer")
        self.assertIsNone(R["fail503"]["head"]["ago"], "no age on a failure")
        self.assertEqual(R["recovered"]["head"]["word"], '35 successful requests · 9 429s · 1 5xx', "the next successful read replaces the failure")
        self.assertIsNone(R["recovered"]["head"]["err"])

    def test_the_section_sits_after_the_sessions_waiting_and_no_terminal_coverage_line_follows(self):
        order = self.R["order"]
        self.assertEqual(order[:3], ["API health", "Sessions waiting", "History"])
        self.assertFalse(any("tmux" in o for o in order), "T331: a stale terminal count in a frame draws no line: %r" % order)

    def test_focus_shows_the_hover_and_blur_hides_it(self):
        R = self.R
        self.assertTrue(R["focusShown"])
        self.assertEqual(R["focusDescribed"], "ah-summary")
        self.assertTrue(R["blurHidden"])
        self.assertIsNone(R["blurDescribed"])

    def test_the_pointer_on_a_focus_shown_tip_keeps_its_rows_and_reads_nothing(self):
        R = self.R
        self.assertFalse(R["focusEnter"]["wait"], "no flash to the loader's dots")
        self.assertGreater(R["focusEnter"]["rows"], 3, "the rows stay through the mouseenter")
        self.assertEqual(R["focusEnter"]["read"], 0)
        self.assertEqual(R["focusEnterAfter"]["fetchN"], 0, "and no read follows")
        self.assertGreater(R["focusEnterAfter"]["rows"], 3)

    def test_escape_dismisses_the_focus_shown_hover_without_moving_focus(self):
        m = self.R["focusEsc"]
        self.assertFalse(m["shown"])
        self.assertIsNone(m["described"])
        self.assertTrue(m["activeIsCell"], "focus stays on the cell")

    def test_a_focus_the_window_regaining_focus_re_dispatches_does_not_pop_the_hover(self):
        R = self.R
        self.assertFalse(R["refocus"]["shown"], "the cell's focus in the window focus's own task, the cell already active, is not a show")
        self.assertEqual(R["refocus"]["read"], 0, "and not a read")
        self.assertTrue(R["refocusLater"]["shown"], "a focus after the next frame is the user's and shows")
        self.assertEqual(R["refocusLater"]["read"], 1)

    def test_a_tab_out_of_an_iframe_onto_the_cell_shows_the_hover(self):
        R = self.R
        self.assertEqual(R["frameTabFrom"], "lab-pane", "focus sat in the iframe (the top document's active element is its host)")
        t = R["frameTab"]
        self.assertEqual(t["active"], "rail-api", "one Tab lands on the cell")
        self.assertGreaterEqual(len(t["winLog"]), 1, "Chromium fired focus on the top window for the frame change: the mark "
                                "was set on this path, and it must not swallow the show")
        self.assertNotIn("rail-api", t["winLog"], "the cell was not the active element at the window's event (the body is)")
        self.assertTrue(t["shown"], "the hover shows: the recorded element was not the cell")
        self.assertEqual(t["described"], "ah-summary")
        self.assertEqual(t["read"], 1, "and reads once")
        self.assertGreater(R["frameTabRows"], 3)

    def test_the_shown_tip_is_a_tooltip_and_the_pinned_card_the_dialog(self):
        R = self.R
        for m in (R["storm"]["mode"], R["focusMode"], R["hoverAfterDialog"]):
            self.assertEqual((m["role"], m["modal"], m["modalClass"]), ("tooltip", None, False),
                             "a tooltip on every show, the hover after a pinned dialog closed included: the close leaves the "
                             "hidden tip's role alone, so the show must put the tooltip role back and drop aria-modal")
            self.assertEqual(m["described"], "ah-summary")
            self.assertFalse(m["focusInside"])
        self.assertTrue(R["hoverAfterDialog"]["shown"])
        m = R["kbMode"]
        self.assertEqual((m["role"], m["modal"], m["modalClass"]), ("dialog", "true", True))
        self.assertTrue(m["focusInside"], "focus moves into the dialog")
        self.assertIsNone(m["described"], "the dialog is not the cell's description")

    def test_the_cell_is_described_by_a_short_summary_and_never_by_the_rows(self):
        R = self.R
        # the frame carries the kernel's own count of failed attempts in the window (T301 review: the dot follows the
        # frame), so the description names it before the document is read, then the reading's sentence replaces it
        self.assertEqual(R["stormDesc0"], "API health: 10 failed attempts in the last 15 min. Reading the details. Press Enter to open it.",
                         "before the answer: the frame's own count and the read in flight, not the loader's markup")
        m = R["storm"]["mode"]
        d = m["descText"]
        self.assertEqual(d, "API health: 35 successful requests · 9 429s · 1 5xx. Press Enter to open it.", "this machine's counts, how to reach the rest")
        self.assertLess(len(d), 160, "bounded: a sentence, not the tip")
        for w in ("kernel restarted", "as of", "State changes", "unknown"):
            self.assertNotIn(w, d, "no transition, no stamp, no machine word in the description")
        self.assertTrue(m["descInTree"])
        self.assertLessEqual(max(m["descBox"]), 1.0, "visually hidden: one pixel or less each way")
        self.assertEqual(R["fail503"]["desc"], "Could not read the API history: HTTP 503. Press Enter to open it.")
        # at focus time, in the focus's own task (where assistive tech reads it), the description carries the frame's
        # own words (a session waiting on a 429); the landed one keeps them: the frame's errors outrank the reading
        self.assertEqual(R["focusDesc0"], "API health: rate limited · 1 waiting. Reading the details. Press Enter to open it.")
        self.assertIn("rate limited", R["focusDesc0"], "the frame's own words, before any read")
        self.assertEqual(R["focusDesc"], "API health: rate limited · 1 waiting. Press Enter to open it.")

    def test_enter_pins_the_section_a_frame_re_reads_it_and_escape_does_not_re_pop_the_hover(self):
        R = self.R
        self.assertTrue(R["kbPinned"])
        self.assertEqual(R["kbFetchPost"], R["kbFetchPre"], "the pin reads nothing new: the focus show's read is the same document")
        self.assertGreater(R["kbRows"], 3)
        self.assertEqual(R["kbHead"]["word"], '35 successful requests · 9 429s · 1 5xx')
        self.assertGreater(R["kbFetchAfter"], R["kbFetchBefore"], "a frame on the open detail re-reads the history")
        self.assertTrue(R["escHidden"])
        self.assertTrue(R["escFocusBack"], "focus returns to the cell")
        self.assertTrue(R["escStillHidden"], "the refocus did not pop the hover back")
        self.assertIsNone(R["escDescribed"])

    def test_a_click_after_the_hover_reads_once_in_all_and_a_click_from_hidden_reads(self):
        R = self.R
        p = R["pinAfterHover"]
        self.assertEqual(p["read"], 0, "the hover's read is the pin's document")
        self.assertEqual((p["mode"]["role"], p["mode"]["modal"]), ("dialog", "true"))
        self.assertGreater(p["rows"], 3, "and its rows are kept: no dots, no second wait")
        self.assertFalse(p["wait"])
        self.assertFalse(R["pinClosed"]["shown"], "Escape closed it (focus goes back to the body: the click came from there)")
        q = R["pinFromHidden"]
        self.assertEqual(q["read"], 1, "nothing was showing: the pin reads")
        self.assertFalse(q["wait"], "and keeps the stamped answer it has until the read lands (the open never drops it)")
        self.assertEqual(q["mode"]["role"], "dialog")

    def test_an_answer_under_a_held_pointer_paints_on_release(self):
        R = self.R
        # the detail still shows the previous frame's word (overloaded, from the frame that re-read the history) until
        # the release: nothing painted under the press
        self.assertEqual(R["heldWord"], "overloaded · 1 waiting", "the frame's re-read landed under the held pointer: not painted")
        self.assertEqual(R["releasedWord"], "30 successful requests", "the release paints it")

    def test_the_light_theme_colours_the_fine_and_quiet_head_dots_and_the_failure_line(self):
        R = self.R
        # T301: fine wears the light theme's accent (the clay), quiet the light label gray #5D574E (the base gray #9aa4ad
        # is about 1.6:1 on the white tip), and the failure line the light error red #B02A1C; the dark line keeps #ef6b6f
        self.assertEqual((R["lightHealthyDot"]["word"], R["lightHealthyDot"]["color"]), ("30 successful requests", "rgb(194, 65, 12)"))
        self.assertEqual((R["lightUnknownDot"]["word"], R["lightUnknownDot"]["color"]), ("no API traffic", "rgb(93, 87, 78)"))
        self.assertEqual(R["lightErr"], "rgb(176, 42, 28)", "the light theme's error-text red")
        self.assertEqual(R["fail503"]["errColor"], "rgb(239, 107, 111)", "the dark tip's status red")

    def test_an_attached_host_is_read_through_the_relay_and_every_machine_is_named(self):
        # T301: the frame's hosts map (a per-host MAP the kernel's tunnel supervisor filled) makes the shell read that
        # host's document through /remote/<host>/api-health and name every machine; worst state wins for the dot
        h = self.R["hosts"]["head"]
        self.assertEqual(h["title"], "API health · 2 machines")
        self.assertEqual(self.R["hosts"]["head"]["mlines"][1], "TESTHOST: rate limited · 2 waiting")
        # T316: no summary head line; this machine is named by its kernel's own name; each line carries its own counts
        self.assertEqual((h["name"], h["word"], h["dot"]), ("HUBHOST", "30 successful requests", "fine"), "this machine's own line, by its name")
        self.assertEqual(h["mlines"], ["HUBHOST: 30 successful requests", "TESTHOST: rate limited · 2 waiting"], "one line per machine, local first")
        self.assertEqual(h["gnames"], ["HUBHOST", "TESTHOST"], "the History draws each machine's bars under its name (its own document, never summed)")
        self.assertEqual(h["graphs"], 2)
        self.assertIn("State changes · HUBHOST", h["names"])
        self.assertIn("/remote/TESTHOST/api-health", self.R["hosts"]["fetched"] or [], "the relay read")
        self.assertFalse(h["unknown"])

    def test_the_click_opens_the_detail_with_range_chips_and_large_bars_and_the_hover_stays_the_day(self):
        # T316: the dot's click is the detail (the spend modal's grammar): range chips 1 hour / 24 hours / 7 days, the day
        # pressed by default, one large stacked histogram per machine drawn from the tier the range names
        d = self.R["detail"]
        self.assertEqual((d["mode"]["role"], d["mode"]["modal"]), ("dialog", "true"))
        self.assertEqual([c["t"] for c in d["chips"]], ["1 hour", "24 hours", "7 days"])
        self.assertEqual([c["on"] for c in d["chips"]], [False, True, False], "the day by default")
        self.assertEqual((d["dayBars"], d["head"]["big"]), (96, True), "the day: 96 quarter-hour bars, large")
        self.assertTrue(d["legendAboveBars"], "the detail names the colours under the chips, before the bars (a short window folded the bottom legend away)")
        st = d["stack"]
        self.assertIsNotNone(st, "a bar with several segments")
        self.assertEqual([x["c"] for x in st][:2], ["ok", "rateLimited"], "successes at the bottom, 429 on them")
        self.assertGreater(st[0]["y"], st[1]["y"], "the 429 segment sits above the successes")
        self.assertAlmostEqual(st[0]["y"], st[1]["y"] + st[1]["h"], delta=0.2, msg="and touches it: stacked, not overlaid")
        self.assertEqual(st[0]["fill"], "rgb(156, 210, 255)", "the accent fill, from the class rule")
        self.assertEqual(st[1]["fill"], "rgb(229, 72, 77)", "the blocked red")
        self.assertEqual(d["legendCount"], 1, "once")
        self.assertEqual((d["hourBars"], d["hourOn"]), (60, True), "the hour: its 60 one-minute bins, the chip pressed")
        self.assertEqual(d["weekBars"], 168, "the week: its 168 hourly bins")
        self.assertTrue(d["closed"], "Escape closes the detail")
        self.assertEqual((d["hoverAfter"]["bars"], d["hoverAfter"]["big"], d["hoverChips"]), (96, False, 0), "the hover stays the day, small, with no chips")

    def test_the_hover_keeps_its_margin_at_830_wide_and_stays_above_the_rail_on_a_short_window(self):
        g = self.R["geo830"]
        self.assertEqual(g["iw"], 830)
        # the premise: the pointer entered near the right edge and the loader's tip was first anchored flush at the right
        # margin, so the measurement after the rows landed was taken against that edge
        self.assertEqual(g["first"]["x"], 820)
        self.assertGreaterEqual(g["first"]["r"], g["iw"] - 6 - 0.5, "the first anchor sat at the right margin")
        self.assertGreater(g["first"]["l"], g["iw"] / 2, "in the right half of the window")
        self.assertGreater(g["first"]["l"] + g["w"], g["iw"], "the tip's natural width did not fit to the right of where the first "
                           "anchor sat, so a measurement taken there (no reset) would have been constrained and the rows wrapped")
        self.assertLessEqual(g["r"], g["iw"] - 6 + 0.5, "the right margin holds")
        self.assertGreaterEqual(g["l"], 5.5)
        self.assertLessEqual(max(g["rowH"]), min(g["rowH"]) * 1.5, "no row wraps: the width was measured after the reset")
        self.assertLessEqual(g["b"], g["cellTop"] - 8 + 0.5)
        g = self.R["geoShort"]
        self.assertEqual(g["ih"], 280)
        self.assertGreaterEqual(g["t"], 5.5)
        self.assertLessEqual(g["b"], g["cellTop"] - 8 + 0.5, "above the rail, never over it or the cell")
        # T301: the section is shorter (no window rows), so on this height it may fit under the cap; when it does not,
        # it clips rather than spills
        self.assertTrue(g["clipped"] or g["h"] <= float(str(g["maxH"]).replace("px", "") or 0) + 0.5,
                        "the section's oldest rows are clipped rather than spilled: %r" % g)
        self.assertLessEqual(g["h"], g["cellTop"] - 14 + 0.5)
        g = self.R["geoWide"]
        self.assertFalse(g["clipped"], "1200x800: the whole section fits")
        self.assertLessEqual(g["b"], g["cellTop"] - 8 + 0.5)
        self.assertGreaterEqual(g["t"], 5.5)


if __name__ == "__main__":
    unittest.main()
