#!/usr/bin/env python3
"""A goal's ⏳ awaiting stamp is RETIRED once the dispatches it was waiting on return (the user
2026-07-22).

The closer's own lift is bounded to the goals a turn actually WORKED ON (`touched`) — correct for goals
merely riding the menu, but it means a goal the session ABANDONS keeps its stamp forever. Live case: a goal
stamped "waiting on two dispatched investigations" at 12:26; both task-notifications landed by 12:31; the
session went idle at 12:32 and filed its later work under other goals, so no closer pass revisited it. Four
and a half hours later the card still claimed the wait with an empty task list behind it.

_lift_spent_awaiting keys on the EVENT, never a timer: the notification that answered each dispatch is in
the transcript and _scan_bg_tasks already pairs launches to results. It is SELF-SCOPING — it lifts only
when the goal itself dispatched background work by stamp time and all of it came back — so a stamp naming
a CI run, a scheduled check-back or a peer handoff owns no such dispatches, never matches, and keeps its
stamp (those remain the 6h backstop's job, the one case a timer is the only tool for).

SYNTHETIC fixtures only: placeholder UUIDs, invented task descriptions.
"""
import contextlib
import errno
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = SourceFileLoader("romp_kernel_awlift", os.path.join(BIN, "romp-kernel")).load_module()

SID = "11111111-2222-3333-4444-999999999999"
BORN, LAUNCH, STAMP, BACK = 100, 200, 300, 400      # goal minted / dispatched / stamped / result landed


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _launch(tid, t):
    """An async Agent dispatch ack — the durable 'this work is now running' record."""
    return {"type": "user", "timestamp": _iso(t), "uuid": "u" + tid, "parentUuid": None,
            "toolUseResult": {"status": "async_launched", "description": "a dispatched investigation"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                     "content": "launched"}]}}


def _notification(tid, t):
    """The standalone <task-notification> user record that ENDS the wait (the dominant live shape)."""
    body = ("<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<status>completed</status>\n<summary>the investigation finished</summary>\n"
            "</task-notification>" % (tid, tid))
    return {"type": "user", "timestamp": _iso(t), "uuid": "n" + tid, "parentUuid": None,
            "message": {"role": "user", "content": body}}


