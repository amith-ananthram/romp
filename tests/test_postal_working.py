#!/usr/bin/env python3
"""The working-note (set_working / `romp --mail working`) goes through the kernel's backend-agnostic store
(POST /working), NOT the tmux @romp-working var — so an SDK session can publish one and postal never shells
tmux for it (the user 2026-06-26). Synthetic only — placeholder ids, hostname-free.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic; constants resolve under here at import
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal", os.path.join(BIN, "romp-postal-service"))


class WorkingNoteThroughKernel(unittest.TestCase):
    def setUp(self):
        self._saved = (pm._publish_working, pm.my_id)
        self.published = []
        pm._publish_working = lambda sid, text: (self.published.append((sid, text)), True)[1]
        pm.my_id = lambda: "sid-self"

    def tearDown(self):
        pm._publish_working, pm.my_id = self._saved

    def test_cli_working_posts_to_the_kernel(self):
        rc = pm.cli_working(["editing", "feed.ts"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.published, [("sid-self", "editing feed.ts")])
        self.assertFalse(hasattr(pm, "tmux"), "the bus has no tmux() helper")

    def test_cli_working_clear_publishes_empty(self):
        pm.cli_working([])
        self.assertEqual(self.published, [("sid-self", "")])

    def test_source_routes_set_working_through_the_kernel_not_tmux(self):
        src = open(os.path.join(BIN, "romp-postal-service")).read()
        self.assertIn('_kernel_post("/working"', src, "_publish_working POSTs to the kernel working endpoint")
        self.assertIn("_publish_working(mid, text)", src, "the set_working MCP tool routes through the kernel")
        self.assertNotIn('"@romp-working"', src, "no tmux @romp-working var write remains in postal")


if __name__ == "__main__":
    unittest.main()
