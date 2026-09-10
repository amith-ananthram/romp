#!/usr/bin/env python3
"""A pane the user cannot see never raises the stale banner, in real browsers (test_kernel_disconnect_banner.py
runs the served function under node). The dashboard shell hides a pane two ways, and each leg drives one with the
shell's own CSS, lifted from the served landing page: the desktop rail takes po-<pane> off the body and
`body:not(.po-chat) #chat-pane{display:none}` hides the pane WRAPPER (the iframe keeps its own display); the phone
shell dissolves the wrappers (display:contents), sets every iframe display:none and shows the one carrying m-on,
which the tab switch moves. The chat frame carries the served shim's paneHidden() and the chat page's publisher
(ui/webview/chat-visibility.ts, bundled here with esbuild). What is measured, per browser and per shell: shown,
the shim says not hidden; hidden after that first show, hidden, by the published word where the browser kept the
iframe's size (Chromium) and by the probe where the viewport went to zero (Firefox); shown again, not hidden; a
frame hidden since load, hidden. Skips LOUDLY without a playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules
holding it and esbuild (CI does not), and per browser when that browser is not installed. Synthetic pages only."""
import json
import os
import re
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_panehiddenword", os.path.join(BIN, "romp-kernel"))

# the shell's two ways of hiding a pane, as the served landing page carries them (setUp checks they are still there)
DESKTOP_RULE = "body:not(.po-chat) #chat-pane{display:none}body:not(.po-fleet) #fleet-pane{display:none}body:not(.po-feed) #feed-pane{display:none}"
PHONE_RULES = (
    "#chat-pane,#fleet-pane,#feed-pane,#files-pane,#tl-pane{display:contents!important}",
    ".pane>iframe{position:static;inset:auto;width:100%;height:100%}",
    "iframe{position:static;display:none;width:100%;height:100%;border:0}",
    "#f-chat.m-on,#f-fleet.m-on,#f-feed.m-on,#f-files.m-on{display:block}",
)


def _node_path():
    for cand in (os.environ.get("ROMP_PLAYWRIGHT_NODE_PATH", ""), os.path.join(ROOT, "vscode-extension", "node_modules")):
        if cand and os.path.isdir(os.path.join(cand, "playwright")) and os.path.isdir(os.path.join(cand, "esbuild")):
            return cand
    return ""


class PaneHiddenWordInBrowsers(unittest.TestCase):
    def setUp(self):
        np = _node_path()
        if not np:
            self.skipTest("no playwright: set ROMP_PLAYWRIGHT_NODE_PATH to a node_modules that holds it and esbuild")
        self.node_path = np
        landing = km._landing()
        self.assertIn(DESKTOP_RULE, landing, "the desktop rail's pane-hiding rule moved: re-anchor")
        for rule in PHONE_RULES:
            self.assertIn(rule, landing, "a phone-shell rule moved: re-anchor: " + rule)
        js = km._shim("chat")
        m = re.search(r"^function paneHidden\(\)\{[^\n]*$", js, re.M)
        self.assertIsNotNone(m, "the shim's paneHidden is one line of the served shim")
        self.assertNotIn("\\", m.group(0), "a backslash in the line would not reach the page verbatim")
        self.td = tempfile.TemporaryDirectory()
        self.spec = os.path.join(self.td.name, "spec.json")
        with open(self.spec, "w") as f:
            json.dump({"desktopRule": DESKTOP_RULE, "phoneRules": "".join(PHONE_RULES), "shim": m.group(0),
                       "chatBody": km._chat_body(), "uiDir": os.path.join(ROOT, "ui", "webview")}, f)

    def tearDown(self):
        if hasattr(self, "td"):
            self.td.cleanup()

    def _drive(self, browser):
        env = dict(os.environ, NODE_PATH=self.node_path)
        r = subprocess.run(["node", os.path.join(HERE, "pane_hidden_word_browser.js"), self.spec, browser],
                           capture_output=True, text=True, timeout=240, env=env)
        self.assertEqual(r.returncode, 0, "the driver failed:\n" + r.stderr[-3000:])
        o = json.loads(r.stdout.strip().splitlines()[-1])
        if o.get("skipped"):
            declared = os.environ.get("ROMP_SERVED_TESTS_ENGINES", "")
            if declared and browser not in [e.strip() for e in declared.split(",")]:
                # a runner that says which engines it installed (CI's extension job: chromium) leaves the other legs
                # as skips even under ROMP_SERVED_TESTS_REQUIRE=1, which honours this prefix (tests/conftest.py)
                self.skipTest("optional: this runner declares no %s (ROMP_SERVED_TESTS_ENGINES=%s): %s" % (browser, declared, o["skipped"]))
            self.skipTest("no playwright %s on this machine; this leg needs it (CI installs none): %s" % (browser, o["skipped"]))
        return o

    def _check(self, browser, o):
        for shell in ("desktop", "phone"):
            s = o[shell]
            where = "%s, %s shell: " % (browser, shell)
            self.assertEqual(s["errors"], [], where + "no script error in any frame")
            shown = s["shown"]
            self.assertTrue(shown["body"], where + "the chat body is up in the frame")
            self.assertTrue(shown["iw"] > 0 and shown["ih"] > 0, where + "a shown frame has a viewport")
            self.assertIs(shown["word"], False, where + "the observer's first word: on screen")
            self.assertIs(shown["shim"], False, where + "shown: not hidden")
            if shell == "desktop":
                self.assertEqual(s["display"]["chat-pane"], "none", where + "the rail's rule: the pane wrapper is display:none")
            else:
                self.assertEqual(s["display"]["chat-pane"], "contents", where + "the phone shell dissolves the wrapper")
                self.assertEqual(s["display"]["f-chat"], "none", where + "the iframe itself is display:none once m-on moved off it")
                self.assertEqual(s["display"]["f-feed"], "block", where + "the feed tab shows with no po-feed on the body")
            hid = s["hidden"]
            self.assertIs(hid["shim"], True, where + "hidden after a first show: no banner from a pane nobody can see: " + json.dumps(hid))
            # which witness carried it is the browser's business: a frame that kept its size (Chromium) is blind to the
            # probe and the word carries it; a frame whose viewport went to zero (Firefox) is carried by the probe
            if hid["iw"] > 0 or hid["ih"] > 0:
                self.assertIs(hid["probe"], False, where + "a frame that kept its size: the probe alone reads it as shown")
                self.assertIs(hid["word"], True, where + "so the published word carries the verdict")
            else:
                self.assertIs(hid["probe"], True, where + "a frame whose viewport went to zero: the probe carries the verdict")
            again = s["reshown"]
            self.assertIs(again["shim"], False, where + "shown again: the shim raises again if genuinely stale")
            self.assertTrue(again["iw"] > 0 and again["ih"] > 0, where + "a re-shown frame has a viewport")
            never = s["neverShown"]
            self.assertIs(never["shim"], True, where + "hidden since load reads hidden")
            self.assertIs(never["probe"], True, where + "a never-shown iframe has a zero viewport: the probe is right at boot, "
                          "so nothing need be published before the observer speaks")
            self.assertIn(never.get("word"), (True, None), where + "the word, if any, agrees: " + json.dumps(never))

    def test_chromium(self):
        self._check("chromium", self._drive("chromium"))

    def test_firefox(self):
        self._check("firefox", self._drive("firefox"))

    def test_webkit(self):
        self._check("webkit", self._drive("webkit"))


if __name__ == "__main__":
    unittest.main()