def _monitor(tid, t, timeout_ms=300000):
    """A non-persistent Monitor launch — the watcher shape; expires at t + timeout + grace with no
    terminal record when its CLI dies mid-watch (em._bg_expired)."""
    return {"type": "assistant", "timestamp": _iso(t), "uuid": "m" + tid, "parentUuid": None,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Monitor", "input": {"timeout_ms": timeout_ms}}]}}


def _clear_placement_memos():
    """The launch-placement memos key on (sid, tool_use id) and on object identity; the sid and the ids
    repeat across tests, so a positive learned from one test's transcript must not answer the next. The
    shared read-only cache is keyed per store path (a fresh tempdir each test) and compares bytes, so it
    cannot serve a stale store; cleared anyway, with its off switch lifted, so no test starts poisoned."""
    km._task_seg_cache.clear(); km._BG_TOPS_CACHE.clear(); km._PLACEMENT_IDX.clear()
    km.jd._shared_clear()


class AwaitingLift(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in ("_alive_sessions", "_mark_views_dirty")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        self.path = str(td / (SID + ".jsonl"))
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        km._SESSION_STAMP_CACHE.clear()
        km._lift_seen.clear()          # stores are re-seeded in place under recycled tempdir inodes
        km._bgall_cache.clear()
        km._bgtasks_cache.clear()
        _clear_placement_memos()
        self.gid = SID + ":g1"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._SESSION_STAMP_CACHE.clear(); km._lift_seen.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        _clear_placement_memos()
        self.td.cleanup()

    def _transcript(self, recs):
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def _seed(self, why="waiting on two dispatched investigations; will act when they return",
              born=BORN, anchor=STAMP, written=None, kind=None):
        """`anchor` is awaitingAt (the audited turn's TRIGGER time); `written` is when the closer actually
        wrote the verdict (its `at`), which defaults to the anchor for the pre-2026-07-27 fixture shape."""
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": born, "mt": born,
              "awaitingWhy": why, "awaitingAt": anchor,
              **({"awaitingKind": kind} if kind else {}),
              "log": [{"ev_t": anchor, "src": "closer", "kind": "awaiting", "why": why,
                       **({"awaitKind": kind} if kind else {}),
                       "at": anchor if written is None else written}]}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))

    def _tick(self, now=BACK + 100):
        km._lift_spent_awaiting(now, {SID: {"state": ""}})

    def _stamp(self):
        nodes = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        return nodes[self.gid].get("awaitingWhy") or None

    # ---- the bug ----
    def test_both_dispatches_returned_lifts_the_stamp(self):
        self._transcript([_launch("t1", LAUNCH), _launch("t2", LAUNCH + 5),
                          _notification("t1", BACK), _notification("t2", BACK + 5)])
        self._seed()
        self.assertIsNotNone(self._stamp(), "precondition: the goal starts stamped")
        self._tick()
        self.assertIsNone(self._stamp(), "every dispatch came back → the wait is over")

    def test_a_faulting_store_forgets_the_gate_so_the_next_tick_retries(self):
        """The inputs gate (_lift_seen) is recorded before the store read; a read FAULT must forget it,
        exactly as a raised ruling does, or the next tick over the same inputs would skip the session
        and the lift would wait for the transcript to change."""
        self._transcript([_launch("t1", LAUNCH), _launch("t2", LAUNCH + 5),
                          _notification("t1", BACK), _notification("t2", BACK + 5)])
        self._seed()
        km._lift_seen.pop(SID, None)
        km.jd._STORE_FAULTS.pop(SID, None)
        target, orig = km.jd.GOALDIR / (SID + ".json"), Path.read_text

        def faulting(path, *a, **kw):
            if path == target:
                raise OSError(errno.EIO, "Input/output error", str(path))
            return orig(path, *a, **kw)
        with mock.patch.object(Path, "read_text", faulting):
            self._tick()
        self.assertIsNotNone(self._stamp(), "nothing is written to a store that could not be read")
        self.assertNotIn(SID, km._lift_seen, "the gate is forgotten on a fault, not spent...")
        self._tick()
        self.assertIsNone(self._stamp(), "...so the very next tick, same inputs, retries and lifts")

    def test_one_still_running_keeps_the_stamp(self):
        self._transcript([_launch("t1", LAUNCH), _launch("t2", LAUNCH + 5),
                          _notification("t1", BACK)])          # t2 never reported
        self._seed()
        self._tick()
        self.assertIsNotNone(self._stamp(), "one dispatch is still out → still genuinely awaiting")

    # ---- kind=job: the watcher is the CARRIER, not the wait (the user 2026-08-15) ----
    def test_a_job_stamps_expired_watcher_does_not_lift_it(self):
        # the observed slurm shape: a watcher armed over an external job dies with a restart (no
        # terminal record, expires past its deadline) — the JOB may still be running, so the stamp
        # stands; the 6h wake is the backstop, per the lift's own design note
        self._transcript([_monitor("t1", LAUNCH)])   # deadline LAUNCH+300s; no terminal record
        self._seed(why="slurm 4821 regenerating the parts; verifies when done", kind="job")
        self._tick(now=LAUNCH + 1000)                # well past deadline + grace → expired
        self.assertIsNotNone(self._stamp(), "a dead watcher is not the external job returning")

    def test_the_same_expired_watcher_lifts_a_kindless_stamp_as_before(self):
        # the legacy trade stands for untyped stamps: expiry counts as returned (the pre-enum rule,
        # 'the awaiting-stamp lift must not wait forever on a dead monitor')
        self._transcript([_monitor("t1", LAUNCH)])
        self._seed(why="watching the long sweep")
        self._tick(now=LAUNCH + 1000)
        self.assertIsNone(self._stamp(), "kindless keeps the pre-enum expiry behavior")

    def test_a_job_stamps_real_terminal_record_still_lifts(self):
        # the watcher genuinely returned and reported — that IS the deciding event, job kind or not
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(why="slurm 4821 regenerating the parts", kind="job")
        self._tick()
        self.assertIsNone(self._stamp(), "a real terminal record ends the wait for every kind")

    # ---- self-scoping: the other awaiting flavors are untouched ----
    def test_a_return_newer_than_the_last_lift_lifts_despite_a_late_reassert(self):
        # the 2026-08-25 audit's watcher shape: assert → lift → the watch re-armed and RETURNED →
        # the closer, auditing a segment cut BEFORE that return, re-asserted seconds after it. The
        # old stand-down read the WRITE time as the epistemic boundary and blocked the lift forever;
        # the discriminator is the EVIDENCE against the last lift — a return newer than the last
        # lift was never ruled on, whatever the re-assert's arrival says.
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        why = "the re-armed watcher; reports when it lands"
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
              "awaitingWhy": why, "awaitingAt": STAMP, "log": [
                  {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why, "at": STAMP + 10},
                  {"ev_t": STAMP, "src": "romp", "kind": "awaiting", "lift": True, "at": STAMP + 20},
                  {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why,
                   "at": BACK + 10}]}                 # the live shape: a SAME-ANCHOR re-assert (two closer
        #                                               rows on one ev_t), written AFTER the 400 return
        #                                               its audit segment never saw
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))
        self._tick()
        self.assertIsNone(self._stamp(),
                          "the return (endT %d) postdates the last lift (%d) — new information lifts"
                          % (BACK, STAMP + 20))

    def test_a_wait_with_no_dispatches_of_its_own_is_untouched(self):
        # a CI run / scheduled check-back / peer handoff: nothing was dispatched, so nothing can be paired
        self._transcript([])
        self._seed(why="waiting on the release pipeline to go green, then will tag")
        self._tick()
        self.assertIsNotNone(self._stamp(), "no dispatches to evidence → the stamp is not ours to lift")

    def test_a_dispatch_launched_after_the_stamp_is_not_owned(self):
        # it cannot be what the stamp was explaining, so its return says nothing about that wait
        self._transcript([_launch("t9", STAMP + 50), _notification("t9", STAMP + 90)])
        self._seed()
        self._tick()
        self.assertIsNotNone(self._stamp(), "only dispatches at/before the stamp can retire it")

    def test_a_dispatch_from_before_the_goal_existed_is_not_owned(self):
        self._transcript([_launch("t0", BORN - 50), _notification("t0", BORN - 10)])
        self._seed()
        self._tick()
        self.assertIsNotNone(self._stamp(), "a task predating the goal is another goal's business")

    # ---- guards ----
    def test_a_dormant_session_is_skipped(self):
        # its tasks died with its CLI; the death notice is the truth there, never a lift
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        km._lift_spent_awaiting(BACK + 100, {})        # no live snapshot for the sid
        self.assertIsNotNone(self._stamp(), "a dormant session is never ruled on here")

    def test_an_unstamped_goal_is_left_alone(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))
        self._tick()
        self.assertIsNone(self._stamp())

    def test_the_lift_is_recorded_in_the_verdict_log(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        self._tick()
        log = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"][self.gid]["log"]
        self.assertTrue(any(e.get("kind") == "awaiting" and e.get("lift") for e in log),
                        "the retraction is journalled like any other verdict, not a silent field wipe")

    # ---- ownership scoping (the user 2026-07-27): placement is authoritative when the judge has spoken ----
    def test_a_return_placed_under_another_card_never_lifts_this_stamp(self):
        # unrelated returns were lifting CI-wait stamps (one lifted the same minute it was re-asserted):
        # the bare time window claimed every task the session launched in [born, at]
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(why="waiting on the release pipeline to go green, then will tag")
        saved = km._bg_placed_tops
        km._bg_placed_tops = lambda sid, path, tids: {"t1": SID + ":gOTHER"}
        try:
            self._tick()
        finally:
            km._bg_placed_tops = saved
        self.assertIsNotNone(self._stamp(), "another card's dispatch can never retire this wait")

    def test_the_goals_own_placed_dispatch_still_lifts(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        saved = km._bg_placed_tops
        km._bg_placed_tops = lambda sid, path, tids: {"t1": self.gid}
        try:
            self._tick()
        finally:
            km._bg_placed_tops = saved
        self.assertIsNone(self._stamp(), "the goal's own thread returned everything → the wait is over")

    def test_a_later_running_dispatch_on_the_same_thread_keeps_the_stamp(self):
        # placed under the same top AFTER the stamp: its flight keeps the wait honest, so no lift
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK), _launch("t2", STAMP + 50)])
        self._seed()
        saved = km._bg_placed_tops
        km._bg_placed_tops = lambda sid, path, tids: {"t1": self.gid, "t2": self.gid}
        try:
            self._tick()
        finally:
            km._bg_placed_tops = saved
        self.assertIsNotNone(self._stamp(), "the thread's own newer dispatch is still out")

    # ---- rolled-up stamps (the user 2026-07-27): frozen invisible, so retire them on the record ----
    def test_a_rolled_up_stamp_is_lifted_and_only_once(self):
        # the roll-down froze a stamped node under a resolved ancestor — every reader skips rolledUp,
        # so the stamp could neither show nor retire. The sweep lifts it, diary-guarded against re-fire.
        self._transcript([])
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": True,
              "blocked": False, "cleared": False, "rolledUp": True, "trail": [], "t": BORN, "mt": BORN,
              "awaitingWhy": "a wait the roll-down froze", "awaitingAt": STAMP,
              "log": [{"ev_t": STAMP, "src": "closer", "kind": "awaiting",
                       "why": "a wait the roll-down froze", "at": STAMP}]}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))
        self._tick()
        log = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"][self.gid]["log"]
        self.assertEqual(len([e for e in log if e.get("kind") == "awaiting" and e.get("lift")]), 1,
                         "the frozen stamp is retired, on the record")
        self._tick()
        log2 = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"][self.gid]["log"]
        self.assertEqual(len([e for e in log2 if e.get("kind") == "awaiting" and e.get("lift")]), 1,
                         "diary-guarded: the sweep never re-lifts")

    # ---- the collapsed window (the user 2026-07-27): mint and stamp in the SAME turn ----
    # awaitingAt is the audited turn's TRIGGER, but a turn dispatches partway through, always after it.
    # When that turn also MINTED the goal, born == awaitingAt and [born, awaitingAt] is a single instant:
    # the fallback matched nothing, so this whole path was dead for the goals it exists to serve. The
    # bound is the stamp's WRITE time, which is after every launch the closer could have audited.
    def test_a_same_turn_mint_and_stamp_still_owns_its_mid_turn_dispatch(self):
        self._transcript([_launch("t1", STAMP + 20), _notification("t1", STAMP + 60)])
        self._seed(born=STAMP, anchor=STAMP, written=STAMP + 90)   # one turn: trigger STAMP, closed later
        self.assertIsNotNone(self._stamp(), "precondition: the goal starts stamped")
        self._tick(now=STAMP + 200)
        self.assertIsNone(self._stamp(),
                          "the dispatch was launched inside the very turn the stamp explains → owned, "
                          "and it came back")

    def test_a_same_turn_dispatch_still_running_keeps_the_stamp(self):
        # the widened bound must not lift a wait that is genuinely still out
        self._transcript([_launch("t1", STAMP + 20)])              # never reported
        self._seed(born=STAMP, anchor=STAMP, written=STAMP + 90)
        self._tick(now=STAMP + 200)
        self.assertIsNotNone(self._stamp(), "its own dispatch is still in flight")

    def test_a_dispatch_after_the_stamp_was_written_is_still_not_owned(self):
        # the bound moved to the WRITE time, not to infinity: a launch the closer could not have seen
        # belongs to a later turn and says nothing about this wait
        self._transcript([_launch("t9", STAMP + 150), _notification("t9", STAMP + 180)])
        self._seed(born=STAMP, anchor=STAMP, written=STAMP + 90)
        self._tick(now=STAMP + 300)
        self.assertIsNotNone(self._stamp(), "launched after the stamp was written → a later turn's work")

    def test_written_at_prefers_the_newest_assertion_and_ignores_lifts(self):
        why = "waiting on a dispatched investigation"
        nd = {"awaitingAt": STAMP,
              "log": [{"ev_t": STAMP, "kind": "awaiting", "why": why, "at": STAMP + 10},
                      {"ev_t": STAMP, "kind": "awaiting", "why": why, "at": STAMP + 90},
                      {"ev_t": STAMP, "kind": "awaiting", "lift": True, "at": STAMP + 500},
                      {"ev_t": STAMP, "kind": "done", "at": STAMP + 900}]}
        self.assertEqual(km._stamp_written_at(nd), STAMP + 90,
                         "the newest ASSERTION bounds ownership; a lift retracts a wait, never asserts one")

    def test_written_at_floors_at_the_anchor_for_a_legacy_record(self):
        self.assertEqual(km._stamp_written_at({"awaitingAt": STAMP, "log": []}), STAMP,
                         "no journalled write time → the old anchor bound, unchanged")
        self.assertEqual(km._stamp_written_at({"awaitingAt": STAMP}), STAMP, "no log at all is safe")

    # ---- the lift's EVIDENCE time (the user 2026-08-06): the stamp's anchor, never wall-clock ----
    # The fold reads a node's diary in (ev_t, at) order, and a closer assert carries its audited TURN's
    # trigger — always older than the moment a lift fires. So a lift stamped `now` outranked every assert
    # the closer could still file on that segment, permanently: a session relaunched its watcher seconds
    # after a lift, the closer re-asserted the wait three times over the next two minutes, and the fold
    # discarded all three. The card sat in Working with no awaiting box and no spin (its session idle), its
    # live watcher demoted to a background-process chip, and its nudge exemption gone.
    def test_the_lift_is_stamped_at_the_anchor_not_wall_clock(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        self._tick(now=BACK + 5000)
        log = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"][self.gid]["log"]
        lift = [e for e in log if e.get("kind") == "awaiting" and e.get("lift")][0]
        self.assertEqual(lift["ev_t"], STAMP,
                         "the lift retracts the wait it LOOKED at, so it carries that stamp's anchor")
        self.assertNotEqual(lift["ev_t"], BACK + 5000, "never the tick's wall clock")

    def test_a_closer_reassert_filed_after_the_lift_restores_the_stamp(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        self._tick()
        self.assertIsNone(self._stamp(), "precondition: the returned dispatch lifted the wait")
        # the session relaunches its watcher and the closer, auditing the SAME turn, says the wait is on
        store = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())
        nd = store["nodes"][self.gid]
        again = "the relaunched watcher on the two open PRs; it deploys once they merge"
        self.assertTrue(km.jd.record_verdict(store, nd, "closer", "awaiting", STAMP, why=again),
                        "the closer's re-assert is allowed to land")
        self.assertEqual(km.jd._fold_node(nd)["awaitingWhy"], again,
                         "the newest RULING wins: an assert filed after the lift puts the stamp back")
        self.assertEqual(km._goal_awaiting_stamp(store["nodes"], self.gid), again,
                         "so the card wears its awaiting box again, and keeps its nudge exemption")

    def test_the_lift_still_wins_when_nothing_is_filed_after_it(self):
        # the ordinary case is unchanged: nobody re-asserts, so the retraction stands
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(anchor=STAMP, written=STAMP + 10)
        self._tick()
        store = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())
        nd = store["nodes"][self.gid]
        self.assertIsNone(km.jd._fold_node(nd)["awaitingWhy"], "the wait is over and stays over")
        self.assertIsNone(km._goal_awaiting_stamp(store["nodes"], self.gid))

    def test_running_only_scan_still_hides_returned_tasks(self):
        # the want_all split must not change the existing running-only view
        self._transcript([_launch("t1", LAUNCH), _launch("t2", LAUNCH + 5), _notification("t1", BACK)])
        running = km._scan_bg_tasks(self.path)
        self.assertEqual([t["id"] for t in running], ["t2"])
        every = km._scan_bg_tasks(self.path, want_all=True)
        self.assertEqual(sorted(t["id"] for t in every), ["t1", "t2"])
        self.assertEqual({t["id"]: t["status"] for t in every}["t1"], "completed")

    # ---- a return the stamping judge already saw cannot end the wait (2026-08-16) ----
    def test_scan_records_when_the_result_landed(self):
        # the substrate: endT is the notification record's transcript time, the lift's evidence
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        every = km._scan_bg_tasks(self.path, want_all=True)
        self.assertEqual(int(every[0].get("endT") or 0), BACK)

    def test_returns_the_stamp_already_knew_do_not_lift_it(self):
        # the incident: a stamp about EXTERNAL work (cluster captures due hours later) was written
        # while the goal's only local dispatch had returned HOURS earlier, in a turn the stamping
        # judge had long since audited — the all-returned test was instantly true and the stamp
        # lifted the same minute it was written, whereupon the lift row mooted the nudge-failure
        # evaluation and the card idled in Working with no reviver left. A return that predates the
        # stamp's ANCHOR (the audited turn's trigger) is evidence the judge stamped WITH; only a
        # return past the anchor can be the event the stamp waited on. (Mid-turn and audit-lag
        # returns — after the anchor, before the write — keep lifting, per the tests above.)
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(why="waiting on the four cluster captures landing overnight",
                   anchor=BACK + 80, written=BACK + 100)   # stamped well after the return landed
        self._tick(now=BACK + 500)
        self.assertIsNotNone(self._stamp(),
                             "a pre-anchor return can't end the wait — the 6h wake owns this one")

    def test_a_lift_drops_the_goals_spent_nudge_record(self):
        # the lift is NEW INFORMATION for the escalation ladder: an idle session never produces the
        # genuine turn the ledger's arm-key dedup waits for, so a latched (failed/moot) record would
        # otherwise silence nudges on this goal forever — erase it with the stamp (2026-08-16)
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed()
        km._mark_auto_nudged(self.gid, "SOME-ARM-TURN", 3, at=BACK - 50)
        d = dict(km._auto_nudge_data())
        n = dict(d.get("nudged", {}))
        n[self.gid] = dict(n[self.gid], moot=True)      # the latch the incident carried
        d["nudged"] = n
        km._write_auto_nudge(d)
        self._tick()
        self.assertIsNone(self._stamp(), "precondition: this lift lands")
        self.assertNotIn(self.gid, km._auto_nudge_data().get("nudged", {}),
                         "the lift erases the spent record so the ladder can re-engage")


