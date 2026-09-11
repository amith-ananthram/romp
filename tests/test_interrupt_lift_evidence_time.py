#!/usr/bin/env python3
"""romp's interrupt block and its LIFT are stamped with the EVENT they are about, never wall-clock now.

The diary is an evidence-time ledger: every judge verdict carries the TRIGGER of the segment it ruled
on, and the fold replays rows in that order. So a lift stamped `now` while that segment is still
running sorts after every verdict about it — permanently — and the fold erases them.

The audited card (the user 2026-08-01): a kernel restart cut a session mid-turn; romp's resume notice
opened the next turn; two minutes in, the interrupt-block lift fired stamped `now`; the session worked
on and ended that turn by asking the user a question. The planner and the closer both filed a block
about it — and both were replayed BEFORE the lift and wiped. The card sat in Working on an idle session
with no block, no waiting chip, no nudge (that same row also read as "evidence newer than the last turn romp
saw end", so _nudge_fire_list held the nudge under a why the stall surface screens), until a peer's
message six minutes later opened a fresh turn whose verdict finally outranked it.

Synthetic fixtures only.
"""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
sb = load_source("romp_sdk_backend_ile", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_ile", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# A SID of this file's own: the judge module is process-shared across every kernel test copy, so the
# append-only overrides journal is shared too — a same-SID block journaled elsewhere would replay here.
SID = "11111111-2222-3333-4444-666666666666"
GID = SID + ":g1"
NOW = 1781100000
CUT_T = NOW - 900          # the restart cut the turn here
RESUME_T = NOW - 890       # romp's resume notice opened the next turn (its TRIGGER: what judges stamp)
LIFT_WALL_T = NOW - 750    # …and the lift fired two minutes into it — the wall-clock stamp that broke
FILED_T = NOW - 600        # the judges ruled on that turn once it ended


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def _seed():
    store = {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "lastNode": GID,
             "nodes": {GID: {"id": GID, "text": "Ship the reconnect banner", "parentId": None,
                             "nodeComplete": False, "blocked": False, "cleared": False,
                             "trail": [], "t": NOW - 3600, "mt": CUT_T}}}
    jd.rollup_status(store, False)
    jd.save_goals(SID, store)
    return store


def _row(nd, kind, src=None):
    return next((e for e in reversed(nd.get("log") or [])
                 if e["kind"] == kind and (src is None or e["src"] == src)), None)


class TheStampsAreEvidenceTimes(unittest.TestCase):
    """Both writers carry the moment they are ABOUT — the stop's own transcript time, and the trigger
    of the turn the re-engagement opened."""

    def setUp(self):
        km._write_auto_nudge({"enabled": True, "nudged": {}})
        fp = jd._overrides_dir() / (SID + ".jsonl")
        if fp.exists():
            fp.unlink()
        _seed()

    def test_the_block_carries_the_stops_own_time(self):
        km._record_interrupt_block(SID, CUT_T)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertEqual(_row(nd, "block")["ev_t"], CUT_T)
        self.assertEqual(nd["mt"], CUT_T, "the display stamp rides the same event")

    def test_the_lift_carries_the_reengagement_not_wall_clock(self):
        km._record_interrupt_block(SID, CUT_T)
        km._lift_interrupt_block(SID, GID, RESUME_T)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertEqual(_row(nd, "unblock")["ev_t"], RESUME_T,
                         "the lift is stamped with the turn it re-engaged into, not when the tick noticed")
        self.assertLess(_row(nd, "unblock")["ev_t"], int(time.time()) - 600,
                        "a wall-clock stamp would outrank every verdict about that turn, forever")
        self.assertEqual(jd.load_goals(SID)["status"][GID], "working", "and the block still lifts")

    def test_the_lift_never_sorts_before_the_block_it_lifts(self):
        # a re-engagement whose turn PREDATES the stop (a stale parse, a clock skew) would fold ahead of
        # the block and leave the card stuck blocked — floored, the same guard the moot-unblock uses
        km._record_interrupt_block(SID, CUT_T)
        km._lift_interrupt_block(SID, GID, CUT_T - 300)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertEqual(_row(nd, "unblock")["ev_t"], CUT_T)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "working")

    def test_a_real_verdict_since_still_owns_the_card(self):
        km._record_interrupt_block(SID, CUT_T)
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][GID], "closer", "block", RESUME_T, why="which host?")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        km._lift_interrupt_block(SID, GID, RESUME_T)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked",
                         "the lift only ever clears a block WE placed")


