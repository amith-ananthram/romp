#!/usr/bin/env python3
"""Every gear select DISPLAYS the stored value, for every value the kernel accepts.

The user (2026-09-01): STATE/distill-model held claude-opus-4-8 — their own pick, the mtimes
proving only pick-writes — while the gear's Distilling row said Follow triage. The store, the
/version payload, and the option list were all correct; the lie was the FACADE: the four model
rows are painted by versionMenu buttons that synced only on a user gesture or an options rewrite,
and fill()'s per-open value writes are deliberately silent (a change event here would POST the
setting back). The effort rows' selectPick facades joined the repaintSelectPicks registry; the
versionMenu buttons never did — so any page reload reset every model row's label to its first
option until the next hand pick, and the row lied about a value the kernel was honoring.

Two guards, pinning the CLASS (a select/option vocabulary or repaint drift must never again show
a silent default):
  * SourcePins — runs everywhere, CI included: versionMenu's label sync joins the ONE repaint
    registry fill() flushes; every kernel-backed select assignment rides setShow, which INJECTS a
    marked option when the stored value is off this kernel's list (an honest row, never a default
    lie).
  * ServedMatrix — the executed guard: boots the hermetic kernel, and for EACH gear select writes
    EACH kernel-acceptable value into its STATE file (the vocabulary is drawn from the kernel's
    own tables, so drift auto-tracks), loads the gear, and asserts the VISIBLE facade label is the
    stored value's own option label. Skips LOUDLY when the extension deps or a playwright browser
    are absent (CI installs no browsers); it executes on any dev box with the extension installed,
    which is where ships are gated.

All fixtures synthetic.
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
from romp_load import load_source
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes: an
#                                   imported TestCase would be collected here a second time)
# Hermetic state BEFORE the load — bin/romp-kernel resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_gsm", os.path.join(BIN, "romp-kernel"))

GEAR = open(os.path.join(ROOT, "ui", "webview", "gear.js")).read()


class SourcePins(unittest.TestCase):
    def test_the_concurrency_options_span_exactly_the_kernels_range(self):
        # the kernel refuses anything outside jd.CONCURRENCY_MIN..MAX (its _CONCURRENCY_VALUES); the gear's
        # loop must paint exactly that range, or a stored value could be off the list (T277). The served
        # matrix below proves it end to end where a browser exists; this pin runs everywhere.
        self.assertIn("for (var ci = %d; ci <= %d; ci++)" % (km.jd.CONCURRENCY_MIN, km.jd.CONCURRENCY_MAX), GEAR,
                      "the gear's concurrency options span exactly the kernel's range")

    def test_every_facade_joins_the_one_repaint_registry(self):
        # the versionMenu label sync must repaint on fill()'s silent writes exactly like the
        # selectPick facades do — one registry, flushed at the end of fill()
        self.assertIn("selectPickPaints.push(syncBtn);", GEAR)
        self.assertIn("selectPickPaints.push(paint);", GEAR)
        self.assertIn("repaintSelectPicks();", GEAR)

    def test_every_kernel_backed_assignment_rides_setShow(self):
        self.assertIn("function setShow(sel, val)", GEAR)
        # a stored value with no option INJECTS a marked one — honest, never the default lie
        self.assertIn("not in this kernel's list", GEAR)
        for sel in ("jm", "im", "dm", "cmm", "je", "ie", "jc", "de", "cme", "upm"):
            self.assertIn("setShow(%s, v." % sel, GEAR, sel + " must render through setShow")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


MODELS_ALL = [c["value"] for c in km.MODEL_CHOICES] + \
             [v["value"] for vs in km.MODEL_VERSIONS.values() for v in vs]
EFFORTS = [c["value"] for c in km.EFFORT_CHOICES]
# select id → (STATE file, every storable value the kernel accepts for it)
MATRIX = {
    "rs-judgemodel":    ("judge-model", MODELS_ALL),
    "rs-indexmodel":    ("index-model", MODELS_ALL),
    "rs-distillmodel":  ("distill-model", ["triage"] + MODELS_ALL),
    "rs-cmtmodel":      ("comment-model", ["session", "default"] + MODELS_ALL),
    "rs-judgeeffort":   ("judge-effort", EFFORTS),
    "rs-indexeffort":   ("index-effort", EFFORTS),
    "rs-judgeconc":     ("judge-concurrency", [str(n) for n in range(km.jd.CONCURRENCY_MIN, km.jd.CONCURRENCY_MAX + 1)]),
    "rs-distilleffort": ("distill-effort", ["triage", "none"] + EFFORTS),
    "rs-cmteffort":     ("comment-effort", ["session"] + EFFORTS),
}

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const out = [];
for (const round of cfg.rounds) {
  for (const [name, val] of Object.entries(round.files)) fs.writeFileSync(cfg.stateDir + "/" + name, val);
  await page.goto(cfg.url + "&r=" + Math.random());
  // gear.js must be INITIALIZED before the open message: the vermenu buttons exist only after
  // fillChoices resolves, which also proves the openSettings listener is installed — posting
  // earlier is silently lost and fill() never runs (first driver draft raced exactly this)
  await page.waitForSelector(".rs-vermenu-btn", { state: "attached", timeout: 15000 });   // panel still hidden here
  await page.evaluate(() => window.postMessage({ romp: "openSettings" }, "*"));
  await page.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  // fill()'s /version assigns land in one tick; once every select HOLDS its value the facades
  // have had their repaint chance in that same tick
  await page.waitForFunction((exp) => Object.entries(exp).every(([id, v]) => {
    const s = document.getElementById(id);
    return s && s.value === v;
  }), round.expect, { timeout: 15000 }).catch(() => {});
  out.push(await page.evaluate((exp) => {
    const r = {};
    for (const id of Object.keys(exp)) {
      const s = document.getElementById(id);
      const o = s && s.selectedIndex >= 0 ? s.options[s.selectedIndex] : null;
      r[id] = { value: s ? s.value : null,
                optLabel: o ? o.textContent : null,
                facade: s && s.nextElementSibling ? s.nextElementSibling.textContent.trim() : null };
    }
    return r;
  }, round.expect));
}
await browser.close();
console.log("MATRIX:" + JSON.stringify(out));
"""


