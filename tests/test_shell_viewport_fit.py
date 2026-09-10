#!/usr/bin/env python3
"""The shell fits the VISIBLE viewport, on every browser (the user 2026-07-29).

Two reports, one root cause. On an iPad the whole bottom of the UI was cut off; in Firefox a strip along
the bottom sat over the rail. The shell sized its column with `100vh` while the body that CLIPS it
(overflow:hidden) was sized `height:100%`. Those are the same number only when no browser chrome is
moving: on iOS Safari `100vh` is the LARGE viewport, the one you get with the address bar collapsed, so
whenever a toolbar is on screen the column was taller than the box containing it and the rail fell out of
the bottom. Two height bases for one box is the bug.

Worse, the accurate measurement already existed and was thrown away: _LANDING_MOBILE_JS publishes the
live visualViewport height as --app-h, but only the MOBILE media query consumed it, and a landscape
tablet is too wide for both mobile breakpoints, so it took the desktop branch.

Verified in real Firefox before/after by forcing --app-h shorter than 100vh (the iOS toolbar case): the
rail measured 870..900 inside a 600px-tall body before, and 570..600 after.
"""
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
km = load_source("romp_kernel_vhfit", os.path.join(BIN, "romp-kernel"))


class OneHeightBasis(unittest.TestCase):
    def setUp(self):
        self.html = km._landing()

    def test_the_column_is_measured_against_the_body_that_clips_it(self):
        self.assertIn(".col{display:flex;flex-direction:column;height:100%;box-sizing:border-box;", self.html)
        self.assertNotIn("height:100vh;box-sizing:border-box", self.html,
                         "the column must not carry a viewport unit of its own")

    def test_the_shell_height_chain_applies_at_every_width(self):
        # 100% → 100dvh → --app-h, last-wins, so an old browser still gets a full-height shell and a
        # modern one tracks chrome appearing and collapsing. NOT inside a media query any more: a
        # landscape tablet matches neither mobile breakpoint.
        self.assertIn("html,body{margin:0;height:100%;height:100dvh;height:var(--app-h,100dvh);"
                      "background:#1e1e1e;overflow:hidden}", self.html)

    def test_no_full_height_box_is_left_on_a_raw_viewport_unit(self):
        # every remaining 100vh in the shell would be a box that ignores the toolbars
        shell = self.html
        for frag in ("width:100vw;height:100vh", ".col{display:flex;flex-direction:column;height:100vh"):
            self.assertNotIn(frag, shell, frag)

    def test_a_lifted_pane_is_sized_by_its_insets_not_by_viewport_units(self):
        # inset:0 already IS the viewport box for a fixed element; the explicit 100vw/100vh overrode it.
        # (background:transparent rides the same rules: an opaque lifted iframe blacks out the window —
        # see test_kernel.test_settings_is_a_fullscreen_modal.)
        self.assertIn("body.settings-open #f-feed{display:block;position:fixed;inset:0;z-index:200;background:transparent}", self.html)
        # The PICKER lift is the one exception on the VERTICAL axis: its height follows --app-h (the
        # shell's live visible height) because the layout viewport ignores the phone keyboard — inset:0
        # left the picker's lower rows behind it — and the --app-h sizing is also what turns the keyboard
        # into an in-iframe resize event for the picker's short-window fold (the user 2026-08-10).
        # Horizontally it stays inset-sized (left:0;right:0), no 100vw.
        self.assertIn("body.picker-open #f-chat{display:block;position:fixed;left:0;right:0;top:0;"
                      "height:var(--app-h,100dvh);z-index:200;background:transparent}", self.html)

    def test_an_unpainted_pane_is_dark_not_white(self):
        # a pane whose document has not painted is a white rectangle in a dark frame (Firefox shows it
        # plainly) — which is exactly what "a white strip at the bottom" looks like
        self.assertIn("iframe{background:#1e1e1e}", self.html)


class RefitsWhenTheVisibleHeightChanges(unittest.TestCase):
    def setUp(self):
        self.js = km._LANDING_MOBILE_JS

    def test_it_drives_app_h_off_the_live_visual_viewport(self):
        # pinch-aware since 2026-08-19: desktop (fine pointer) reads innerHeight outright — pinch-immune
        # in every browser, no scale arithmetic (desktop Firefox does not reliably report vv.scale during
        # a pinch); the visual viewport drives the fit only on coarse-pointer devices, where the soft
        # keyboards and collapsing toolbars it exists for live, scale-guarded against mobile pinches.
        self.assertIn("var coarse=window.matchMedia&&matchMedia('(pointer: coarse)').matches;", self.js)
        self.assertIn("var h=(!coarse||!vv)?window.innerHeight:Math.round(vv.height*(vv.scale||1));", self.js)
        self.assertIn("setProperty('--app-h',h+'px')", self.js)

    def test_it_refits_on_the_events_ios_actually_changes_the_height_on(self):
        # iOS collapses its toolbars AS YOU SCROLL, with no window resize; the visual viewport's own
        # scroll event is where that settles. pageshow covers a back/forward-cache restore. Since
        # 2026-09-08 every event binds `refit`, the one-frame coalescer, and the set also covers a
        # return from the background (visibilitychange, window focus) and a keyboard the composer's
        # blur dismissed (focusout) — the installed iPhone app came back keyboard-short otherwise.
        # Behavior is pinned by test_kernel_mobile.MobileFitExecutes; these are the wiring strings.
        for ev in ("'resize',refit", "'orientationchange',refit", "'pageshow',refit", "'focus',refit"):
            self.assertIn("window.addEventListener(" + ev, self.js)
        self.assertIn("document.addEventListener('visibilitychange',function(){if(document.visibilityState==='visible')refit();});", self.js)
        self.assertIn("document.addEventListener('focusout',refit);", self.js)
        self.assertIn("window.visualViewport.addEventListener('scroll',refit)", self.js)
        self.assertIn("window.visualViewport.addEventListener('resize',refit)", self.js)
        # one fit per animation frame, however many events a keyboard slide or a resume fires
        self.assertIn("fitRaf=window.requestAnimationFrame(function(){fitRaf=0;fit();});", self.js)

    def test_the_fit_runs_even_with_no_mobile_tab_bar(self):
        # it must apply on a desktop/tablet layout too, so the fit and its listeners come BEFORE the
        # #mtabs early return — that ordering is the whole reason a landscape tablet gets a real height
        fit_at = self.js.index("fit();window.addEventListener('resize',refit)")
        # the line-anchored form: barfit() looks the bar up the same way, guarded, BEFORE the wiring
        bar_at = self.js.index("\nvar bar=document.getElementById('mtabs');if(!bar)return;\n")
        self.assertLess(fit_at, bar_at)


if __name__ == "__main__":
    unittest.main()
