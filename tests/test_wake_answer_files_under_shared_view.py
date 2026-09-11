#!/usr/bin/env python3
"""An answered wake files its outcome row whatever store its caller holds (2026-09-09).

The nudge walk (_auto_nudge_session) reads a session's goal store through the shared read-only view
(kernel/judge.py load_goals_shared_or_fault) and hands that view to _wake_goal. When the judges have
ruled on a wake's answer, the answered leg records answeredAt and files the answer into the goal's
diary (_file_wake_answer): that row is the event the closer's filed-since gate reads, so it re-audits
with the answer in view. The filing writes (record_verdict, rollup_status, save_goals), so it must take
its own writer load of the store: a write through the frozen view raises FrozenStoreError inside the
helper's own catch, and the caller sees only a False return. Before the fix, the first answered wake
per kernel process filed no row and never retried, a frozen-store-write judge-errors row was filed,
and the shared cache switched itself off until the next restart (regression:
https://github.com/romp-on/romp/pull/1141; the other caller, _awaiting_wake_outcomes, already loaded
a writer copy and was unaffected).

The tests here drive the real path: a file-backed store under a private synthetic sid, read by the
REAL shared loader (never stubbed), the walk's own store binding, and the answered leg with the
response judgment stubbed to "answered and ruled" (the response gates are pinned elsewhere; the
mechanism under test is the write moment). SYNTHETIC fixtures only."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wakeans", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                        # the kernel's OWN judge instance: the cache, its off
#                                                   switch and the error log live there

SID = "7e7e7e7e-1111-4222-8333-0000000000a7"     # private to this module (synthetic)
GID = SID + ":g1"
NOW = 1_790_000_000
ANCHOR = NOW - 20 * 3600                          # the closer's stamp: a wait older than the dead-man
FIRE = NOW - 7 * 3600                             # the wake went out
ANSWER = NOW - 6 * 3600                           # the response the judges ruled on
WHY = "the build it dispatched; reports when it returns"


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append((sid, body))


def _stamped_node():
    return {"id": GID, "text": "a goal", "parentId": None, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100,
            "awaitingWhy": WHY, "awaitingAt": ANCHOR,
            "log": [{"ev_t": ANCHOR, "src": "closer", "kind": "awaiting", "why": WHY, "at": 1}]}


def _wake_in_flight():
    return {"wake": True, "anchor": ANCHOR, "count": 1, "lastTurnId": "t1", "armAtoms": 0, "at": FIRE}


class AnsweredWakeFilesWhenItsCallerHoldsTheSharedView(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved_state = jd.STATE
        jd._rebind_state(td)                      # every state path, the shared cache cleared, its off switch lifted
        jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.saved = {k: getattr(km, k) for k in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working",
            "_interrupt_suppresses_nudge", "_backend_queued", "_backend_rewind_pending", "_last_state",
            "_session_awaiting", "_closer_settled", "_revivers_pending", "_pending_ops",
            "_all_outstanding_delegated", "_peer_answered_at", "_nudge_response_ready")}
        self.saved_jd = {k: getattr(jd, k) for k in ("parsed_session", "_segs", "plan_units")}
        self.saved_backend = km.Sessions.backend_for
        # the session-level gates read idle-and-settled, so the walk reaches the goal loop
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: None
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a, **k: ""
        km._pending_ops = {}
        km._all_outstanding_delegated = lambda nodes, gid: False
        km._peer_answered_at = lambda sid: 0
        # the judges ruled on the wake's response: the answered leg is the one under test
        km._nudge_response_ready = lambda *a, **k: (True, {"id": "s9", "t": ANSWER})
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        self.turns = [{"id": "t1", "ended": True, "end": 100, "t": 90, "atoms": []}]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.fb = _FakeBackend()
        km.Sessions.backend_for = lambda sid: self.fb
        self.live = {SID: {"state": ""}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {GID: "working"},
            "nodes": {GID: _stamped_node()}}))
        (td / "auto-nudge.json").write_text(json.dumps({"enabled": True, "nudged": {GID: _wake_in_flight()}}))
        self.stats0 = jd.shared_store_stats()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        for k, v in self.saved_jd.items():
            setattr(jd, k, v)
        km.Sessions.backend_for = self.saved_backend
        journal = jd._overrides_dir() / (SID + ".jsonl")   # this sid's override journal goes with the class
        if journal.exists():
            journal.unlink()
        jd._rebind_state(self.saved_state)      # also clears the cache and lifts an off switch this class tripped
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.td.cleanup()

    def _delta(self, key):
        return jd.shared_store_stats()[key] - self.stats0[key]

    def _frozen_write_rows(self):
        try:
            rows = [json.loads(l) for l in jd.ERRORS.read_text().splitlines() if l.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if r.get("err") == "frozen-store-write"]

    def _filed_answers(self):
        nd = jd.load_goals(SID)["nodes"][GID]
        return nd, [e for e in nd["log"] if e.get("src") == "nudge" and e.get("kind") == "awaiting"
                    and not e.get("lift")]

    def _assert_answer_filed_cleanly(self, stderr):
        rec = km._auto_nudge_data()["nudged"][GID]
        self.assertEqual(rec.get("answeredAt"), ANSWER, "the answered leg ran: the record re-arms from the answer")
        nd, filed = self._filed_answers()
        st = jd.shared_store_stats()
        self.assertEqual(len(filed), 1, "the answer is a FILED event in the goal's diary (shared cache off=%d, "
                         "poisoned=%d, frozen-store-write rows=%d); helper stderr: %r"
                         % (st["off"], st["poisoned"] - self.stats0["poisoned"], len(self._frozen_write_rows()), stderr))
        self.assertEqual(filed[0].get("ev_t"), ANCHOR, "filed at the stamp's own anchor (the coalesce rule)")
        self.assertEqual(filed[0].get("why"), WHY)
        self.assertEqual(nd.get("awaitingAt"), ANCHOR, "the anchor never moves")
        look = {"closerLookT": nd["log"][0]["at"]}    # the closer last looked when the stamp was filed
        self.assertTrue(jd._filed_since({GID: dict(nd, **look)}, {None: [GID]}, GID,
                                        jd._look_stamp(dict(nd, **look))),
                        "the row re-nominates the closer: its filed-since gate opens on the answer")
        self.assertEqual(jd.shared_store_stats()["off"], 0, "no write reached the shared read-only view")
        self.assertEqual(self._delta("poisoned"), 0)
        self.assertEqual(self._frozen_write_rows(), [], "no frozen-store-write row was filed")
        self.assertEqual(self.fb.sent, [], "an answered wake sends nothing")

    def test_the_walk_files_the_answer_it_read_through_the_shared_view(self):
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            out = km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"}, NOW, self.live, nudged, {})
        self.assertNotIsInstance(out, str, "a session gate held the walk before the goal loop: %r" % (out,))
        self.assertGreaterEqual(self._delta("miss") + self._delta("hit"), 1,
                                "fixture: the walk's store read went through the shared loader")
        self._assert_answer_filed_cleanly(err.getvalue())

    def test_the_answered_leg_files_when_handed_the_view_itself(self):
        # the walk's binding, taken here by hand: the shared read-only view object is what _wake_goal holds
        store, fault = jd.load_goals_shared_or_fault(SID)
        self.assertIsNone(fault)
        self.assertIsInstance(store, jd.FrozenStore, "fixture: the store is the shared view, not a writer copy")
        stamp = km._goal_awaiting_stamp_full(store.get("nodes", {}), GID)
        self.assertIsNotNone(stamp, "fixture: the goal is stamped")
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            fired = km._wake_goal(SID, GID, stamp, nudged, self.turns, store, NOW, self.turns[-1], self.live)
        self.assertFalse(fired, "an answered wake never escalates")
        self._assert_answer_filed_cleanly(err.getvalue())
        self.assertEqual(len(store["nodes"][GID]["log"]), 1, "the view object itself is untouched")

    def test_a_second_answered_wake_in_the_same_process_files_too(self):
        # the failure mode was per process: the first answered wake tripped the cache off, and every later
        # one was served a private copy and filed. Two answers in one process must both land, and the
        # cache must still be on after both.
        for n in (1, 2):
            (Path(self.td.name) / "auto-nudge.json").write_text(json.dumps(
                {"enabled": True, "nudged": {GID: _wake_in_flight()}}))
            km._autonudge_cache.clear()
            nudged = dict(km._auto_nudge_data().get("nudged", {}))
            before = jd.shared_store_stats()
            with contextlib.redirect_stderr(io.StringIO()):
                km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"}, NOW, self.live, nudged, {})
            after = jd.shared_store_stats()
            self.assertGreaterEqual((after["miss"] + after["hit"]) - (before["miss"] + before["hit"]), 1,
                                    "fixture: walk %d read the store through the shared loader" % n)
            nd, filed = self._filed_answers()
            self.assertEqual(len(filed), n, "answer %d filed its row" % n)
            self.assertEqual(after["off"], 0, "the shared cache is still on after answer %d" % n)
            self.assertEqual(after["poisoned"], before["poisoned"], "answer %d wrote nothing through the view" % n)
        self.assertEqual(self._frozen_write_rows(), [])


if __name__ == "__main__":
    unittest.main()
