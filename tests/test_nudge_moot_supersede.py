#!/usr/bin/env python3
"""A verdict filed after the nudge's evidence supersedes the nudge (the user 2026-07-29).

The audited incident, all fixtures SYNTHETIC: a card sat blocked on a question ("open the PR for the
notes-api branch, or hold it?"). The user answered in the thread; the unblocker ruled the question
answered and lifted the block. Five seconds later the stall nudge fired anyway — its arm predated the
answer — and its response turn was cut by a kernel restart. The failed-nudge evaluator then scored
that cut turn as "the response didn't resolve this" and filed a needs-you block OVER the unblock,
presenting the already-answered decision brief. The user re-answered a question they had answered
five minutes earlier.

The discipline, at both ends of the race: a verdict the judges based on a newer world than the one
the nudge machinery is acting on means they have already ruled, so the machinery stands down —
- fire time: _nudge_fire_list drops a goal whose diary gained a verdict about EVIDENCE (ev_t) newer
  than the ARM turn, even if the goal reads plain 'working' in the fresh store (the freshly-unblocked
  case above). Evidence time, NOT filing time: rulings about the arm turn itself always FILE after
  it, so the original `at` comparison read the arm's own audit as a moved story and silently gagged
  every goal that audit touched (the same-trigger wedge, tests/test_awaiting_same_trigger_wedge.py);
- eval time: _mark_nudge_failed retires the record as `moot` (no failed chip, no block) when a
  non-nudge verdict was FILED (`at`) after the RESPONSE turn — there the question really is "did a
  judge look after the reply", and moot (stand down) is the safe direction.
A moot record keeps the anti-loop gate (lastTurnId pins the arm), and a genuinely still-stalled goal
re-arms on the next GENUINE ended turn, judged against the post-verdict world.

AND (the user 2026-07-30, the day after the guard shipped): only rows REAL judges filed supersede.
Placing a nudge reply always runs _reopen(by="nudge") on the target — filing a "reopened (nudge)" row
(src planner) whose arrival necessarily postdates the response turn — so the guard as first shipped
read the pipeline's own unseal as a fresh ruling and mooted EVERY placed-but-unresolved reply. The
audited card (synthetic here): correctly blocked-on-you ("the one remaining step is a live deploy,
which needs your credentials"), the reply-placement's reopen lifted the block, the planner resolved
only a sibling question card and left the goal working, and the evaluator stood down moot on that
same reopen row — no chip, no needs-you block, the anti-loop arm pinned, the card parked in Working
with no reviver until the user happened to message the session. jd.nudge_pipeline_row names the
machinery's own rows (src=="nudge" plus _reopen(by="nudge")'s two why strings) and the moot scan
skips them.

AND (2026-08-09): two more row shapes never moot — both matched the bare filed-after test on a
finished idle session and together silenced the escalation with no reviver left, the card wedged in
Working. A BLOCK row agrees with the escalation, so it can never mean stand down; a late-FILED block
about pre-response evidence (a slow closer pass) is filing time masquerading as a newer world, the
fire-side same-trigger lesson met again at the eval end. And an UNBLOCK evidenced by the RESPONSE
TURN ITSELF is the unblocker judging the very reply this evaluator scores and moving laterally — it
lifted a block and resolved nothing (the synthetic shape: the reply wrongly claimed a service restart
was already queued and nothing was owed; the unblocker believed it, the planner's nudge unit resolved
nothing), so the stall stands and the failed-nudge block must land. An unblock evidenced ELSEWHERE
(the 2026-07-29 shape above: the user's own earlier answer) still moots.
"""
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
km = load_source("romp_kernel_nudgemoot", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
G1 = SID + ":g1"
NOW = 1781100000
ARM_T = NOW - 600                # the ended turn the stall was armed on
RESP_T = NOW - 120               # the nudge-response turn the evaluator scores


def _node(nid, text, **kw):
    d = {"id": nid, "text": text, "parentId": None, "nodeComplete": False,
         "blocked": False, "cleared": False, "trail": [], "t": NOW - 3600, "mt": NOW - 3600, "log": []}
    d.update(kw)
    return d


def _store(nodes, status=None):
    return {"rompUuid": SID, "seq": len(nodes), "lastNode": G1, "nodes": nodes, "placements": {},
            "status": status if status is not None else {n: "working" for n in nodes}}


class FireListArmOrdering(unittest.TestCase):
    """_nudge_fire_list's arm_t guard: the stall inference is stale once the judges filed anything newer."""

    def test_a_verdict_on_newer_evidence_drops_the_fire(self):
        # the audited shape: the user's ANSWER opened a turn after the arm and the unblock rode it
        # (ev_t = the answer's trigger) → plain 'working' in the fresh store, but the "it looks
        # stalled" read predates the answer
        log = [{"ev_t": ARM_T, "src": "planner", "kind": "block", "why": "open the PR, or hold it?", "at": ARM_T + 5},
               {"ev_t": ARM_T + 290, "src": "unblocker", "kind": "unblock", "why": "answered in the thread",
                "at": ARM_T + 300}]
        fresh = _store({G1: _node(G1, "ship the notes-api", log=log)})
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False)], arm_t=ARM_T), [],
                         "the judges ruled on the answer's turn — the status check would ask about a moved story")

    def test_old_history_before_the_arm_still_fires(self):
        log = [{"ev_t": ARM_T - 900, "src": "planner", "kind": "block", "why": "?", "at": ARM_T - 890},
               {"ev_t": ARM_T - 800, "src": "unblocker", "kind": "unblock", "why": "answered", "at": ARM_T - 790}]
        fresh = _store({G1: _node(G1, "ship the notes-api", log=log)})
        self.assertEqual([f[0] for f in km._nudge_fire_list(fresh, [(G1, 1, False)], arm_t=ARM_T)], [G1],
                         "a diary that predates the arm is exactly the stalled case — the nudge stands")

    def test_a_row_without_at_still_counts_on_ev_t(self):
        log = [{"ev_t": ARM_T + 60, "src": "closer", "kind": "block", "why": "?"}]   # no `at` (older writer)
        fresh = _store({G1: _node(G1, "ship the notes-api", log=log)})
        self.assertEqual(km._nudge_fire_list(fresh, [(G1, 1, False)], arm_t=ARM_T), [])

    def test_no_arm_t_keeps_the_old_contract(self):
        log = [{"ev_t": ARM_T + 60, "src": "unblocker", "kind": "unblock", "why": "answered", "at": ARM_T + 70}]
        fresh = _store({G1: _node(G1, "ship the notes-api", log=log)})
        self.assertEqual([f[0] for f in km._nudge_fire_list(fresh, [(G1, 1, False)])], [G1],
                         "callers that pass no arm turn get the pre-guard behavior unchanged")


