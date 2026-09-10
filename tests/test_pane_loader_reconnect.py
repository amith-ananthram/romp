"""The pane loader that a WS drop puts up must be able to come back DOWN.

The user 2026-07-28: the dashboard announced it had lost the connection every few seconds, and after each
one the chat pane sat under the romp loader until a manual reload.

_pane_spin's loader has exactly two ways down, and on a RE-show — the one `romp:wsdown` triggers — neither
of them worked:

  MutationObserver   fires when the content container gains a child. render.ts's ensureView inserts one
                     `.thread` div per session ONCE and keeps it in a map for the life of the page, so
                     after the first load the container's direct children never change again. Every
                     post-reconnect push mutates nodes that are already there, and a childList observer
                     is deaf to that. Right on a cold load, unreachable on a reconnect.
  30s failsafe       was armed once at page load, so it only ever covered the FIRST show. By the time a
                     drop re-showed the loader, that timer had fired long ago.

So the loader went up on the drop with nothing left that could take it down — a pane frozen behind an
overlay while the socket underneath had already reconnected and was streaming fine.

The fix keeps both exits but makes them repeatable: the failsafe re-arms on every show, and the shim
fires `romp:wsup` on a reconnect, which hides the loader — the socket being back is precisely the event
that ends "romp is reconnecting". Staleness of what's on screen is the shell's reload prompt's job
(raiseStale, same onopen), not the loader's.

Source-pinning, like the other _pane_spin tests (this JS has no jsdom harness).
"""
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


class PaneLoaderReconnect(unittest.TestCase):
    def setUp(self):
        self.js = km._pane_spin("content", "live-ask")

    def test_the_failsafe_is_armed_per_show_not_once_per_page_load(self):
        """The regression itself. A timer set in the load path cannot cover a show that happens minutes
        later, which is every show after the first one."""
        self.assertIn("function arm(){clearTimeout(fail);fail=setTimeout(hide,30000);}", self.js)
        self.assertIn("function show(){o.classList.remove('gone');arm();}", self.js,
                      "showing the loader must (re-)arm its own failsafe")
        self.assertNotIn("if(ready())hide();}setTimeout(hide,30000);", self.js,
                         "the old load-time-only arming is gone")

    def test_hiding_cancels_the_failsafe(self):
        """Otherwise a stale timer from an earlier show fires over a pane that is already live, and the
        next show inherits a shortened window."""
        self.assertIn("function hide(){clearTimeout(fail);o.classList.add('gone');}", self.js)

    def test_the_loader_comes_down_when_the_socket_comes_back(self):
        """The event-based exit for the re-show path. Without it the only way down is the failsafe, i.e.
        30 seconds of romp logo over a pane whose socket reconnected in under two."""
        self.assertIn("window.addEventListener('romp:wsup',function(){hide();});", self.js)
        # …while the corner BADGE (a pane that has content) waits for the first fresh frame instead
        # (2026-09-07; test_pane_shim_return.py owns that half)
        self.assertIn("window.addEventListener('romp:wsfresh',function(){badge(false);});", self.js)
        self.assertIn("window.addEventListener('romp:wsdown',function(){if(ready()){badge(true);}else{show();}});", self.js,
                      "the drop still raises SOMETHING — a matched pair; since T217 a pane with "
                      "content gets the translucent badge and only an empty pane the opaque sheet")

    def test_the_shim_fires_wsup_on_a_reconnect_only(self):
        """A first connect must NOT fire it: the loader is legitimately up during a cold load and has to
        stay there until real content lands (an 8s timer that hid it early was the 2026-07-03 bug)."""
        shim = km._shim("chat")
        self.assertIn('if(wasReconn){var ann=restartAnnounced&&Date.now()-restartAnnounced<30000;'
                      'restartAnnounced=0;', shim,
                      "the wasReconn gate stands; T217 spends the announced-restart latch inside it")
        self.assertIn('try{window.dispatchEvent(new Event("romp:wsup"));}catch(e){}\nenqueue({type:"wsup"});}', shim,
                      "wsup still rides the same wasReconn gate as the reload prompt")
        self.assertIn("var wasReconn=everConnected;everConnected=true;", shim,
                      "and wasReconn still means 'this socket had connected before'")

    def test_every_pane_that_carries_the_loader_gets_both_halves(self):
        """The chat is only where it was noticed: the same loader ships on the feed and fleet pages, and
        they drop and reconnect the same way. The timeline deliberately has no _pane_spin (it owns a
        bars-area loader instead, the user 2026-06-26) — it still carries the shim, so it gets the event
        whether or not anything listens today."""
        for page in (km._chat_page(), km._feed_page(), km._fleet_page()):
            self.assertIn("window.addEventListener('romp:wsup',function(){hide();});", page)
            self.assertIn("window.addEventListener('romp:wsfresh',function(){badge(false);});", page)
            self.assertIn('new Event("romp:wsup")', page)
        self.assertIn('new Event("romp:wsup")', km._timeline_page())
        self.assertNotIn("window.addEventListener('romp:wsup',function(){hide();});", km._timeline_page(),
                         "the timeline still owns no _pane_spin overlay")


if __name__ == "__main__":
    unittest.main()
