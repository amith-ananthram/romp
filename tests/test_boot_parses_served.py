#!/usr/bin/env python3
"""T323 stage 1, served: a REAL hermetic kernel boots over three synthetic living sessions and parses none of them
until a client asks. /perf's `parses` (cold kernel parses per session) reads zero after the boot settles with no client;
the session that was BLOCKED before the boot and does not move afterwards reads blocked in the feed route from the
store the previous kernel wrote, still with no parse (the manager's case, 2026-09-10); and once a chat client connects,
the parses that happen are exactly the shown tabs' (playwright drives the real page; skipped where no browser is
installed, the way the other served guards skip). Synthetic only: invented text, placeholder uuids, TESTHOST."""
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
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment

WEB = "aaaaaaaa-1111-2222-3333-444444444444"     # will be blocked in the store before the boot, untouched after
API = "bbbbbbbb-1111-2222-3333-444444444444"
TESTS = "cccccccc-1111-2222-3333-444444444444"
ALL = (WEB, API, TESTS)


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def transcript(t0, stopped):
    recs = [uline(t0, "wire the fixtures directory into the integration suite", "u1"),
            aline(t0 + 20, "digging in: reading the suite's layout", "a1", "u1", "tool_use")]
    if stopped:
        recs.append(uline(t0 + 60, "[Request interrupted by user]", "u2", "a1"))
    else:
        recs.append(aline(t0 + 60, "done: the suite reads the fixtures directory now", "a2", "a1"))
    return recs


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
await page.waitForFunction(() => document.querySelectorAll(".turn").length >= 1, null, { timeout: 30000 });
await page.waitForTimeout(1500);
await browser.close();
console.log("RESULT:ok");
"""


class ServedBootParses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="romp-t323s1-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "goals"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        for sid, name, colour in ((WEB, "web", "#9cd2ff"), (API, "api", "#1EA1EB"), (TESTS, "tests", "#54B204")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\t#0c1a2e\n" % (name, cwd, colour))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(state, "states", sid + ".jsonl").write_text(json.dumps({"t": t0 + 70, "state": "idle"}) + "\n")
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in transcript(t0, stopped=(sid == WEB))))
        # the previous kernel's verdict on web: its focus goal BLOCKED on the user (a genuine stop), the interrupt
        # marker pointing at it; nothing about web changes after the boot, so the card must read blocked from here
        g = WEB + ":gw"
        Path(state, "goals", WEB + ".json").write_text(json.dumps(
            {"rompUuid": WEB, "seq": 3, "lastNode": g, "closedTurns": [],
             "nodes": {g: {"id": g, "text": "wire the fixtures directory into the integration suite", "parentId": None,
                           "nodeComplete": False, "blocked": True, "cleared": False, "trail": [], "t": t0,
                           "log": [{"t": t0 + 61, "judge": "interrupt", "verdict": "block", "why": "stopped mid-turn"}]}},
             "placements": {}, "status": {g: "blocked"}}))
        Path(state, "auto-nudge.json").write_text(json.dumps({"enabled": False, "nudged": {}, "intrBlocked": {WEB: g}}))
        # the previous kernel's tick memo: it had looked at every session with these very files (T323 stage 1's
        # persisted _TICK_SEEN), so this boot has nothing new to evaluate for any of them
        def _stat(p):
            try:
                st = os.stat(p); return [st.st_mtime, st.st_size]
            except OSError:
                return [0.0, 0]
        memo = {}
        for sid in ALL:
            files = _stat(os.path.join(proj, sid + ".jsonl")) + _stat(os.path.join(state, "states", sid + ".jsonl")) \
                + _stat(os.path.join(state, "goals", sid + ".json"))
            for job in ("interrupt-block", "working-notes"):
                memo["%s|%s" % (job, sid)] = files
        Path(state, "tick-seen.json").write_text(json.dumps(memo))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        # every file predates the boot by construction (written before the kernel starts): "unchanged since boot"
        cls.port, cls.token = _free_port(), "testtok-t323"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"),
                                      stderr=subprocess.STDOUT, env=env)
        for _ in range(200):   # a bounded wait for the serve loop, half a second at a time
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
        try:
            cls.kernel.terminate()
            cls.kernel.wait(timeout=15)
        except Exception:
            cls.kernel.kill()
        shutil.rmtree(cls.lab, ignore_errors=True)

    def _get(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), headers={"X-Romp-Token": self.token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())

    def _parses(self):
        return self._get("/perf")["parses"]

    def _settled(self):
        """The boot reconcile and a few pusher cycles have run (a bounded wait on /version's boot marks)."""
        for _ in range(40):
            try:
                v = self._get("/version")
                if v.get("uptime_s", 0) >= 4:
                    return
            except Exception:
                pass
            time.sleep(0.5)

    def test_1_a_boot_with_no_client_parses_nothing_and_the_blocked_card_comes_from_the_store(self):
        self._settled()
        p = self._parses()
        self.assertEqual(p["kernel"], 0, "no client asked, so the kernel parsed no transcript at boot: %r" % p)
        self.assertEqual(p["bySid"], {}, "per session: none")
        # the judges' first pass still parses what it enumerates (their checkpoint resume is stages 3 and 4); this
        # stage only orders it newest first; pin the count so a regression to double parsing shows
        self.assertLessEqual(p["judge"], 2 * len(ALL), "the judges parse each session about once at boot (a key file moving "
                                                        "during the boot costs one re-parse): %r" % p)
        feed = self._get("/feed.json")
        cards = [c for c in (feed.get("asks") or []) if isinstance(c, dict)]   # the feed's cards ride `asks`
        web = [c for c in cards if str(c.get("sid") or c.get("fsid") or "").startswith(WEB[:8])]
        self.assertTrue(web, "web's card is in the feed: %r" % [c.get("sid") for c in cards][:10])
        self.assertTrue(any(c.get("column") == "needs_input" for c in web),
                        "web reads blocked (needs_input) from the store the previous kernel wrote: %r" % [c.get("column") for c in web])
        self.assertEqual(self._parses()["kernel"], 0, "and the feed route parsed nothing to say so (it is cache-only; the judges' own parses ride total)")

    def test_2_a_chat_client_parses_only_what_it_shows(self):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("no playwright in vscode-extension/node_modules")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        before = self._parses()["kernel"]
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token)}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, CFG=cfg, EXT_PKG=os.path.join(EXT, "package.json")))
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-2000:] + p.stderr[-2000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        after = self._parses()
        asked = after["kernel"] + after["hits"]     # stage 2: the judges may have parsed a tab first, then the kernel's ask is a hit
        self.assertGreaterEqual(asked, 1, "a connected chat client's own tabs are parsed or served on demand: %r" % after)
        self.assertLessEqual(after["kernel"] - before, len(ALL), "and nothing beyond the shown tabs (every living session is a tab here): %r" % after)
        self.assertTrue(set(after["bySid"]) <= {s[:8] for s in ALL}, after["bySid"])


if __name__ == "__main__":
    unittest.main()
