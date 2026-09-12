"""The WHOLE chat pane takes a file drop, and nothing else on the dashboard navigates on one (the user 2026-09-12: an
image dropped beside the box replaced the page with the image — the browser's default for an unhandled drop). A real browser
against a hermetic kernel, synthetic sessions and bytes: a file dropped on the transcript (not the box) attaches to the active
session's box, arriving as the kernel's saved copy (the dropFile round trip) with the pane's ring shown while the drag is over it
and gone after; with a second column open on the other session, a drop on that column attaches there and only there; a drop on
the shell's own chrome and on the feed pane is refused (default prevented, not-allowed) and neither document navigates. Skips
LOUDLY when the extension deps or a playwright browser are absent (CI installs none). ORIGINAL docstring of the skeleton this
copies follows for the fixture's sake: The chat names the session it messages (the user 2026-09-09), in a real browser against a hermetic kernel: the composer's
resting placeholder reads "Message <name>…" with the name bold in the session's identity colour and follows the active tab;
since T335 (the user 2026-09-10) the name is painted with the strip's own perceptual fade of the identity colour, so it sits
with the faded placeholder text, and since T341 (the user 2026-09-11: the full fade read too faint) at HALF strength; this test
reads the same session's at-rest label colour once the tab is inactive and requires the placeholder's name colour to sit
halfway between it and the identity colour, dark and light (PH_SHOTS=<dir> writes screenshots of the box under the strip; PH_BEFORE_DIST=<dist> serves another tree's bundle for the before shots and skips the fade checks);
the FOCUSED box's border is that colour too, in the accent ring's own 1px geometry, the accent its fallback (T345, the user
2026-09-11; PH_SHOTS adds romp_chat-composer-focus-ring-{web,api}-{dark,light}.png);
the statusline badge (the name in black on that colour) is a SETTING, off by default (the maintainers via the user,
2026-09-10), and a flip of the setting shows it and hides it again without a reload. Skips LOUDLY when the extension deps
or a playwright browser are absent (CI installs none). Synthetic sessions and text only."""
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
from romp_load import load_source
from pathlib import Path

from tests.dist_copy import copy_dist

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
_cred = load_source("romp_credentials_dropanywhere", os.path.join(ROOT, "kernel", "credentials.py"))
SID_A = "11111111-2222-4333-8444-000000000901"
SID_B = "11111111-2222-4333-8444-000000000902"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _transcript(sid, cwd, pairs):
    """`pairs` closed user/assistant turns (an OPEN turn would invite the boot reconcile to resume it)."""
    out, parent, t = [], None, 1_700_000_000
    for i in range(pairs):
        u = "%s-a%04x" % (sid[:23], i)
        a = "%s-b%04x" % (sid[:23], i)
        ts = lambda k: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t + i * 60 + k))
        out.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": ts(0), "sessionId": sid, "cwd": cwd,
                    "message": {"role": "user", "content": "please keep going with the notes-api search module (part %d)" % (i + 1)}})
        out.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": ts(5), "sessionId": sid, "cwd": cwd,
                    "message": {"id": "msg_lab_%s_%04d" % (sid[-3:], i), "type": "message", "role": "assistant", "model": "claude-sonnet-5",
                                "content": [{"type": "text", "text": "Note %d: the tokenizer fixture set covers the hyphen cases now." % (i + 1)}],
                                "stop_reason": "end_turn"}})
        parent = a
    return "\n".join(json.dumps(r) for r in out) + "\n"


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
const die = async (why) => { out.ms = Date.now() - out.t0; fs.writeSync(1, "RESULT:" + JSON.stringify({ ...out, died: why }) + "\n"); await browser.close(); process.exit(0); };
const T = 20000;
const waitFn = async (fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await die(why + " (" + String(e).split("\n")[0] + ")"); });
const waitTabs = (fid, sids) => waitFn(([fid, sids]) => { const d = document.getElementById(fid) && document.getElementById(fid).contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id); return sids.every((s) => ids.includes(s)); }, [fid, sids], fid + " never showed tabs " + sids.join(","));
const waitActive = (fid, sid) => waitFn(([fid, sid]) => { const d = document.getElementById(fid).contentDocument; const t = d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid; }, [fid, sid], fid + " never activated " + sid);
// A synthetic OS file drop on an element of a document: a File in a DataTransfer, the enter → over → drop sequence a browser
// dispatches, each event cancelable and bubbling as the real ones are. Reads what the page did with it: the ring and the box's
// outline while the drag was over, whether the drop's default was prevented, and the document's location before and after.
const dropOn = (fid, sel, name) => page.evaluate(async ([fid, sel, name]) => {
  const w = fid ? document.getElementById(fid).contentWindow : window;
  const d = w.document; const el = sel ? d.querySelector(sel) : d.body;
  if (!el) return { missing: sel };
  const before = w.location.href;
  const file = new w.File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82])], name, { type: "image/png" });
  const dt = new w.DataTransfer(); dt.items.add(file);
  const fire = (type) => { const e = new w.DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }); el.dispatchEvent(e); return { prevented: e.defaultPrevented, effect: dt.dropEffect }; };
  fire("dragenter"); const over = fire("dragover");
  const ringOn = d.body.classList.contains("drop-over"); const ta = d.getElementById("composer-input"); const boxOn = !!ta && ta.classList.contains("drop-target");
  const drop = fire("drop");
  await new Promise((r) => setTimeout(r, 50));
  return { over, drop, ringOn, boxOn, ringAfter: d.body.classList.contains("drop-over"), boxAfter: !!ta && ta.classList.contains("drop-target"),
           before, after: w.location.href, stillChat: !!d.getElementById("tabs") };
}, [fid, sel, name]);
const chips = (fid) => page.evaluate((fid) => { const d = document.getElementById(fid).contentDocument;
  return Array.from(d.querySelectorAll("#composer-files .composer-file")).map((c) => ({ pending: c.classList.contains("composer-file-pending"), text: (c.textContent || "").trim().slice(0, 60), title: c.title || "" })); }, fid);
