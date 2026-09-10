#!/usr/bin/env python3
"""Every bundle the dashboard shell's page names is one the webview build emits.

The shell page (kernel.py _landing) loads its bundles by name from dist/ (age-color-global.js,
shell-perf.js, palette-main.js), and vscode-extension/esbuild.js decides which entries the build
writes. Nothing ties the two together: a tag for a bundle the build never emits serves a 404 the page
swallows (the script does not run, the page loads, and every test of the html still passes), so the
collector or hotkeys that tag was meant to load are silently absent. This runs the real webview build
in memory (esbuild.js exports its config for the tests, and the build writes nothing to dist/ under
write: false) and checks every /dist/ name the landing html carries against what the build emits. The
names are derived from the html, so a new tag is covered the day it is added. Skips loudly without the
extension deps (npm ci not run here), like the other served-page guards. Synthetic only: no state is
read, none is written.
"""
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
EXT = os.path.join(ROOT, "vscode-extension")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)   # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_landing_bundles", os.path.join(BIN, "romp-kernel"))

# The webview build as esbuild.js configures it, run in memory: the basenames it would write to dist/.
DRIVER = r"""
const path = require("path");
const esbuild = require("esbuild");
const { webview } = require("./esbuild.js");
esbuild.build({ ...webview, write: false, logLevel: "silent" }).then((r) => {
  process.stdout.write(JSON.stringify(r.outputFiles.map((f) => path.basename(f.path))));
}, (e) => { console.error(String(e && e.message || e)); process.exit(1); });
"""


class LandingBundlesBuilt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "esbuild")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the build guard needs them")
        b = subprocess.run(["node", "-e", DRIVER], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        cls.emitted = set(json.loads(b.stdout))

    def test_every_bundle_the_shell_page_names_is_emitted_by_the_build(self):
        named = sorted(set(re.findall(r"<script src=/dist/([\w.-]+\.js)\?v=", km._landing())))
        self.assertTrue(named, "no bundle tag found in the landing html: did the tag's shape change?")
        self.assertTrue(self.emitted, "the build emitted nothing")
        for name in named:
            self.assertIn(name, self.emitted,
                          "%s is loaded by the shell page but the webview build emits no such bundle "
                          "(vscode-extension/esbuild.js entryPoints)" % name)


if __name__ == "__main__":
    unittest.main()
