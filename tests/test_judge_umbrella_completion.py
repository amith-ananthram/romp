#!/usr/bin/env python3
"""Bottom-up completion is a TRIGGER now, not a rule (the user 2026-07-15, the load-testing card).

The old is_complete backstop — "a node with children, all complete, is complete" — was the one
completion path with no author, no evidence, and no diary row. It assumed children enumerate the
parent's work, but the planner files prerequisites/retries as children: "Run long soak
experiment" auto-completed (and settled, and rolled its checkmark down) when its "retry the
connection" child closed, though the experiment never ran. Now:
  - rollup_status is_complete = own verdict (nodeComplete) or the settledDone grandfather —
    never the children;
  - all-children-done NOMINATES the node to the CLOSER (_subtree_done_candidates rides the turn
    menu with a steps-finished note), whose done/block/considered-omission is the ruling;
  - a landed reply stamps the candidate's LOOK watermark (nd["closerLookT"], 2026-08-13 — the
    retired umbSig/starvedSig signatures could not see a verdict LANDING on an existing child,
    which orphaned a finished card for a day); a new FILING in the top subtree re-arms the ask.
All fixtures synthetic (placeholder UUIDs, invented text)."""
import json
import os
import tempfile
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
em = load_source("romp_event_model_umb", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_umb", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
T1 = T0 + 100


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "message": {"role": "user", "content": text}}
    if ps is not None:
        r["promptSource"] = ps
    return r


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def build_session(records, now=NOW, rompuuid=SID):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (rompuuid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(p), rompuuid=rompuuid, candidate_files=[str(p)], now=now)


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _row(kind, ev, src="closer", why=None):
    return {"ev_t": ev, "src": src, "kind": kind, **({"why": why} if why else {}), "at": ev}


def _node(s, g, text, parent=None, done=False, blocked=False, log=None, **extra):
    nd = {"id": SID + ":" + g, "text": text,
          "parentId": (SID + ":" + parent) if parent else None,
          "nodeComplete": done, "blocked": blocked, "cleared": False, "trail": [], "t": T0,
          "log": log if log is not None else
                 ([_row("done", T1)] if done else [_row("block", T1, why="need input?")] if blocked else [])}
    nd.update(extra)
    s["nodes"][nd["id"]] = nd
    return nd


class VerdictsOnlyCompletion(unittest.TestCase):
    def test_children_alone_never_complete_a_parent(self):
        # the load-testing shape: prerequisite child done, the parent's own ask (the experiment)
        # untouched — the parent must stay honestly working, with no settle and no authorless flip
        s = _store()
        _node(s, "g1", "Run long soak test on the second worker")
        _node(s, "g2", "Retried network connection to the second worker", parent="g1", done=True)
        jd.rollup_status(s, session_closed=True)
        top = s["nodes"][SID + ":g1"]
        self.assertEqual(s["status"][SID + ":g1"], "working", "no verdict, no completion")
        self.assertFalse(top.get("nodeComplete"))
        self.assertEqual([e.get("kind") for e in top["log"]], [], "no settle, no synthesized history")
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(s["status"][SID + ":g1"], "working", "idempotent — never flips on a later pass")

    def test_settled_grandfather_stays_completed(self):
        # a store completed under the OLD bottom-up rule has a settle event in its diary but no done
        # verdict; it must not wake up as Working after the flip (the migration guarantee)
        s = _store()
        _node(s, "g1", "an old umbrella", log=[_row("settle", T1, src="romp")])
        _node(s, "g2", "its finished step", parent="g1", done=True)
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(s["status"][SID + ":g1"], "completed",
                         "the settle event grandfathers the bottom-up era store")
        rows = len(s["nodes"][SID + ":g1"]["log"])
        jd.rollup_status(s, session_closed=True)
        self.assertEqual(len(s["nodes"][SID + ":g1"]["log"]), rows, "and nothing re-appends")


