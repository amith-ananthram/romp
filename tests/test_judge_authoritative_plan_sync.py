#!/usr/bin/env python3
"""Authoritative-tier plan-sync (the user 2026-07-01): the agent's OWN live to-do list (Claude
Code's Task tool) is mirrored DETERMINISTICALLY into the goal graph as `agentTask` nodes, and an
agent-declared-OPEN item is authoritative — its open state trumps a judge/rollup 'done'.

Covers:
- em.declared_plan   — folding TaskCreate/TaskUpdate (+ results) into ordered {key,text,status};
                       a TaskCreate the CLI rejected (is_error result) is not a step.
- em.task_store_plan — the AUTHORITATIVE live task store read the sync prefers over the fold.
- jd._sync_declared_plan — find-or-create by stable Task id; idempotent; status refresh; reopen;
                       store-first sourcing (fold only when no store dir; unreadable store = loud skip).
- jd.rollup_status   — an agentTask-open descendant holds its top WORKING even when the umbrella
                       was flat-marked complete; a crossed-off item lets normal roll-up proceed.

All fixtures SYNTHETIC (invented text, placeholder UUIDs, hostname TESTHOST).
"""
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
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600

# Hermetic task-store root: em.task_store_plan reads $CLAUDE_CONFIG_DIR/tasks/<fsid> at call time, so
# pin it to an empty tmpdir for the whole module — every legacy test below exercises the FOLD branch
# deterministically instead of depending on the developer's real ~/.claude/tasks not knowing SID.
_ENV_TMP = tempfile.TemporaryDirectory()
_ENV_PREV = None


def setUpModule():
    global _ENV_PREV
    _ENV_PREV = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = _ENV_TMP.name


def tearDownModule():
    if _ENV_PREV is None:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
    else:
        os.environ["CLAUDE_CONFIG_DIR"] = _ENV_PREV
    _ENV_TMP.cleanup()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}] if text else [],
                        "stop_reason": stop}}


def tcreate(t, uuid, parent, subject, active, tool_id):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tool_id, "name": "TaskCreate",
                                     "input": {"subject": subject, "activeForm": active}}]}}


def tupdate(t, uuid, parent, task_id, status, tool_id):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tool_id, "name": "TaskUpdate",
                                     "input": {"taskId": task_id, "status": status}}]}}


def tcreate_raw(t, uuid, parent, inp, tool_id):
    """A TaskCreate with an arbitrary input — the malformed shapes the CLI rejects."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tool_id, "name": "TaskCreate", "input": inp}]}}


def tres(t, uuid, parent, tool_use_id, text, is_error=False):
    b = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if is_error:
        b["is_error"] = True
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [b]}}


def build_session(records, now=NOW, rompuuid=SID):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (rompuuid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(p), rompuuid=rompuuid, candidate_files=[str(p)], now=now)


def plan_session(items, now=NOW):
    """A synthetic session that declares `items` = [(subject, active, [(status_after,)...])] via the
    Task tool. Returns the parsed session. Task #N ids are assigned in creation order."""
    recs = [uline(T0, "run the migration", "u1")]
    parent, t, n = "u1", T0 + 5, 0
    for subject, active, updates in items:
        n += 1
        tc = "tc%d" % n
        recs.append(tcreate(t, "ac%d" % n, parent, subject, active, tc)); t += 1
        recs.append(tres(t, "rc%d" % n, "ac%d" % n, tc,
                         "Task #%d created successfully. Use TaskUpdate to update it." % n)); t += 1
        parent = "rc%d" % n
        for j, (status,) in enumerate(updates):
            tu = "tu%d_%d" % (n, j)
            recs.append(tupdate(t, "au%d_%d" % (n, j), parent, str(n), status, tu)); t += 1
            recs.append(tres(t, "ru%d_%d" % (n, j), "au%d_%d" % (n, j), tu, "Task #%d updated." % n)); t += 1
            parent = "ru%d_%d" % (n, j)
    recs.append(aline(t + 1, "On it.", "aend", parent, stop="end_turn"))
    return build_session(recs, now=now)


REJECTED = ("InputValidationError: TaskCreate failed due to the following issue:\n"
            "The required parameter `subject` is missing")


