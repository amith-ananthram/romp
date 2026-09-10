#!/usr/bin/env python3
"""The settled gate must not fire while the session is awaiting its own background work (the user
2026-08-08).

_session_closed read "the turn ended" (end_turn / an idle atom) as "the session handed back the
floor" — but a turn that ends with a live awaited background task has handed nothing back: the
harness re-invokes the session the moment the task's <task-notification> lands. Live case: a turn
ended announcing a batch of background comparisons; the rollup settled the focus card to Completed
inside the 57-second gap before the notification re-invoked the session, which then worked three
more minutes with the Working and Blocked columns empty (working chip, empty board). Sticky
completion then correctly refused to reopen without a user gesture, so the premature settle was
unrecoverable.

_session_settled keys on events, never timers: the transcript's launch↔notification pairing
(em._scan_bg_tasks — durable across kernel restarts, unlike any live snapshot) says what is still
awaited; the hold releases when the notification lands, when the launch predates the live CLI's
spawn (a ghost — its notification can never arrive), or when the closer has audited the launch's
turn without affirming a wait (a service, e.g. a dev server — otherwise it would hold the settle
forever). A live ⏳ awaiting stamp anywhere open re-affirms the hold past that audit.

SYNTHETIC fixtures only: placeholder UUIDs, invented prompts and task descriptions.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_settle_bg", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-666666666666"
T0 = 1780000000                      # prompt lands
LAUNCH = T0 + 60                     # background task dispatched
ENDED = T0 + 120                     # turn ends, task still running
BACK = T0 + 300                      # notification lands, session resumes
NOW = T0 + 600


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _prompt(t, uuid="p1"):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": None,
            "message": {"role": "user", "content": "run the comparison batch and report"}}


def _launch(tid, t):
    """A Bash run_in_background launch — the durable dispatch record."""
    return {"type": "assistant", "timestamp": _iso(t), "uuid": "a" + tid, "parentUuid": None,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": "./run-batch.sh", "run_in_background": True,
                           "description": "comparison batch"}}], "stop_reason": None}}


def _ack(tid, t):
    return {"type": "user", "timestamp": _iso(t), "uuid": "k" + tid, "parentUuid": None,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "started in background"}]}}


def _end(t, uuid="e1", text="Harness is running in the background; I'll pick it up when it finishes."):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _notification(tid, t):
    """The standalone <task-notification> user record that ends the wait (the dominant live shape)."""
    body = ("<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<status>completed</status>\n<summary>the batch finished</summary>\n"
            "</task-notification>" % (tid, tid))
    return {"type": "user", "timestamp": _iso(t), "uuid": "n" + tid, "parentUuid": None,
            "message": {"role": "user", "content": body}}


class SettleAwaitsBackgroundTasks(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        self.path = str(Path(self.td.name) / (SID + ".jsonl"))
        self.gid = SID + ":g1"

    def tearDown(self):
        self.td.cleanup()

    def _transcript(self, recs):
        prev = None
        for r in recs:                    # a real transcript is a uuid→parentUuid chain; the parse walks it
            r["parentUuid"] = prev
            prev = r["uuid"]
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._BG_SCAN_CACHE.clear()

    def _store(self, **node_extra):
        """A focus card the planner already ruled done (the done-but-unsettled shape the gate protects)."""
        nd = {"id": self.gid, "text": "Run the comparison batch", "parentId": None,
              "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
              "t": T0, "mt": ENDED,
              "log": [{"ev_t": ENDED, "src": "planner", "kind": "done",
                       "why": "the batch is dispatched", "at": ENDED}]}
        nd.update(node_extra)
        return {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V,
                "nodes": {self.gid: nd}, "placements": {}, "status": {}, "lastNode": self.gid}

    def _mid_wait(self):
        """Turn ended, launch unresolved — the live incident's 57-second gap."""
        self._transcript([_prompt(T0), _launch("t1", LAUNCH), _ack("t1", LAUNCH + 1), _end(ENDED)])
        return jd.parsed_session(SID, [self.path], NOW)

    def _after_back(self):
        """The notification landed, the session resumed and ended cleanly — nothing awaited."""
        self._transcript([_prompt(T0), _launch("t1", LAUNCH), _ack("t1", LAUNCH + 1), _end(ENDED),
                          _notification("t1", BACK),
                          _end(BACK + 60, uuid="e2", text="Batch read and folded in; done.")])
        return jd.parsed_session(SID, [self.path], NOW)

    # ---- the bug ----
    def test_hold_while_awaited_task_unresolved(self):
        session = self._mid_wait()
        store = self._store()
        self.assertTrue(jd._session_closed(session), "the raw floor still reads the turn as ended")
        self.assertFalse(jd._session_settled(SID, self.path, session, store),
                         "an ended turn with an unresolved awaited launch has not handed back the floor")

    def test_no_settle_while_awaited(self):
        session = self._mid_wait()
        store = self._store()
        jd.rollup_status(store, jd._session_settled(SID, self.path, session, store))
        self.assertEqual(store["status"][self.gid], "working",
                         "the done-but-unsettled card stays in Working through the wait")
        self.assertNotIn("settle", [e.get("kind") for e in store["nodes"][self.gid]["log"]],
                         "no settle event is filed mid-wait")
        self.assertIn(self.gid, store.get("confirming", []),
                      "the held card still carries the done-confirming cue")

    # ---- the releases ----
    def test_settles_once_notification_lands_and_turn_ends(self):
        session = self._after_back()
        store = self._store()
        self.assertTrue(jd._session_settled(SID, self.path, session, store))
        jd.rollup_status(store, jd._session_settled(SID, self.path, session, store))
        self.assertEqual(store["status"][self.gid], "completed")

    def test_ghost_launch_never_holds(self):
        """A launch from before the live CLI spawned can never be answered — no hold."""
        session = self._mid_wait()
        store = self._store()
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps({"spawnedAt": LAUNCH + 30}))
        self.assertTrue(jd._session_settled(SID, self.path, session, store))

    def test_audited_unstamped_launch_is_a_service(self):
        """The closer swept the launch's turn without affirming a wait — a dev-server-shaped task
        must not hold the settle forever."""
        session = self._mid_wait()
        store = self._store()
        store["closedTurns"] = [t["id"] for t in session["turns"]]
        self.assertTrue(jd._session_settled(SID, self.path, session, store))

    def test_live_awaiting_stamp_holds_past_the_audit(self):
        """A live ⏳ stamp on an open top re-affirms the wait even after the launch turn was swept."""
        session = self._mid_wait()
        store = self._store()
        g2 = SID + ":g2"
        store["nodes"][g2] = {"id": g2, "text": "Fold the batch results in", "parentId": None,
                              "nodeComplete": False, "blocked": False, "cleared": False,
                              "trail": [], "t": T0, "mt": ENDED,
                              "awaitingWhy": "waiting on the comparison batch", "awaitingAt": ENDED,
                              "log": [{"ev_t": ENDED, "src": "closer", "kind": "awaiting",
                                       "why": "waiting on the comparison batch", "at": ENDED}]}
        store["seq"] = 2
        store["closedTurns"] = [t["id"] for t in session["turns"]]
        self.assertFalse(jd._session_settled(SID, self.path, session, store))


if __name__ == "__main__":
    unittest.main()
