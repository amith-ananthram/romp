"""Feed + timeline are EXPENSIVE to build (re-segment every session ~2.7s) and were rebuilt on EVERY push,
so a reload/idle tick paid the full cost (the user 2026-06-25, who found reload/startup still very slow). They're
now cached, keyed on a fleet fingerprint that busts on any transcript/states/postal change, a judge pass, a
live tmux badge change, a colormap/session-flags change, or a 5s time bucket (so age labels keep advancing).
"""
import os
import time
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


class FleetCacheTest(unittest.TestCase):
    def test_sig_is_stable_when_nothing_changes(self):
        now, tmux = int(time.time()), km._tmux_sessions()
        self.assertEqual(km._fleet_view_sig(now, tmux), km._fleet_view_sig(now, tmux))

    def test_sig_busts_on_a_judge_pass(self):
        now, tmux = int(time.time()), km._tmux_sessions()
        a = km._fleet_view_sig(now, tmux)
        km._judge_gen[0] += 1
        try:
            self.assertNotEqual(a, km._fleet_view_sig(now, tmux), "a judge pass must rebuild the views")
        finally:
            km._judge_gen[0] -= 1

    def test_time_bucket_advances_so_age_labels_refresh(self):
        tmux = km._tmux_sessions()
        self.assertEqual(km._fleet_view_sig(0, tmux), km._fleet_view_sig(4, tmux), "same 5s bucket → cache hit")
        self.assertNotEqual(km._fleet_view_sig(0, tmux), km._fleet_view_sig(5, tmux), "next 5s bucket → refresh")

    def test_cached_feed_and_timeline_reuse_on_a_matching_sig(self):
        feed_save = list(km._built_feed)
        tl_save = list(km._built_timeline)
        dirty_save = km._views_dirty[0]
        try:
            km._views_dirty[0] = 0.0
            f_sentinel = {"type": "feed", "cards": [], "working": []}
            km._built_feed[:] = [("SIG",), f_sentinel, time.time(), time.time()]
            self.assertIs(km._cached_feed(0, {}, ("SIG",)), f_sentinel, "matching sig reuses, no rebuild")
            t_sentinel = {"type": "data"}
            km._built_timeline[:] = [("SIG",), t_sentinel, time.time(), time.time()]
            self.assertIs(km._cached_timeline(0, {}, ("SIG",)), t_sentinel)
        finally:
            km._built_feed[:] = feed_save
            km._built_timeline[:] = tl_save
            km._views_dirty[0] = dirty_save

    def test_views_dirty_mark_busts_the_cache_past_sig_and_throttle(self):
        """An optimistic kernel-side mutation (a parked-op chip, a follow-up reopen, a clear, a
        model-pending stamp) lives in memory or a goal store — NO file-mtime signature sees it, and the
        REBUILD_MIN_S throttle would otherwise serve the stale pre-change payload on the very push meant
        to show it (the user 2026-07-05: a reply on a distilled card lagged its move to Working)."""
        feed_save = list(km._built_feed)
        dirty_save = km._views_dirty[0]
        try:
            f_stale = {"type": "feed", "cards": ["stale"]}
            km._built_feed[:] = [("SIG",), f_stale, time.time(), time.time()]   # fresh build: same sig AND inside REBUILD_MIN_S
            km._mark_views_dirty()                                 # the mutation lands after the build
            got = km._cached_feed(int(time.time()), {}, ("SIG",))
            self.assertIsNot(got, f_stale, "a dirty mark newer than the build must force a rebuild")
        finally:
            km._built_feed[:] = feed_save
            km._views_dirty[0] = dirty_save

    def test_a_mutation_landing_mid_build_is_not_swallowed_by_that_build(self):
        """A build takes ~1-1.6s and reads the stores one session at a time, so a mutation landing
        MID-build may or may not have been read — its payload can predate the gesture while its finish
        postdates it. The dirty floor therefore keys on the build's START: comparing against the finish
        swallowed exactly that case (the user 2026-07-28: a reply landed while a build was in flight,
        the reply's dirty mark lost to that build's completion, and the pre-reply payload — the card
        still Completed — was re-served until the next sig bust, the window a client fallback needs to
        bounce a just-replied card back to Completed)."""
        feed_save = list(km._built_feed)
        dirty_save = km._views_dirty[0]
        real_build = km.build_feed
        def build_with_midflight_reply(now, tmux):
            time.sleep(0.005)                         # a real build runs ~1s; keep the mark measurably
            km._mark_views_dirty()                    # past the start stamp, then: the reply lands while
            return {"type": "feed", "cards": []}      # this build is mid-read. fresh dict → `is` tells builds apart
        try:
            km._views_dirty[0] = 0.0
            km._built_feed[:] = [None, None, 0.0, 0.0]
            km.build_feed = build_with_midflight_reply
            f1 = km._cached_feed(int(time.time()), {}, ("SIG",))
            km.build_feed = lambda now, tmux: {"type": "feed", "cards": []}
            f2 = km._cached_feed(int(time.time()), {}, ("SIG",))
            self.assertIsNot(f2, f1, "a mark set during the build postdates its start → must rebuild")
            f3 = km._cached_feed(int(time.time()), {}, ("SIG",))
            self.assertIs(f3, f2, "the mark predates the SECOND build's start → reuse, no rebuild loop")
        finally:
            km.build_feed = real_build
            km._built_feed[:] = feed_save
            km._views_dirty[0] = dirty_save

    def test_a_connect_still_serves_the_warmed_build_even_when_dirty(self):
        """connect NEVER rebuilds (instant reload is the contract) — the pusher's next tick, woken by
        _mark_views_dirty itself, refreshes the view for everyone within a beat."""
        feed_save = list(km._built_feed)
        dirty_save = km._views_dirty[0]
        try:
            f_warm = {"type": "feed", "cards": ["warm"]}
            km._built_feed[:] = [("OLD",), f_warm, time.time(), time.time()]
            km._mark_views_dirty()
            self.assertIs(km._cached_feed(int(time.time()), {}, ("NEW",), connect=True), f_warm)
        finally:
            km._built_feed[:] = feed_save
            km._views_dirty[0] = dirty_save

    def test_mark_views_dirty_wakes_the_pusher(self):
        km._pusher_wake.clear()
        try:
            km._mark_views_dirty()
            self.assertTrue(km._pusher_wake.is_set(), "the dirty mark must also wake the pusher NOW")
        finally:
            km._pusher_wake.clear()


if __name__ == "__main__":
    unittest.main()