def rejected_plan_session(accepted=True):
    """A session whose TaskCreate calls the CLI REJECTED — a subject-less {agent_hint, prompt} call and a
    {tasks: [...]} batch, each answered by an is_error tool_result naming the missing field, so no task
    exists for either — after one accepted create (Task #1) unless `accepted` is False."""
    recs = [uline(T0, "run the migration", "u1")]
    parent, t = "u1", T0 + 5
    if accepted:
        recs.append(tcreate(t, "ac1", parent, "Design v3", "Designing v3", "tc1")); t += 1
        recs.append(tres(t, "rc1", "ac1", "tc1", "Task #1 created successfully. Use TaskUpdate to update it.")); t += 1
        parent = "rc1"
    recs.append(tcreate_raw(t, "ax1", parent, {"agent_hint": "overnight pipeline", "prompt": "run the thing"},
                            "tx1")); t += 1
    recs.append(tres(t, "rx1", "ax1", "tx1", REJECTED, is_error=True)); t += 1
    recs.append(tcreate_raw(t, "ax2", "rx1", {"tasks": [{"subject": "Rewire store"}]}, "tx2")); t += 1
    recs.append(tres(t, "rx2", "ax2", "tx2", REJECTED, is_error=True)); t += 1
    recs.append(aline(t + 1, "On it.", "aend", "rx2", stop="end_turn"))
    return build_session(recs)


REJECTED_UPDATE = ("InputValidationError: TaskUpdate failed due to the following issue:\n"
                   "The value of `status` must be one of pending, in_progress, completed")


def rejected_update_session(status="done", then=None):
    """One accepted create (Task #1) followed by a TaskUpdate the CLI REJECTED — a status outside its set,
    answered by an is_error tool_result — so the store still holds the item open; `then`, when given, is
    the status of an ACCEPTED update after it (the skip is per call, not per item)."""
    recs = [uline(T0, "run the migration", "u1"),
            tcreate(T0 + 5, "ac1", "u1", "Design v3", "Designing v3", "tc1"),
            tres(T0 + 6, "rc1", "ac1", "tc1", "Task #1 created successfully. Use TaskUpdate to update it."),
            tupdate(T0 + 7, "ax1", "rc1", "1", status, "tx1"),
            tres(T0 + 8, "rx1", "ax1", "tx1", REJECTED_UPDATE, is_error=True)]
    parent, t = "rx1", T0 + 9
    if then is not None:
        recs.append(tupdate(t, "au1", parent, "1", then, "tu1")); t += 1
        recs.append(tres(t, "ru1", "au1", "tu1", "Task #1 updated.")); t += 1
        parent = "ru1"
    recs.append(aline(t, "On it.", "aend", parent, stop="end_turn"))
    return build_session(recs)


def fresh_store():
    return {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {}}


def agent_nodes(store):
    return {nd["agentTask"]["key"]: nd for nd in store["nodes"].values() if nd.get("agentTask")}


class DeclaredPlanAdapter(unittest.TestCase):
    def test_folds_taskcreate_taskupdate_in_order(self):
        s = plan_session([
            ("Design v3", "Designing v3", [("completed",)]),
            ("Rewire store", "Rewiring store", []),
        ])
        items = em.declared_plan(s)
        self.assertEqual([it["text"] for it in items], ["Design v3", "Rewire store"],
                         "creation order preserved")
        self.assertEqual({it["key"]: it["status"] for it in items},
                         {"1": "completed", "2": "pending"})

    def test_last_update_wins(self):
        s = plan_session([("Phase 2a", "Doing 2a", [("in_progress",), ("completed",)])])
        self.assertEqual(em.declared_plan(s)[0]["status"], "completed")

    def test_empty_when_no_task_tool(self):
        s = build_session([uline(T0, "hi", "u1"), aline(T0 + 5, "hello", "a1", "u1")])
        self.assertEqual(em.declared_plan(s), [])

    def test_a_rejected_taskcreate_is_not_a_declared_step(self):
        """A TaskCreate the CLI rejected — its paired tool_result carries is_error and an InputValidationError
        naming the missing `subject` — created nothing, so it is not a step of the plan: neither the
        subject-less {agent_hint, prompt} call nor the {tasks: [...]} batch. Without the skip each folded
        to a keyless item with empty text (mirroring the kernel's _fold_tasks skip)."""
        self.assertEqual(em.declared_plan(rejected_plan_session(accepted=False)), [])
        items = em.declared_plan(rejected_plan_session())
        self.assertEqual([(it["key"], it["text"]) for it in items], [("1", "Design v3")],
                         "the accepted create (its result carries Task #N) still folds")

    def test_a_rejected_taskupdate_moves_no_step(self):
        """The TaskCreate skip's twin (the #942 review): a TaskUpdate the CLI rejected — its paired
        tool_result carries is_error — wrote nothing to the store, so the step keeps the status the store
        holds. Without the skip the refused value folded as the item's status. The skip is per call: an
        accepted update after the rejected one still applies (mirroring the kernel's _fold_tasks)."""
        self.assertEqual(em.declared_plan(rejected_update_session())[0]["status"], "pending")
        self.assertEqual(em.declared_plan(rejected_update_session(then="in_progress"))[0]["status"], "in_progress")


