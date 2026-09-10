#!/usr/bin/env python3
"""Same-tick nudge coalescing + goal-grounded nudge quotes (the user 2026-07-24).

The 2026-07-23 incident, all fixtures SYNTHETIC: a session came to rest with three top goals still
'working'. The tick sent three separate status checks in the same second; the SDK queue folded two of
them into ONE turn, whose single romp-goal-id parse hid the second goal — its response was never found,
so it was stamped nudge-failed against a reply that plainly resolved it. The third stamp raced the parse
of the still-landing second nudge turn and blocked its goal the same way. Meanwhile the nudges quoted the
users RAW minting fragments back (truncated dictation), while earlier nudges on the same goals had shown
the planner's titles — incoherent across two fires of the same goal.

What this file covers:
- _nudge_bundle_body: ONE message for several same-tick fires, numbered titles + whys, every goal id.
- _followup_body: an injected nudge on a FLAT goal quotes the TITLE form, never the raw mint quote;
  a TYPED follow-up keeps the mint-quote fallback.
- _nudge_fire_list: the last-moment store re-read drops goals the judges resolved mid-tick.
- _nudge_response_ready: the failed-stamp's gates — fold membership via _seg_followup_all, the
  parse-lag guard, and its 6h backstop.
- jd._seg_followup_all / jd._bundle_keys: the multi-target judge contract.
- _auto_nudge_session end-to-end: two due goals → one bundled send, per-goal records with fire time.
- prompt pins: the planner's bundled-nudge note and PLAN_SYS's cross-goal done sweep.
"""
import inspect
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bundle", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
G1, G2, G3 = SID + ":g1", SID + ":g2", SID + ":g3"
NOW = 1781100000
T0 = NOW - 3600


def _node(nid, text, parent=None, **kw):
    d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
         "blocked": False, "cleared": False, "t": T0, "mt": T0, "log": []}
    d.update(kw)
    return d


def _store(nodes, status=None):
    return {"rompUuid": SID, "seq": len(nodes), "nodes": nodes, "placements": {},
            "status": status if status is not None else {n: "working" for n in nodes}}


def _atom(uuid, text, typ="user"):
    # the shape jd._atom_text reads: message.content text blocks
    return {"uuid": uuid, "type": typ, "t": T0,
            "message": {"content": [{"type": "text", "text": text}]}}


def _nudge_seg(seg_id, gids):
    """A synthetic parsed nudge segment whose trigger carries romp-injected + one goal-id marker per gid
    (the bundled form; two gids also models the SDK folding two separately-sent nudges into one turn)."""
    text = ("Where does this stand?" + "<!-- romp-injected --><!-- romp-auto -->"
            + "".join("<!-- romp-goal-id: %s -->" % g for g in gids))
    return {"id": seg_id, "t": T0, "trigger": "u1", "atoms": [_atom("u1", text)]}


class SegFollowupAll(unittest.TestCase):
    def test_every_listed_goal_id_in_order(self):
        seg = _nudge_seg("s1", [G1, G2, G3])
        self.assertEqual(jd._seg_followup_all(seg), [G1, G2, G3])
        self.assertEqual(jd._seg_followup(seg), G1, "the single-target parse stays the FIRST id (primary)")

    def test_single_and_none_and_dedup(self):
        self.assertEqual(jd._seg_followup_all(_nudge_seg("s1", [G2])), [G2])
        self.assertEqual(jd._seg_followup_all({"id": "s0", "atoms": [], "trigger": None}), [])
        self.assertEqual(jd._seg_followup_all(_nudge_seg("s1", [G1, G1, G2])), [G1, G2],
                         "a repeated marker (echoed text) dedups")


class BundleKeys(unittest.TestCase):
    def test_first_target_owns_the_bare_seg_id_but_is_processed_last(self):
        # the bare seg_id is the unit's collection key: written LAST so a crash mid-bundle leaves the
        # unit re-collectable, while suffixed keys let finished targets dedup on the re-run.
        self.assertEqual(jd._bundle_keys("s1", [G1, G2, G3]),
                         [(G2, "s1#n2"), (G3, "s1#n3"), (G1, "s1")])
        self.assertEqual(jd._bundle_keys("s1", [G1]), [(G1, "s1")], "a single target is unchanged")


