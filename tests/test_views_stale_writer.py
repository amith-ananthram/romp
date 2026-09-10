#!/usr/bin/env python3
"""The stale-writer guard on the views store's write door (the user 2026-08-31, from the nightly
optimizer: a tag silently lost all 7 of its members, and re-adding recreated it with only 1).

The full-blob path (setTimelineViews) is last-writer-wins by design — its own comment says so —
so a dashboard whose in-memory copy predates newer edits (a page that slept through a kernel
restart, a second dashboard, a long-lived Obsidian panel) posts the whole stale world back and
every member or tag added since vanishes silently. The v2 per-tag mtimes are forge-proof at the
write door, so the door now enforces the cards-move rule on itself: a writer whose evidence time
(the echoed `at` stamp, else its newest tag mtime) predates a tag's newer store state stands down
for that tag, loudly. Fresh writers — every RMW path, and any dashboard that echoes the blob the
kernel just served — are byte-identical to before. Synthetic sids only."""
import json
import os
import tempfile
import time
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
km = load_source("romp_kernel_stale", os.path.join(BIN, "romp-kernel"))

MEMBERS7 = [{"host": "", "sid": "s%d" % i} for i in range(7)]


class StaleWriterGuard(unittest.TestCase):
    def setUp(self):
        try:
            km._views_path().unlink()
        except OSError:
            pass
        km._flags_cache.clear()
        self.notices = []
        self._sync = km._sync_notice
        km._sync_notice = lambda text, ok=True: self.notices.append((text, ok))

    def tearDown(self):
        km._sync_notice = self._sync
        try:
            km._views_path().unlink()
        except OSError:
            pass
        km._flags_cache.clear()

    def _seed(self):
        """Tag T with 7 members through the RMW door (mtime + at land), then a STALE full blob:
        the client's copy from before those members existed."""
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "gT", "name": "web", "color": "", "members": []}]})
        km._flags_cache.clear()
        stale = json.loads(json.dumps(km._timeline_views()))       # the dashboard's old copy
        time.sleep(1.1)                                            # int-second mtimes need a tick
        cur = json.loads(json.dumps(km._timeline_views()))
        cur["tags"][0]["members"] = list(MEMBERS7)                 # the 7 members land (RMW shape)
        km._set_timeline_views(cur)
        km._flags_cache.clear()
        return stale

    def test_the_reproduction_a_stale_full_blob_cannot_wipe_members(self):
        stale = self._seed()
        km._set_timeline_views(stale)                              # the clobber that ate 7 members
        km._flags_cache.clear()
        t = next(x for x in km._timeline_views()["tags"] if x["id"] == "gT")
        self.assertEqual(len(t["members"]), 7,
                         "the store's newer members SURVIVE a stale writer — the exact bug shape")
        self.assertTrue(self.notices and not self.notices[0][1],
                        "the refusal is LOUD (a dashboard notice), never a silent divergence")
        self.assertIn("stale dashboard write", self.notices[0][0])

    def test_a_stale_blob_omitting_the_tag_cannot_delete_it(self):
        stale = self._seed()
        stale["tags"] = []                                         # the wipe-by-absence shape
        km._set_timeline_views(stale)
        km._flags_cache.clear()
        tags = km._timeline_views()["tags"]
        self.assertEqual([t["id"] for t in tags], ["gT"], "a stale writer cannot delete newer state")
        self.assertEqual(len(tags[0]["members"]), 7)

    def test_a_fresh_writer_still_removes_members_and_deletes_tags(self):
        self._seed()
        fresh = json.loads(json.dumps(km._timeline_views()))       # echoes the served `at`
        fresh["tags"][0]["members"] = fresh["tags"][0]["members"][:3]
        km._set_timeline_views(fresh)
        km._flags_cache.clear()
        self.assertEqual(len(km._timeline_views()["tags"][0]["members"]), 3,
                         "an informed removal lands — the guard refuses staleness, not removals")
        fresh2 = json.loads(json.dumps(km._timeline_views()))
        fresh2["tags"] = []
        km._set_timeline_views(fresh2)
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"], [], "an informed deletion lands too")
        self.assertEqual(len(self.notices), 0, "fresh writers are never noisy")

    def test_the_rmw_paths_always_carry_the_newest_evidence(self):
        self._seed()
        t, err = km._edit_tag("web", remove=["s0", "s1"])
        self.assertIsNone(err)
        km._flags_cache.clear()
        self.assertEqual(len(km._timeline_views()["tags"][0]["members"]), 5,
                         "_edit_tag deep-copies the store, so its writes are informed by construction")
        self.assertIsNone(km._edit_tag("web", delete=True)[1])
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"], [])
        self.assertEqual(len(self.notices), 0)

    def test_a_legacy_tag_is_stamped_on_its_first_read_so_an_evidence_less_writer_stands_down(self):
        # a tag never touched since before the mtime feature has no stamp in the FILE; the first
        # read gives it one (the 2026-09-05 review — so the foreign-file rule can tell it
        # from a client's own create), and from then on the guard judges it like any other tag: a
        # blob with no evidence at all (no `at`, no mtimes) cannot de-member it, loudly, while a
        # copy echoing the served `at` can. Before this change the file's tag stayed unstamped and the
        # legacy last-writer-wins stood for it.
        km._atomic_write(km._views_path(), json.dumps(
            {"active": "all", "tags": [{"id": "gL", "name": "legacy", "color": "",
                                        "members": [{"host": "", "sid": "s1"}]}]}))
        km._flags_cache.clear()
        served = km._timeline_views()
        self.assertTrue(served["tags"][0].get("mtime"), "stamped on the first read")
        km._set_timeline_views({"active": "all", "tags": [
            {"id": "gL", "name": "legacy", "color": "", "members": []}]})
        km._flags_cache.clear()
        self.assertEqual(len(km._timeline_views()["tags"][0]["members"]), 1, "no evidence, no de-membering")
        self.assertTrue(self.notices and not self.notices[0][1], "…and the refusal is loud")
        fresh = json.loads(json.dumps(km._timeline_views()))
        fresh["tags"][0]["members"] = []
        km._set_timeline_views(fresh)
        km._flags_cache.clear()
        self.assertEqual(km._timeline_views()["tags"][0]["members"], [], "an informed removal lands")

    def test_the_at_stamp_is_served_and_survives_the_normalizer(self):
        self._seed()
        v = km._timeline_views()
        self.assertTrue(v.get("at"), "the store carries its write stamp")
        self.assertEqual(km._norm_timeline_views(json.loads(json.dumps(v))).get("at"), v["at"],
                         "clients echo the blob wholesale — the stamp survives the round trip")
        self.assertTrue(km._views_client().get("at"),
                        "…and the rendered client blob serves it, so every dashboard echoes it")


if __name__ == "__main__":
    unittest.main()
