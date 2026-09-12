#!/usr/bin/env python3
"""The PASS FRAME (the user 2026-07-21): one frozen view of the evidence per judge pass, so every stage
judges the same world. Without it each stage read the live transcript at its own moment — a turn ending
mid-pass was invisible to the planner (stage 1) yet visible to the closer (stage 2), which swept the
freshly-ended turn against a tree the planner had not yet ruled on (the ui g139 stranded top). The frame
pins parsed_session from first touch to pass end (first touch wins across worker threads), pins the
caption memo's fileset key so a mid-pass write can't stamp stale tasks under a fresh key, and only
freezes EVIDENCE — goal/caption stores keep flowing (the closer must see this pass's planner verdicts).

The parse's KEY is pinned with the parse (2026-09-07; _frame_parse_key): the first toucher of a
session pins its (fileset key, cut) pair BEFORE reading, every later caller in the pass gets that pair,
and the caption memo keys on it, so a key can never be newer than the content it stands for. The cut the
parse runs under stays live; the pair a parse was actually served under is recorded beside it.
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_passframe", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class PassFrame(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td)
        (self.td / "judge-units-cache").mkdir(parents=True, exist_ok=True)
        self.path = self.td / (SID + ".jsonl")
        recs = [uline(T0, "start the work", "u1"),
                aline(T0 + 10, "Working on it now, first step underway.", "a1", "u1", stop="tool_use")]
        self.path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        jd.end_pass_frame(True)          # belt: never inherit a frame a crashed test left open
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()   # a test's cache-hit premise must not ride an earlier test's entry
        self._saved = (jd._PENDING_CUT_FN, jd.em.parse_session, jd._fileset_key)

    def tearDown(self):
        jd.end_pass_frame(True)
        jd._PENDING_CUT_FN, jd.em.parse_session, jd._fileset_key = self._saved

    def _append(self, rec):
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _states_row(self, t, state):
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.STATESDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": t, "state": state}) + "\n")

    def _live_pair(self):
        # the pair a frameless call computes: the candidates plus the states file when it exists, and the cut
        return (jd._fileset_key(jd._parse_key_files(SID, [str(self.path)])[2]), jd._pending_cut(SID))

    def _ended_work_tasks(self, tasks):
        return [t for t in tasks if t["kind"] == "work" and not t.get("live")]

    def test_frame_pins_the_parse_across_a_mid_pass_write(self):
        self.assertTrue(jd.begin_pass_frame())
        first = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertFalse(first["turns"][-1]["ended"], "the frame captured the turn still open")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        again = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(again, first, "every later look in the pass returns the SAME frozen parse")
        jd.end_pass_frame(True)
        fresh = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(fresh, first)
        self.assertTrue(fresh["turns"][-1]["ended"], "the next pass sees the ended turn, whole")

    def test_without_a_frame_the_live_world_shows_through(self):
        first = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        fresh = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(fresh, first, "no frame → each look re-reads reality (the pre-frame behavior)")

    def test_a_warm_first_touch_pins_the_cached_parse(self):
        # The cache-HIT path returned without pinning (found in review 2026-09-06): a session whose parse
        # was already in _PARSE_CACHE froze nothing under the frame, so a turn ending mid-pass was visible
        # to a later stage and invisible to an earlier one - the same two-worlds shape the frame exists
        # to prevent, for every warm session (idle sessions are warm nearly always).
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)     # frameless: fills the cache
        self.assertTrue(jd.begin_pass_frame())
        first = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(first, warm, "premise: the pass's first touch is a cache hit")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        again = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(again, first, "the hit was pinned: a later stage sees the SAME frozen parse")
        self.assertFalse(again["turns"][-1]["ended"], "the mid-pass append stays out of this pass")
        jd.end_pass_frame(True)
        fresh = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(fresh, first)
        self.assertTrue(fresh["turns"][-1]["ended"], "the next pass sees the ended turn, whole")

    def test_a_warm_cache_without_a_frame_still_reads_live(self):
        # the no-frame path is unchanged: an unchanged file hits the cache, a grown one re-parses at once
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(jd.parsed_session(SID, [str(self.path)], T0 + 100), warm,
                      "an unchanged file is served from the cache")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        fresh = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(fresh, warm)
        self.assertTrue(fresh["turns"][-1]["ended"], "no frame: the appended turn shows on the next look")

    def test_a_warm_hit_yields_to_a_concurrent_first_toucher(self):
        # first touch wins on the HIT path too: the pin is a setdefault, never an overwrite. A worker thread
        # that pinned the session between this caller's cache read and its lock take keeps its pin, and this
        # caller is handed the peer's parse, so no two stages of the pass ever hold different objects
        jd.parsed_session(SID, [str(self.path)], T0 + 100)             # frameless: fills the cache
        self.assertTrue(jd.begin_pass_frame())
        peer, peer_pair = {"turns": [], "peer": True}, ("peer-pair", "")

        class RacedCache(dict):
            # parsed_session reads the cache BEFORE it takes _frame_lock, so a peer pinning here is the
            # concurrent first toucher, and the lock is free to take
            def get(self, k, default=None):
                with jd._frame_lock:
                    jd._frame["parses"].setdefault(SID, peer)
                    jd._frame["served"][SID] = peer_pair
                return dict.get(self, k, default)
        real = jd._PARSE_CACHE
        jd._PARSE_CACHE = RacedCache(real)
        try:
            got = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        finally:
            jd._PARSE_CACHE = real
        self.assertIs(got, peer, "first touch wins: the hit path yields to the peer's pin")
        self.assertIs(jd._frame["parses"][SID], peer, "and the peer's pin stands")
        self.assertEqual(jd._frame["served"][SID], peer_pair, "the served pair stays the winner's")

    def test_frame_ownership_nests(self):
        self.assertTrue(jd.begin_pass_frame(), "first opener owns the frame")
        pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertFalse(jd.begin_pass_frame(), "a tier under the producer's frame joins, not owns")
        jd.end_pass_frame(False)          # the joiner's end is a no-op
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self.assertIs(jd.parsed_session(SID, [str(self.path)], T0 + 100), pinned,
                      "the frame survives a joiner's end — the mid-pass write stays invisible")
        jd.end_pass_frame(True)
        self.assertTrue(jd.parsed_session(SID, [str(self.path)], T0 + 100)["turns"][-1]["ended"],
                        "the owner's end unfreezes the world — the write shows on the next look")

    def test_caption_memo_key_rides_the_frame(self):
        # a transcript growing MID-PASS must not stamp the pass's (older) task list under the file's
        # NEW key — the next pass would cache-hit that key and never caption the growth
        self._append(aline(T0 + 20, "Finished the first stretch of work here.", "a2", "a1",
                           stop="end_turn"))
        self.assertTrue(jd.begin_pass_frame())
        v1 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        self.assertTrue(v1, "the ended turn yields caption tasks")
        self._append(uline(T0 + 200, "now do a second thing", "u2", "a2"))
        self._append(aline(T0 + 210, "Second thing finished and checked in.", "a3", "u2",
                           stop="end_turn"))
        self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 300), v1,
                         "inside the pass the frozen task list holds")
        jd.end_pass_frame(True)
        v2 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 300)
        ids = {w["id"] for t in v2 for w in t["writes"]}
        self.assertGreater(len(ids), len({w["id"] for t in v1 for w in t["writes"]}),
                           "the next pass captions the growth — the memo never went stale")

    # ── the parse KEY rides the frame with the parse ──

    def test_the_first_touch_pins_the_pair_of_the_files_as_they_were_before_the_read(self):
        self.assertTrue(jd.begin_pass_frame())
        before = self._live_pair()
        jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertEqual(jd._frame["keys"][("parse", SID)], before,
                         "the pair pinned is the files' key as they were BEFORE the parse read them")
        self.assertEqual(jd._frame["served"][SID], before, "and the parse was served under that same pair")

    def test_a_gate_pin_holds_the_pre_append_pair_while_the_parse_holds_the_appended_content(self):
        # a gate (the evidence gate's _stage_sig) pins the pair before the stage's first parse; an append in
        # between leaves the pinned pair alone and the parse reads the newer content: content newer than the
        # key (one redundant run next pass), never older (a missed run)
        self.assertTrue(jd.begin_pass_frame())
        pair, cut, fr = jd._frame_parse_key(SID, [str(self.path)])     # standing in for the gate
        self.assertEqual(cut, "", "a first toucher reads the live cut")
        self.assertIs(fr, jd._frame, "and is told which frame it pinned into")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertTrue(sess["turns"][-1]["ended"], "the parse holds the appended content")
        self.assertEqual(jd._frame["keys"][("parse", SID)], pair, "the pinned pair stays at the pre-append value")
        self.assertEqual(jd._frame["served"][SID], pair, "the parse was served under the pinned pair")
        self.assertEqual(jd._PARSE_CACHE[SID][0], pair, "the cache slot is the pinned pair")
        again = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(again, sess, "every later look in the pass returns the pinned parse")
        jd.end_pass_frame(True)
        fresh = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIsNot(fresh, sess, "the entry stored under the pre-append pair is never served for the grown file's key")
        self.assertEqual(jd._PARSE_CACHE[SID][0], self._live_pair(), "the frameless look re-keys the cache on the live pair")

    def test_the_parse_runs_under_the_live_cut_and_the_served_pair_records_it(self):
        # the cut rule (the user's call, 2026-09-07): the judged world is the LIVE cut's, as before the frame
        # existed; a cut that arms between a gate's pin and the stage's parse shows as a served pair that
        # differs from the pinned one (the gate then withholds its stamp), and the cache slot carries the cut
        # the parse was made under
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self.assertTrue(jd.begin_pass_frame())
        pair, _cut, _fr = jd._frame_parse_key(SID, [str(self.path)])
        self.assertEqual(pair[1], "", "premise: no cut when the gate pinned")
        jd._PENDING_CUT_FN = lambda fsid: "a1"                            # a bare rollback arms, cutting a2 away
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertEqual([a["uuid"] for t in sess["turns"] for a in t["atoms"] if a.get("uuid")], ["u1", "a1"],
                         "the parse honours the live cut: the rolled-back tail is not judged")
        served = jd._frame["served"][SID]
        self.assertEqual(served, (pair[0], "a1"), "the served pair carries the cut the parse was made under")
        self.assertNotEqual(served, jd._frame["keys"][("parse", SID)], "and differs from the pinned pair")
        self.assertEqual(jd._PARSE_CACHE[SID][0], served, "the cache slot is (pinned fileset key, live cut)")

    def test_without_a_frame_the_live_pair_is_returned_and_nothing_is_pinned(self):
        pair, cut, fr = jd._frame_parse_key(SID, [str(self.path)])
        self.assertEqual(pair, self._live_pair())
        self.assertEqual(cut, "")
        self.assertIsNone(fr)
        self.assertIsNone(jd._frame, "no frame was opened")

    def test_a_failed_stat_pins_none_for_the_whole_pass(self):
        # the tag is PRESENT with None: a later caller in the same pass whose stat would succeed still gets
        # None, so no fresh key is ever pinned over a parse that was read earlier (the keyless-parse hole)
        self.assertTrue(jd.begin_pass_frame())
        real = jd._fileset_key

        def vanished(files):
            raise OSError("a candidate vanished between the exists() and the stat")
        jd._fileset_key = vanished
        pair, cut, fr = jd._frame_parse_key(SID, [str(self.path)])
        jd._fileset_key = real
        self.assertIsNone(pair)
        self.assertEqual(cut, "", "the cut was read (the pin is the first toucher's)")
        self.assertIn(("parse", SID), fr["keys"], "the tag is present ...")
        self.assertIsNone(fr["keys"][("parse", SID)], "... with value None")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        pair2, cut2, _fr = jd._frame_parse_key(SID, [str(self.path)])
        self.assertIsNone(pair2, "a later call whose stat would succeed still returns the pinned None")
        self.assertIsNone(cut2, "and reads nothing")
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertNotIn(SID, jd._PARSE_CACHE, "the parse ran uncached")
        self.assertIs(jd._frame["parses"][SID], sess, "but is pinned under the frame")
        self.assertIsNone(jd._frame["served"][SID], "served under no pair: the gate reads this as run-and-do-not-stamp")

    def test_the_key_and_the_parse_pin_into_the_same_frame(self):
        # a parse can span a pass boundary (a pusher tick job's parse while the producer ends frame A and
        # begins frame B): the parse must land in the frame its key went into, never keyless in the next one
        real = jd.em.parse_session
        crossed = {}

        def boundary_inside(*a, **k):
            crossed["a"] = jd._frame
            jd.end_pass_frame(True)
            jd.begin_pass_frame()
            crossed["b"] = jd._frame
            return real(*a, **k)
        jd.em.parse_session = boundary_inside
        self.assertTrue(jd.begin_pass_frame())
        sess = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        a, b = crossed["a"], crossed["b"]
        self.assertIsNot(a, b)
        self.assertIn(("parse", SID), a["keys"]); self.assertIs(a["parses"].get(SID), sess)
        self.assertNotIn(("parse", SID), b["keys"], "the new frame holds no key for the session ...")
        self.assertNotIn(SID, b["parses"], "... and no parse: neither, never a parse alone")

    def test_a_fork_lane_keys_the_frame_and_the_frameless_cache_alike(self):
        # a /clear fork lane's leaf is not the anchor file: _judge_candidates adds the anchor once; the frame's
        # key is computed from the RAW leaf list exactly as the frameless cache key is, so the two agree
        fork = self.td / "99999999-8888-7777-6666-555555555555.jsonl"
        fork.write_text("\n".join(json.dumps(r) for r in [
            uline(T0 + 500, "fresh start after the clear", "u9"),
            aline(T0 + 510, "Picking it up from here.", "a9", "u9", stop="end_turn")]) + "\n")
        warm = jd.parsed_session(SID, [str(fork)], T0 + 600)             # frameless: fills the cache
        frameless_key = jd._PARSE_CACHE[SID][0]
        self.assertEqual(len(frameless_key[0]), 2, "premise: the leaf plus the anchor, once")
        self.assertTrue(jd.begin_pass_frame())
        pair, _cut, _fr = jd._frame_parse_key(SID, [str(fork)])
        self.assertEqual(pair, frameless_key, "the framed key equals the frameless one")
        self.assertIs(jd.parsed_session(SID, [str(fork)], T0 + 600), warm, "so the pass's first touch is a cache hit")

    def test_a_warm_hit_records_its_served_pair_and_holds_when_the_slot_moves(self):
        # the hit path's pin (the warm-first-touch case above) is what writes fr["served"] for a warm session,
        # which the evidence gate's served-pair check reads, and what keeps answering when the cache slot moves
        # under the pass. With the KEY pinned too, an unchanged slot answers the same object with or without
        # the pin, so this case moves the slot between the touches: a wholesale clear (the overflow eviction)
        # and a bare rollback arming mid-pass (the live cut is part of the slot's key)
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)     # frameless: fills the cache
        self.assertTrue(jd.begin_pass_frame())
        first = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(first, warm, "premise: the pass's first touch is a cache hit")
        self.assertIs(jd._frame["parses"][SID], first, "the hit is pinned")
        self.assertEqual(jd._frame["served"][SID], jd._frame["keys"][("parse", SID)],
                         "and recorded as served under the pinned pair, which the gate's served-pair check reads")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()                                          # the overflow eviction
        again = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(again, first, "the slot is gone: the pinned parse still answers")
        jd._PENDING_CUT_FN = lambda fsid: "a1"                            # a bare rollback arms mid-pass
        again = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        self.assertIs(again, first, "the live cut moved the slot's key: the pinned parse still answers")
        self.assertFalse(again["turns"][-1]["ended"], "the mid-pass append stays out of this pass")

    def test_a_losing_parse_never_overwrites_the_winners_served_pair(self):
        # two readers race a first touch on the miss path; a bare rollback arms between them. The
        # competitor parses under the live cut, pins first and records served=(K, cut). The outer reader
        # parsed under the pre-cut world (its pair equals the pinned one); it loses and must leave
        # fr["served"] alone: were the loser's pair recorded, the gate would read a served pair equal to the
        # pinned one and stamp the cut world as if it were the pinned one
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        real, competitor = jd.em.parse_session, {}

        def race(*a, **k):
            jd.em.parse_session = real                                   # the competitor parses for real
            jd._PENDING_CUT_FN = lambda fsid: "a1"                       # a bare rollback arms between the two
            competitor["parse"] = jd.parsed_session(SID, [str(self.path)], T0 + 100)   # pins FIRST
            return real(*a, **k)                                         # the outer's own parse, pre-cut
        jd.em.parse_session = race
        self.assertTrue(jd.begin_pass_frame())
        got = jd.parsed_session(SID, [str(self.path)], T0 + 100)
        pinned = jd._frame["keys"][("parse", SID)]
        self.assertEqual(pinned[1], "", "premise: the pair was pinned before the cut armed")
        self.assertIs(got, competitor["parse"], "the outer reader lost and is handed the winner's parse")
        self.assertIs(jd._frame["parses"][SID], competitor["parse"])
        served = jd._frame["served"][SID]
        self.assertEqual(served, (pinned[0], "a1"), "the served pair is the WINNER's: the cut it parsed under")
        self.assertNotEqual(served, pinned, "so a gate reading it withholds its stamp")

    def test_a_pair_that_cannot_be_computed_memoizes_nothing(self):
        # a failed stat pins None for the pass; tasks_for then answers [] and writes no memo, because a memo
        # under a None key would be served to nobody (and one written under a made-up key would be trusted)
        self.assertTrue(jd.begin_pass_frame())
        real = jd._fileset_key

        def vanished(files):
            raise OSError("a candidate vanished between the exists() and the stat")
        jd._fileset_key = vanished
        self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100), [])
        self.assertFalse((jd.PCACHE / (SID + ".json")).exists(), "no memo is written under a None key")
        jd._fileset_key = real
        self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100), [],
                         "the pinned None holds for the pass: nothing is served, nothing memoized")
        self.assertFalse((jd.PCACHE / (SID + ".json")).exists())

    # ── the caption memo keys on the pinned pair (tasks_for re-keyed on the frame's pair) ──

    def test_the_caption_memo_keys_on_the_gate_pinned_pair_not_its_own_stat(self):
        # the last turn is OPEN when the gate pins; the turn's final record and the idle row land; the index
        # tier's tasks_for must memo under the PRE-append pair, so the next pass (live key K1 != K0) recomputes
        # and queues the turn's final caption. A memo keyed on its own live stat wrote {key: K1, tasks: K0's}
        # and the final caption was never queued until the transcript moved again.
        self.assertTrue(jd.begin_pass_frame())
        pair, _cut, _fr = jd._frame_parse_key(SID, [str(self.path)])     # the gate's pin, before any read
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self._states_row(T0 + 61, "idle")
        jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        o = json.loads((jd.PCACHE / (SID + ".json")).read_text())
        self.assertEqual(o["key"], json.loads(json.dumps(list(pair))), "the memo's key is the pre-append pair")
        jd.end_pass_frame(True)
        v2 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 300)
        self.assertTrue(self._ended_work_tasks(v2), "the next pass queues the ended turn's work caption")
        self.assertNotEqual(json.loads((jd.PCACHE / (SID + ".json")).read_text())["key"], o["key"],
                            "under the live key, which the memo now carries")

    def test_a_warm_pass_thread_first_touch_then_an_append_then_the_caption_memo(self):
        # the index-tier variant: a pass thread's parsed_session is the pass's first toucher on a cache HIT (the
        # turn open), the final record lands, then tasks_for. The frozen world (turn open) is what the memo
        # holds, under the frozen key; the next pass sees the ended turn whole. (A pusher tick job's touch is
        # not this case: it is not a pass thread and pins nothing, see the two tests below.)
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)     # frameless: fills the cache, turn open
        self.assertTrue(jd.begin_pass_frame())
        self.assertIs(jd.parsed_session(SID, [str(self.path)], T0 + 100), warm, "premise: a cache hit pins")
        pinned = jd._frame["keys"][("parse", SID)]
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self._states_row(T0 + 61, "idle")
        v1 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        self.assertFalse(self._ended_work_tasks(v1), "the frozen world: the turn is still open, no final caption yet")
        o = json.loads((jd.PCACHE / (SID + ".json")).read_text())
        self.assertEqual(o["key"], json.loads(json.dumps(list(pinned))), "memoized under the frozen pair")
        jd.end_pass_frame(True)
        v2 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 300)
        self.assertTrue(self._ended_work_tasks(v2), "the next pass captions the ended turn: nothing dropped")

    def _elsewhere(self, fn, *a):
        """Run fn on a fresh plain thread (the kernel's pusher, say): no pass mark, whatever the frame's state."""
        out, err = [], []

        def run():
            try:
                out.append(fn(*a))
            except BaseException as e:               # surfaced on the test thread, never swallowed by the worker
                err.append(e)
        t = threading.Thread(target=run, name="pusher")
        t.start(); t.join(10)
        self.assertFalse(t.is_alive(), "the thread finished")
        if err:
            raise err[0]
        return out[0]

    def test_a_thread_outside_the_pass_reads_live_and_pins_nothing(self):
        # the pin is scoped to PASS THREADS (review find, 2026-09-08): the frame is a module global, so before
        # this a pusher tick job's first touch, warm or cold, pinned that job to the pass-start world for as
        # long as the tiers ran, model calls included. A thread that neither opened nor joined the frame nor
        # runs in a judge pool sees no frame: it reads the live file at every call and leaves no pin behind.
        warm = jd.parsed_session(SID, [str(self.path)], T0 + 100)     # frameless: fills the cache, turn open
        self.assertTrue(jd.begin_pass_frame())
        self.assertIs(jd._pass_frame(), jd._frame, "the opener is a pass thread")
        self.assertIsNone(self._elsewhere(jd._pass_frame), "a plain thread is not")
        self.assertIs(self._elsewhere(jd.parsed_session, SID, [str(self.path)], T0 + 100), warm,
                      "a cache hit from outside the pass answers the cached parse...")
        self.assertNotIn(SID, jd._frame["parses"], "...and pins nothing")
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        live = self._elsewhere(jd.parsed_session, SID, [str(self.path)], T0 + 100)
        self.assertTrue(live["turns"][-1]["ended"], "outside the pass the mid-pass append shows through")
        self.assertNotIn(SID, jd._frame["parses"], "still nothing pinned")
        pinned = jd.parsed_session(SID, [str(self.path)], T0 + 100)   # the pass thread's own first touch
        self.assertIs(jd._frame["parses"].get(SID), pinned, "the pass thread pins (the live parse the cache now holds)")
        self.assertIs(pinned, live)
        jd.end_pass_frame(True)
        self.assertIsNone(jd._pass_frame(), "the end unmarks the caller")

    def test_a_pool_worker_is_a_pass_thread_and_a_joiner_too(self):
        # the tiers fan their per-session work through this module's pools; a worker pins into the frame like
        # the tier thread that submitted it (the _TimedPool mark), and a thread that JOINS the frame
        # (begin_pass_frame answering False: run_index and run_triage under the kernel producer) is a pass
        # thread from then on
        self.assertTrue(self._elsewhere(jd.begin_pass_frame), "the producer's thread opens the frame")
        self.assertIsNone(jd._pass_frame(), "this thread has not joined: not a pass thread yet")
        with jd.ThreadPoolExecutor(max_workers=1) as ex:
            got = ex.submit(jd.parsed_session, SID, [str(self.path)], T0 + 100).result()
            self.assertIs(jd._frame["parses"].get(SID), got, "a pool worker's touch pins")
            self.assertIs(ex.submit(jd._pass_frame).result(), jd._frame)
        self.assertFalse(jd.begin_pass_frame(), "this thread joins the open frame")
        self.assertIs(jd._pass_frame(), jd._frame, "a joiner is a pass thread")
        self.assertIs(jd.parsed_session(SID, [str(self.path)], T0 + 100), got, "and reads the pinned world")
        jd.end_pass_frame(True)

    def test_tier_entries_and_producer_are_frame_wrapped(self):
        jsrc = open(jd.__file__).read()
        for fn in ("def run_triage", "def run_index"):
            body = jsrc.split(fn, 1)[1]
            self.assertLess(body.find("begin_pass_frame()"), body.find("def ", 10),
                            "%s opens (or joins) a pass frame before any stage runs" % fn)
        ksrc = open(os.path.join(os.path.dirname(BIN), "kernel", "kernel.py")).read()
        self.assertIn("_own_frame = jd.begin_pass_frame()", ksrc,
                      "the kernel producer pins ONE frame for both tiers")
        self.assertIn("jd.end_pass_frame(_own_frame)", ksrc,
                      "and ends it (join path plus the finally safety net)")
        self.assertEqual(ksrc.count("jd.end_pass_frame(_own_frame)"), 2,
                         "normal-path end after the join AND the leak-proof finally")



class PlannerSkipsCaptioned(PassFrame):
    """T358: tasks_for plans the UNCAPTIONED units only and reads no unit text for a captioned one; its memo file keys on
    the captions file beside the transcript pair, so a caption filed since misses it with no transcript change, while a
    strike (which files no caption) leaves the memo, and the unit, in place."""
    def _finish(self):
        self._append(aline(T0 + 60, "All done: shipped and verified.", "a2", "a1", stop="end_turn"))
        self._states_row(T0 + 61, "idle")

    def test_a_caption_filed_after_the_memo_drops_the_unit_with_no_transcript_change(self):
        self._finish()
        v1 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        work = self._ended_work_tasks(v1)
        self.assertTrue(work, "the ended turn's work caption is planned")
        o1 = json.loads((jd.PCACHE / (SID + ".json")).read_text())
        for w in work[0]["writes"]:
            jd.append_caption(SID, w["id"], w["grain"], w["t"], "Shipped the fix.")
        v2 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        self.assertEqual(self._ended_work_tasks(v2), [], "the captioned unit is gone from the plan")
        self.assertEqual([t["kind"] for t in v2], ["prompt"], "the uncaptioned prompt unit stays")
        o2 = json.loads((jd.PCACHE / (SID + ".json")).read_text())
        self.assertEqual(o2["key"], o1["key"], "the transcript pair did not change")
        self.assertNotEqual(o2["capKey"], o1["capKey"], "the captions file did: the memo was rewritten")

    def test_a_strike_after_the_memo_keeps_the_unit_planned(self):
        self._finish()
        v1 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        work = self._ended_work_tasks(v1); self.assertTrue(work)
        task = {"fsid": SID, "writes": work[0]["writes"]}
        jd._caption_strike(task, set(), {})                        # one empty capture: no caption filed
        v2 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        self.assertEqual([t["writes"] for t in self._ended_work_tasks(v2)], [work[0]["writes"]], "still planned, from the memo")
        for _ in range(jd.CAPTION_FAIL_CAP):
            jd._caption_strike(task, set(), {})                    # ...until the cap files the tombstone caption
        v3 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        self.assertEqual(self._ended_work_tasks(v3), [], "a tombstoned unit is captioned: dropped from the plan")

    def test_an_unreadable_captions_file_plans_nothing_for_that_session_and_raises_nothing(self):
        """The captions file exists but will not stat (EACCES, ENOTDIR, EIO): _file_key's sentinel is no key; tasks_for answers []
        for that session instead of raising out of the whole pass (every other session's captions proceed)."""
        self._finish()
        cap = jd.CAPDIR / (SID + ".jsonl")
        real = jd._file_key
        jd._file_key = lambda p: object() if p == str(cap) else real(p)
        n0 = jd._CAPTIONS_STATS["unstatable"]; jd._SAID_ONCE.clear()
        import contextlib, io
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100), [])
                self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100), [])
        finally:
            jd._file_key = real
        self.assertEqual(jd._CAPTIONS_STATS["unstatable"], n0 + 2, "counted per pass")
        self.assertEqual(err.getvalue().count("cannot be stat'ed"), 1, "said once, loudly")
        self.assertFalse((jd.PCACHE / (SID + ".json")).exists(), "no memo for a pass that planned nothing")
        self.assertTrue(self._ended_work_tasks(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)), "the next pass plans")

    def test_a_captioned_unit_is_skipped_before_its_text_is_read(self):
        self._finish()
        v1 = jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100)
        done = {w["id"] for t in v1 for w in t["writes"]}
        calls = []
        saved = jd._unit_text, jd._prompt_text
        jd._unit_text = lambda atoms, *a, **k: calls.append("work") or ""
        jd._prompt_text = lambda atoms, *a, **k: calls.append("prompt") or ""
        try:
            (jd.PCACHE / (SID + ".json")).unlink()
            self.assertEqual(jd.tasks_for(SID, str(self.path), [str(self.path)], T0 + 100, done=done), [])
        finally:
            jd._unit_text, jd._prompt_text = saved
        self.assertEqual(calls, [], "no unit text was built for a captioned unit")

if __name__ == "__main__":
    unittest.main()