class NudgeQuotePrecedence(unittest.TestCase):
    """An injected nudge on a FLAT goal quotes the planner's TITLE + why — never the raw minting
    fragment (the user 2026-07-24). The mint quote stays the TYPED follow-up's fallback (g13 was about
    the USER's voice; a nudge is romp speaking)."""

    def setUp(self):
        self._orig_load = jd.load_goals
        nodes = {G1: _node(G1, "Assess switching the detector to HD",
                           why="Investigate whether HD detection is viable now.",
                           quote="And another question I had was the previous agent we went through")}
        jd.load_goals = lambda sid: _store(nodes)

    def tearDown(self):
        jd.load_goals = self._orig_load

    def test_injected_flat_nudge_quotes_title_and_why_not_the_mint_quote(self):
        out = km._followup_body(G1, None, km.AUTO_NUDGE_TEXT, injected=True, auto=True)
        self.assertIn("> Assess switching the detector to HD", out, "the planner's title leads the quote")
        self.assertIn("> Investigate whether HD detection is viable now.", out, "the planner's why rides along")
        self.assertNotIn("previous agent", out, "the raw minting fragment is never quoted by a nudge")

    def test_typed_followup_keeps_the_mint_quote_fallback(self):
        out = km._followup_body(G1, None, "sounds good")
        self.assertIn("> And another question I had was the previous agent", out,
                      "a TYPED follow-up still re-raises the thread in the user's own words")


class NudgeBundleBody(unittest.TestCase):
    def _nodes(self):
        return {G1: _node(G1, "Ship the auth refactor", why="End-to-end auth revamp."),
                G2: _node(G2, "Write the migration guide"),
                G3: _node(G3, "Fix the flaky login test", agentTask={"status": "open"}),
                SID + ":a": _node(SID + ":a", "Add CSRF tokens", parent=G1, blocked=True,
                                  blockWhy="Pick the token TTL.")}

    def test_one_message_numbers_every_goal_and_carries_every_marker(self):
        out = km._nudge_bundle_body([G1, G2, G3], self._nodes(), set())
        self.assertIn("> 1. Ship the auth refactor — End-to-end auth revamp.", out)
        self.assertIn("> 2. Write the migration guide", out)
        self.assertIn("> 3. Fix the flaky login test", out)
        self.assertIn("Where do these 3 stand?", out)
        for g in (G1, G2, G3):
            self.assertIn("<!-- romp-goal-id: %s -->" % g, out, "every bundled goal id must ride the tail")
        self.assertIn("<!-- romp-injected -->", out)
        self.assertIn("<!-- romp-auto -->", out)

    def test_hierarchical_goal_enumerates_its_open_pieces(self):
        out = km._nudge_bundle_body([G1, G2], self._nodes(), set())
        self.assertIn("• Add CSRF tokens (blocked) — Pick the token TTL.", out)

    def test_stalled_goals_get_the_fork_line_by_number(self):
        out = km._nudge_bundle_body([G1, G3], self._nodes(), {G3})
        self.assertIn("On #2 you've still got open items on your to-do list", out)
        out2 = km._nudge_bundle_body([G1, G2], self._nodes(), set())
        self.assertNotIn("to-do list", out2, "no fork line when nothing is agent-open")

    def test_long_piece_lists_are_capped(self):
        nodes = {G1: _node(G1, "Big umbrella")}
        for i in range(9):
            nid = SID + (":p%d" % i)
            nodes[nid] = _node(nid, "piece %d" % i, parent=G1)
        out = km._nudge_bundle_body([G1], nodes, set())
        self.assertIn("…and 3 more", out, "9 open pieces cap at 6 + a count")