class SyncFindOrCreate(unittest.TestCase):
    def test_mints_one_node_per_item_idempotently(self):
        store = fresh_store()
        s = plan_session([("Phase A", "Doing A", []), ("Phase B", "Doing B", [])])
        self.assertTrue(jd._sync_declared_plan(store, s, "seg1", T0 + 50, prompt_uuid="u-trigger-1"))
        self.assertEqual(len(agent_nodes(store)), 2)
        # re-sync of the SAME session mutates nothing (no duplicate nodes, returns False)
        self.assertFalse(jd._sync_declared_plan(store, s, "seg1", T0 + 60))
        self.assertEqual(len(store["nodes"]), 2)
        for nd in store["nodes"].values():                       # minted as tops for the grouper to place
            self.assertIsNone(nd["parentId"])
            self.assertEqual(nd["agentTask"]["status"], "open")
            self.assertFalse(nd["nodeComplete"])
            # the syncing segment's trigger is the mirror's deep-link anchor (the user 2026-07-11: mirror
            # mints carried None, so once archived — parse-free projection — their text was a dead click)
            self.assertEqual(nd["promptUuid"], "u-trigger-1")

    def test_status_refresh_stamps_authoritative_done(self):
        store = fresh_store()
        jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [])]), "seg1", T0 + 50)
        nd = agent_nodes(store)["1"]
        self.assertEqual(nd["agentTask"]["status"], "open")
        # the agent crosses it off → the SAME node flips to authoritative-done
        done_s = plan_session([("Phase A", "Doing A", [("completed",)])])
        self.assertTrue(jd._sync_declared_plan(store, done_s, "seg2", T0 + 100))
        self.assertEqual(len(agent_nodes(store)), 1, "still one node (find-or-create by key)")
        self.assertEqual(nd["agentTask"]["status"], "done")
        self.assertTrue(nd["nodeComplete"])

    def test_crossoff_appends_the_completing_segment_to_the_trail(self):
        # DONE-ANCHOR, plan-sync edition (the user 2026-07-14): a mirror completed HERE (the agent crossed
        # it off) used to keep only its mint-time trail, so the distiller read nothing but the announcement
        # segment and the card's summary link anchored on a stub. The syncing segment — where the crossing-
        # off work lives — must ride the trail, mirroring the closer's recap append.
        store = fresh_store()
        jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [])]), "seg1", T0 + 50)
        nd = agent_nodes(store)["1"]
        self.assertEqual(nd["trail"], ["seg1"], "minted with the declaring segment")
        done_s = plan_session([("Phase A", "Doing A", [("completed",)])])
        jd._sync_declared_plan(store, done_s, "seg2", T0 + 100)
        self.assertEqual(nd["trail"], ["seg1", "seg2"],
                         "the completing segment joins the trail so distiller + fallback anchor can see it")
        # idempotent: a re-sync with the same segment never duplicates the trail entry
        jd._sync_declared_plan(store, done_s, "seg2", T0 + 110)
        self.assertEqual(nd["trail"], ["seg1", "seg2"])

    def test_reopen_clears_only_our_done(self):
        store = fresh_store()
        # born OPEN under watch, then completed → kept as authoritative-done
        jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [])]), "s0", T0)
        jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [("completed",)])]), "s1", T0 + 10)
        nd = agent_nodes(store)["1"]
        self.assertTrue(nd["nodeComplete"])
        # agent RE-OPENS it (completed → in_progress) → the done WE stamped is withdrawn
        jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [("in_progress",)])]), "s2", T0 + 20)
        self.assertEqual(nd["agentTask"]["status"], "open")
        self.assertFalse(nd["nodeComplete"])

    def test_completed_item_is_never_minted_retroactively(self):
        """The regression (the user 2026-07-01): an idle session's ALREADY-completed to-do items must NOT
        pop up as fresh completed cards. A done/cancelled item with no node is skipped, not minted."""
        store = fresh_store()
        s = plan_session([("Done phase", "Doing", [("completed",)]),
                          ("Cancelled phase", "Doing", [("cancelled",)]),
                          ("Live phase", "Doing", [])])
        jd._sync_declared_plan(store, s, "s1", T0 + 10)
        keys = set(agent_nodes(store))
        self.assertEqual(keys, {"3"}, "only the OPEN item (#3) is minted; done/cancelled backlog is not")
        self.assertEqual(agent_nodes(store)["3"]["agentTask"]["status"], "open")

    def test_backlog_done_node_self_heals_away(self):
        """A pre-fix backlog mint — a DONE agentTask node that was never watched-open (agentBornOpen absent)
        — is deleted on the next sync, clearing the flooded completed cards."""
        store = fresh_store()
        store["seq"] = 1
        store["nodes"]["11111111-2222-3333-4444-555555555555:g1"] = {
            "id": "11111111-2222-3333-4444-555555555555:g1", "text": "Phase 1 (already done)", "parentId": None,
            "nodeComplete": True, "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0,
            "agentTask": {"key": "1", "status": "done", "raw": "completed"}}   # NO agentBornOpen → backlog
        s = plan_session([("Phase 1 (already done)", "Doing", [("completed",)])])
        self.assertTrue(jd._sync_declared_plan(store, s, "s1", T0 + 10))
        self.assertEqual(agent_nodes(store), {}, "the born-done backlog node is self-healed away")

    def test_a_rejected_taskcreate_mints_no_placeholder_card(self):
        """What the fold-path leak cost: an item with empty text is minted as a standalone open card reading
        "(declared step)" that no TaskUpdate can ever close — no task with that key exists — so it holds
        the session working and re-mints after every clear. A rejected create must mint nothing."""
        store = fresh_store()
        changed = jd._sync_declared_plan(store, rejected_plan_session(accepted=False), "seg1", T0 + 50)
        self.assertEqual([nd["text"] for nd in store["nodes"].values()], [],
                         "rejected-only session: no mirror, no placeholder card")
        self.assertFalse(changed)
        store = fresh_store()
        self.assertTrue(jd._sync_declared_plan(store, rejected_plan_session(), "seg1", T0 + 50))
        self.assertEqual({k: nd["text"] for k, nd in agent_nodes(store).items()}, {"1": "Design v3"},
                         "only the accepted create is mirrored")
        self.assertNotIn("(declared step)", [nd["text"] for nd in store["nodes"].values()])

    def test_a_rejected_taskupdate_leaves_the_mirrored_node_open(self):
        """What the fold-path leak costs on the sync side: a rejected completing update read as the agent
        crossing the item off, so the mirror flipped to authoritative-done for a task the store still holds
        open. The node must stay open — the fold gave the sync no new status."""
        store = fresh_store()
        jd._sync_declared_plan(store, plan_session([("Design v3", "Designing v3", [])]), "seg1", T0 + 50)
        nd = agent_nodes(store)["1"]
        self.assertEqual(nd["agentTask"]["status"], "open")
        self.assertFalse(jd._sync_declared_plan(store, rejected_update_session(status="completed"), "seg2", T0 + 100),
                         "a rejected update is not new information: nothing to mutate")
        self.assertEqual(nd["agentTask"]["status"], "open", "the store still holds the item open")
        self.assertFalse(nd["nodeComplete"])

    def test_open_backlog_node_is_adopted_not_deleted(self):
        """A pre-fix OPEN agentTask node (marker absent) is ADOPTED (marker added), never deleted — so it
        keeps holding its goal working and is protected from the done-heal when it later completes."""
        store = fresh_store()
        store["seq"] = 1
        nid = "11111111-2222-3333-4444-555555555555:g1"
        store["nodes"][nid] = {"id": nid, "text": "Live phase", "parentId": None, "nodeComplete": False,
                               "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0,
                               "agentTask": {"key": "1", "status": "open", "raw": "in_progress"}}
        jd._sync_declared_plan(store, plan_session([("Live phase", "Doing", [("in_progress",)])]), "s1", T0 + 10)
        self.assertIn(nid, store["nodes"], "an open backlog node is kept")
        self.assertTrue(store["nodes"][nid].get("agentBornOpen"), "and adopted (marker added)")


