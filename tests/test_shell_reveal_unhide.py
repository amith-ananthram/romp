#!/usr/bin/env python3
"""A REVEAL un-hides a desktop-toggled-off pane (the user 2026-08-13): clicking a feed card that jumps
into a CLOSED chat used to land invisibly — the hidden iframe's WS stays live, so the focus/scroll ran
under display:none and the click read as a no-op. The shell's two reveal arrivals (the pane's own
postMessage and the kernel's app=shell push) now both un-hide via __rompPaneToggle(p, true) — the same
move the Log jump (feed) and toggleFleet (chat) precedents make — before the mobile tab switch, and the
un-hide persists in romp-panes like any manual toggle. Source pins on the inline shell JS."""
import os
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = open(os.path.join(os.path.dirname(HERE), "bin", "romp-kernel")).read()


class RevealUnhidesThePane(unittest.TestCase):
    def test_the_reveal_helper_unhides_then_tab_switches(self):
        self.assertIn("function reveal(p){try{window.__rompPaneToggle&&window.__rompPaneToggle(p,true);}"
                      "catch(e){}userSwitch(p);}", SRC,
                      "un-hide FIRST (guarded: the collapse script parses later), then the mobile tab through userSwitch, "
                      "which drops the file relay's remembered tab the way a tab tap does (tests/test_pane_state_broadcast.py)")
        self.assertIn("function userSwitch(p){window.__rompFilesTabFrom=null;show(p);}", SRC)

    def test_both_reveal_arrivals_use_it(self):
        self.assertIn("if(m.romp==='reveal'&&m.pane)reveal(m.pane);", SRC,
                      "the pane's own postMessage (revealSelfPane — the only path that reaches the shell)")
        self.assertIn("if(m&&m.type==='reveal'&&m.pane)reveal(m.pane);", SRC,
                      "the kernel's app=shell push")

    def test_hover_paths_stay_reveal_free(self):
        # showAskPath / glowTurns are hover affordances — they must never yank a hidden pane open.
        # They send no reveal at all, so it's enough that ONLY the two arrivals above call reveal().
        self.assertEqual(SRC.count(")reveal(m.pane);"), 2)


if __name__ == "__main__":
    unittest.main()
