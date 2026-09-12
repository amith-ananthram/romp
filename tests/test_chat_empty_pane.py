#!/usr/bin/env python3
"""A chat pane with NO sessions must show something immediately, not spin for 30 seconds.

This is a contract between two files that have to agree, which is exactly why it broke:

  kernel.py's _pane_spin  hides the romp loader on an EVENT — a MutationObserver that fires when the
                          content container gains a child whose id is not the always-present
                          #live-ask host — plus a long failsafe timeout for a genuinely dead kernel.
  render.ts               is the only thing that ever puts children in #content.

While a session is loading that is precisely right. With NO sessions there is nothing to load, so
render.ts rendered nothing, the observer never fired, and the only escape left was the failsafe: every
load of a fresh install sat under the loader for the full 30s. A brand-new user's first impression of
romp was a half-minute spinner over an empty pane, identical on every refresh, because it was a timer
rather than work (the user 2026-07-27, on a clean v0.1.1 install).

The fix is not to shorten the timer — it was 8s once and that was WORSE, firing during a normal slow
cold start and leaving the loader hidden over a still-blank pane. The fix is that "no sessions" is a
real, renderable state, so the event path handles it and the failsafe goes back to meaning only what it
says: the kernel died.

Source-pinning, like the other chat-render tests (the renderer has no jsdom harness).
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
KERNEL = open(os.path.join(ROOT, "kernel", "kernel.py"), encoding="utf-8").read()
RENDER = open(os.path.join(ROOT, "ui", "webview", "render.ts"), encoding="utf-8").read()
CSS = open(os.path.join(ROOT, "ui", "webview", "styles.css"), encoding="utf-8").read()


class ChatEmptyPane(unittest.TestCase):
    def test_the_loader_still_hides_on_content_arriving_not_on_a_timer(self):
        """Pin the mechanism the placeholder depends on. If the hide ever stops keying off a real
        child of the container, the placeholder below stops being what dismisses the loader."""
        spin = re.search(r"def _pane_spin\(.*?\n\n\ndef ", KERNEL, re.S)
        self.assertIsNotNone(spin, "_pane_spin not found")
        body = spin.group(0)
        self.assertIn("MutationObserver", body, "the hide must stay event-based")
        self.assertIn("children[i].id!==IGN", body, "hide keys off a child that is not the ignored host")

    def test_the_failsafe_is_long_enough_to_only_mean_a_dead_kernel(self):
        """It exists so the loader can never permanently stick. It must NOT be tuned down as a
        substitute for rendering something — an 8s version used to fire mid-cold-start and hid the
        loader over a still-blank pane."""
        m = re.search(r"setTimeout\(hide,(\d+)\)", KERNEL)
        self.assertIsNotNone(m, "the failsafe timeout is gone")
        self.assertGreaterEqual(int(m.group(1)), 30000,
                                "shortening the failsafe re-creates the blank-pane gap it was raised to fix")

    def test_zero_sessions_renders_a_real_child_into_content(self):
        """The actual regression: something must land in #content so the observer fires."""
        self.assertIn("function syncNoSessionsPlaceholder", RENDER)
        fn = re.search(r"function syncNoSessionsPlaceholder\(.*?\n\}", RENDER, re.S).group(0)
        self.assertIn('getElementById("content")', fn, "the placeholder must go in the watched container")
        self.assertIn("appendChild", fn, "it must actually add a child — that is what hides the loader")
        self.assertNotIn("live-ask", fn, "and must not reuse the ignored host's id, which never counts")

    def test_it_runs_on_every_push_and_is_idempotent(self):
        """renderTabs runs on every kernel push (0.5-3s). Appending each time would pile up copies."""
        self.assertRegex(RENDER, r"syncNoSessionsPlaceholder\(visibleIds\.length, ids\.length, ids\.filter\(heldHere\)\.length\)",
                         "renderTabs must drive it from the visible session count (and, since the chat split, how many this column holds)")
        fn = re.search(r"function syncNoSessionsPlaceholder\(.*?\n\}", RENDER, re.S).group(0)
        self.assertIn("if (existing) { existing.textContent = txt; return; }", fn, "must not append a second placeholder per push")

    def test_the_placeholder_is_removed_once_a_session_exists(self):
        """Otherwise it lingers above the first real transcript."""
        fn = re.search(r"function syncNoSessionsPlaceholder\(.*?\n\}", RENDER, re.S).group(0)
        self.assertRegex(fn, r"visibleCount > 0[\s\S]*?existing\?\.remove\(\)")

    def test_it_says_what_to_do_next_and_is_styled(self):
        """A new user's first screen. 'Nothing here' is a dead end; name the way out."""
        self.assertIn("No sessions yet", RENDER)
        self.assertIn("romp new", RENDER)
        self.assertRegex(CSS, r"\.tx-empty\s*\{", "reuses the existing empty-state style")


if __name__ == "__main__":
    unittest.main()
