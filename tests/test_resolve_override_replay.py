#!/usr/bin/env python3
"""A user resolve survives a racing judge-pass save (the user 2026-07-10).

The kernel's _resolve_node runs on a handler thread while a triage pass may
hold the same session's goal store in memory across a model call; both sides
write the whole store file (atomic rename, last writer wins), so the pass's
stale save could erase the user's resolve — the nodeComplete flag AND its
diary event alike, leaving nothing to re-derive the action from. Fix: the
kernel journals the resolve to overrides/<fsid>.jsonl before touching the
store (the cleared.jsonl pattern — the event is the write) and jd.load_goals
replays the journal idempotently, so the clobbered write re-applies on the
very next load.

All fixtures are SYNTHETIC (placeholder UUIDs, invented text).

Covers:
- the clobber interleave: a stale judge copy saved after the user's resolve
  erases it on disk; the next load_goals re-applies it, with exactly one
  user/done event in the node's log
- idempotence: when the resolve's own save survived, later loads add no
  second event
- the supersede guard: a later user reopen outranks the journal — replay
  must NOT re-complete a card the user deliberately reopened
- a journal line naming a node the store lacks is skipped without error
- a corrupt journal line is skipped, later lines still replay
- restore (task #27): an undo-clear whose nodes end up in NEITHER the live
  store nor the archive re-inserts from the journaled payload; a node alive
  in either file is left alone (a later re-clear parks it in the archive,
  and replay defers to that)
- followup/move (task #27): a clobbered optimistic reopen re-applies on the
  next load; a survived one is never doubled
- block (2026-07-16): a kernel-side block verdict (nudge-failed / interrupt)
  clobbered by a pass's stale save re-applies on the next load; a survived one
  is never doubled; a user reply at/after the stamp supersedes the replay
"""
import json
import shutil
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
import os

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-666666666666"
NID = "goal:1"
T0 = 1781100000


def _open_store():
    """A store with one OPEN top goal, in the on-disk shape load_goals expects."""
    return {"rompUuid": SID, "seq": 1, "placements": {}, "status": {NID: "working"},
            "nodes": {NID: {"id": NID, "parentId": None, "t": T0, "mt": T0,
                            "text": "wire the widget", "log": []}}}


def _publish_raw(store):
    """Write the store file DIRECTLY, bypassing save_goals' rebase. Since the store CAS (2026-07-22) a
    stale save REBASES instead of clobbering, so the old "pass erased it" interleave can no longer be
    produced through save_goals. This stands in for a store that lost an event by a route the CAS cannot
    cover — a crash between publish steps, or a writer that never went through load_goals — which is
    exactly what the override journal still backstops."""
    (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))