class NudgeFireList(unittest.TestCase):
    """The last-moment re-read (the closer race): a nudge once fired in the same second the closer
    marked its goal done — the tick's snapshot predated the verdict."""

    def test_resolved_goals_drop_and_working_goals_keep(self):
        fresh = _store({G1: _node(G1, "still open"),
                        G2: _node(G2, "closed meanwhile", nodeComplete=True),
                        G3: _node(G3, "blocked meanwhile", blocked=True)},
                       status={G1: "working", G2: "completed", G3: "blocked"})
        out = km._nudge_fire_list(fresh, [(G1, 1, False), (G2, 2, False), (G3, 1, True)])
        self.assertEqual([f[0] for f in out], [G1])

    def test_cleared_and_vanished_goals_drop(self):
        fresh = _store({G1: _node(G1, "crossed off", cleared=True)})
        fresh["status"][G1] = "cleared"                # the rollup's export (one truth, 2026-08-13):
        #                                                every writer rollups before save, and a cleared
        #                                                node's status IS "cleared" (judge rollup_status)
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False), (G2, 1, False)]), [],
                         "a cleared card and a compacted-away card both lose their nudge")

    def test_status_rolled_goal_drops_even_with_clean_flags(self):
        fresh = _store({G1: _node(G1, "rolled")}, status={G1: "completed"})
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False)]), [])


class NudgeResponseReady(unittest.TestCase):
    """The failed-stamp's structural gates. The stamp is the INTERRUPT: it must wait for the response
    to be VISIBLE and RULED — the 2026-07-23 stamps blocked two goals against a reply that resolved
    them, one hidden by the fold's first-id-only parse, one by parse lag."""

    def setUp(self):
        self._orig_segs = jd._segs

    def tearDown(self):
        jd._segs = self._orig_segs

    def _turns(self, arm_atoms_now=3, extra=None):
        arm = {"id": "arm", "t": T0, "end": T0 + 10, "ended": True,
               "atoms": [{}] * arm_atoms_now, "segs": []}
        return [arm] + (extra or [])

    def _rec(self, **kw):
        rec = {"count": 1, "lastTurnId": "arm", "armAtoms": 3, "at": NOW - 60}
        rec.update(kw)
        return rec

    def test_arm_turn_unchanged_means_the_response_has_not_arrived(self):
        ready, resp = km._nudge_response_ready(self._turns(), _store({}), self._rec(), G2, NOW)
        self.assertFalse(ready, "armAtoms unchanged AND still the last turn → nothing has happened yet")

    def test_parse_lag_holds_the_stamp_for_a_modern_record(self):
        # the arm turn GREW (the send folded in) but this goal's segment isn't visible yet: a modern
        # record was sent WITH markers by construction, so a missing segment is parse lag, not legacy.
        jd._segs = lambda tn, store: tn.get("segs", [])
        turns = self._turns(arm_atoms_now=5)
        ready, resp = km._nudge_response_ready(turns, _store({}), self._rec(), G2, NOW)
        self.assertFalse(ready, "no visible segment for a modern record → wait for the parse")

    def test_the_6h_backstop_still_surfaces_a_lost_send(self):
        jd._segs = lambda tn, store: tn.get("segs", [])
        turns = self._turns(arm_atoms_now=5)
        ready, resp = km._nudge_response_ready(turns, _store({}), self._rec(at=NOW - 7 * 3600), G2, NOW)
        self.assertTrue(ready, "a segment that never shows within the backstop stamps anyway (fail loud)")

    def test_legacy_record_keeps_stamp_now(self):
        jd._segs = lambda tn, store: tn.get("segs", [])
        rec = {"count": 1, "lastTurnId": "arm"}          # pre-gate record: no armAtoms, no at
        ready, resp = km._nudge_response_ready(self._turns(), _store({}), rec, G2, NOW)
        self.assertTrue(ready)
        self.assertIsNone(resp)

    def test_folded_second_goal_is_found_and_waits_for_the_ruling(self):
        # ONE segment carrying BOTH goal ids (the fold / a bundle): the old first-id-only match made G2
        # invisible here, so it was stamped failed instantly. Now its segment is found; unplaced → wait.
        seg = _nudge_seg("resp-seg", [G1, G2])
        jd._segs = lambda tn, store: tn.get("segs", [])
        turns = self._turns(arm_atoms_now=5, extra=[
            {"id": "resp", "t": T0 + 20, "end": T0 + 30, "ended": True, "atoms": [{}], "segs": [seg]}])
        store = _store({})
        ready, resp = km._nudge_response_ready(turns, store, self._rec(), G2, NOW)
        self.assertFalse(ready, "visible but UNPLACED → the planner hasn't ruled; not needs-you yet")
        store["placements"]["resp-seg"] = None            # the planner processed it (even a no-op records a key)
        ready, resp = km._nudge_response_ready(turns, store, self._rec(), G2, NOW)
        self.assertTrue(ready, "placed → every gate passed; the stamp may proceed")
        self.assertEqual(resp["id"], "resp-seg")