class SubtreeDoneCandidates(unittest.TestCase):
    def test_nomination_and_exclusions(self):
        s = _store()
        _node(s, "g1", "all steps finished, unruled")                     # the one true candidate
        _node(s, "g2", "finished step", parent="g1", done=True)
        _node(s, "b1", "blocked parent", blocked=True)                    # blocked → the block is the ruling
        _node(s, "b2", "finished step", parent="b1", done=True)
        _node(s, "o1", "parent with an open step")
        _node(s, "o2", "still open step", parent="o1")
        _node(s, "s1", "sealed under a done ancestor", done=True)
        _node(s, "s2", "unruled interior", parent="s1")
        _node(s, "s3", "finished leaf", parent="s2", done=True)
        _node(s, "t1", "agent still owes work here")
        _node(s, "t2", "open to-do", parent="t1", done=True,
              agentTask={"key": "k", "status": "open"})
        _node(s, "l1", "childless open leaf")
        cands = {nd["id"] for nd in jd._subtree_done_candidates(s)}
        self.assertEqual(cands, {SID + ":g1"},
                         "only the open, unruled, all-children-done, unsealed, no-open-todo parent")

    def test_look_stamp_gates_and_a_new_filing_rearms(self):
        s = _store()
        _node(s, "g1", "all steps finished, unruled")
        _node(s, "g2", "finished step", parent="g1", done=True)           # done row at=T1
        s["nodes"][SID + ":g1"]["closerLookT"] = T1                       # the closer looked at this world
        self.assertEqual(jd._subtree_done_candidates(s), [],
                         "nothing filed since the look → never re-asked")
        _node(s, "g3", "a newly finished step", parent="g1", done=True,
              log=[_row("done", T1 + 50)])                                # a NEW filing → re-arm
        self.assertEqual([nd["id"] for nd in jd._subtree_done_candidates(s)], [SID + ":g1"])

    def test_a_verdict_landing_on_an_existing_child_rearms(self):
        # THE g7 defect (2026-08-12): the retired child-id-set signature promised "a child's state
        # flipped" re-arms, but an id-only set never delivered it — a done verdict FILED on an
        # already-listed child left the umbrella orphaned forever. The filing watermark sees it.
        s = _store()
        _node(s, "g1", "all steps finished, unruled")
        g2 = _node(s, "g2", "finished step", parent="g1", done=True)
        s["nodes"][SID + ":g1"]["closerLookT"] = T1
        self.assertEqual(jd._subtree_done_candidates(s), [], "looked, nothing new")
        g2["log"].append(_row("done", T1 + 80, src="agent"))              # same child SET, new FILING
        self.assertEqual([nd["id"] for nd in jd._subtree_done_candidates(s)], [SID + ":g1"],
                         "the filing is the event — the id set never changed and must not matter")


class CloserRulesTheCandidate(unittest.TestCase):
    def setUp(self):
        self._llm = jd.closer_llm
        self.session = build_session([uline(T0, "run the power experiment", "u1", ps="typed"),
                                      aline(T0 + 20, "Connection restored; experiment still queued.", "a1", "u1")])
        self.turn = self.session["turns"][0]

    def tearDown(self):
        jd.closer_llm = self._llm

    def _cand_store(self):
        s = _store()
        _node(s, "g1", "Run long soak experiment")
        _node(s, "g2", "Retried the connection", parent="g1", done=True)
        return s

    def test_candidate_rides_the_menu_with_the_note_and_a_done_lands(self):
        s = self._cand_store()
        seen = {}
        jd.closer_llm = lambda tt, mt, *_a: (seen.update(mt=mt),
                                             '{"done": [{"goal": 1, "why": "the experiment ran to completion"}], "block": []}')[1]
        newly = jd._close_turn(s, self.turn)
        self.assertEqual(newly, [SID + ":g1"], "the closer's done on a candidate is a real completion")
        top = s["nodes"][SID + ":g1"]
        self.assertTrue(top["nodeComplete"])
        self.assertEqual([e for e in top["log"] if e.get("kind") == "done"][0].get("src"), "closer",
                         "the completion has an author and a diary row")
        self.assertEqual(top["trail"], [],
                         "DONE-ANCHOR is turn-menu-only (the user 2026-08-14): a candidate is ruled "
                         "from goal history, so the ruling turn's recap holds none of its work — "
                         "appending it aimed the card's deep-links at unrelated prose. The umbrella "
                         "keeps its organic trail (none here); its children's tails carry the links.")
        self.assertIn("Run long soak experiment", seen["mt"])
        self.assertIn("is finished", seen["mt"], "the steps-finished note rides the menu")
        self.assertGreaterEqual(s["nodes"][SID + ":g1"].get("closerLookT") or 0, T1,
                                "the landed reply stamps the look watermark (below apply, so the "
                                "reply's own filing is covered)")

    def test_considered_omission_stamps_and_the_goal_stays_open(self):
        s = self._cand_store()
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [], "block": []}'
        self.assertEqual(jd._close_turn(s, self.turn), [])
        self.assertFalse(s["nodes"][SID + ":g1"].get("nodeComplete"), "left open — steps are not the ask")
        self.assertGreaterEqual(s["nodes"][SID + ":g1"].get("closerLookT") or 0, T1)
        jd.closer_llm = lambda tt, mt, *_a: (_ for _ in ()).throw(
            AssertionError("an unchanged completion set must not re-run the closer"))
        self.assertEqual(jd._close_turn(s, self.turn), [], "stamped + nothing touched → no LLM call")

    def test_failed_parse_does_not_stamp(self):
        s = self._cand_store()
        jd.closer_llm = lambda tt, mt, *_a: ""                            # call failed → retry next pass
        self.assertIsNone(jd._close_turn(s, self.turn))
        self.assertIsNone(s["nodes"][SID + ":g1"].get("closerLookT"),
                          "no ruling landed → the candidate stays armed")