class ResolveOverrideReplay(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = Path(tempfile.mkdtemp())
        jd._rebind_state(self._td)             # STATE + every derived dir, incl. the overrides journal

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _user_events(self, store):
        return [e for e in store["nodes"][NID].get("log", [])
                if e.get("src") == "user" and e.get("kind") == "done"]

    def test_a_stale_pass_no_longer_erases_the_resolve(self):
        # Since the store CAS (2026-07-22) the pass's stale save REBASES onto the user's resolve instead
        # of clobbering it, so the resolve survives on disk directly — no heal round-trip needed.
        jd.save_goals(SID, _open_store())
        judge_copy = jd.load_goals(SID)               # the pass loads BEFORE the user acts
        # the user resolves: journal first, then apply + save (what _resolve_node does)
        jd.append_override(SID, NID, "resolve", T0 + 100)
        user_store = jd.load_goals(SID)               # replay applies the journaled resolve
        self.assertTrue(user_store["nodes"][NID].get("nodeComplete"))
        jd.save_goals(SID, user_store)
        jd.save_goals(SID, judge_copy)                # the pass's STALE save lands last (the race)
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertTrue(raw["nodes"][NID].get("nodeComplete"),
                        "the stale save rebases onto the resolve instead of erasing it")
        healed = jd.load_goals(SID)
        self.assertTrue(healed["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._user_events(healed)), 1, "and it is still recorded exactly once")
        self.assertEqual(healed["nodes"][NID]["mt"], T0 + 100)

    def test_the_journal_still_heals_a_store_missing_the_resolve(self):
        # BACKSTOP: the CAS covers concurrent saves, but not a store that lost the event another way
        # (crash mid-publish, a writer with no base revision). The journal replay still re-applies it.
        jd.save_goals(SID, _open_store())
        jd.append_override(SID, NID, "resolve", T0 + 100)
        self.assertTrue(jd.load_goals(SID)["nodes"][NID].get("nodeComplete"))
        _publish_raw(_open_store())                   # a store on disk WITHOUT the resolve
        healed = jd.load_goals(SID)
        self.assertTrue(healed["nodes"][NID].get("nodeComplete"), "the journaled resolve re-applies")
        self.assertEqual(len(self._user_events(healed)), 1)

    def test_surviving_resolve_gains_no_second_event(self):
        jd.save_goals(SID, _open_store())
        jd.append_override(SID, NID, "resolve", T0 + 100)
        jd.save_goals(SID, jd.load_goals(SID))        # the resolve's own save survives
        for _ in range(3):                            # every later load replays as a no-op
            store = jd.load_goals(SID)
            self.assertTrue(store["nodes"][NID].get("nodeComplete"))
            self.assertEqual(len(self._user_events(store)), 1)

    def test_later_user_reopen_outranks_the_journal(self):
        jd.save_goals(SID, _open_store())
        jd.append_override(SID, NID, "resolve", T0 + 100)
        store = jd.load_goals(SID)                    # resolve applies and survives
        jd.save_goals(SID, store)
        store = jd.load_goals(SID)                    # the user later reopens (follow-up / undo)
        self.assertTrue(jd.record_verdict(store, store["nodes"][NID], "user", "reopen", T0 + 200,
                                          why="Follow-up: not actually finished."))
        jd.save_goals(SID, store)
        healed = jd.load_goals(SID)                   # replay must NOT re-complete past the reopen
        self.assertFalse(healed["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._user_events(healed)), 1)

    def test_unknown_node_is_skipped(self):
        jd.save_goals(SID, _open_store())
        jd.append_override(SID, "goal:gone", "resolve", T0 + 100)
        store = jd.load_goals(SID)                    # no crash, nothing applied
        self.assertFalse(store["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._user_events(store)), 0)

    def test_corrupt_line_is_skipped_later_lines_replay(self):
        jd.save_goals(SID, _open_store())
        jd._overrides_dir().mkdir(parents=True, exist_ok=True)
        with (jd._overrides_dir() / (SID + ".jsonl")).open("a") as f:
            f.write("{not json\n")
        jd.append_override(SID, NID, "resolve", T0 + 100)
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._user_events(store)), 1)


class RestoreOverrideReplay(unittest.TestCase):
    """The undo-clear payload journal: replay re-inserts only truly-lost nodes."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._td = Path(tempfile.mkdtemp())
        jd._rebind_state(self._td)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    PAYLOAD = {NID: {"id": NID, "parentId": None, "t": T0, "mt": T0,
                     "text": "wire the widget", "nodeComplete": True, "log": []}}

    def test_lost_restore_reinserts_from_the_journal(self):
        # the clobber end-state: the node is in NEITHER the live store NOR the archive
        jd.save_goals(SID, {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {}})
        jd.append_restore(SID, self.PAYLOAD, {NID: "completed"}, T0 + 100)
        healed = jd.load_goals(SID)
        self.assertIn(NID, healed["nodes"])
        self.assertEqual(healed["nodes"][NID]["text"], "wire the widget")
        self.assertEqual(healed["status"].get(NID), "completed")

    def test_restore_defers_to_the_archive(self):
        # the user re-cleared after the undo: the archive owns the node again — replay must not revive it
        jd.save_goals(SID, {"rompUuid": SID, "seq": 1, "placements": {}, "status": {}, "nodes": {}})
        jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": dict(self.PAYLOAD), "status": {}})
        jd.append_restore(SID, self.PAYLOAD, {}, T0 + 100)
        store = jd.load_goals(SID)
        self.assertNotIn(NID, store["nodes"])

    def test_restore_leaves_a_live_node_alone(self):
        live = _open_store()
        live["nodes"][NID]["text"] = "wire the widget, renamed since"
        jd.save_goals(SID, live)
        jd.append_restore(SID, self.PAYLOAD, {}, T0 + 100)
        store = jd.load_goals(SID)
        self.assertEqual(store["nodes"][NID]["text"], "wire the widget, renamed since")


class FollowupMoveReplay(unittest.TestCase):
    """The optimistic follow-up / Move-to-Working journal ops."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._td = Path(tempfile.mkdtemp())
        jd._rebind_state(self._td)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _done_store(self):
        s = _open_store()
        s["nodes"][NID]["nodeComplete"] = True
        s["nodes"][NID]["log"] = [{"ev_t": T0 + 50, "src": "planner", "kind": "done", "at": T0 + 50}]
        s["status"][NID] = "completed"
        return s

    def _reopens(self, store):
        return [e for e in store["nodes"][NID].get("log", [])
                if e.get("src") == "user" and e.get("kind") == "reopen"]

    def test_a_stale_pass_no_longer_erases_the_followup_reopen(self):
        jd.save_goals(SID, self._done_store())
        judge_copy = jd.load_goals(SID)               # the pass holds the pre-reply store
        self.assertTrue(jd.optimistic_followup(SID, NID, now=T0 + 100))   # journals + applies + saves
        jd.save_goals(SID, judge_copy)                # the stale save now REBASES onto the reopen
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertFalse(raw["nodes"][NID].get("nodeComplete"),
                         "the stale save rebases onto the reopen instead of erasing it")
        healed = jd.load_goals(SID)
        self.assertFalse(healed["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._reopens(healed)), 1)

    def test_the_journal_still_heals_a_store_missing_the_followup(self):
        jd.save_goals(SID, self._done_store())
        self.assertTrue(jd.optimistic_followup(SID, NID, now=T0 + 100))
        _publish_raw(self._done_store())              # a store on disk WITHOUT the reopen
        healed = jd.load_goals(SID)
        self.assertFalse(healed["nodes"][NID].get("nodeComplete"), "the journaled reopen re-applies")
        self.assertEqual(len(self._reopens(healed)), 1)

    def test_survived_followup_is_never_doubled(self):
        jd.save_goals(SID, self._done_store())
        self.assertTrue(jd.optimistic_followup(SID, NID, now=T0 + 100))
        for _ in range(3):
            store = jd.load_goals(SID)
            self.assertEqual(len(self._reopens(store)), 1)

    def test_historical_move_journal_row_still_replays(self):
        # The "move" producer (user_move, the messageless Move to Working) was removed 2026-07-25 —
        # but journals are forever: a "move" row written before the removal must still replay its
        # reopen onto a store whose save raced it. Synthetic row, exactly what the old producer wrote.
        jd.save_goals(SID, self._done_store())
        jd.append_override(SID, NID, "move", T0 + 100)
        healed = jd.load_goals(SID)
        self.assertFalse(healed["nodes"][NID].get("nodeComplete"))
        self.assertEqual(len(self._reopens(healed)), 1)


class BlockOverrideReplay(unittest.TestCase):
    """The kernel-side block journal op (2026-07-16, g52): _mark_nudge_failed's diary row was erased
    by a planner pass's stale save while auto-nudge.json kept `failed` — the chip's retire path keyed
    on the erased row. The block now rides the journal like every other out-of-band write."""

    def setUp(self):
        self._saved_state = jd.STATE
        self._td = Path(tempfile.mkdtemp())
        jd._rebind_state(self._td)

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _blocks(self, store, src="nudge"):
        return [e for e in store["nodes"][NID].get("log", [])
                if e.get("src") == src and e.get("kind") == "block"]

    def test_a_stale_pass_no_longer_erases_the_nudge_block(self):
        # This is the g52 race, and the flicker the user saw on 2026-07-22: the block landed, a stale
        # pass republished a pre-block snapshot, and the card flashed back to 'working' for one push
        # before the next load healed it. The CAS now rebases, so the block never leaves disk at all.
        jd.save_goals(SID, _open_store())
        judge_copy = jd.load_goals(SID)               # the pass loads BEFORE the kernel stamps
        store = jd.load_goals(SID)                    # the kernel's write: verdict + journal + save
        why = "romp followed up once and the response didn't resolve this"
        self.assertTrue(jd.record_verdict(store, store["nodes"][NID], "nudge", "block", T0 + 100, why=why))
        jd.append_block(SID, NID, "nudge", why, T0 + 100)
        jd.save_goals(SID, store)
        jd.save_goals(SID, judge_copy)                # the stale save lands last (the g52 race)
        raw = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertTrue(raw["nodes"][NID].get("blocked"),
                        "the stale save rebases onto the block instead of erasing it — no working flicker")
        healed = jd.load_goals(SID)
        self.assertTrue(healed["nodes"][NID].get("blocked"))
        self.assertEqual(len(self._blocks(healed)), 1, "and the block is recorded exactly once")
        self.assertIn("didn't resolve", healed["nodes"][NID].get("blockWhy") or "")

    def test_the_journal_still_heals_a_store_missing_the_block(self):
        jd.save_goals(SID, _open_store())
        why = "romp followed up once and the response didn't resolve this"
        jd.append_block(SID, NID, "nudge", why, T0 + 100)
        _publish_raw(_open_store())                   # a store on disk WITHOUT the block
        healed = jd.load_goals(SID)
        self.assertTrue(healed["nodes"][NID].get("blocked"), "the journaled block re-applies")
        self.assertEqual(len(self._blocks(healed)), 1)

    def test_survived_block_is_never_doubled(self):
        # src "interrupt" here so both journaled srcs are covered by the suite
        jd.save_goals(SID, _open_store())
        store = jd.load_goals(SID)
        why = "you stopped this session mid-turn"
        self.assertTrue(jd.record_verdict(store, store["nodes"][NID], "interrupt", "block", T0 + 100, why=why))
        jd.append_block(SID, NID, "interrupt", why, T0 + 100)
        jd.save_goals(SID, store)
        for _ in range(3):                            # every later load replays as a no-op
            store = jd.load_goals(SID)
            self.assertTrue(store["nodes"][NID].get("blocked"))
            self.assertEqual(len(self._blocks(store, src="interrupt")), 1)

    def test_a_user_reply_supersedes_the_replay(self):
        # The interleave the guard exists for: the kernel stamps the block (journal + save), but the
        # USER's reply handler was already holding a pre-stamp copy — its save lands last, carrying
        # their reopen and not the block row. Replay must NOT re-block past the user's answer: the
        # chip/card would pin back on needs-you over an already-answered ask.
        jd.save_goals(SID, _open_store())
        user_copy = jd.load_goals(SID)                # the reply handler's pre-stamp copy
        store = jd.load_goals(SID)
        jd.record_verdict(store, store["nodes"][NID], "nudge", "block", T0 + 100, why="the ask")
        jd.append_block(SID, NID, "nudge", "the ask", T0 + 100)
        jd.save_goals(SID, store)
        jd.record_verdict(user_copy, user_copy["nodes"][NID], "user", "reopen", T0 + 150,
                          why="Follow-up: answered.")
        jd.save_goals(SID, user_copy)                 # the user's racing save REBASES onto the block row
        healed = jd.load_goals(SID)
        self.assertFalse(healed["nodes"][NID].get("blocked"),
                         "the user's newer reply still outranks the block — never re-blocked")
        # Since the CAS the block row is PRESERVED rather than erased, and the fold arbitrates: a user
        # event at a later ev_t outranks the judge's block, so the card reads unblocked with its history
        # intact (better than the old behavior, where the row had to be kept out of the store entirely).
        self.assertEqual(len(self._blocks(healed)), 1, "the block row survives as history")
        self.assertEqual(healed["status"].get(NID), "working", "and the rolled-up status is working")


if __name__ == "__main__":
    unittest.main()