class RollupAuthority(unittest.TestCase):
    def _umbrella_with_child(self, child_task_status, umbrella_complete=True):
        """An umbrella top flat-marked complete, with one child carrying an agentTask."""
        store = fresh_store()
        store["nodes"] = {
            "T": {"id": "T", "text": "umbrella", "parentId": None, "nodeComplete": umbrella_complete,
                  "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0},
            "C": {"id": "C", "text": "phase 2b", "parentId": "T", "nodeComplete": (child_task_status == "done"),
                  "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0,
                  "agentTask": {"key": "2", "status": child_task_status, "raw": "in_progress"}},
        }
        store["lastNode"] = "T"
        return store

    def test_open_todo_trumps_completed_umbrella(self):
        """The screenshot bug: the umbrella was flat-DONE'd but a to-do under it is still open, so the
        card must read WORKING, not completed."""
        store = self._umbrella_with_child("open")
        jd.rollup_status(store, True)                            # session closed → would normally settle-complete
        self.assertEqual(store["status"]["T"], "working",
                         "an authoritative-open descendant holds the top working")

    def test_crossed_off_todo_lets_umbrella_complete(self):
        """Positive control: once the agent crosses the item off, normal completion resumes."""
        store = self._umbrella_with_child("done")
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "completed")

    def test_no_done_rolldown_onto_open_subtree(self):
        """A completed sibling branch must not stamp done onto the open to-do's node via roll-down."""
        store = self._umbrella_with_child("open", umbrella_complete=False)
        # add a genuinely-done sibling so bottom-up doesn't complete T either
        store["nodes"]["D"] = {"id": "D", "text": "phase 1", "parentId": "T", "nodeComplete": True,
                               "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0}
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "working")
        self.assertFalse(store["nodes"]["C"].get("nodeComplete"),
                         "the open to-do node is never auto-completed")