class ServedMatrix(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served matrix needs them")
        cls.lab = tempfile.mkdtemp(prefix="gear-matrix-")
        # a fresh bundle of THIS tree's gear.js — the kernel serves the copy, never the live dist
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(cls.state, exist_ok=True)
        cls.port = _free_port()
        cls.token = "testtok-matrix"
        env = _lab.kernel_env(cls.lab, os.path.join(cls.lab, "claude"), dist, cls.port, cls.token)
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(os.path.join(cls.lab, "kernel.log"), "w"),
                                      stderr=subprocess.STDOUT, env=env)
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

    def test_every_select_displays_every_acceptable_stored_value(self):
        rounds = []
        depth = max(len(vals) for _, vals in MATRIX.values())
        for r in range(depth):
            files, expect = {}, {}
            for sel_id, (fname, vals) in MATRIX.items():
                v = vals[min(r, len(vals) - 1)]
                files[fname] = v
                expect[sel_id] = v
            rounds.append({"files": files, "expect": expect})
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"stateDir": self.state, "rounds": rounds,
                       "url": "http://127.0.0.1:%d/feed?token=%s" % (self.port, self.token)}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the matrix needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-2000:] + p.stderr[-2000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("MATRIX:")), None)
        self.assertIsNotNone(line, "driver printed no matrix:\n" + p.stdout[-2000:])
        results = json.loads(line[len("MATRIX:"):])
        bad = []
        for r, (round_cfg, got) in enumerate(zip(rounds, results)):
            for sel_id, want in round_cfg["expect"].items():
                g = got.get(sel_id) or {}
                if g.get("value") != want:
                    bad.append("%s round %d: select holds %r, stored %r — the option vocabulary lost a kernel-acceptable value"
                               % (sel_id, r, g.get("value"), want))
                elif not g.get("facade") or not g.get("optLabel") or g["optLabel"] not in g["facade"]:
                    bad.append("%s round %d: stored %r, option label %r, but the row SHOWS %r — the facade lies"
                               % (sel_id, r, want, g.get("optLabel"), g.get("facade")))
        self.assertEqual(bad, [], "\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
