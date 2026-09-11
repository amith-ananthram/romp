#!/usr/bin/env python3
"""Hover discoverability on the landing page (the user 2026-08-10): the shell's own buttons carry
data-keycmd=<command id>, which palette-main's sweep turns into a "(⌘⇧O)" tooltip tail showing the
command's CURRENT binding; the pane toggles, whose titles are rewritten per toggle by inline JS, read
the same hint through window.__rompKeyHint. Source pins over kernel.py's HTML/JS strings — the kernel
module is deliberately NOT imported (importing it runs boot reconcile against live state)."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
SRC = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py"), encoding="utf-8").read()


class HotkeyHints(unittest.TestCase):
    def test_every_rail_button_with_a_command_carries_its_id(self):
        # the Log's opener left the rail (T290): log.open stays a palette command and the mobile bar's #merr
        for el_id, cmd in [("rail-refresh", "kernel.restart"),
                           ("rail-net", "net.open"), ("rail-gear", "settings.open"),
                           ("rail-usage", "usage.open")]:
            self.assertRegex(SRC, "id=%s data-keycmd=%s|id=%s[^>]* data-keycmd=%s" % (el_id, cmd, el_id, cmd),
                             "%s advertises %s on hover" % (el_id, cmd))

    def test_the_mobile_bar_mirrors_the_rail(self):
        for act, cmd in [("usage", "usage.open"), ("net", "net.open"), ("restart", "kernel.restart"),
                         ("errs", "log.open"), ("settings", "settings.open")]:
            self.assertRegex(SRC, "data-act=%s[^>]* data-keycmd=%s|data-act=%s data-keycmd=%s"
                             % (act, cmd, act, cmd), "mobile %s advertises %s" % (act, cmd))

    def test_pane_toggles_append_the_live_hint_and_refresh_on_rebinds(self):
        self.assertIn("window.__rompKeyHint&&window.__rompKeyHint('pane.'+(k==='fleet'?'outline':k))", SRC,
                      "the toggle titles read the hint through the shell global (fleet wears its Outline id)")
        self.assertIn("(h?' ('+h+')':'')", SRC, "unbound pane commands show no empty parens")
        self.assertIn("window.addEventListener('romp:keys',apply);", SRC,
                      "a rebind (or palette-main's boot nudge) refreshes the titles")
        self.assertIn("window.addEventListener('storage',function(e){if(!e||!e.key||e.key===SK)reconcile(true);apply();});",
                      SRC, "a gear save in another tab re-reads the optional panes before refreshing the titles")


if __name__ == "__main__":
    unittest.main()