class BlockedAuthority(unittest.TestCase):
    """Stalled-with-open-to-dos (plans/stalled-open-todos-nudge.md): the agent CANNOT self-mark a to-do
    blocked (Claude Code's to-do system has no such state), so when the fork nudge elicits "blocked because
    …" the PLANNER blocks the open agentTask node — and that block must STICK. The rollup contract that
    makes it stick: blocked outranks the authoritative-open tier (open_task only gates completeness), and
    the stale-block heal clears blocks only on COMPLETE nodes, which an open-task node never is. These pin
    that contract so the fork-nudge → planner-block → needs-you path can't silently break."""

    def _blocked_open_todo(self, umbrella_complete=False):
        store = fresh_store()
        store["nodes"] = {
            "T": {"id": "T", "text": "umbrella", "parentId": None, "nodeComplete": umbrella_complete,
                  "blocked": False, "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0},
            "C": {"id": "C", "text": "wire the adapter", "parentId": "T", "nodeComplete": False,
                  "blocked": True, "blockWhy": "needs the staging credentials from the user",
                  "cleared": False, "trail": ["seg0"], "t": T0, "mt": T0 + 10, "agentBornOpen": True,
                  "agentTask": {"key": "1", "status": "open", "raw": "pending"}},
        }
        store["lastNode"] = "T"
        return store

    def test_block_on_open_todo_rolls_up_blocked(self):
        store = self._blocked_open_todo()
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "blocked",
                         "a planner block on an open agent to-do pulls the top to blocked/needs-you")
        self.assertTrue(store["nodes"]["C"]["blocked"], "the raw block flag survives the rollup")
        self.assertEqual(store["nodes"]["C"]["blockWhy"], "needs the staging credentials from the user")

    def test_block_outranks_a_flat_done_umbrella(self):
        """Even when the closer flat-DONE'd the top, a blocked still-open to-do keeps the card in
        needs-you: the open-task authority holds the top un-complete, so any_blocked isn't short-circuited
        and the stale-block heal never fires."""
        store = self._blocked_open_todo(umbrella_complete=True)
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "blocked")

    def test_block_survives_a_declared_plan_resync(self):
        """_sync_declared_plan refreshes agentTask status but must not touch a planner block: while the
        agent's list still says the item is open, the block (+ its why) rides along un-clobbered."""
        store = self._blocked_open_todo()
        jd._sync_declared_plan(store, plan_session([("wire the adapter", "Wiring", [])]), "s1", T0 + 50)
        self.assertTrue(store["nodes"]["C"]["blocked"], "re-sync of a still-open item keeps the block")
        self.assertEqual(store["nodes"]["C"]["blockWhy"], "needs the staging credentials from the user")
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "blocked")

    def test_crossing_off_the_blocked_todo_heals_the_block(self):
        """Positive control: once the agent completes the item, the block's answer is moot — the sync
        stamps authoritative-done and the rollup's stale-block heal clears the raw flag + blockWhy.
        The TOP no longer auto-completes off its finished child (the 2026-07-15 verdicts-only flip):
        it stays working and is nominated to the closer instead."""
        store = self._blocked_open_todo()
        jd._sync_declared_plan(store, plan_session([("wire the adapter", "Wiring", [("completed",)])]),
                               "s2", T0 + 100)
        jd.rollup_status(store, True)
        self.assertEqual(store["status"]["T"], "working",
                         "the unruled top stays honestly open (no bottom-up completion)")
        self.assertEqual([nd["id"] for nd in jd._subtree_done_candidates(store)], ["T"],
                         "…and the finished subtree nominates it to the closer")
        self.assertFalse(store["nodes"]["C"]["blocked"], "a complete node can't stay blocked")
        self.assertNotIn("blockWhy", store["nodes"]["C"])


