#!/usr/bin/env python3
"""T349 (the user 2026-09-11): commenting on a line inside a rendered TABLE broke the table's rendering. The comment mark is
painted by wrapping every text node of the matched range in an inline <mark> (render.ts ensureCommentMark, over
comments.ts findAnchorRange / sliceRanges), and marked's table HTML carries newline text nodes BETWEEN the cells and rows,
directly under <tr>, <tbody>, <thead> and <table>: a mark placed there gets its own anonymous table cell, so the columns
shift and the table reflows. The pass now skips those structural parents (comments.ts markSkipsParent) and wraps each
cell's own text, so a mark rides a row cell by cell and the table's boxes stay.

The lab: a hermetic kernel serving one synthetic session whose reply holds a markdown table (a header row and three body
rows, three columns of invented text) and two seeded threads, one on a single cell's text, one on a whole row's text. On
the real /chat page, dark and light: the table keeps its row and cell counts and no mark is a direct child of a table,
a row group or a row; every mark inside the table sits inside a cell; the body rows' cell edges line up with the header's
(the column alignment); the row thread paints one mark per cell and the cell thread one; a click on the row thread's mark
opens the thread dialog. With TBL_SHOTS=<dir> the driver writes screenshots (dark and light); TBL_BEFORE_DIST=<dist> serves
another tree's bundle for the before shots and skips the assertions, unless TBL_BEFORE_ASSERT=1 keeps them (how the test
is proven red against the bundle before the change: marks directly under <tr>, a row with more boxes than cells). Skips
LOUDLY without the extension deps or a Playwright browser; the CI extension job installs Chromium and runs served files
with ROMP_SERVED_TESTS_REQUIRE=1, which turns any skip into a failure there. SYNTHETIC fixtures only (session web, the
notes-api demo world, placeholder uuids)."""
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

SID = "aaaaaaaa-1111-2222-3333-444444444444"
TID_CELL = "cccccccc-1111-2222-3333-444444444444"   # the thread on one cell's text
TID_ROW = "dddddddd-1111-2222-3333-444444444444"    # the thread on a whole row's text
U_UUID = "11111111-2222-3333-4444-555555555555"
A_UUID = "22222222-3333-4444-5555-666666666666"
REPLY = ("Here is where the notes-api suites stand:\n\n"
         "| Suite | Cases | State |\n|---|---|---|\n"
         "| Alpha | 8 | green |\n| Bravo | 12 | pending |\n| Charlie | 5 | done |\n\n"
         "The pending one waits on the tokenizer fixture.")
