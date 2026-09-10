#!/usr/bin/env python3
"""Every inline JS blob the kernel serves must PARSE (the user 2026-08-13: an edit ate spendDet's
closing brace in _LANDING_USAGE_JS, the whole usage script died on one SyntaxError, and the bottom
bar rendered NOTHING — no test read the blobs as JavaScript, so a broken script shipped green through
both suites). node --check each _*_JS module attribute — the RUNTIME value, not the source text, so
Python-level escapes are resolved exactly as the browser receives them. Skipped only where node is
genuinely absent (GitHub's runners ship it)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load — the kernel resolves its state root (and runs boot reconcile)
# at import time; a bare run must never touch real state.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_jsparse", os.path.join(BIN, "romp-kernel"))

NODE = shutil.which("node")


class InlineJsParses(unittest.TestCase):
    @unittest.skipUnless(NODE, "node not installed on this machine")
    def test_every_inline_js_blob_parses(self):
        blobs = {n: v for n, v in vars(km).items() if n.endswith("_JS") and isinstance(v, str)}
        self.assertGreaterEqual(len(blobs), 10, "the kernel's inline scripts should all be discovered")
        for name, js in sorted(blobs.items()):
            with self.subTest(blob=name):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                    f.write(js)
                    path = f.name
                try:
                    r = subprocess.run([NODE, "--check", path], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, name + " does not parse:\n" + r.stderr[:2000])
                finally:
                    os.unlink(path)


if __name__ == "__main__":
    unittest.main()