def forked_plan_session():
    """The 2026-07-09 g204 shape: the completing TaskUpdate RAN (its tool_result is in the file) but an
    api-error retry forked the transcript graph and the leaf's live chain bypasses it, so the fold's
    last word on task #1 is 'in_progress'. Records: create + in_progress on the live chain; the
    completed update + result on a DEAD branch (aend, the leaf, parents past them onto ru0)."""
    recs = [uline(T0, "ship the docs rewrite", "u1"),
            tcreate(T0 + 1, "ac1", "u1", "Ship the docs rewrite", "Shipping the docs", "tc1"),
            tres(T0 + 2, "rc1", "ac1", "tc1", "Task #1 created successfully. Use TaskUpdate to update it."),
            tupdate(T0 + 3, "au0", "rc1", "1", "in_progress", "tu0"),
            tres(T0 + 4, "ru0", "au0", "tu0", "Task #1 updated."),
            # the orphan branch: executed, recorded, then abandoned by the retry
            tupdate(T0 + 5, "aux", "ru0", "1", "completed", "tux"),
            tres(T0 + 6, "rux", "aux", "tux", "Task #1 updated."),
            # the surviving chain: the retry's settle parents past the orphan onto ru0
            aline(T0 + 20, "Done here.", "aend", "ru0", stop="end_turn")]
    return build_session(recs)