class TheFoldReplaysTheVerdictAfterTheLift(unittest.TestCase):
    """The incident, at the fold: the judges' block about the resumed turn must survive a lift that
    fired while that turn was still running."""

    def _log(self, lift_ev):
        return [{"ev_t": CUT_T, "src": "interrupt", "kind": "block", "at": CUT_T,
                 "why": jd.INTERRUPT_BLOCK_WHY},
                {"ev_t": lift_ev, "src": "user", "kind": "unblock", "at": LIFT_WALL_T,
                 "why": "you re-engaged"},
                {"ev_t": RESUME_T, "src": "planner", "kind": "block", "at": FILED_T,
                 "why": "needs the staging host name"},
                {"ev_t": RESUME_T, "src": "closer", "kind": "block", "at": FILED_T + 13,
                 "why": "needs the staging host name"}]

    def test_an_evidence_time_lift_lets_the_verdict_land(self):
        self.assertEqual(jd._fold_node_state({"id": GID, "log": self._log(RESUME_T)}), "blocked",
                         "the judges ruled on the turn the lift re-engaged into — newer information")

    def test_a_wall_clock_lift_erases_it(self):
        # the defect, pinned: nothing about these rows changes except the lift's stamp
        self.assertEqual(jd._fold_node_state({"id": GID, "log": self._log(LIFT_WALL_T)}), "open",
                         "a mid-segment wall-clock stamp replays after the verdicts about that segment")

    def test_a_verdict_about_an_OLDER_turn_is_still_superseded(self):
        rows = self._log(RESUME_T)
        rows.append({"ev_t": CUT_T - 60, "src": "closer", "kind": "block", "at": FILED_T + 40,
                     "why": "a replayed pre-cut segment"})
        self.assertEqual(jd._fold_node_state({"id": GID, "log": rows}), "blocked")
        rows = [r for r in rows if r["src"] not in ("planner", "closer")] + [rows[-1]]
        self.assertEqual(jd._fold_node_state({"id": GID, "log": rows}), "open",
                         "a catch-up ruling on a turn that predates the re-engagement stays stale")