class StarvedCandidates(unittest.TestCase):
    """Evidence-starved open cards (the user 2026-07-17, quartz): minted, then never touched —
    no placement, no diary — so neither the turn menu nor subtree-done nomination can ever reach them.
    They ride the closer's menu once OTHER work in their top's subtree settles (the re-arm event), with
    a no-work-filed note; a landed reply stamps store["starvedSig"] so an unchanged settled set never
    re-badgers."""

    def _board(self):
        # the quartz shape: an umbrella whose campaign settled around two stale, never-touched cards
        s = _store()
        _node(s, "g1", "Fix the cache-size detection", umbrella=True)
        _node(s, "g2", "Config misreads the cache as unbounded", parent="g1")          # starved branch
        _node(s, "g3", "Deployed improved metric-trend detection fix", parent="g2")         # starved leaf
        _node(s, "g4", "Deployed config-pin build for instant detection", parent="g1", done=True, mt=T1)
        return s

    def test_nomination_needs_a_settled_sibling_and_skips_the_reachable(self):
        s = self._board()
        _node(s, "g5", "watch the campaign data land", parent="g1",
              log=[_row("unblock", T1, src="planner")])                   # diary row → reachable normally
        _node(s, "g6", "mirror of the agent's own to-do", parent="g1",
              agentTask={"key": "k", "status": "open"})                   # authoritative: still owed
        _node(s, "g7", "needs the user's call", parent="g1", blocked=True)
        cands = {nd["id"] for nd in jd._starved_candidates(s)}
        self.assertEqual(cands, {SID + ":g2", SID + ":g3"},
                         "only the untouched, unruled, unsettled-nowhere cards ride")

    def test_all_children_done_shape_belongs_to_the_subtree_done_channel(self):
        # the two nomination channels are exclusive: a node whose children are ALL done rides the
        # umbSig (steps-finished) nomination — the starved channel must never re-badger it after that
        # channel's stamp (caught live: the omission test's second look re-ran the closer).
        s = _store()
        _node(s, "g1", "Run long soak experiment")
        _node(s, "g2", "Retried the connection", parent="g1", done=True, mt=T1)
        self.assertEqual(jd._starved_candidates(s), [],
                         "all-children-done is the subtree-done channel's shape")

    def test_no_settled_sibling_no_nomination(self):
        s = _store()
        _node(s, "g1", "Fix the cache-size detection", umbrella=True)
        _node(s, "g2", "Config misreads the cache as unbounded", parent="g1")
        self.assertEqual(jd._starved_candidates(s), [],
                         "nothing settled since the mint → nothing to judge the card against")

    def test_look_stamp_gates_and_a_new_filing_rearms(self):
        s = self._board()
        for nid in (SID + ":g2", SID + ":g3"):
            s["nodes"][nid]["closerLookT"] = T1                # the closer looked at this world (g4's done)
        self.assertEqual(jd._starved_candidates(s), [],
                         "nothing filed since the look → never re-asked")
        _node(s, "g8", "Verified detection in offline mode", parent="g1", done=True,
              log=[_row("done", T1 + 50)])                     # a new FILING in the subtree → re-arm
        self.assertEqual({nd["id"] for nd in jd._starved_candidates(s)},
                         {SID + ":g2", SID + ":g3"}, "another piece filing re-arms the ask")

    def test_a_post_outage_filing_with_old_evidence_still_rearms(self):
        # the retired settled-set signature compared EVIDENCE times (mt >= mint) — a backlog sweep's
        # verdict carries an OLD ev_t but a brand-new FILING (`at`); the watermark keys on the filing
        s = self._board()
        for nid in (SID + ":g2", SID + ":g3"):
            s["nodes"][nid]["closerLookT"] = T1
        old_evidence = {"ev_t": T0 - 500, "src": "closer", "kind": "done", "at": T1 + 90}
        _node(s, "g9", "Swept-backlog completion", parent="g1", done=True, log=[old_evidence])
        self.assertEqual({nd["id"] for nd in jd._starved_candidates(s)},
                         {SID + ":g2", SID + ":g3"},
                         "old evidence, new information — the arrival domain sees it")