class StoreAuthoritativeSourcing(unittest.TestCase):
    """The plan-sync reads the LIVE task store, not the transcript fold (the 2026-07-09 phantom-open
    mirror): an api-error retry orphaned the completing TaskUpdate onto a dead transcript branch, the
    fold re-reported the item open forever, and the mirror card re-minted itself after every clear."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self._td.name

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev
        self._td.cleanup()

    def _store_dir(self):
        d = Path(self._td.name) / "tasks" / SID
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_item(self, key, subject, status):
        (self._store_dir() / ("%s.json" % key)).write_text(json.dumps(
            {"id": key, "subject": subject, "activeForm": None, "status": status}))

    def test_the_fold_loses_the_orphaned_completion(self):
        """Pin the failure mechanism itself: the completing update is IN the file but off the live
        chain, so the fold still says in_progress — this is why the fold cannot be the source."""
        self.assertEqual(em.declared_plan(forked_plan_session())[0]["status"], "in_progress")

    def test_store_completion_beats_the_fold(self):
        """The bug end-to-end: a mirror watched-open flips to authoritative-done from the STORE even
        though the transcript fold (orphaned completion) still claims the item is open."""
        store = fresh_store()
        s = forked_plan_session()
        jd._sync_declared_plan(store, s, "s0", T0 + 4)     # no store dir yet → fold mints it open
        nd = agent_nodes(store)["1"]
        self.assertEqual(nd["agentTask"]["status"], "open")
        self._write_item("1", "Ship the docs rewrite", "completed")
        self.assertTrue(jd._sync_declared_plan(store, s, "s1", T0 + 30))
        self.assertEqual(nd["agentTask"]["status"], "done")
        self.assertTrue(nd["nodeComplete"])

    def test_store_prevents_the_phantom_remint_after_a_clear(self):
        """The g204 loop: the phantom card was cleared (its node archived away), the stale fold still
        says open — with the store saying completed, the sync must NOT re-mint the mirror."""
        store = fresh_store()                              # the cleared node is gone entirely
        self._write_item("1", "Ship the docs rewrite", "completed")
        self.assertFalse(jd._sync_declared_plan(store, forked_plan_session(), "s1", T0 + 30))
        self.assertEqual(agent_nodes(store), {}, "a completed item is never minted retroactively")

    def test_no_store_dir_falls_back_to_the_fold(self):
        """A session with no live task store (legacy transcript, replay) keeps the fold path."""
        store = fresh_store()
        self.assertTrue(jd._sync_declared_plan(store, plan_session([("Phase A", "Doing A", [])]),
                                               "s0", T0 + 5))
        self.assertEqual(agent_nodes(store)["1"]["agentTask"]["status"], "open")

    def test_deleted_status_is_not_open(self):
        """A TaskUpdate-deleted item must not hold a goal open or mint a mirror."""
        store = fresh_store()
        self._write_item("1", "Ship the docs rewrite", "deleted")
        self.assertFalse(jd._sync_declared_plan(store, forked_plan_session(), "s1", T0 + 30))
        self.assertEqual(agent_nodes(store), {})

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod can't block root")
    def test_unreadable_store_skips_the_sync_loudly(self):
        """The store EXISTS but can't be read → the sync logs a task-store row and skips the pass —
        it must never silently degrade to the lossy fold (repo policy: fail loudly)."""
        store = fresh_store()
        d = self._store_dir()
        self._write_item("1", "Ship the docs rewrite", "completed")
        prev_errors = jd.ERRORS
        errlog = Path(self._td.name) / "judge-errors.jsonl"
        jd.ERRORS = errlog
        os.chmod(d, 0o000)
        try:
            self.assertFalse(jd._sync_declared_plan(store, forked_plan_session(), "s1", T0 + 30))
        finally:
            os.chmod(d, 0o755)
            jd.ERRORS = prev_errors
        self.assertEqual(agent_nodes(store), {}, "no fold fallback: the pass is skipped")
        rows = [json.loads(l) for l in errlog.read_text().splitlines()]
        self.assertEqual([r["err"] for r in rows], ["task-store"])
        self.assertEqual(rows[0]["judge"], "planner")
        self.assertEqual(rows[0]["fsid"], SID)
        self.assertIn("no silent fold", rows[0]["note"])


class TaskStorePlanReader(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self._td.name
        self.d = Path(self._td.name) / "tasks" / SID
        self.d.mkdir(parents=True)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._prev
        self._td.cleanup()

    def test_reads_items_in_numeric_order(self):
        for k, st in (("2", "pending"), ("10", "completed"), ("1", "in_progress")):
            (self.d / (k + ".json")).write_text(json.dumps({"id": k, "subject": "step " + k, "status": st}))
        items = em.task_store_plan(SID)
        self.assertEqual([it["key"] for it in items], ["1", "2", "10"])
        self.assertEqual(items[2]["status"], "completed")

    def test_missing_dir_is_none_and_empty_dir_is_authoritative(self):
        self.assertIsNone(em.task_store_plan("99999999-8888-7777-6666-555555555555"))
        self.assertEqual(em.task_store_plan(SID), [], "a readable empty dir IS the answer: no plan")

    def test_a_corrupt_item_file_is_skipped(self):
        (self.d / "1.json").write_text(json.dumps({"id": "1", "subject": "good", "status": "pending"}))
        (self.d / "2.json").write_text("{not json")
        self.assertEqual([it["key"] for it in em.task_store_plan(SID)], ["1"])


if __name__ == "__main__":
    unittest.main()
