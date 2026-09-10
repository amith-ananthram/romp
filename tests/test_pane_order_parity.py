#!/usr/bin/env python3
"""Mobile/desktop pane-order parity (the user 2026-08-30): the mobile bottom tabs must list the
panes in the SAME order the desktop presents them — "mobile is a re-layout of the desktop, never a
re-ordering". The desktop's presentation order is the rail strip's left-to-right list (the user's
own choice, 2026-07-05); both the rail buttons and the mobile #mtabs now render from the one
_PANE_ORDER constant, so the pin below survives any future reorder of that constant: it asserts
the two BUILT surfaces agree, not any particular sequence."""
import json
import os
import re
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_pop", os.path.join(BIN, "romp-kernel"))


def _pane_seq(fragment):
    return re.findall(r"data-pane=(\w+)", fragment)


class PaneOrderParity(unittest.TestCase):
    def _built(self):
        html = km._landing()
        # anchor on the body's TAGS — the bare class names also appear in the <style> block
        rail = html[html.index("<div class=rail-scroll>"):html.index("<div id=rail-usage")]
        mtabs = html[html.index("<nav id=mtabs>"):html.index("<span class=mtabs-div>")]
        return rail, mtabs

    def test_mobile_tabs_wear_the_desktop_rail_order(self):
        rail, mtabs = self._built()
        self.assertEqual(_pane_seq(mtabs), _pane_seq(rail),
                         "one ordering, not two: #mtabs must list the panes exactly as the "
                         "desktop rail strip does (change one, both move)")
        self.assertEqual(len(_pane_seq(rail)), 4, "all four panes present on both surfaces")

    def test_both_surfaces_render_from_the_one_constant(self):
        # the mechanism, not just the outcome: a future hand-edit of either HTML block back to a
        # literal list would pass the parity test above right up until someone reorders — this pin
        # fails at the re-hardcoding itself
        order = [k for k, _ in km._PANE_ORDER]
        rail, mtabs = self._built()
        self.assertEqual(_pane_seq(rail), order)
        self.assertEqual(_pane_seq(mtabs), order)
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("+ _rail_buttons_html() +", src)
        self.assertIn("+ _mtab_buttons_html() +", src)

    def test_the_initial_mobile_pane_keys_on_chat_not_position(self):
        _, mtabs = self._built()
        m = re.search(r"<button data-pane=chat class=on>", mtabs)
        self.assertIsNotNone(m, "chat is the initially shown pane by KEY — wherever it sits")
        self.assertEqual(mtabs.count("class=on"), 1)

    def test_labels_match_the_pn_map(self):
        # the JS-side PN map (key → user-facing label) and the constant must agree, or a pane
        # wears one name in the shell chrome and another in the tabs. The map is JSON spliced into
        # the bell's script from the constant at import (the #957 review: a hand-written PN was a
        # second list, and drifted), so it parses as JSON here.
        html = km._landing()
        pn = re.search(r"var PN=\{([^}]*)\}", html).group(1)
        self.assertEqual(dict(km._PANE_ORDER), json.loads("{" + pn + "}"))

    def test_the_pn_map_is_built_from_the_constant(self):
        # the mechanism, as test_both_surfaces_render_from_the_one_constant pins it for the HTML: the
        # bell's map is derived, not typed, and the constant is defined before the string built from it
        src = open(os.path.join(BIN, "romp-kernel")).read()
        # assertTrue(needle in src), not assertIn: a failure must not print the kernel's source
        self.assertTrue('var PN=""" + json.dumps(dict(_PANE_ORDER)) + """' in src, "PN is json.dumps of the constant")
        self.assertFalse("var PN={chat:" in src, "no hand-written pane-label map remains")
        self.assertLess(src.index("_PANE_ORDER = ("), src.index('_LANDING_ERRS_JS = """'),
                        "the constant is defined before the script constant that is built from it")


if __name__ == "__main__":
    unittest.main()