class NudgeFailedMootWhenSuperseded(unittest.TestCase):
    """_mark_nudge_failed retires (moot) instead of blocking when the diary moved past the response."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        # patch the KERNEL's own jd instance (km imports its own copy; a separately-loaded jd is a
        # different module object and the kernel would keep reading the live state dirs)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._autonudge_cache.clear()
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
        km._path_of = lambda sid, now=None: "/nonexistent"
        (td / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {G1: {"count": 1, "lastTurnId": "t1", "at": RESP_T - 5}}}))

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of = self._saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _write_store(self, log):
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            _store({G1: _node(G1, "ship the notes-api", log=log)})))

    def test_a_later_unblock_retires_the_record_instead_of_blocking(self):
        # the audited shape: the unblocker ruled "answered" AFTER the response turn the evaluator
        # is scoring — filing "the response didn't resolve this" would contradict the diary and
        # resurface the answered brief
        self._write_store([{"ev_t": RESP_T - 60, "src": "planner", "kind": "block", "why": "?", "at": RESP_T - 50},
                           {"ev_t": RESP_T - 10, "src": "unblocker", "kind": "unblock",
                            "why": "answered in the thread", "at": RESP_T + 30}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "moot")
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][G1]["blocked"],
                         "no procedural block over a diary that says the question was answered")
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertFalse(rec.get("failed"), "no 'nudge failed' chip either — the ask was superseded, not ignored")
        self.assertTrue(rec.get("moot"), "the episode is retired durably — the anti-loop arm stays pinned")

    def test_a_diary_that_did_not_move_still_blocks(self):
        self._write_store([{"ev_t": RESP_T - 300, "src": "planner", "kind": "block", "why": "?", "at": RESP_T - 290},
                           {"ev_t": RESP_T - 200, "src": "unblocker", "kind": "unblock", "why": "answered",
                            "at": RESP_T - 190}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][G1]["blocked"], "the genuine failed-nudge → block behavior stands")
        self.assertTrue(km._auto_nudge_data()["nudged"][G1].get("failed"))

    def test_a_nudge_row_after_the_response_does_not_moot(self):
        # only REAL judges supersede; the machinery's own rows never gag its own escalation
        self._write_store([{"ev_t": RESP_T + 5, "src": "nudge", "kind": "block",
                            "why": "procedural", "at": RESP_T + 10}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")

    def test_the_reply_placements_own_reopen_does_not_moot(self):
        # THE 2026-07-30 WEDGE: placing the reply files _reopen(by="nudge")'s row (src planner, so the
        # bare src=="nudge" exclusion missed it) after the response turn — that row is the pipeline
        # talking to itself, not a judge ruling on newer evidence, and reading it as one mooted every
        # placed-but-unresolved reply. The goal had been CORRECTLY blocked-on-you; the unseal lifted
        # the block, the planner left the goal working, and moot then killed the re-block — Working
        # forever, no reviver.
        self._write_store([{"ev_t": RESP_T - 60, "src": "closer", "kind": "block",
                            "why": "the one remaining step is a live deploy, which needs your credentials",
                            "at": RESP_T - 50},
                           {"ev_t": RESP_T, "src": "planner", "kind": "reopen",
                            "why": "reopened (nudge)", "at": RESP_T + 9}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][G1]["blocked"],
                        "the escalation re-blocks: an unresolved nudge on an idle session IS needs-you")
        self.assertTrue(km._auto_nudge_data()["nudged"][G1].get("failed"),
                        "the record settles failed (chip + block), not moot (silence)")

    def test_the_reply_placements_ancestor_unblock_does_not_moot(self):
        # _reopen(by="nudge")'s companion row on blocked ancestors — same machinery, same exclusion
        self._write_store([{"ev_t": RESP_T, "src": "planner", "kind": "unblock",
                            "why": "unblocked by reopen (nudge)", "at": RESP_T + 9}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")

    def test_a_genuine_verdict_alongside_the_pipelines_reopen_still_moots(self):
        # the exclusion is SURGICAL: a real judge row after the response supersedes exactly as before,
        # however many pipeline rows sit next to it
        self._write_store([{"ev_t": RESP_T, "src": "planner", "kind": "reopen",
                            "why": "reopened (nudge)", "at": RESP_T + 9},
                           {"ev_t": RESP_T + 2, "src": "unblocker", "kind": "unblock",
                            "why": "answered in passing", "at": RESP_T + 30}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "moot")
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][G1]["blocked"])

    def test_an_unblock_evidenced_by_the_response_itself_does_not_moot(self):
        # THE 2026-08-09 WEDGE (synthetic): the session finished its work; the closer blocked
        # ("restarting the api service is left to you"); the status-check reply wrongly claimed the
        # restart was already queued and nothing was owed; the unblocker believed it and unblocked —
        # evidenced by that very reply — while the planner's nudge unit resolved nothing. The bare
        # filed-after scan read the late closer block AND the reply-evidenced unblock as a moved
        # story: moot, no chip, no block, and no reviver left — Working forever on an idle session.
        self._write_store([
            {"ev_t": ARM_T, "src": "planner", "kind": "block",
             "why": "restart the api service yourself", "at": RESP_T - 25},
            {"ev_t": ARM_T, "src": "unblocker", "kind": "unblock",
             "why": "answered in passing: the work moved past the refusal", "at": RESP_T - 12},
            {"ev_t": ARM_T, "src": "closer", "kind": "block",
             "why": "the service restart is left to you", "at": RESP_T + 7},
            {"ev_t": RESP_T, "src": "unblocker", "kind": "unblock",
             "why": "answered in passing: the restart is already queued", "at": RESP_T + 80}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][G1]["blocked"],
                        "the escalation lands: an unresolved status check on an idle session IS needs-you")
        rec = km._auto_nudge_data()["nudged"][G1]
        self.assertTrue(rec.get("failed"))
        self.assertFalse(rec.get("moot"), "a lateral unblock off the reply itself is not a stand-down")

    def test_a_late_filed_block_does_not_moot(self):
        # a slow pass filing a BLOCK about pre-response evidence after the response turn: filing time
        # is not a newer world (the fire-side same-trigger lesson, at the eval end) — and a block
        # agrees with the escalation, so it can never mean stand down
        self._write_store([{"ev_t": ARM_T, "src": "closer", "kind": "block",
                            "why": "needs your credentials", "at": RESP_T + 40}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "failed")
        self.assertTrue(km._auto_nudge_data()["nudged"][G1].get("failed"))

    def test_a_moot_record_is_settled_and_never_reevaluated(self):
        self._write_store([{"ev_t": RESP_T - 10, "src": "unblocker", "kind": "unblock",
                            "why": "answered", "at": RESP_T + 30}])
        self.assertEqual(km._mark_nudge_failed(G1, ev_t=RESP_T), "moot")
        self.assertIsNone(km._mark_nudge_failed(G1, ev_t=RESP_T),
                          "a settled (moot) episode is skipped exactly like a failed one")
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][G1]["blocked"])


if __name__ == "__main__":
    unittest.main()
