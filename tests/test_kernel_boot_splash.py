"""A boot splash paints with the dashboard shell the instant a reload lands, so the user sees "something's
happening" before the panes connect + parse (the user 2026-06-26, who noted a delay before anything even shows
and suggested immediately popping up some kind of loading romp dialogue with some moving dots).

It's a full-window centered romp wordmark + pulsing accent-blue dots in the served landing HTML; it fades the
moment ANY pane posts {romp:'ready'} (the timeline lanes render first, no parse), with a 5s backstop so a
slow/closed pane can never trap the user behind it. Pins the shell HTML + the timeline's ready signal.
"""
import os
import pathlib
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


class BootSplash(unittest.TestCase):
    def setUp(self):
        self.html = km._landing()

    def test_the_shell_paints_a_centered_romp_loader(self):
        self.assertIn("id=romp-boot", self.html, "a full-window boot overlay rides in the shell HTML")
        self.assertIn("romp-swirl-o.svg", self.html, "the centered o-glyph is the swirl")
        self.assertIn("class=rl-o", self.html, "the swirl spins as the lowercase 'o' in the wordmark")
        self.assertIn("Anta-Regular.ttf", self.html, "the wordmark is set in Anta, like the README hero")
        self.assertIn("#1EA1EB", self.html, "the R wears the swirl's blue arm colour")
        self.assertIn("rl-dots", self.html, "the moving-dots loading cue below it")
        self.assertIn("@keyframes rl-bnc", self.html, "the dots are animated")
        # the overlay sits before the panes so it covers the whole window from the first paint
        self.assertLess(self.html.index("id=romp-boot"), self.html.index("id=f-chat"))

    def test_the_splash_fades_on_first_content_with_a_backstop(self):
        self.assertIn("romp==='ready'", self.html, "fade the splash when a pane signals first content")
        self.assertIn("classList.add('gone')", self.html)
        self.assertIn("setTimeout(hide,5000)", self.html, "a backstop so it can never trap the user")

    def test_the_timeline_signals_ready_to_the_shell(self):
        tv = (pathlib.Path(BIN).parent / "ui" / "romp-timeline-view.js").read_text()
        self.assertIn("_signalReady()", tv, "the timeline tells the shell it has first content")
        self.assertIn("postMessage({ romp: 'ready' }", tv)
        self.assertIn("this._readySent", tv, "signalled at most once")
        # fired on the main paint AND the empty-state paint (so the splash always clears)
        self.assertEqual(tv.count("this._signalReady()"), 2, "called on both the lanes paint and the empty-state paint")


class PaneSpinner(unittest.TestCase):
    """Each sub-panel shows the spinning romp logo while it loads its data, then it fades when the pane's
    content container gets its first child (the user 2026-06-26). The chat is the slow one; the others flash
    briefly. Self-contained overlay in each pane page — no bundle change — with a backstop so it never sticks."""

    # The timeline is NOT here (the user 2026-06-26): its full-pane _pane_spin hid the instant #host got its
    # first child (the wrap, on construction — before any bars), leaving an empty bar gap, so the view owns a
    # bars-area loader instead (see test_kernel_timeline_split.TimelinePageLoader + the JS barsloader test).
    PANES = {"chat": ("content", None), "feed": ("feed-list", None),
             "fleet": ("fleet-list", None)}

    def test_every_pane_shows_a_spinning_logo_that_hides_on_first_content(self):
        for name, (cid, _) in self.PANES.items():
            html = getattr(km, "_%s_page" % name)()
            self.assertIn("id=pane-spin", html, "%s pane has the loading overlay" % name)
            self.assertIn("romp-swirl-o.svg", html, "%s loader uses the centered o-glyph" % name)
            self.assertIn("class=rl-o", html, "%s loader shows the wordmark (swirl as the 'o')" % name)
            self.assertIn("rl-dots", html, "%s loader has the dots (same look as the splash)" % name)
            self.assertIn("rotate(-360deg)", html, "%s swirl spins (reverse, matching the splash)" % name)
            self.assertIn("getElementById('%s')" % cid, html, "%s observes its content container" % name)
            self.assertIn("MutationObserver", html, "%s hides the spinner on first content" % name)
            # 30s failsafe (the user 2026-07-04, was 8s): content arrival via the MutationObserver is the
            # real hide signal; the timeout exists only so the loader can never trap the user — 8s fired
            # DURING slow cold starts, dropping the loader onto a still-blank pane (the very gap it covers).
            self.assertIn("setTimeout(hide,30000)", html, "%s has a can't-trap failsafe" % name)


if __name__ == "__main__":
    unittest.main()