class TheNudgeIsNotHeldByTheLift(unittest.TestCase):
    """The second half of the silence: _nudge_fire_list holds a goal whose diary gained evidence NEWER
    than the last turn romp watched end, under a why the stall surface screens — so the card showed no
    nudge and no stalled chip either. The lift's stamp is one layer; the guard counting only JUDGE rows
    is the other (test_nudge_hold_judge_rows.py), and the lift now clears both."""

    def _fresh(self, lift_ev):
        return {"nodes": {GID: {"id": GID, "log": [
                    {"ev_t": CUT_T, "src": "interrupt", "kind": "block", "at": CUT_T},
                    {"ev_t": lift_ev, "src": "user", "kind": "unblock", "at": LIFT_WALL_T}]}},
                "status": {GID: "working"}}

    def test_an_evidence_time_lift_leaves_the_nudge_free_to_fire(self):
        held = []
        keep = km._nudge_fire_list(self._fresh(RESUME_T), [(GID, 0, True)],
                                   arm_t=CUT_T, seen_t=RESUME_T, held=held)
        self.assertEqual([f[0] for f in keep], [GID])
        self.assertEqual(held, [], "nothing in the diary postdates the turn romp saw end")

    def test_not_even_a_wall_clock_lift_can_hold_it_now(self):
        held = []
        keep = km._nudge_fire_list(self._fresh(LIFT_WALL_T), [(GID, 0, True)],
                                   arm_t=CUT_T, seen_t=RESUME_T, held=held)
        self.assertEqual([f[0] for f in keep], [GID],
                         "a lift is romp's own bookkeeping, never a judge ruling on an unseen turn")
        self.assertEqual(held, [])

    def test_a_judge_ruling_on_an_unseen_turn_still_holds(self):
        fresh = self._fresh(RESUME_T)
        fresh["nodes"][GID]["log"].append(
            {"ev_t": LIFT_WALL_T, "src": "closer", "kind": "awaiting", "at": FILED_T})
        held = []
        keep = km._nudge_fire_list(fresh, [(GID, 0, True)], arm_t=CUT_T, seen_t=RESUME_T, held=held)
        self.assertEqual(keep, [])
        self.assertEqual([(f[0], why) for f, why, _ev in held], [(GID, jd.WHY_TURN_IN_FLIGHT)])
        self.assertIn(jd.WHY_TURN_IN_FLIGHT, jd.WHY_IN_FLIGHT,
                      "the hold's why is in-flight class: it presents as the Analyzing… swirl "
                      "(and the sweep pops it on the turn's own end) — no silent card since 2026-08-13")