const chipLanded = (fid, name, why) => waitFn(([fid, name]) => { const d = document.getElementById(fid).contentDocument;
  return Array.from(d.querySelectorAll("#composer-files .composer-file")).some((c) => !c.classList.contains("composer-file-pending") && ((c.title || "") + (c.textContent || "")).includes(name)); }, [fid, name], why);

await page.goto(cfg.url);
await waitTabs("f-chat", [cfg.sidA, cfg.sidB]);
const fr = await (await page.$("#f-chat")).contentFrame();
await fr.click('#tabs .tab[data-id="' + cfg.sidA + '"]'); await waitActive("f-chat", cfg.sidA);
// 1. a drop on the TRANSCRIPT (not the box) of column 1: the pane rings while the drag is over it, the drop is taken, the
// kernel's saved copy comes back as the box's attachment chip; the ring is gone after
out.transcript = await dropOn("f-chat", "#content", "shot-a.png");
await chipLanded("f-chat", "shot-a.png", "the drop on the transcript never landed in the box");
out.transcriptChips = await chips("f-chat");
// 2. the shell's own document (a drop that missed every pane): refused, no navigation
out.shell = await dropOn(null, null, "shot-shell.png");
// 3. the feed pane's document: refused, no navigation
out.feed = await dropOn("f-feed", null, "shot-feed.png");
// 4. a split: column 2 on the other session; a drop on ITS transcript attaches there and only there
out.split = await page.evaluate((sidB) => { const f = window.__rompSplitChat(sidB); return { frameId: f && f.id }; }, cfg.sidB);
await waitTabs("f-chat-2", [cfg.sidB]);   // a column holds the session moved into it and no other (chat columns are tab groups since 2026-09-12)
await waitActive("f-chat-2", cfg.sidB);
out.col2 = await dropOn("f-chat-2", "#content", "shot-b.png");
await chipLanded("f-chat-2", "shot-b.png", "the drop on column 2's transcript never landed in its box");
out.col2Chips = await chips("f-chat-2");
out.col1ChipsAfter = await chips("f-chat");
out.hrefs = { shell: await page.evaluate(() => location.href), chat: await page.evaluate(() => document.getElementById("f-chat").contentWindow.location.pathname),
              chat2: await page.evaluate(() => document.getElementById("f-chat-2").contentWindow.location.pathname), feed: await page.evaluate(() => document.getElementById("f-feed").contentWindow.location.pathname) };
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedDropAnywhere(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one part of the shared result."""
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="drop-anywhere-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        os.makedirs(os.path.join(state, "names"), exist_ok=True)
        os.makedirs(os.path.join(state, "sdk"), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        # two synthetic SDK sessions with closed-turn transcripts (nothing is ever resumed or spawned), each with an identity
        # colour in the registry (the third field): the name in the placeholder and on the badge wears it
        os.makedirs(os.path.join(state, "states"), exist_ok=True)
        for sid, name, color in ((SID_A, "web", "#e57373"), (SID_B, "api", "#64b5f6")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\t\n" % (name, cwd, color))
            # idle for two hours (the last states row's time is a dormant session's idle-since): past the faded threshold, so
            # an INACTIVE tab's label wears the strip's at-rest fade, the level the placeholder's name is measured against (T335)
            Path(state, "states", sid + ".jsonl").write_text(json.dumps({"t": int(time.time()) - 7200, "state": "waiting"}) + "\n")
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, cwd, 3))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.port = _free_port()
        cls.token = "testtok-dropanywhere"
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
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "sidA": SID_A, "sidB": SID_B}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            so = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode()
            cls.driver_error = "driver timed out; partial output:\n%s" % so[-3000:]
            return
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served leg needs one (CI installs none)")
        if p.returncode != 0:
            cls.driver_error = "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:]
            return
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            cls.driver_error = "driver printed no result:\n" + p.stdout[-3000:]
            return
        r = json.loads(line[len("RESULT:"):])
        if "died" in r:
            cls.driver_error = "driver aborted early: %s\n%s" % (r["died"], json.dumps(r, indent=1)[-2500:])
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

    @staticmethod
    def _rgb(c):
        # the tab's --chip-bg is the raw registry hex; an inline style colour reads back normalised — compare as rgb
        c = (c or "").strip().replace(" ", "")
        if c.startswith("#") and len(c) == 7:
            return "rgb(%d,%d,%d)" % tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        return c

    def test_1_a_drop_on_the_transcript_attaches_to_the_box_and_the_pane_rings_while_the_drag_is_over(self):
        r = self._r(); t = r["transcript"]
        self.assertTrue(t["over"]["prevented"], "dragover accepted: the drop is permitted anywhere in the pane: %r" % t)
        # (the cursor a handler sets — copy here, not-allowed on the shell and the feed — is not readable back off a synthetic
        # DataTransfer, so it is pinned at source in drop-anywhere.test.ts rather than asserted here)
        self.assertTrue(t["ringOn"] and t["boxOn"], "the pane rings and the box shows the landing spot while the drag is over: %r" % t)
        self.assertTrue(t["drop"]["prevented"], "the drop is taken, never the browser's navigation")
        self.assertFalse(t["ringAfter"] or t["boxAfter"], "the ring is gone after the drop")
        self.assertEqual((t["before"], t["stillChat"]), (t["after"], True), "the pane did not navigate")
        self.assertTrue(any("shot-a.png" in (c["title"] + c["text"]) and not c["pending"] for c in r["transcriptChips"]),
                        "the kernel's saved copy is the box's attachment: %r" % r["transcriptChips"])

    def test_2_the_shell_and_the_feed_refuse_a_file_drag_and_never_navigate(self):
        r = self._r()
        for where in ("shell", "feed"):
            d = r[where]
            self.assertTrue(d["over"]["prevented"], "%s: dragover handled (else the drop would navigate): %r" % (where, d))
            self.assertTrue(d["drop"]["prevented"], "%s: the drop is swallowed" % where)
            self.assertEqual(d["before"], d["after"], "%s: no navigation" % where)
        self.assertTrue(r["hrefs"]["shell"].startswith("http://127.0.0.1:"), "the dashboard is still the dashboard: %r" % r["hrefs"])
        self.assertEqual(r["hrefs"]["feed"], "/feed")

    def test_3_each_split_column_is_its_own_drop_area(self):
        r = self._r(); c = r["col2"]
        self.assertEqual(r["split"]["frameId"], "f-chat-2")
        self.assertTrue(c["ringOn"] and c["drop"]["prevented"] and c["before"] == c["after"], "column 2 took the drop on its own document: %r" % c)
        self.assertTrue(any("shot-b.png" in (x["title"] + x["text"]) and not x["pending"] for x in r["col2Chips"]), "…into ITS box: %r" % r["col2Chips"])
        self.assertFalse(any("shot-b.png" in (x["title"] + x["text"]) for x in r["col1ChipsAfter"]), "column 1's box is untouched by column 2's drop: %r" % r["col1ChipsAfter"])
        self.assertTrue(any("shot-a.png" in (x["title"] + x["text"]) for x in r["col1ChipsAfter"]), "…and keeps its own attachment")
        self.assertEqual((r["hrefs"]["chat"], r["hrefs"]["chat2"]), ("/chat", "/chat"), "neither column navigated")

    def test_4_the_whole_story_runs_in_well_under_half_a_minute(self):
        self.assertLess(self._r()["ms"], 25000)


if __name__ == "__main__":
    unittest.main()