class CloserRulesTheStarved(unittest.TestCase):
    def setUp(self):
        self._llm = jd.closer_llm
        self.session = build_session([uline(T0, "check the detection campaign", "u1", ps="typed"),
                                      aline(T0 + 20, "The config-pin build shipped; detection is authoritative now.",
                                            "a1", "u1")])
        self.turn = self.session["turns"][0]

    def tearDown(self):
        jd.closer_llm = self._llm

    def _board(self):
        s = _store()
        _node(s, "g1", "Fix the cache-size detection", umbrella=True)
        _node(s, "g2", "Deployed improved metric-trend detection fix", parent="g1")
        _node(s, "g3", "Deployed config-pin build for instant detection", parent="g1", done=True, mt=T1)
        return s

    def test_starved_rides_the_menu_with_the_note_and_a_done_lands(self):
        s = self._board()
        seen = {}
        jd.closer_llm = lambda tt, mt, *_a: (seen.update(mt=mt),
                                             '{"done": [{"goal": 1, "why": "superseded by the shipped '
                                             'config-pin build"}], "block": []}')[1]
        newly = jd._close_turn(s, self.turn)
        self.assertEqual(newly, [SID + ":g2"], "the closer's done on a starved card is a real completion")
        nd = s["nodes"][SID + ":g2"]
        self.assertTrue(nd["nodeComplete"])
        self.assertEqual([e for e in nd["log"] if e.get("kind") == "done"][0].get("src"), "closer",
                         "the completion has an author and a diary row")
        self.assertIn("Deployed improved metric-trend detection fix", seen["mt"])
        self.assertIn("no work filed since creation", seen["mt"], "the no-work-filed note rides the menu")
        self.assertGreaterEqual(s["nodes"][SID + ":g2"].get("closerLookT") or 0, T1,
                                "the landed reply stamps the look watermark")

    def test_considered_omission_stamps_and_never_reasks(self):
        s = self._board()
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [], "block": []}'
        self.assertEqual(jd._close_turn(s, self.turn), [])
        self.assertFalse(s["nodes"][SID + ":g2"].get("nodeComplete"), "left open on purpose")
        self.assertGreaterEqual(s["nodes"][SID + ":g2"].get("closerLookT") or 0, T1)
        jd.closer_llm = lambda tt, mt, *_a: (_ for _ in ()).throw(
            AssertionError("an unchanged settled set must not re-run the closer"))
        self.assertEqual(jd._close_turn(s, self.turn), [], "stamped + nothing new settled → no LLM call")

    def test_failed_parse_does_not_stamp(self):
        s = self._board()
        jd.closer_llm = lambda tt, mt, *_a: ""                            # call failed → retry next pass
        self.assertIsNone(jd._close_turn(s, self.turn))
        self.assertIsNone(s["nodes"][SID + ":g2"].get("closerLookT"),
                          "no ruling landed → the card stays armed")


class PromptPins(unittest.TestCase):
    def test_closer_sys_carries_the_steps_finished_rule(self):
        self.assertIn("Steps-finished rule:", jd.CLOSER_SYS)
        self.assertIn("not a promised breakdown", jd.CLOSER_SYS)

    def test_closer_sys_carries_the_no_work_filed_rule(self):
        self.assertIn("No-work-filed rule:", jd.CLOSER_SYS)
        self.assertIn("the ruling needs the covering work, named", jd.CLOSER_SYS)


if __name__ == "__main__":
    unittest.main()