class RestartReconcile(unittest.TestCase):
    """Restart orphans (the user 2026-08-24): a kernel/backend restart kills tracked subagents and
    workflows WITH the claude process — the terminal record never lands, so the transcript pairing
    shows them running forever and the awaiting-agents stamp orphaned (~16h over an EMPTY registry).
    The reconciliation is event-keyed: the backend's lifecycle set (present-but-empty is
    authoritative) names what is actually alive, and a transcript-"running" task absent from it
    died with its process — its return event IS the backend's (re)spawn. SYNTHETIC fixtures."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in ("_alive_sessions", "_mark_views_dirty", "_sdk_spawned_at")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        self.path = str(td / (SID + ".jsonl"))
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        km._sdk_spawned_at = lambda sid: self.spawn      # the CLI epoch — the restart moment
        self.spawn = BACK                                # default: the backend respawned after the stamp
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        self.gid = SID + ":g1"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        self.td.cleanup()

    def _transcript(self, recs):
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def _seed(self, kind, why="waiting on a dispatched investigation", anchor=STAMP, written=None):
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
              "awaitingWhy": why, "awaitingAt": anchor,
              **({"awaitingKind": kind} if kind else {}),
              "log": [{"ev_t": anchor, "src": "closer", "kind": "awaiting", "why": why,
                       **({"awaitKind": kind} if kind else {}), "at": anchor if written is None else written}]}
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))

    def _stamp(self):
        nodes = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        return nodes[self.gid].get("awaitingWhy") or None

    def _tick(self, snap, now=BACK + 100):
        km._lift_spent_awaiting(now, {SID: snap})

    def test_a_task_that_vanished_across_a_restart_retires_the_stamp(self):
        # the DoD case: a bgTasks entry that vanishes across a simulated restart retires the mark.
        # The launch is in the transcript, its notification never lands (killed with the process),
        # and the respawned backend's lifecycle set is PRESENT and does not know the task.
        self._transcript([_launch("t-restart-1", LAUNCH)])
        self._seed("task")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "the vanished task returned AT the respawn — stamp lifted")

    def test_no_lifecycle_set_means_no_reconciliation(self):
        # a tmux CLI (or an SDK gap mid-reattach) carries no set: registry-absent is NOT evidence —
        # the transcript-running task keeps the wait honest exactly as before
        self._transcript([_launch("t-restart-2", LAUNCH)])
        self._seed("task")
        self._tick({"state": ""})
        self.assertIsNotNone(self._stamp(), "no authoritative set -> the old conservative read holds")

    def test_a_live_registry_entry_keeps_the_stamp(self):
        self._transcript([_launch("t-restart-3", LAUNCH)])
        self._seed("agents")
        self._tick({"state": "", "bgTasks": [{"toolUseId": "t-restart-3", "desc": "x", "since": LAUNCH}]})
        self.assertIsNotNone(self._stamp(), "the registry still tracks it — genuinely in flight")

    def test_job_stamps_ignore_the_registry(self):
        # the watcher dying with a restart is the CARRIER going, not the job returning — kind=job
        # keeps requiring a real terminal record (the 2026-08-15 rule survives the reconciliation)
        self._transcript([_launch("t-restart-4", LAUNCH)])
        self._seed("job")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "the slurm job may run on — only its terminal record lifts")

    def test_a_restart_alone_does_not_lift_a_dispatchless_job_stamp(self):
        # a JOB stamp names compute the kernel cannot observe (a CI run, a remote queue). A CLI respawn
        # is the CARRIER dying, not the job ending — yet a routine kernel restart alone lifted a
        # correctly-labelled job stamp that owned nothing (review find on #936, 2026-09-07). The same
        # shape with kind=task DOES lift: an in-harness task died with its process.
        self._transcript([])                              # nothing dispatched anywhere
        self._seed("job", why="slurm 4821 regenerating the parts; verifies when done")
        self.spawn = BACK                                 # the backend respawned AFTER the stamp
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "a restart is not the external job returning")
        self._seed("task", why="the background build finishing")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "an in-harness task stamp: the respawn killed what it watched")

    def test_a_return_before_the_stamp_was_written_is_not_the_world_emptying_after_it(self):
        # the dispatch-less branch measures endings from the stamp's WRITE time, not the audited turn's
        # trigger: a background item that returned mid-turn, before the closer even wrote the stamp,
        # used to lift it within one pusher cycle of its write (review find on #936, 2026-09-07)
        self.spawn = STAMP - 50                           # no respawn after the stamp
        # launched BEFORE the goal was born, so the stamp owns no dispatch and the dispatch-less branch
        # (not the owned-dispatch rule) is what decides
        self._transcript([_launch("t1", BORN - 50), _notification("t1", BACK)])   # returned at BACK…
        self._seed("task", anchor=STAMP, written=BACK + 50)                        # …the stamp written AFTER it
        self._tick({"state": "", "bgTasks": []}, now=BACK + 200)
        self.assertIsNotNone(self._stamp(), "the return predates the stamp's write — not an ending after it")
        self._seed("task", anchor=STAMP, written=STAMP)                         # written BEFORE the return
        self._tick({"state": "", "bgTasks": []}, now=BACK + 200)
        self.assertIsNone(self._stamp(), "a return after the write is the ending the stamp waited on")

    def test_a_dispatchless_agents_stamp_over_an_empty_registry_lifts(self):
        # the live 2026-08-24 shape: a closer misread peer sessions as agents and stamped kind=agents
        # with NO dispatch recorded anywhere; after a restart nothing can ever end that wait
        self._transcript([])
        self._seed("agents", why="workers still building the pieces; merges when they report")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "no dispatch anywhere + empty authoritative set -> orphan, lifted")

    def test_the_dispatchless_lift_respects_the_anchor_and_the_kind(self):
        # (2026-08-25 audit) the respawn is ONE sufficient evidence, not the only one: an agents
        # stamp over a world with NOTHING running anywhere — registry authoritatively empty, no
        # subagents, no raw-running task in the pairing — lifts regardless of the spawn epoch (the
        # misread-peer-as-agents shape: the notification it claims to await can never arrive). A
        # world with a dispatch still RUNNING keeps every stamp, exactly as before.
        self._transcript([_monitor("t1", LAUNCH, timeout_ms=30_000_000)])   # one genuinely-running task
        self._seed("agents")
        self.spawn = STAMP - 50                       # the stamp POSTDATES the last restart
        self._tick({"state": "", "bgTasks": [{"toolUseId": "t1"}]})   # …and the registry agrees it lives
        self.assertIsNotNone(self._stamp(), "something IS running — no lift without its return")
        self._transcript([])
        self._seed("agents")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "nothing running anywhere → the wait can never end; lifted")
        self.spawn = BACK
        self._seed(None)                              # a kindless stamp never matches the agents-orphan rule
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "kindless stamps keep the conservative dispatch-less skip")
        self._seed("agents")
        self._tick({"state": "", "bgTasks": [], "subagents": [{"type": "Task", "since": BACK}]})
        self.assertIsNotNone(self._stamp(), "live subagents ARE the wait — never lifted from under them")


class _HorizonBase(unittest.TestCase):
    """Fixture for the evidence-HORIZON tests (2026-09-07): a live session on a PRIVATE sid (the
    goal-store fixture rule), every outside fact the sweep reads stubbed at its documented seam — the
    CLI epoch (_sdk_spawned_at), placement (_bg_placed_tops), the peer reply (_peer_answered_at, the
    scalar seam _peer_answered names) — and the diary read back from the STORE, the row the gates
    themselves read. SYNTHETIC fixtures."""

    PSID = "55555555-6666-7777-8888-aaaaaaaaaaaa"
    REPLY = STAMP + 50                                # a peer's answer after the stamp was written

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in
                      ("_alive_sessions", "_mark_views_dirty", "_sdk_spawned_at", "_bg_placed_tops",
                       "_peer_answered_at", "_postal_wait_maps")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        self.path = str(td / (self.PSID + ".jsonl"))
        km._alive_sessions = lambda now, tmux: [{"sid": self.PSID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        km._sdk_spawned_at = lambda sid: self.spawn
        self.spawn = STAMP - 50                       # default: the CLI predates the stamp — no respawn story
        self.gid, self.other = self.PSID + ":g1", self.PSID + ":g2"
        km._peer_answered_at = lambda sid: self.reply
        self.reply = 0                                # default: no peer ever answered
        km._postal_wait_maps = lambda: ({}, {}, {})   # main returns (last_any, last_ask, last_await) since #1056
        self._saved_watches = list(km._pr_watches)
        km._lift_seen.pop(self.PSID, None)
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km._pr_watches[:] = self._saved_watches
        try:                                          # while STATE is still the tempdir — after the restore
            (km.jd._overrides_dir() / (self.PSID + ".jsonl")).unlink()   # this resolves to the shared floor
        except OSError:
            pass
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._lift_seen.pop(self.PSID, None)
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        self.td.cleanup()

    def _transcript(self, recs):
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def _write(self, nd):
        (km.jd.GOALDIR / (self.PSID + ".json")).write_text(json.dumps(
            {"rompUuid": self.PSID, "seq": 1, "placements": {}, "status": {}, "nodes": {self.gid: nd}}))

    def _seed(self, kind, why="watching the rebuild; will pick the result up when it lands", written=None):
        self._write({"id": self.gid, "text": "rebuild the notes-api index", "parentId": None,
                     "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                     "t": BORN, "mt": BORN, "awaitingWhy": why, "awaitingAt": STAMP,
                     **({"awaitingKind": kind} if kind else {}),
                     "log": [{"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why,
                              **({"awaitKind": kind} if kind else {}),
                              "at": STAMP if written is None else written}]})

    def _node(self):
        return json.loads((km.jd.GOALDIR / (self.PSID + ".json")).read_text())["nodes"][self.gid]

    def _stamp(self):
        return self._node().get("awaitingWhy") or None

    def _lifts(self):
        return [e for e in self._node().get("log") or [] if e.get("kind") == "awaiting" and e.get("lift")]

    def _tick(self, snap=None, now=BACK + 100):
        km._lift_seen.pop(self.PSID, None)
        km._lift_spent_awaiting(now, {self.PSID: snap if snap is not None else {"state": ""}})


class LiftHorizonJournaled(_HorizonBase):
    """Each sweep lift that rests on a recorded ending journals that ending's time as its evidence
    horizon (`endEv`, record_verdict end_ev) — the value the stand-down gates compare fresh evidence
    against, in place of the row's arrival (the 2026-09-07 defect: a lift filed late for old evidence
    silenced a fresh assert on evidence it never ruled on). One test per leg, through the real sweep,
    asserting the journaled row: a leg that drops end_ev leaves the key missing."""

    def test_a_the_dispatch_return_lift_journals_its_newest_citable_return(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(None)
        self._tick()
        self.assertIsNone(self._stamp(), "fixture: the return lifted the stamp")
        self.assertEqual(self._lifts()[-1].get("endEv"), BACK, "the horizon is the return the lift cites")

    def test_b_the_peer_supersede_lift_journals_the_reply_time(self):
        self._transcript([])
        self._seed("peer", why="asked the web session to verify; holding for the answer")
        self.reply = self.REPLY
        self._tick()
        self.assertIsNone(self._stamp(), "fixture: the reply superseded the stamp")
        self.assertEqual(self._lifts()[-1].get("endEv"), self.REPLY,
                         "the reply is the evidence this lift ruled on")

    def test_c_the_empty_world_lift_journals_the_newest_ending_it_cites(self):
        # the agents shape whose deciding event is the CLI respawn past the anchor
        self._transcript([])
        self._seed("agents", why="workers still building the pieces; merges when they report")
        self.spawn = BACK
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "fixture: the orphaned agents stamp lifted")
        self.assertEqual(self._lifts()[-1].get("endEv"), BACK, "the respawn is the ending it cites")
        # the task/job shape (2026-09-05): every launch placed on a sibling top, the world emptied
        # after the stamp — the horizon is the newest terminal record
        km._bg_placed_tops = lambda sid, path, tids: {t: self.other for t in tids}
        self.spawn = STAMP - 50
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("job")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "fixture: the in-harness world emptied after the stamp")
        self.assertEqual(self._lifts()[-1].get("endEv"), BACK, "the last in-harness ending it cites")

    def test_d_an_agents_lift_resting_on_no_recorded_ending_journals_the_registry_read(self):
        # the misread-peers-as-agents shape: nothing ever ran, no respawn past the anchor. The lift is
        # right (the notification can never arrive), and its ruling is "the registry shows nothing
        # running NOW", so the registry read's moment is its horizon, as the dead-man journals its wake.
        # Journaling none left the gates on the row's anchor, which disowns nothing: a lagging closer's
        # re-assert from a segment triggered inside (anchor, lift) stood, and the next pass re-lifted the
        # same empty world: a permanent flap on no new information, each re-lift re-arming the nudge
        # ladder (review find, 2026-09-08; pre-fix: no endEv, the re-assert stands, two lift rows). The
        # clock is journaled as read, fraction and all: floored to the second it disowned an assert
        # triggered in the read's own second, which stood and was re-lifted (pre-fix: int(now), 500)
        why = "workers still building the pieces; merges when they report"
        self._transcript([])
        self._seed("agents", why=why)
        self._tick({"state": "", "bgTasks": []}, now=500.5)
        self.assertIsNone(self._stamp(), "fixture: lifted — the wait can never end")
        self.assertEqual(self._lifts()[-1].get("endEv"), 500.5,
                         "the registry read, as read, is what the lift ruled through")
        # the lagging closer: a segment triggered inside (anchor, lift) re-asserts the same wait
        s = km.jd.load_goals(self.PSID)
        km.jd.apply_close(s, km.jd.open_menu(s), {"done": {}, "block": {},
                                                  "awaiting": {1: {"why": why, "kind": "agents"}}}, t=STAMP + 50)
        self.assertIsNone(s["nodes"][self.gid].get("awaitingWhy"),
                          "a segment inside (anchor, lift) predates the registry read: the writer yields")
        km.jd.save_goals(self.PSID, s)
        self._tick({"state": "", "bgTasks": []}, now=505.5)
        self.assertEqual(len(self._lifts()), 1, "nothing stood, so nothing re-lifts: one row, no flap")
        # …and a segment triggered AFTER the lift is a new wait: it stands
        km.jd.apply_close(s, km.jd.open_menu(s), {"done": {}, "block": {},
                                                  "awaiting": {1: {"why": why, "kind": "agents"}}}, t=600)
        self.assertEqual(s["nodes"][self.gid].get("awaitingWhy"), why, "a later turn's ruling stands")

    def test_g_a_dead_watchers_unspent_ceiling_is_not_a_return(self):
        # a Monitor the pairing still shows running, absent from a PRESENT registry (dead), whose CLI
        # spawn predates the stamp and whose ceiling lies AHEAD: nothing it can cite postdates the anchor.
        # The "returned after the anchor" test and the horizon the lift journals read ONE conditioning
        # (_return_ev); the older hand-kept copy still counted the unspent ceiling, so this lifted off a
        # horizon (the spawn, 250) OLDER than the anchor it retracted (300): a lift citing evidence from
        # before the wait began (review find, 2026-09-08). The ceiling passing is the event: it lifts then.
        self._transcript([_monitor("t1", LAUNCH, timeout_ms=400_000)])      # ceiling 600
        self._seed(None)
        self._tick({"state": "", "bgTasks": []}, now=500)          # registry present and empty: t1 is dead
        self.assertIsNotNone(self._stamp(), "nothing returned after the anchor yet: the stamp stands")
        self.assertEqual(self._lifts(), [], "no lift, so no horizon older than the wait it would retract")
        self._tick({"state": "", "bgTasks": []}, now=800)          # the ceiling passed: a recorded ending
        self.assertIsNone(self._stamp(), "the spent ceiling is the return")
        self.assertEqual(self._lifts()[-1].get("endEv"), 600, "…and the horizon is that ceiling")

    def test_f_a_returned_watchers_unspent_ceiling_is_not_the_horizon(self):
        # a Monitor keeps its recorded ceiling after it returns (the completion path sets endT and
        # leaves `deadline`): a watcher that fired at 400 under a 600 ceiling journals 400 — the
        # return — never the ceiling, and never the clock a min() floor falls to (pre-fold: 500)
        self._transcript([_monitor("t1", LAUNCH, timeout_ms=400_000), _notification("t1", BACK)])
        self._seed(None)
        self._tick(now=500)
        self.assertIsNone(self._stamp(), "fixture: the return lifted the stamp")
        self.assertEqual(self._lifts()[-1].get("endEv"), BACK, "the return — not the ceiling, not the clock")

    def test_e_the_peer_supersede_predicate_returns_the_reply_time(self):
        nd = {"awaitingWhy": "w", "awaitingAt": STAMP,
              "log": [{"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": "w", "at": STAMP}]}
        self.assertEqual(km._peer_stamp_superseded(nd, (self.REPLY, {})), self.REPLY,
                         "the superseding reply's time, not a bare True")
        self.assertEqual(km._peer_stamp_superseded(nd, self.REPLY), self.REPLY, "a legacy scalar too")
        self.assertEqual(km._peer_stamp_superseded(dict(nd, awaitingPeers=["p1"]), (0, {"p1": self.REPLY + 20})),
                         self.REPLY + 20, "pair-aware: the awaited pair's own reply")
        self.assertEqual(km._peer_stamp_superseded(nd, (STAMP - 10, {})), 0, "a reply before the write: not superseded")
        self.assertEqual(km._peer_stamp_superseded(dict(nd, awaitingKind="job"), (self.REPLY, {})), 0,
                         "peer-scoped: a job stamp stands through mail")


class LiftHorizonGate(_HorizonBase):
    """The sweep's own stand-down (the 2026-08-19 rule) mirrors the closer's: after a re-assert, a lift
    fires only on a return NEWER than what the prior lift RULED ON — its journaled horizon — never newer
    than that lift's filing. The diary: assert → a lift filed late (at T_ARR) for evidence up to T_EV →
    the closer re-asserts after the filing. The watcher's return lands at T_X."""

    T_EV, T_ARR, REASSERT_AT, NOW = 350, 420, 470, 500

    def _diary(self, t_x, why="the re-armed watcher; reports when it lands", monitor=False, legacy=False):
        self._transcript([_monitor("t1", LAUNCH, timeout_ms=400_000) if monitor else _launch("t1", LAUNCH),
                          _notification("t1", t_x)])
        self._write({"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
                     "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
                     "awaitingWhy": why, "awaitingAt": STAMP, "log": [
                         {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why, "at": STAMP},
                         {"ev_t": STAMP, "src": "romp", "kind": "awaiting", "lift": True,
                          **({} if legacy else {"endEv": self.T_EV}), "at": self.T_ARR},
                         {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why,
                          "at": self.REASSERT_AT}]})

    def test_a_a_return_past_the_lifts_horizon_lifts_even_though_it_predates_the_filing(self):
        # T_EV < T_X < T_ARR: read by filing time, the return was "already ruled on" and the stamp
        # stayed (main: the stamp is still standing); the lift's horizon says it never saw this return
        self._diary(t_x=380)
        self._tick(now=self.NOW)
        self.assertIsNone(self._stamp(), "a return the prior lift never ruled on is new information — lift")
        self.assertEqual(self._lifts()[-1].get("endEv"), 380, "…and the new lift journals that return")

    def test_b_a_return_the_lift_already_ruled_on_stands_the_re_lift_down(self):
        self._diary(t_x=340)
        self._tick(now=self.NOW)
        self.assertIsNotNone(self._stamp(), "every citable return predates the horizon — the flap, yielded")
        self.assertEqual(len(self._lifts()), 1, "no second lift row")

    def test_c_a_returned_watchers_unspent_ceiling_does_not_defeat_the_stand_down(self):
        # the gate measures the same conditioned return evidence the lift journals: a watcher returned
        # at 340 (before the 350 horizon) under a 600 ceiling cites 340, so the re-lift stands down.
        # Folding the ceiling made every re-lift of a returned watcher look like new information
        # (pre-fold: the stamp is lifted — the flap the 2026-08-19 rule exists to stop)
        self._diary(t_x=340, monitor=True)
        self._tick(now=self.NOW)
        self.assertIsNotNone(self._stamp(), "a ceiling that never passed is not a return — yield")
        self.assertEqual(len(self._lifts()), 1)

    def test_b2_the_boundary_is_inclusive_a_return_at_the_horizon_is_the_one_already_cited(self):
        # `<=`: a return AT the horizon is the very return the prior lift cited (its own end_ev is the
        # newest return it cited); re-lifting off it is the flap. A `<` mutant lifts here.
        self._diary(t_x=self.T_EV)
        self._tick(now=self.NOW)
        self.assertIsNotNone(self._stamp(), "equal evidence was ruled on — yield")
        self.assertEqual(len(self._lifts()), 1)

    def test_d_a_legacy_lift_row_keeps_the_arrival_compare_it_always_had(self):
        # a lift filed before horizons were journaled (no endEv): its arrival is its horizon, the compare
        # this stand-down made before 2026-09-07, so the upgrade changes nothing for it (review find,
        # 2026-09-08). Read as its ev_t (the anchor) instead, every citable return looked new, and the
        # re-assert was re-lifted once at the first pass after the upgrade, re-arming the nudge ladder
        self._diary(t_x=380, legacy=True)                          # a T_EV horizon would admit 380; T_ARR does not
        self._tick(now=self.NOW)
        self.assertIsNotNone(self._stamp(), "a return before the legacy row's filing was ruled on: yield")
        self.assertEqual(len(self._lifts()), 1, "no second lift row")
        self._diary(t_x=430, legacy=True)                          # past the filing: new information, as ever
        self._tick(now=self.NOW)
        self.assertIsNone(self._stamp(), "a return past the legacy row's filing lifts, as before")

    def test_e_a_fractional_ceiling_is_journaled_whole_so_the_stand_down_holds(self):
        # a Monitor's recorded ceiling is t + timeout_ms/1000, fractional when timeout_ms is not a whole
        # second. The lift journals the ceiling it cites; int()-truncated at the journal, the next pass
        # measured 600.5 against 600, the inclusive stand-down never held, and the stamp was re-lifted on
        # the same ceiling after every re-assert (review find, 2026-09-08; pre-fix: endEv 600, two lifts)
        why = "the re-armed watcher; reports when it lands"
        self._transcript([_monitor("t1", LAUNCH, timeout_ms=400_500)])      # ceiling 600.5, no notification
        self._seed("task", why=why)
        self._tick(now=800.5)                                      # past ceiling + grace: the watcher expired
        self.assertIsNone(self._stamp(), "fixture: the spent ceiling lifted the stamp")
        self.assertEqual(self._lifts()[-1].get("endEv"), 600.5, "the ceiling as recorded, fraction and all")
        nd = self._node()                                          # the closer re-asserts after the filing
        nd["log"].append({"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why, "awaitKind": "task",
                          "at": nd["log"][-1]["at"] + 1})
        nd["awaitingWhy"], nd["awaitingAt"], nd["awaitingKind"] = why, STAMP, "task"
        self._write(nd)
        self._tick(now=805.5)
        self.assertIsNotNone(self._stamp(), "the ceiling it cites is the one already ruled on: yield")
        self.assertEqual(len(self._lifts()), 1, "no second lift row")


