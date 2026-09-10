#!/usr/bin/env python3
"""An auto-nudge must NOT reopen an already-completed goal (the user 2026-06-30). Repro of the
completed→blocked regression: the auto-nudge fires on a 'working' goal, a later judge pass completes it, then
the agent's nudge-reply ("…blocked on you: waiting for your go-ahead") is processed as a nudge unit. The OLD
nudge-phase called _reopen() unconditionally — un-completing the goal — and then re-blocked it from the reply,
so a completed card flipped to blocked (rollup precedence: blocked > completed). The fix: if the nudge target
is already done, the nudge is moot → place nothing, leave it completed. Synthetic transcript only.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_nudgereopen", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
GID = SID + ":g1"


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class NudgeNoReopenCompleted(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        # a completed top goal (the closer finished it)
        self.store = {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V,
                      "nodes": {GID: {"id": GID, "text": "Clarify the design", "parentId": None,
                                      "nodeComplete": True, "blocked": False, "cleared": False,
                                      "trail": [], "t": 1000, "mt": 2000}},
                      "placements": {}, "status": {GID: "completed"}, "lastNode": GID}
        jd.save_goals(SID, self.store)
        # stubs: a nudge resolution that, if it ran, would BLOCK the goal (so the bug would manifest)
        self._plan_calls = []
        self._saved = (jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.plan_llm = lambda *a, **k: self._plan_calls.append(k) or '{"ops":[{"do":"block","n":1,"why":"waiting on the user"}]}'
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None

    def tearDown(self):
        jd.plan_llm, jd.opener_llm, jd._group_store = self._saved

    def _nudge_transcript(self, declared_todo=None):
        """One ENDED turn opened by an AUTO-nudge message (romp-injected + romp-auto + romp-goal-id) targeting
        GID, with an assistant 'waiting on you' reply → classified as a nudge unit for GID. `declared_todo`
        prepends a prior turn declaring that item OPEN via the Task tool, so _sync_declared_plan (which reads
        the WHOLE session) sees the agentTask fixture's item as genuinely still open instead of flipping it
        to completed for being absent from the declared plan."""
        path = os.path.join(self.td, SID + ".jsonl")
        t = 3000
        nudge_text = ("Status on the goal above: what's done, what's left, and is anything blocked?"
                      "<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: %s -->" % GID)
        recs = []
        parent = None
        if declared_todo:
            recs += [
                {"type": "user", "uuid": "u0", "parentUuid": None, "timestamp": _iso(t - 100),
                 "message": {"role": "user", "content": "do the migration"}},
                {"type": "assistant", "uuid": "ac0", "parentUuid": "u0", "timestamp": _iso(t - 98),
                 "message": {"role": "assistant", "stop_reason": "tool_use",
                             "content": [{"type": "tool_use", "id": "tc1", "name": "TaskCreate",
                                          "input": {"subject": declared_todo, "activeForm": "Working on it"}}]}},
                {"type": "user", "uuid": "rc0", "parentUuid": "ac0", "timestamp": _iso(t - 96),
                 "message": {"role": "user",
                             "content": [{"type": "tool_result", "tool_use_id": "tc1",
                                          "content": "Task #1 created successfully. Use TaskUpdate to update it."}]}},
                {"type": "assistant", "uuid": "a0", "parentUuid": "rc0", "timestamp": _iso(t - 94),
                 "message": {"role": "assistant", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "Planned; starting."}]}},
            ]
            parent = "a0"
        recs += [
            {"type": "user", "uuid": "n1", "parentUuid": parent, "timestamp": _iso(t),
             "message": {"role": "user", "content": nudge_text}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "n1", "timestamp": _iso(t + 2),
             "message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "Done — it's blocked on you: waiting for your go-ahead."}]}},
        ]
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        return path

    def test_auto_nudge_reply_does_not_reopen_or_block_a_completed_goal(self):
        path = self._nudge_transcript()
        # sanity: the segment really is classified as a nudge unit targeting GID
        sess = jd.parsed_session(SID, [path], time.time())
        units = [u for u in jd.plan_units(sess) if u[1] == "nudge"]
        self.assertEqual(len(units), 1, "the auto-nudge reply is a nudge unit")
        self.assertEqual(units[0][5], GID, "...targeting the completed goal")

        jd._plan_session(SID, path, time.time())

        store = jd.load_goals(SID)
        nd = store["nodes"][GID]
        self.assertTrue(nd.get("nodeComplete"), "the completed goal stays completed — never reopened")
        self.assertFalse(nd.get("blocked"), "and is NOT re-blocked by the nudge reply")
        self.assertFalse(any(e["kind"] == "reopen" for e in nd.get("log", [])),
                         "_reopen was never called (no reopen event in the diary)")
        self.assertEqual(store["status"].get(GID), "completed", "rolled-up status stays completed")
        self.assertEqual(self._plan_calls, [], "the nudge LLM resolution is never even invoked on a done goal")
        # the unit is recorded processed so it doesn't re-run every pass
        self.assertIn(units[0][0], store["placements"])

    def test_nudge_resolves_a_flat_done_umbrella_with_open_agent_todos(self):
        # track g9 (the user 2026-07-02): the closer flat-DONE'd + SETTLED the umbrella, but the agent's own
        # to-do list still holds OPEN items under it, so the board (authoritative tier) shows WORKING and
        # the FORK nudge fires. The moot-guard must NOT discard the reply — done/settled are the stale part
        # here — and open_menu's seal must let the open item through, so the planner can land the reply's
        # blocker on it (the agent can't self-block a to-do; this is the only place "blocked" gets said).
        cid = SID + ":g2"
        self.store["nodes"][GID].update({"settledDone": True, "settledAt": 2000})
        self.store["nodes"][cid] = {"id": cid, "text": "migrate the stores", "parentId": GID,
                                    "nodeComplete": False, "blocked": False, "cleared": False,
                                    "trail": [], "t": 1500, "mt": 1500, "agentBornOpen": True,
                                    "agentTask": {"key": "1", "status": "open", "raw": "pending"}}
        self.store["status"] = {GID: "working"}
        jd.save_goals(SID, self.store)
        captured = {}

        def fake_plan(text, menu_text, **k):
            captured.update(k); captured["menu"] = menu_text
            return '{"ops":[{"do":"block","goal":2,"why":"needs the push go-ahead from the user"}]}'
        jd.plan_llm = fake_plan
        path = self._nudge_transcript(declared_todo="migrate the stores")
        jd._plan_session(SID, path, time.time())
        store = jd.load_goals(SID)
        self.assertIn("menu", captured, "the planner IS consulted — open agent to-dos make the nudge non-moot")
        self.assertIn("migrate the stores", captured["menu"],
                      "the open to-do is IN the menu (authoritative-open pierces the done/settled seal)")
        self.assertEqual(captured.get("agent_open_nums"), [2],
                         "the note names exactly the agent's own open items")
        self.assertTrue(store["nodes"][cid]["blocked"], "the reply's blocker lands on the open item")
        self.assertEqual(store["nodes"][cid]["blockWhy"], "needs the push go-ahead from the user")
        jd.rollup_status(store, False)
        self.assertEqual(store["status"][GID], "blocked", "…and the goal rolls up blocked/needs-you")

    def test_a_done_goal_with_no_open_todos_is_still_moot(self):
        # the guard's original case (the user 2026-06-30) must be unchanged: done + settled, NO agent
        # to-dos → the reply is discarded, nothing reopened, the planner never invoked.
        self.store["nodes"][GID].update({"settledDone": True})
        jd.save_goals(SID, self.store)
        path = self._nudge_transcript()
        jd._plan_session(SID, path, time.time())
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][GID].get("nodeComplete"))
        self.assertFalse(store["nodes"][GID].get("blocked"))
        self.assertEqual(self._plan_calls, [], "still moot without open agent to-dos")

    def test_open_menu_seal_respects_a_user_clear_over_the_agent_list(self):
        # the user's cross-off outranks the agent's to-do list: a CLEARED umbrella seals its subtree even
        # when an agentTask-open item sits under it — the live to-do must not resurrect a card the user
        # dismissed. (The done/settled seal, by contrast, IS pierced — covered by the resolve test above.)
        cid = SID + ":g2"
        self.store["nodes"][GID].update({"cleared": True})
        self.store["nodes"][cid] = {"id": cid, "text": "migrate the stores", "parentId": GID,
                                    "nodeComplete": False, "blocked": False, "cleared": False,
                                    "trail": [], "t": 1500, "agentBornOpen": True,
                                    "agentTask": {"key": "1", "status": "open", "raw": "pending"}}
        menu = jd.open_menu(self.store)
        self.assertEqual([nd["id"] for nd in menu], [], "a user-cleared umbrella seals its live to-dos too")

    def test_reopen_of_a_completed_goal_writes_an_instrumentation_line(self):
        # The reopen of a completed goal is attributable from the goal's own DIARY now (the nudge-diag
        # side-log was retired with the P3.4 sweep, 2026-07-07): the reopen event carries the caller in why.
        Gx = SID + ":gx"
        st = {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V,
              "nodes": {Gx: {"id": Gx, "text": "x", "parentId": None, "nodeComplete": True,
                             "blocked": False, "cleared": False, "trail": [], "t": 1}},
              "placements": {}, "status": {}}
        jd._reopen(st, Gx, by="followup")
        ev = [e for e in st["nodes"][Gx]["log"] if e["kind"] == "reopen"]
        self.assertEqual(len(ev), 1)
        self.assertIn("followup", ev[0]["why"])


if __name__ == "__main__":
    unittest.main()
