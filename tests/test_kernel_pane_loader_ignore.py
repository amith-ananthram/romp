"""The chat pane's romp loader (swirl + 'romp' + dots) must stay up until REAL chat content arrives. Moving the
live-ask picker host INSIDE #content (2026-06-27) made #content always carry a child, so the loader — which
hid as soon as #content had any child — vanished instantly over a blank chat (tab names pulsed, no loader).
_pane_spin now takes an ignore_id so a permanent non-content child (#live-ask) doesn't count. (the user 2026-06-27.)"""
import inspect
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


class PaneLoaderIgnore(unittest.TestCase):
    def test_chat_loader_ignores_the_live_ask_host(self):
        js = km._pane_spin("content", "live-ask")
        self.assertIn("IGN='live-ask'", js, "the ignore id is wired in")
        # readiness counts only children whose id != the ignore id
        self.assertIn("c.children[i].id!==IGN", js)
        self.assertIn("if(ready())hide();", js, "hide only once a REAL child exists")
        self.assertNotIn("if(c.children.length)hide();", js, "the old any-child hide is gone")

    def test_other_panes_unaffected_count_all_children(self):
        js = km._pane_spin("feed-list")
        self.assertIn("IGN=''", js, "no ignore id → empty")
        # with IGN empty, ready() returns true on ANY child (original behavior)
        self.assertIn("if(!IGN||c.children[i].id!==IGN)return true;", js)

    def test_chat_page_passes_the_ignore_id(self):
        src = inspect.getsource(km)
        self.assertIn('_pane_spin("content", "live-ask")', src, "the chat page opts the picker host out of the loader gate")

    def test_loader_hide_is_content_driven_with_only_a_long_failsafe_timeout(self):
        # the user 2026-07-03: the old 8s timeout fired DURING a normal slow cold start (serial fleet build,
        # empty-until-data feed/chat), hiding the loader over a still-blank pane. Hiding must be event-driven
        # (the MutationObserver on real content); the timeout is only a can't-trap failsafe → a long window.
        js = km._pane_spin("content", "live-ask")
        self.assertIn("new MutationObserver", js, "content arrival is the real hide signal")
        self.assertIn("setTimeout(hide,30000)", js, "the timeout is a long failsafe, not an in-load hide")
        self.assertNotIn("setTimeout(hide,8000)", js, "the premature 8s hide is gone")


if __name__ == "__main__":
    unittest.main()
