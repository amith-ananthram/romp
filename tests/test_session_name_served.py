"""The chat names the session it messages (the user 2026-09-09), in a real browser against a hermetic kernel: the composer's
resting placeholder reads "Message <name>…" with the name bold in the session's identity colour and follows the active tab;
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
_cred = load_source("romp_credentials_sessionname", os.path.join(ROOT, "kernel", "credentials.py"))
SID_A = "11111111-2222-4333-8444-000000000801"
SID_B = "11111111-2222-4333-8444-000000000802"


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
const chatDoc = () => { const f = document.getElementById("f-chat"); return f && f.contentDocument; };
const waitTabs = (sids) => waitFn((sids) => { const d = document.getElementById("f-chat") && document.getElementById("f-chat").contentDocument; if (!d) return false;
  const ids = Array.from(d.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id); return sids.every((s) => ids.includes(s)); }, sids, "the chat never showed tabs " + sids.join(","));
const waitActive = (sid) => waitFn((sid) => { const d = document.getElementById("f-chat").contentDocument; const t = d.querySelector("#tabs .tab.active[data-id]"); return !!t && t.dataset.id === sid; }, sid, "the chat never activated " + sid);
// the naming chrome: the composer's resting placeholder overlay ("Message <name>…", the name bold in the identity colour) and the
// statusline badge (the name in black on that colour) — both read against the ACTIVE tab's own colour (--chip-bg)
const naming = () => page.evaluate(() => {
  const d = document.getElementById("f-chat").contentDocument;
  const tab = d.querySelector("#tabs .tab.active[data-id]"); const tabBg = tab ? tab.style.getPropertyValue("--chip-bg") : null;
  const ta = d.getElementById("composer-input"); const ph = d.getElementById("composer-ph"); const nm = ph && ph.querySelector(".composer-ph-name");
  const badge = d.querySelector("#statusline .chip-session");
  return { active: tab ? tab.dataset.id : null, tabBg, phShown: !!ph && getComputedStyle(ph).display !== "none", phName: nm ? nm.textContent : null,
           phColor: nm ? nm.style.color : null, phBold: nm ? getComputedStyle(nm).fontWeight : null, nativeHidden: !!ta && ta.classList.contains("ph-on"),
           badgeText: badge ? badge.textContent : null, badgeBg: badge ? badge.style.background : null, badgeFg: badge ? getComputedStyle(badge).color : null };
});
const setBadge = (on) => page.evaluate((on) => { const s = JSON.parse(localStorage.getItem("romp:settings") || "{}"); s.showSessionBadge = on; localStorage.setItem("romp:settings", JSON.stringify(s)); }, on);
const badgeIs = (want, why) => waitFn((want) => !!document.getElementById("f-chat").contentDocument.querySelector("#statusline .chip-session") === want, want, why);

await page.goto(cfg.url);
await waitTabs([cfg.sidA, cfg.sidB]);
const fr = await (await page.$("#f-chat")).contentFrame();
await fr.click('#tabs .tab[data-id="' + cfg.sidA + '"]'); await waitActive(cfg.sidA);
await waitFn(() => { const d = document.getElementById("f-chat").contentDocument; const ph = d.getElementById("composer-ph"); return !!(ph && ph.querySelector(".composer-ph-name")); }, null, "the placeholder overlay never named the session");
out.a = await naming();
// the other tab: the placeholder follows the active session
await fr.click('#tabs .tab[data-id="' + cfg.sidB + '"]'); await waitActive(cfg.sidB);
await waitFn((b) => { const d = document.getElementById("f-chat").contentDocument; const nm = d.querySelector("#composer-ph .composer-ph-name"); return !!nm && nm.textContent !== b; }, out.a.phName, "the placeholder never followed the tab switch");
out.b = await naming();
// the badge: off by default; a flip of the setting (from the shell, as a gear save in another pane lands) shows it, and back
await setBadge(true);
await badgeIs(true, "the badge never came up after the setting was switched on");
out.bOn = await naming();
await setBadge(false);
await badgeIs(false, "the badge never went after the setting was switched off");
out.bOff = await naming();
// ---- the question flow (the user 2026-09-10): a box wearing the "answering" tint while its placeholder still reads the
// resting form (a re-render used to leave it so) draws ONE text — the overlay, tinted; the native placeholder stays transparent ----
const probe = (cls) => page.evaluate((cls) => {
  const d = document.getElementById("f-chat").contentDocument; const ta = d.getElementById("composer-input"); const ph = d.getElementById("composer-ph");
  ta.classList.toggle("answering", cls);
  const tint = d.createElement("span"); tint.style.color = "color-mix(in srgb, var(--accent) 65%, var(--dim))"; d.body.appendChild(tint);
  const dim = d.createElement("span"); dim.style.color = "var(--dim)"; d.body.appendChild(dim);
  const r = { phShown: getComputedStyle(ph).display !== "none", native: getComputedStyle(ta, "::placeholder").color, overlay: getComputedStyle(ph).color,
              tint: getComputedStyle(tint).color, dim: getComputedStyle(dim).color, resting: ta.placeholder.startsWith("Message ") };
  tint.remove(); dim.remove(); return r;
}, cls);
out.answering = await probe(true);
out.resting = await probe(false);
out.ms = Date.now() - out.t0;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedSessionName(unittest.TestCase):
    """One kernel, one page, one driver run in setUpClass; each method asserts one part of the shared result."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served leg needs them")
        cls.lab = tempfile.mkdtemp(prefix="session-name-")
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
        for sid, name, color in ((SID_A, "web", "#e57373"), (SID_B, "api", "#64b5f6")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\t\n" % (name, cwd, color))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True}))
            Path(proj, sid + ".jsonl").write_text(_transcript(sid, cwd, 3))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))   # park sends
        cls.port = _free_port()
        cls.token = "testtok-sessionname"
        cls.env = dict(os.environ,
                       XDG_STATE_HOME=os.path.join(cls.lab, "xdg"),
                       CLAUDE_CONFIG_DIR=claude,
                       ROMP_MANAGER_PORT="1", ROMP_KERNEL_NO_OPEN="1",
                       ROMP_SERVE_TOKEN=cls.token, ROMP_KERNEL_PORT=str(cls.port),
                       ROMP_DIST_DIR=dist, ROMP_MODEL_CATALOG="off",
                       ROMP_TMUX_SOCKET="romp-sessionname-%d" % cls.port,
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

    @staticmethod
    def _rgb(c):
        # the tab's --chip-bg is the raw registry hex; an inline style colour reads back normalised — compare as rgb
        c = (c or "").strip().replace(" ", "")
        if c.startswith("#") and len(c) == 7:
            return "rgb(%d,%d,%d)" % tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        return c

    def test_1_the_placeholder_names_the_active_session_bold_in_its_colour_and_follows_the_tab(self):
        r = self._r(); a, b = r["a"], r["b"]
        self.assertEqual((a["active"], a["phName"]), (SID_A, "web"))
        self.assertEqual((b["active"], b["phName"]), (SID_B, "api"), "the placeholder follows the active tab")
        for c in (a, b):
            self.assertTrue(c["phShown"], "an empty box shows the overlay")
            self.assertTrue(c["nativeHidden"], "…and the native placeholder is transparent beneath it")
            self.assertTrue(c["tabBg"], "the active tab wears the identity colour")
            self.assertEqual(self._rgb(c["phColor"]), self._rgb(c["tabBg"]), "the name wears the tab's colour")
            self.assertIn(c["phBold"], ("600", "700", "bold"))
        self.assertNotEqual(a["tabBg"], b["tabBg"], "two sessions, two colours")

    def test_2_the_badge_is_off_by_default_and_a_flip_of_the_setting_shows_it_and_hides_it_live(self):
        r = self._r(); a, b, on, off = r["a"], r["b"], r["bOn"], r["bOff"]
        self.assertIsNone(a["badgeText"]); self.assertIsNone(b["badgeText"], "off by default: the placeholder alone names the session")
        self.assertEqual(on["badgeText"], "api", "the setting on: the badge names the active session")
        self.assertEqual(self._rgb(on["badgeBg"]), self._rgb(on["tabBg"]), "…on its colour")
        self.assertEqual(on["badgeFg"], "rgb(0, 0, 0)", "…with the name in black")
        self.assertIsNone(off["badgeText"], "the setting off again: the badge goes, no reload")
        self.assertEqual(off["phName"], "api", "…and the placeholder still names the session")

    def test_3_an_answering_box_draws_one_placeholder_the_tinted_overlay_over_a_transparent_native_one(self):
        # the user 2026-09-10: in the question flow the composer's placeholder appeared twice, a hair apart — the
        # tinted native text showing through the name overlay. The "answering" tint rule sat at the overlay's
        # transparent rule's specificity and came later in the sheet, so it won.
        r = self._r(); a, b = r["answering"], r["resting"]
        self.assertTrue(a["resting"] and a["phShown"], "the overlay shows: the placeholder still reads the resting form")
        self.assertEqual(a["native"], "rgba(0, 0, 0, 0)", "the native placeholder is transparent beneath it, tint or no tint")
        self.assertEqual(a["overlay"], a["tint"], "…and the overlay wears the answering tint in its place")
        self.assertNotEqual(a["tint"], a["dim"], "the probe tells the two colours apart")
        self.assertEqual(b["native"], "rgba(0, 0, 0, 0)"); self.assertEqual(b["overlay"], b["dim"], "resting again: dim, one text")


if __name__ == "__main__":
    unittest.main()