class _TickFixture(unittest.TestCase):
    """The shared world for the tick tests: a hermetic state root, one session whose transcript ends in
    a user interrupt, its focus goal in Working, and the re-engagement append. No tests of its own: the
    concrete classes below derive from it, so each test is collected once."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("api\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        jd.CLOSER_ON = False
        jd.GOALDIR.mkdir(parents=True)
        km._downtime[:] = []
        km._parse_cache.clear()
        km._autonudge_cache.clear()
        km._pending_ops.clear()
        km._write_auto_nudge({"enabled": True, "nudged": {}, "intrBlocked": {}})
        self.live = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}
        recs = [uline(NOW - 3600, "wire up the reconnect banner", "u1"),
                aline(NOW - 3580, "on it", "a1", "u1", "tool_use"),
                uline(CUT_T, "[Request interrupted by user]", "u2", "a1")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        g = {"id": GID, "text": "Ship the reconnect banner", "parentId": None, "nodeComplete": False,
             "blocked": False, "cleared": False, "trail": [], "t": NOW - 3600}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": GID, "closedTurns": [], "nodes": {GID: g},
             "placements": {}, "status": {GID: "working"}}))

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON) = self.saved
        km._pending_ops.clear()
        km._parse_cache.clear()
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _reengage(self):
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(RESUME_T, "use the staging host for now", "u3", "u2")) + "\n")
            f.write(json.dumps(aline(RESUME_T + 40, "done — which host do you want for prod?",
                                     "a2", "u3", "end_turn")) + "\n")
        km._parse_cache.clear()


class EndToEndThroughTheTick(_TickFixture):
    """Through the parse: a genuine stop blocks the focus goal, the user's next message lifts it — and
    the closer's verdict about THAT message's turn puts the card back in needs-you, where the six-minute
    silent Working card should have been."""

    def test_the_lift_records_the_reengagement_turns_trigger(self):
        km._interrupt_block_tick(NOW, self.live)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked")
        self._reengage()
        km._interrupt_block_tick(NOW, self.live)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertEqual(jd.load_goals(SID)["status"][GID], "working")
        self.assertEqual(_row(nd, "unblock")["ev_t"], RESUME_T,
                         "the message's own turn trigger — the stamp the judges will use for it too")

    def test_the_verdict_on_the_reengaged_turn_puts_the_card_back_in_needs_you(self):
        km._interrupt_block_tick(NOW, self.live)
        self._reengage()
        km._interrupt_block_tick(NOW, self.live)
        st = jd.load_goals(SID)                       # the judges audit that turn once it ends
        self.assertTrue(jd.record_verdict(st, st["nodes"][GID], "closer", "block", RESUME_T,
                                          why="which host for prod?"))
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked",
                         "the turn ended by asking the user — the card belongs in needs-you, not Working")

    def test_a_machine_cut_lift_is_stamped_the_same_way(self):
        # the audited shape: romp's own resume notice is the re-engagement, and the tick lifts on it
        km._interrupt_block_tick(NOW, self.live)
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(RESUME_T, sb.BOOT_RESUME_NUDGE, "u3", "u2")) + "\n")
            f.write(json.dumps(aline(RESUME_T + 40, "picked it up; which host for prod?",
                                     "a2", "u3", "end_turn")) + "\n")
        km._parse_cache.clear()
        km._interrupt_block_tick(NOW, self.live)
        nd = jd.load_goals(SID)["nodes"][GID]
        self.assertEqual(_row(nd, "unblock")["ev_t"], RESUME_T)
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][GID], "planner", "block", RESUME_T, why="which host for prod?")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked",
                         "a restart-cut session that comes back and asks a question still reaches the user")


class TheTickPushesNothingInline(_TickFixture):
    """The tick's flips reach the feed through the writers' own dirty mark
    and pusher wake — the next cycle's rebuild — never through an inline _push_all on the tick. The
    stand-down lift (a marker whose block a judge now owns) writes nothing and so moves nothing.
    _views_dirty is a module global shared across the suite: each test records its own floor."""

    def setUp(self):
        super().setUp()
        self.pushes = []
        self._push = km._push_all
        km._push_all = lambda *a, **k: self.pushes.append(1)
        self._was_set = km._pusher_wake.is_set()
        km._pusher_wake.clear()

    def tearDown(self):
        km._push_all = self._push
        if self._was_set:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        super().tearDown()

    def test_the_block_marks_the_views_dirty_wakes_the_pusher_and_pushes_nothing_inline(self):
        floor = time.time()
        km._interrupt_block_tick(NOW, self.live)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked")
        self.assertEqual(self.pushes, [], "no inline push from the tick")
        self.assertTrue(km._pusher_wake.is_set(), "the writer woke the pusher; its next cycle carries the flip")
        self.assertGreater(km._views_dirty[0], floor, "…and marked the views dirty, so that cycle rebuilds")

    def test_the_lift_does_the_same(self):
        km._interrupt_block_tick(NOW, self.live)
        km._pusher_wake.clear()
        self._reengage()
        floor = time.time()
        km._interrupt_block_tick(NOW, self.live)
        self.assertEqual(jd.load_goals(SID)["status"][GID], "working")
        self.assertEqual(self.pushes, [])
        self.assertTrue(km._pusher_wake.is_set())
        self.assertGreater(km._views_dirty[0], floor)

    def test_a_stand_down_lift_moves_nothing(self):
        km._interrupt_block_tick(NOW, self.live)
        st = jd.load_goals(SID)                          # a judge now owns the block: the diary's last block
        self.assertTrue(jd.record_verdict(st, st["nodes"][GID], "closer", "block", RESUME_T,
                                          why="which host for prod?"))
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        self.assertEqual(km._intr_blocked(SID), GID, "the marker still points at the card")
        self._reengage()
        km._pusher_wake.clear()
        floor = km._views_dirty[0]
        km._interrupt_block_tick(NOW, self.live)
        self.assertIsNone(km._intr_blocked(SID), "the marker is retired")
        self.assertEqual(jd.load_goals(SID)["status"][GID], "blocked", "…and the judge's block stands")
        self.assertEqual(self.pushes, [], "nothing new to show: no push")
        self.assertEqual(km._views_dirty[0], floor, "no store write, no dirty mark")
        self.assertFalse(km._pusher_wake.is_set(), "and no wake")


if __name__ == "__main__":
    unittest.main()
