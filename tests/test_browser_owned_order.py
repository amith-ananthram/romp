#!/usr/bin/env python3
"""Session order belongs to the VIEWER, not to a kernel (the user 2026-07-31).

Each kernel used to own the order of its own sessions and the browser concatenated the per-host lists, so
hosts always drew as blocks and a drag that mixed them was undone on the next merge — no kernel can record
an order over sids belonging to another one. The arrangement now lives in the browser (ui/webview/
view-order.ts), and each kernel's list is only the arrival-order SEED it starts from.

This pins the kernel-side half: the timeline page's inline boot must hand a lane drag to the browser store
rather than posting it back here. Synthetic ids only.
"""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bwo", os.path.join(BIN, "romp-kernel"))


class TimelineBootWritesTheBrowser(unittest.TestCase):
    def test_a_lane_drag_goes_to_the_browser_store_not_back_to_the_kernel(self):
        boot = km._TIMELINE_BOOT
        self.assertIn("window.__rompTimelineWriteOrder=function(order){"
                      "if(window.__rompWriteOrder)window.__rompWriteOrder(order);};", boot)
        self.assertNotIn('post({type:"writeOrder"', boot,
                         "posting it here would put a kernel back in charge of a per-viewer choice")

    def test_the_write_helper_comes_from_the_federation_bundle_the_page_already_loads(self):
        # the inline boot cannot import a module, so federation.js publishes the ONE implementation — and
        # the page has to load it BEFORE the boot, or the drag would find no helper to call
        src = inspect.getsource(km._timeline_page)
        self.assertIn("federation.js", src)
        self.assertLess(src.index("federation.js"), src.index("_TIMELINE_BOOT"),
                        "the bundle defining __rompWriteOrder loads ahead of the boot that calls it")
        fed = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "federation.ts"), encoding="utf-8").read()
        self.assertIn("w.__rompWriteOrder =", fed)


class TheKernelListIsNowOnlyASeed(unittest.TestCase):
    def test_the_kernel_still_orders_and_gc_s_its_own_sessions(self):
        # unchanged on purpose: it is what a viewer with no arrangement sees, and where a NEW session
        # enters the list. Only the browser's overlay decides the rest.
        self.assertTrue(callable(km._session_order))
        self.assertTrue(callable(km._merge_session_order))
        self.assertTrue(callable(km._gc_session_order))


if __name__ == "__main__":
    unittest.main()