EXACT_CELL = "pending"                 # one cell (the Bravo row's state)
EXACT_ROW = "Charlie\t5\tdone"         # a whole row, as a selection across cells reads back (tabs between the cells)


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
const page = await browser.newPage({ viewport: { width: 1100, height: 760 }, deviceScaleFactor: 2 });
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
await page.click('#tabs .tab[data-id="' + cfg.sid + '"]');
await page.waitForSelector("#content table", { timeout: 20000 });
await page.waitForFunction((tids) => tids.every((t) => document.querySelector('mark.cmt-hl[data-tid="' + t + '"]')), [cfg.tidCell, cfg.tidRow], { timeout: 20000 });
await page.mouse.move(900, 700); await page.waitForTimeout(400);
const measure = () => page.evaluate((tids) => {
  const table = document.querySelector("#content table");
  const rows = Array.from(table.rows).map((tr) => {
    const cells = Array.from(tr.cells).map((c) => { const r = c.getBoundingClientRect(); return { left: r.left, right: r.right, width: r.width, display: getComputedStyle(c).display, text: c.textContent.trim() }; });
    return { cells: tr.cells.length, children: tr.children.length, childTags: Array.from(tr.children).map((c) => c.tagName), cellBoxes: cells, height: tr.getBoundingClientRect().height };
  });
  const stray = table.querySelectorAll(":scope > mark, :scope > thead > mark, :scope > tbody > mark, :scope > tfoot > mark, tr > mark").length;
  const marks = tids.map((t) => Array.from(document.querySelectorAll('mark.cmt-hl[data-tid="' + t + '"]')).map((m) => ({
    text: m.textContent, inCell: !!m.closest("td, th"), parent: m.parentElement.tagName, display: getComputedStyle(m).display, inTable: !!m.closest("table") })));
  const tb = table.getBoundingClientRect();
  return { rows, stray, marks, table: { width: tb.width, height: tb.height, left: tb.left },
           theme: document.body.classList.contains("theme-light") ? "light" : "dark" };
}, [cfg.tidCell, cfg.tidRow]);
const out = {};
out.dark = await measure();
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); const tb = await (await page.$("#content table")).boundingBox(); await page.screenshot({ path: cfg.shots + "/romp_chat-comment-table-mark-dark" + (cfg.shotSuffix || "") + ".png", clip: { x: Math.max(0, tb.x - 60), y: Math.max(0, tb.y - 60), width: Math.min(1100, tb.width + 120), height: tb.height + 120 } }); }
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
out.light = await measure();
if (cfg.shots) { const tb = await (await page.$("#content table")).boundingBox(); await page.screenshot({ path: cfg.shots + "/romp_chat-comment-table-mark-light" + (cfg.shotSuffix || "") + ".png", clip: { x: Math.max(0, tb.x - 60), y: Math.max(0, tb.y - 60), width: Math.min(1100, tb.width + 120), height: tb.height + 120 } }); }
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
// the row thread's mark opens the thread dialog, anchored on the page
await page.click('mark.cmt-hl[data-tid="' + cfg.tidRow + '"]');
let popped = true;
try { await page.waitForSelector('#cmt-pop[data-mode="thread"]', { timeout: 10000 }); } catch (e) { popped = false; }
out.pop = await page.evaluate(() => { const p = document.getElementById("cmt-pop"); if (!p) return null; const r = p.getBoundingClientRect(); return { mode: p.dataset.mode, top: r.top, left: r.left, width: r.width, height: r.height, visible: getComputedStyle(p).display !== "none" }; });
out.popped = popped;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentTableMark(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
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
        cls.lab = tempfile.mkdtemp(prefix="comment-table-mark-")
        cls.before = os.environ.get("TBL_BEFORE_DIST", "")
        if not cls.before:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(cls.before or os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high",
             "lastSid": SID, "alive": True, "model": "claude-opus-5", "liveModel": "Opus 5"}))
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # a CLOSED turn: an open one would invite the boot reconcile to resume it — no real CLI here
        Path(proj, SID + ".jsonl").write_text(
            json.dumps({"type": "user", "uuid": U_UUID, "parentUuid": None,
                        "timestamp": "2026-09-05T00:00:00.000Z", "sessionId": SID,
                        "message": {"role": "user", "content": "Where do the notes-api suites stand?"}}) + "\n" +
            json.dumps({"type": "assistant", "uuid": A_UUID, "parentUuid": U_UUID,
                        "timestamp": "2026-09-05T00:00:05.000Z", "sessionId": SID,
                        "message": {"role": "assistant", "model": "claude-opus-5",
                                    "content": [{"type": "text", "text": REPLY}],
                                    "stop_reason": "end_turn"}}) + "\n")
        # two threads anchored on the reply, in the store the kernel's own create writes: one on a cell, one on a row
        now = int(time.time())
        Path(state, "comments", SID + ".json").write_text(json.dumps({"threads": [
            {"tid": TID_CELL, "sid": TID_CELL, "anchorUuid": A_UUID, "cutUuid": A_UUID, "anchorT": now - 900, "exact": EXACT_CELL,
             "status": "open", "createdT": now - 600, "lastSeenT": now - 600, "name": "web-comment-1", "color": "#e8b220"},
            {"tid": TID_ROW, "sid": TID_ROW, "anchorUuid": A_UUID, "cutUuid": A_UUID, "anchorT": now - 800, "exact": EXACT_ROW,
             "status": "open", "createdT": now - 500, "lastSeenT": now - 500, "name": "web-comment-2", "color": "#9cd2ff"}]}))
        cls.port, cls.token = _free_port(), "testtok-cmttable"
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

    def test_marks_on_a_cell_and_on_a_row_leave_the_tables_rows_cells_and_columns_untouched(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": SID,
                       "tidCell": TID_CELL, "tidRow": TID_ROW,
                       "shots": os.environ.get("TBL_SHOTS", ""), "shotSuffix": "-before" if self.before else ""}, f)
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
        if self.before and not os.environ.get("TBL_BEFORE_ASSERT"):
            self.skipTest("a before-the-change dist: screenshots only, the assertions describe the change")
        for theme in ("dark", "light"):
            m = r[theme]
            self.assertEqual(m["theme"], theme)
            # the table's structure: a header row and three body rows, three cells each, and nothing but cells in a row
            self.assertEqual(len(m["rows"]), 4, "four rows in %s: %r" % (theme, [row["childTags"] for row in m["rows"]]))
            for row in m["rows"]:
                self.assertEqual((row["cells"], row["children"]), (3, 3), "three cells and nothing else in the row (a mark between cells makes a fourth box): %r" % row)
                self.assertTrue(all(t in ("TD", "TH") for t in row["childTags"]), "only cells in a row: %r" % row["childTags"])
                self.assertTrue(all(c["display"] == "table-cell" for c in row["cellBoxes"]))
            self.assertEqual(m["stray"], 0, "no mark is a direct child of the table, a row group or a row in %s" % theme)
            # the column alignment: every body row's cell edges line up with the header's
            head = m["rows"][0]["cellBoxes"]
            for row in m["rows"][1:]:
                for k, (hc, bc) in enumerate(zip(head, row["cellBoxes"])):
                    self.assertLess(abs(hc["left"] - bc["left"]), 1.0, "column %d's left edge lines up in %s: %r vs %r" % (k, theme, hc, bc))
                    self.assertLess(abs(hc["right"] - bc["right"]), 1.0, "…and its right edge")
            # the marks: one on the cell, one per cell across the row, every one inside a cell, all inline
            cell_marks, row_marks = m["marks"]
            self.assertEqual([x["text"] for x in cell_marks], [EXACT_CELL], "the cell thread paints its one word: %r" % cell_marks)
            self.assertEqual([x["text"] for x in row_marks], EXACT_ROW.split("\t"), "the row thread paints one mark per cell, the separators untouched: %r" % row_marks)
            for x in cell_marks + row_marks:
                self.assertTrue(x["inTable"] and x["inCell"], "a mark sits inside a cell, never between them: %r" % x)
                self.assertIn(x["parent"], ("TD", "TH"), "…its parent is the cell itself: %r" % x)
                self.assertEqual(x["display"], "inline", "an inline mark, so it changes no table box: %r" % x)
        # the badge and the dialog: a click on the row thread's mark opens the thread dialog on the page
        self.assertTrue(r["popped"], "the row thread's mark opens its dialog: %r" % r["pop"])
        self.assertEqual(r["pop"]["mode"], "thread")
        self.assertTrue(r["pop"]["visible"] and r["pop"]["width"] > 100 and 0 <= r["pop"]["top"] < 760, "the dialog is on the page: %r" % r["pop"])


if __name__ == "__main__":
    unittest.main()
