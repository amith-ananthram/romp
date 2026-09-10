"""The pane rail (rotated to a horizontal BOTTOM BAR, the user 2026-07-05) splits into a SCROLLABLE group
(toggles + usage, scrolling SIDEWAYS on a narrow window) and a FIXED group (refresh + network + gear), pinned
to the RIGHT (margin-left:auto) so the actions never get pushed off. The old vertical-bar DEGRADE ladder
(fitRail/data-ruc, the user 2026-06-27/07-01) is GONE — the usage bars are horizontal fill bars only
~text-height tall, so they always fit and there is nothing to degrade."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class RailFit(unittest.TestCase):
    def test_rail_splits_into_scroll_group_and_fixed_actions(self):
        land = km._landing()
        # the toggles + usage live in a scrollable wrapper; the actions in a fixed, right-pinned one
        self.assertIn("<div class=rail-scroll>", land)
        self.assertIn("<div class=rail-acts>", land)
        # the actions are AFTER the scroll wrapper in the DOM (so they're the fixed right-hand group)
        self.assertLess(land.index("class=rail-scroll"), land.index("class=rail-acts"))
        self.assertLess(land.index("id=rail-usage"), land.index("class=rail-acts"), "usage scrolls; actions are fixed")

    def test_scroll_group_styling_and_hidden_scrollbar(self):
        land = km._landing()
        # the bottom bar is a HORIZONTAL row now; the scroll group scrolls sideways, the actions pin RIGHT
        self.assertIn(".rail-scroll{flex:0 1 auto;min-width:0;display:flex;flex-direction:row;align-items:center;gap:12px;"
                      "overflow-x:auto;overflow-y:hidden;scrollbar-width:none}", land)
        self.assertIn(".rail-scroll::-webkit-scrollbar{width:0;height:0}", land)
        self.assertIn(".rail-acts{flex:0 0 auto;display:flex;flex-direction:row;align-items:center;gap:6px;margin-left:auto}", land)
        self.assertIn(".pane-rail{flex:0 0 auto;box-sizing:border-box;display:flex;flex-direction:row;align-items:center;gap:14px;"
                      "padding:0 12px;height:30px;background:#202021;border-top:1px solid #2c2c2d;z-index:10;overflow:hidden}", land)

    def test_the_vertical_degrade_ladder_is_gone(self):
        # horizontal fill bars can't overflow the bar's height, so the whole fitRail/data-ruc machinery was
        # removed (the user 2026-07-05). Guard the CODE doesn't creep back (match code, not the prose that
        # documents the removal — a bare "data-ruc" still lives in an explanatory comment).
        land = km._landing()
        js = km._LANDING_USAGE_JS
        self.assertNotIn("[data-ruc=", land, "no vertical degrade CSS rules")
        self.assertNotIn("function fitRail", js, "no vertical-fit routine")
        self.assertNotIn("dataset.ruc", js)
        self.assertNotIn("var sc=el.parentNode", js)


if __name__ == "__main__":
    unittest.main()