class AutoNudgeBundlesSameTick(unittest.TestCase):
    """End-to-end through _auto_nudge_session: two goals due in the same tick → ONE bundled send,
    per-goal records (with the fire time), and no premature failed-stamp on the immediate next tick."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._autonudge_cache.clear()
        self._orig_km = {n: getattr(km, n) for n in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working",
            "_interrupt_suppresses_nudge", "_backend_queued", "_backend_rewind_pending",
            "_last_state", "_session_awaiting", "_turn_romp_injected", "_closer_settled",
            "_revivers_pending", "_pending_ops")}
        self._orig_jd = {n: getattr(jd, n) for n in ("parsed_session", "load_goals", "load_goals_shared_or_fault",
                                                     "_segs", "plan_units")}
        self._orig_backend = km.Sessions.backend_for
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a: False
        km._turn_romp_injected = lambda tn: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a: None
        km._pending_ops = {}
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        self.turns = [{"id": "t1", "t": T0, "end": T0 + 10, "ended": True, "atoms": [{}, {}, {}]}]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.store = _store({G1: _node(G1, "Ship the auth refactor"),
                             G2: _node(G2, "Write the migration guide")})
        # The walk's SNAPSHOT is the shared read-only view (2026-09-09); its writers and the fire list reload
        # fresh through load_goals. Both are stubbed: with only load_goals stubbed, the shared read found no
        # store file in a fresh process (and delegated to the stub), but under the parallel runner it read a
        # store an earlier module had left at the shared placeholder sid, and nothing was due.
        jd.load_goals = lambda sid: self.store
        jd.load_goals_shared_or_fault = lambda sid: (self.store, None)
        self.sent = []
        test = self

        class FakeBackend:
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(lambda sid: FakeBackend())

    def tearDown(self):
        for n, v in self._orig_km.items():
            setattr(km, n, v)
        for n, v in self._orig_jd.items():
            setattr(jd, n, v)
        km.Sessions.backend_for = self._orig_backend
        jd.STATE = self.saved_state
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _tick(self):
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        fired = km._auto_nudge_session({"sid": SID, "path": "/nonexistent.jsonl"},
                                       NOW, {}, nudged, {})
        return fired, nudged

    def test_two_due_goals_send_one_bundle_with_per_goal_records(self):
        fired, _ = self._tick()
        self.assertTrue(fired)
        self.assertEqual(len(self.sent), 1, "same-tick fires coalesce into ONE message")
        body = self.sent[0]
        self.assertIn("<!-- romp-goal-id: %s -->" % G1, body)
        self.assertIn("<!-- romp-goal-id: %s -->" % G2, body)
        self.assertIn("> 1. Ship the auth refactor", body)
        self.assertIn("> 2. Write the migration guide", body)
        recs = km._auto_nudge_data().get("nudged", {})
        for g in (G1, G2):
            self.assertEqual(recs[g]["lastTurnId"], "t1")
            self.assertEqual(recs[g]["armAtoms"], 3)
            self.assertEqual(recs[g]["at"], NOW, "the fire time feeds the parse-lag backstop")

    def test_the_immediate_next_tick_neither_resends_nor_stamps_failed(self):
        self._tick()
        self.sent.clear()
        fired, _ = self._tick()
        self.assertEqual(self.sent, [], "same arm turn → no re-fire")
        self.assertFalse(fired)
        recs = km._auto_nudge_data().get("nudged", {})
        self.assertFalse(any(r.get("failed") for r in recs.values()),
                         "no response visible yet → the stamp waits (no premature needs-you block)")

    def test_a_goal_the_closer_resolved_mid_tick_is_dropped_from_the_bundle(self):
        # first load (the tick's snapshot) shows both working; the RE-READ at send time shows G2 done.
        snap = self.store
        done = _store({G1: _node(G1, "Ship the auth refactor"),
                       G2: _node(G2, "Write the migration guide", nodeComplete=True)},
                      status={G1: "working", G2: "completed"})
        jd.load_goals_shared_or_fault = lambda sid: (snap, None)   # the tick's snapshot
        jd.load_goals = lambda sid: done                            # the send-moment re-read
        self._tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("<!-- romp-goal-id: %s -->" % G1, self.sent[0])
        self.assertNotIn("<!-- romp-goal-id: %s -->" % G2, self.sent[0],
                         "romp must not status-check a goal that was completed as it asked")
        recs = km._auto_nudge_data().get("nudged", {})
        self.assertIn(G1, recs)
        self.assertNotIn(G2, recs, "a dropped goal gets no record — it never fired")

    def test_the_bundle_body_renders_the_send_moment_world(self):
        # T120 (the user 2026-08-27): the card body used to render the tick's snapshot while the
        # redundancy judge deliberated for seconds — a retitle landing in that gap shipped stale.
        # The send-moment re-read that drops resolved items also hands the body the fresh nodes.
        snap = self.store
        fresh = _store({G1: _node(G1, "Ship the auth refactor, retitled by the planner"),
                        G2: _node(G2, "Write the migration guide")})
        jd.load_goals_shared_or_fault = lambda sid: (snap, None)   # the tick's snapshot
        jd.load_goals = lambda sid: fresh                           # the send-moment re-read
        self._tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("retitled by the planner", self.sent[0],
                      "the delivered card speaks the send-moment world, not the snapshot")


class PromptPins(unittest.TestCase):
    def test_plan_sys_sweeps_other_cards_the_segment_resolved(self):
        # the user 2026-07-24: a multi-topic reply that answers another card's question in passing must
        # close that card too — the planner is the only judge whose menu shows EVERY open card (the
        # closer's is scoped to the turn's placements, by design).
        self.assertIn("never exempts the other cards", jd.PLAN_SYS)
        self.assertIn("Scan every listed card", jd.PLAN_SYS)

    def test_bundled_nudge_note_scopes_the_ruling_to_goal_one(self):
        src = inspect.getsource(jd.plan_llm)
        self.assertIn("bundled status checks", src)
        self.assertIn("ruled separately", src)

    def test_nudge_phase_resolves_every_bundled_target(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "judge.py")).read_text()
        self.assertIn("for target, _pkey in _bundle_keys(seg_id, targets):", src,
                      "the judge's nudge phase iterates ALL bundled targets, not just the first marker")


if __name__ == "__main__":
    unittest.main()


class NudgeFireListAwaitingHold(unittest.TestCase):
    """A LIVE awaiting stamp holds the fire (the 2026-08-19 audit): a status ask over a judge's own
    'this goal is waiting, nothing owed' ruling is the wrong-reason nudge — one fired 43 seconds
    AFTER the closer filed 'awaiting: the full test suite it kicked off', because the stamp's ev_t
    equals the seen turn's and the offending-rows check can never catch it. Parity with the other
    two writers (_wake_goal, _mark_nudge_failed), which both re-check the stamp at the write moment."""

    def test_a_live_awaiting_stamp_holds_the_fire(self):
        nd = _node(G1, "waiting on the suite")
        nd["awaitingWhy"] = "the full test suite it kicked off"
        nd["awaitingAt"] = 100
        fresh = _store({G1: nd, G2: _node(G2, "genuinely stalled")},
                       status={G1: "working", G2: "working"})
        out = km._nudge_fire_list(fresh, [(G1, 1, False), (G2, 1, False)])
        self.assertEqual([f[0] for f in out], [G2],
                         "the stamped goal is the judges' ruled wait; the unstamped one still fires")