if __name__ == "__main__":
    unittest.main()


class LiftStandsDown(unittest.TestCase):
    """The lift joins the stand-down rule (the 2026-08-19 nudge audit): a writer whose evidence
    predates the diary yields. The lift's evidence is the newest RETURN it can cite; a closer
    assert filed AFTER every citable return means the judge ruled on a fresher world (the session
    re-armed something the ownership window can't see), and lifting anyway produced 2-3 second
    stamp↔lift flaps that reset the nudge ladder (fires 5s after a lift; three first-nudges in 21
    minutes, each answered "still running")."""

    def setUp(self):
        AwaitingLift.setUp(self)

    def tearDown(self):
        AwaitingLift.tearDown(self)

    _transcript = AwaitingLift._transcript
    _seed = AwaitingLift._seed
    _tick = AwaitingLift._tick
    _stamp = AwaitingLift._stamp

    def test_a_reassert_after_a_lift_stands_the_next_lift_down(self):
        # the FLAP: assert → lift → the closer re-asserts AFTER the lift, citing a fresher world —
        # every return this lift could cite preceded the prior lift, so lifting again just flaps
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(written=BACK + 60)
        import json as _json
        gp = km.jd.GOALDIR / (SID + ".json")
        store = _json.loads(gp.read_text())
        nd = store["nodes"][self.gid]
        nd["log"] = [
            {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": "w", "at": STAMP},
            {"ev_t": BACK + 10, "src": "romp", "kind": "awaiting", "lift": True, "at": BACK + 10},
            {"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": nd["awaitingWhy"], "at": BACK + 60},
        ]
        gp.write_text(_json.dumps(store))
        self._tick(now=BACK + 120)
        self.assertIsNotNone(self._stamp(), "a re-assert after a lift means a fresher ruling — yield")

    def test_a_first_stamp_still_lifts_on_audit_lag_returns(self):
        # NO prior lift: the original design holds — a return the judge never saw still lifts,
        # even when the stamp's write time postdates it (the same-turn suite pins depend on this)
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(written=BACK + 60)                    # written after the return, but never lifted before
        self._tick(now=BACK + 120)
        self.assertIsNone(self._stamp(), "first-stamp audit-lag lift preserved")


class LiftKeepsLiveLedgerRecords(unittest.TestCase):
    """A lift drops only SPENT ledger records (failed/moot/answered latches — the 2026-08-16
    idle-in-Working fix). A LIVE mid-count record is the once-per-stall invariant itself: dropping
    it reset the counter on every stamp↔lift flap and the same arm turn drew fresh first-nudges
    minutes apart while the escalation ladder never engaged."""

    def test_live_records_survive_the_drop(self):
        import tempfile as _tf
        from pathlib import Path as _P
        with _tf.TemporaryDirectory() as td:
            saved = km.jd.STATE
            try:
                km.jd.STATE = _P(td)
                km._write_auto_nudge({"enabled": True, "nudged": {
                    "g-live": {"count": 2, "lastTurnId": "u9"},
                    "g-failed": {"count": 1, "lastTurnId": "u1", "failed": True},
                    "g-moot": {"count": 1, "lastTurnId": "u2", "moot": True},
                    "g-answered": {"count": 1, "lastTurnId": "u3", "answeredAt": 5}}})
                for gid in ("g-live", "g-failed", "g-moot", "g-answered"):
                    km._drop_auto_nudge_rec(gid)
                left = km._auto_nudge_data().get("nudged", {})
                self.assertIn("g-live", left, "mid-episode memory survives — the ladder can escalate")
                self.assertEqual(left["g-live"].get("count"), 2, "…with its count intact")
                for gid in ("g-failed", "g-moot", "g-answered"):
                    self.assertNotIn(gid, left, "spent latches still drop (the 2026-08-16 fix)")
            finally:
                km.jd.STATE = saved


class InHarnessWaitLift(unittest.TestCase):
    """A task/job stamp whose dispatches sit on ANOTHER top lifts when the in-harness world it stood
    over goes EMPTY after the stamp (2026-09-05). The live specimen: the closer stamped a top kind=job
    for a Monitor plus a background command the session itself was running; the planner placed both
    launches on a sibling top, so the stamp owned nothing (`own == []`) and the dispatch-less skip
    kept it — for 17 hours, over an authoritatively empty registry, nothing pending anywhere. The
    agents-kind orphan rule already lifts that shape; task/job now take the same lift, keyed on the
    same authority (the backend's present-and-empty lifecycle set, no live subagents, no armed kernel
    watch) plus the watermark the whole sweep uses: the LAST in-harness item's ending — a terminal
    record, a Monitor's recorded ceiling, the launch ledger's stop tombstone, or the CLI respawn that
    killed everything — must postdate the stamp's anchor. A world already empty when the closer
    stamped is a wait on something the registry cannot see (a CI run): the dead-man's, untouched.

    SYNTHETIC fixtures; a PRIVATE sid (the goal-store fixture rule: load_goals replays the per-sid
    override journal, and the shared placeholder sid's journal is written by other modules)."""

    PSID = "44444444-5555-6666-7777-888888888888"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in
                      ("_alive_sessions", "_mark_views_dirty", "_sdk_spawned_at", "_bg_placed_tops")}
        self.saved_jd = (km.jd.STATE, km.jd.GOALDIR)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        (td / "sdk").mkdir()
        self.path = str(td / (self.PSID + ".jsonl"))
        km._alive_sessions = lambda now, tmux: [{"sid": self.PSID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        km._sdk_spawned_at = lambda sid: self.spawn
        self.spawn = STAMP - 50                       # default: the CLI predates the stamp — no respawn story
        self.gid, self.other = self.PSID + ":g1", self.PSID + ":g2"
        # the planner placed every launch on the SIBLING top: this goal owns no dispatch
        km._bg_placed_tops = lambda sid, path, tids: {t: self.other for t in tids}
        self._saved_watches = list(km._pr_watches)
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km._pr_watches[:] = self._saved_watches
        try:                                          # while STATE is still the tempdir — after the restore
            (km.jd._overrides_dir() / (self.PSID + ".jsonl")).unlink()   # this resolves to the shared floor
        except OSError:
            pass
        km.jd.STATE, km.jd.GOALDIR = self.saved_jd
        km._SESSION_STAMP_CACHE.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        self.td.cleanup()

    def _transcript(self, recs):
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        km._bgall_cache.clear(); km._bgtasks_cache.clear()

    def _seed(self, kind, anchor=STAMP, why="watching the rebuild; will pick the result up when it lands"):
        nd = {"id": self.gid, "text": "rebuild the notes-api index", "parentId": None,
              "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
              "awaitingWhy": why, "awaitingAt": anchor,
              **({"awaitingKind": kind} if kind else {}),
              "log": [{"ev_t": anchor, "src": "closer", "kind": "awaiting", "why": why,
                       **({"awaitKind": kind} if kind else {}), "at": anchor}]}
        sib = {"id": self.other, "text": "wire the web session's watcher", "parentId": None,
               "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
               "log": []}
        (km.jd.GOALDIR / (self.PSID + ".json")).write_text(json.dumps(
            {"rompUuid": self.PSID, "seq": 1, "placements": {}, "status": {},
             "nodes": {self.gid: nd, self.other: sib}}))

    def _reg(self, **fields):
        (km.jd.STATE / "sdk" / (self.PSID + ".json")).write_text(json.dumps(fields))

    def _node(self):
        return json.loads((km.jd.GOALDIR / (self.PSID + ".json")).read_text())["nodes"][self.gid]

    def _stamp(self):
        return self._node().get("awaitingWhy") or None

    def _tick(self, snap, now=BACK + 100):
        km._lift_spent_awaiting(now, {self.PSID: snap} if snap is not None else {})

    def test_a_job_stamp_over_dispatches_placed_elsewhere_lifts_when_the_world_empties_after_it(self):
        # the specimen: the background command returned AFTER the stamp; the registry is present and
        # empty; the goal owns nothing (placed on the sibling) — the wait it described is over
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("job")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp(), "nothing in-harness runs any more, and it ended after the stamp")

    def test_the_lift_is_the_agents_lift_exactly(self):
        # same writer, same row: a romp/awaiting LIFT anchored at the stamp (never wall-clock)
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("task")
        self._tick({"state": "", "bgTasks": []})
        row = [e for e in self._node()["log"] if e.get("kind") == "awaiting"][-1]
        self.assertEqual((row.get("src"), row.get("lift"), row.get("ev_t")), ("romp", True, STAMP))

    def test_a_task_stamp_takes_the_same_lift(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("task")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp())

    def test_one_live_registry_entry_keeps_the_stamp(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK), _launch("t2", LAUNCH + 1)])
        self._seed("job")
        self._tick({"state": "", "bgTasks": [{"toolUseId": "t2", "desc": "x", "since": LAUNCH + 1}]})
        self.assertIsNotNone(self._stamp(), "something is still running in-harness — the wait stands")

    def test_live_subagents_keep_the_stamp(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("task")
        self._tick({"state": "", "bgTasks": [], "subagents": [{"type": "Task", "since": BACK}]})
        self.assertIsNotNone(self._stamp())

    def test_no_authoritative_registry_means_no_move(self):
        # a tmux CLI carries no lifecycle set: registry-absent is not evidence of anything
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("job")
        self._tick({"state": ""})
        self.assertIsNotNone(self._stamp(), "no authoritative source → no move")
        # …and a DORMANT session is skipped outright (its tasks died with its CLI; the death owns it)
        self._seed("job")
        self._tick(None)
        self.assertIsNotNone(self._stamp())

    def test_emptiness_that_predates_the_stamp_is_not_the_waits_ending(self):
        # the closer stamped AFTER the last return, knowing it — the wait is about something the
        # registry cannot see (a CI run); the dead-man owns it, this sweep does not
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("job", anchor=BACK + 100)
        self._tick({"state": "", "bgTasks": []}, now=BACK + 500)
        self.assertIsNotNone(self._stamp(), "the world was already empty when the judge stamped")

    def test_nothing_ever_dispatched_keeps_a_job_stamp(self):
        # a genuinely external job with no in-harness carrier at all: no ending event exists here
        self._transcript([])
        self._seed("job")
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "no item ended after the stamp — nothing to key on")

    def test_a_respawn_after_the_stamp_is_a_sufficient_ending(self):
        # the sibling's launch has no terminal record (killed with the process); the CLI epoch is
        # newer than the stamp, so everything the stamp could have watched died at that moment
        self._transcript([_launch("t1", LAUNCH)])
        self._seed("task")
        self.spawn = STAMP + 50
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp())

    def test_a_ledger_stop_tombstone_after_the_stamp_is_the_ending_event(self):
        # a Monitor called off with TaskStop suppresses its notification — the transcript never
        # learns — but the launch ledger journals the stop; that tombstone is the exact event
        self._transcript([_monitor("m1", LAUNCH, timeout_ms=30_000_000)])
        self._seed("job")
        self._reg(bgLedgerEnded=[{"tid": "m1", "why": "stopped", "at": BACK}])
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNone(self._stamp())
        self._seed("job")
        self._reg(bgLedgerEnded=[{"tid": "m1", "why": "stopped", "at": STAMP - 10}])
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "a stop the judge already knew about is not new information")

    def test_an_armed_kernel_watch_keeps_a_job_stamp(self):
        # a PR watch is the kernel's own carrier of an external wait: its delivery IS the ending
        # event, so the stamp stands while the watch is armed for this session
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed("job")
        km._pr_watches.append({"pr": 7, "repo": "notes-api/notes-api", "sid": self.PSID, "at": LAUNCH,
                               "_next": 0, "_fails": 0, "_busy": False})
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "the kernel is still watching something for this session")

    def test_a_kindless_stamp_keeps_the_conservative_skip(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        self._seed(None)
        self._tick({"state": "", "bgTasks": []})
        self.assertIsNotNone(self._stamp(), "a kindless stamp may be a peer wait — untouched, as before")


class LiftGateFallback(unittest.TestCase):
    """The lift's inputs gate (_lift_seen) records a session's fingerprint BEFORE the load and forgets it
    when the ruling raises or the store read FAULTS (AwaitingLift's
    test_a_faulting_store_forgets_the_gate_so_the_next_tick_retries). One more load is no better than
    those: a store that parsed while its override JOURNAL could not be read. _replay_overrides logs
    history-unreadable and returns the store WITHOUT the user's rows (a restore that carries the stamp,
    here), so the ruling "nothing stamped" stood against a fingerprint that, for an idle session, may not
    move for hours. The replay marks that store `_unread`, and the gate forgets its entry for a marked
    store, so the next cycle retries."""

    def setUp(self):
        AwaitingLift.setUp(self)
        km._lift_seen.pop(SID, None)                     # process-global; the harness never clears it
        self.loads = []
        real = km.jd.load_goals
        km.jd.load_goals = lambda fsid: (self.loads.append(fsid), real(fsid))[1]
        self.addCleanup(setattr, km.jd, "load_goals", real)

    def tearDown(self):
        km._lift_seen.pop(SID, None)
        AwaitingLift.tearDown(self)

    _transcript = AwaitingLift._transcript
    _seed = AwaitingLift._seed
    _tick = AwaitingLift._tick
    _stamp = AwaitingLift._stamp

    def _read_fails_once(self, target):
        """Patch Path.read_text so the FIRST read of `target` (the journal, which _replay_overrides reads
        that way) raises OSError (the EMFILE/EIO shape a busy kernel meets) and every later read is real.
        Returns the counter of raised reads."""
        real = Path.read_text
        state = {"fired": 0}
        def flaky(p, *a, **k):
            if p == target and not state["fired"]:
                state["fired"] += 1
                raise OSError(errno.EMFILE, "synthetic: too many open files")
            return real(p, *a, **k)
        Path.read_text = flaky
        self.addCleanup(setattr, Path, "read_text", real)
        return state

    def test_a_stamp_in_the_file_still_lifts_while_the_journal_does_not_read(self):
        """The mark changes what the gate REMEMBERS, not what the lift RULES: a store that parsed with its
        stamp in the file lifts as before and publishes without the mark; only the gate entry is forgotten,
        so a stamp the unread journal held is retried next cycle."""
        self._transcript([_launch("t1", LAUNCH), _launch("t2", LAUNCH + 5),
                          _notification("t1", BACK), _notification("t2", BACK + 5)])
        self._seed()                                     # the stamp lives in the store FILE
        km.jd.append_override(SID, SID + ":g9", "resolve", BACK + 50)   # a journal exists: a row for a node the
        #                                                                  store lacks, skipped on replay
        state = self._read_fails_once(km.jd._overrides_dir() / (SID + ".jsonl"))
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(state["fired"], 1)
        self.assertGreaterEqual(len(self.loads), 1)
        self.assertNotIn(SID, km._lift_seen, "ruled without the journal: the gate forgets the entry")
        self.assertIsNone(self._stamp(), "...and the ruling on the parsed store stands: every dispatch is back, the stamp lifts")
        self.assertNotIn("_unread", json.loads((km.jd.GOALDIR / (SID + ".json")).read_text()),
                         "the mark never reaches the file")

    def test_a_swallowed_journal_read_failure_is_not_recorded_as_a_ruling(self):
        self._transcript([_launch("t1", LAUNCH), _notification("t1", BACK)])
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {}}))
        why = "waiting on two dispatched investigations; will act when they return"
        nd = {"id": self.gid, "text": "a goal", "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN,
              "awaitingWhy": why, "awaitingAt": STAMP,
              "log": [{"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why, "at": STAMP}]}
        km.jd.append_restore(SID, {self.gid: nd}, {}, BACK + 50)   # the stamp lives in the journal only
        state = self._read_fails_once(km.jd._overrides_dir() / (SID + ".jsonl"))
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()                                 # the replay logged history-unreadable and skipped
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(state["fired"], 1)
        self.assertNotIn(SID, km._lift_seen, "the journal was not read: no entry")
        self._tick()                                     # nothing on disk moved
        self.assertGreaterEqual(len(self.loads), 2, "the journal reads now, unchanged: loaded anyway")
        nodes = json.loads((km.jd.GOALDIR / (SID + ".json")).read_text())["nodes"]
        self.assertIn(self.gid, nodes, "the restore replayed and the node was saved back")
        self.assertIsNone(nodes[self.gid].get("awaitingWhy") or None, "...with its stamp lifted")
