#!/usr/bin/env python3
"""Branch lineage on the TIMELINE (the user 2026-08-14): a forked lane carries `branch` (its
connector endpoint) only while the parent's lane is in the same build; its copied pre-fork bars are
clipped in that case and shown in full when the parent's lane is gone (as if one session all
along); comment threads never become lanes — each rides its parent lane as a square marker. All
fixtures SYNTHETIC."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_TMUX_AVAILABLE"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

km._limit_hold = lambda sid: None

PARENT = "11111111-2222-3333-4444-555555555555"
CHILD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
THREAD = "99999999-8888-7777-6666-555555555555"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class TimelineLineageBase(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._parse_cache.clear()
        km._dismissed_lanes.clear()   # a durable module-level set: an earlier test file dismissing
        #                               the shared placeholder sid silently filtered these lanes
        self.now = int(time.time())
        self.fork_t = self.now - 300
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        t = self.now - 600
        shared = [uline(t, "how should the retry loop back off?", "u1"),
                  aline(t + 20, "Use exponential backoff.", "a1", parent="u1")]
        own = [uline(self.fork_t + 60, "and inside the fork?", "u9", parent="a1"),
               aline(self.fork_t + 80, "Cap the delay at two minutes.", "a9", parent="u9")]
        for sid, name, recs in ((PARENT, "parent", shared), (CHILD, "child", shared + own)):
            (jd.NAMES / sid).write_text("%s\t%s" % (name, self.cdir))
            (self.proj / (sid + ".jsonl")).write_text(
                "\n".join(json.dumps(r) for r in recs) + "\n")
        self._saved_sdk = km._sdk
        kids = {PARENT: [{"sid": CHILD, "name": "child", "cut": "a1", "t": self.fork_t}]}

        class FakeBe:
            def fork_children(self):
                return kids

            def owns(self, sid):
                return False

            def __getattr__(self, name):
                return lambda *a, **k: None

        self._fake_be = FakeBe()
        km._sdk = lambda: self._fake_be

    def tearDown(self):
        km._sdk = self._saved_sdk
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _lane(self, tl, sid):
        return next((l for l in tl["sessions"] if l["id"] == sid), None)


class BranchOnTheTimeline(TimelineLineageBase):
    def test_the_forked_lane_carries_its_branch_and_clips_the_copied_history(self):
        tl = km.build_timeline(self.now, tmux={})
        lane = self._lane(tl, CHILD)
        self.assertEqual(lane["branch"], {"fromId": PARENT, "t": self.fork_t, "cut": "a1"})
        starts = [b["start"] for b in tl["turns"][CHILD]]
        self.assertTrue(starts, "the child's own post-fork work still draws")
        self.assertTrue(all(st > self.fork_t for st in starts),
                        "copied pre-branch bars belong to the parent's lane: %r" % starts)
        self.assertTrue(any(b["start"] < self.fork_t for b in tl["turns"][PARENT]),
                        "the parent's lane still owns the shared history")

    def test_a_gone_parent_returns_the_whole_story_to_the_child(self):
        (jd.NAMES / PARENT).unlink()                 # the parent session is gone from the timeline
        jd._discover_cache.clear()
        tl = km.build_timeline(self.now, tmux={})
        self.assertIsNone(self._lane(tl, CHILD)["branch"],
                          "no parent lane, no connector endpoint")
        self.assertTrue(any(b["start"] < self.fork_t for b in tl["turns"][CHILD]),
                        "with the parent gone the child reads as one session all along")

    def test_the_skeleton_carries_lineage_without_parsing(self):
        tl = km.build_timeline(self.now, tmux={}, with_bars=False)
        self.assertEqual(self._lane(tl, CHILD)["branch"]["fromId"], PARENT)
        self.assertEqual(tl["turns"], {}, "the skeleton still parses nothing")

    def test_an_unforked_lane_carries_no_branch(self):
        tl = km.build_timeline(self.now, tmux={})
        self.assertIsNone(self._lane(tl, PARENT)["branch"])


class CommentSquares(TimelineLineageBase):
    def test_threads_ride_their_parent_lane_as_markers(self):
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1",
             "anchorT": self.now - 580, "exact": "exponential backoff", "status": "open",
             "createdT": self.now - 200, "lastSeenT": self.now},
            {"tid": "t2", "sid": "t2", "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "backoff", "status": "resolved",
             "createdT": self.now - 100, "lastSeenT": self.now},
            {"tid": "t3", "sid": "t3", "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "x", "status": "promoted", "promotedName": "sidework",
             "createdT": self.now - 50, "lastSeenT": self.now}]})
        tl = km.build_timeline(self.now, tmux={})
        marks = self._lane(tl, PARENT)["comments"]
        self.assertEqual([m["tid"] for m in marks], [THREAD, "t2"],
                         "open + resolved ride the lane; a promoted thread IS a session")
        self.assertEqual(marks[0]["t"], self.now - 580, "anchorT places the square at the message")
        self.assertEqual(marks[1]["t"], self.now - 100, "older rows fall back to createdT")
        self.assertEqual(marks[0]["uuid"], "a1")

    def test_a_lane_with_no_threads_pays_one_stat_and_carries_none(self):
        tl = km.build_timeline(self.now, tmux={})
        self.assertEqual(self._lane(tl, CHILD)["comments"], [])


if __name__ == "__main__":
    unittest.main()
