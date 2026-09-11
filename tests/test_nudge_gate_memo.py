#!/usr/bin/env python3
"""The nudge walk's planner-placement gate is derived once per (parse, store) and served while both stand
(2026-09-09), and the walk reads the store through the shared read-only view.

Measured on the maintainer's box (py-spy over the live kernel, 90 s): the pusher's jobs stage was 77% of the
kernel's samples, _auto_nudge_tick 91% of that, and the gate's derivation (re-segmenting every turn, applying
the seams, building the plan units, normalizing every recorded placement key) the bulk of it, run for every
idle session on every cycle with nothing changed; the walk's fresh goal-store load per session per cycle was
most of the 66 loads a cycle. Pins: the gate is served on the second call and re-derived when the parse or the
store moves, its answer equals the direct derivation, a failed derivation is never cached, a parse the cache
does not hold is derived every time, the memo is bounded, the walk reads through the shared view and never a
fresh load, and the counters ride /perf.

Synthetic transcript, states and store under a temp root; a private synthetic sid (the goal-store fixture
rule); the closer is off so the walk reaches the gate without judge marks."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
from unittest.mock import patch

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_nudge_gate", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-888888888801"
NOW = 1_800_000_000
T0 = NOW - 3600


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


class _Gate(unittest.TestCase):
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
        self.saved = (jd.STATE, jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATESDIR, km.NAMES, jd.CLOSER_ON)
        jd._rebind_state(td)
        jd.NAMES, jd.PROJECTS = names, proj
        km.NAMES = names
        jd.CLOSER_ON = False
        jd.GOALDIR.mkdir(parents=True)
        (td / "states").mkdir()
        self.recs = [uline(T0, "wire up the reconnect banner", "u1"),
                     aline(T0 + 20, "done, the banner reconnects", "a1", "u1")]
        self._write(self.recs)
        g = {"id": SID + ":g1", "text": "Ship the reconnect banner", "parentId": None, "nodeComplete": False,
             "blocked": False, "cleared": False, "trail": [], "t": T0}
        self.store = {"rompUuid": SID, "seq": 1, "lastNode": g["id"], "closedTurns": [], "nodes": {g["id"]: g},
                      "placements": {}, "status": {g["id"]: "working"}}
        self._save_store()
        self._reset()

    def _write(self, recs):
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _save_store(self):
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(self.store))

    def _reset(self):
        km._parse_cache.clear()
        km._nudge_gate_memo.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        for k in km._NUDGE_GATE_STATS:
            km._NUDGE_GATE_STATS[k] = 0

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        (jd.STATE, jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATESDIR, km.NAMES, jd.CLOSER_ON) = self.saved
        self._reset()
        self.td.cleanup()

    def _turns(self, now=NOW):
        return jd.parsed_session(SID, [str(self.tpath)], now)["turns"]

    def _view(self):
        store, fault = jd.load_goals_shared_or_fault(SID)
        self.assertIsNone(fault)
        return store

    def _direct(self, turns, store):
        live = {sg["id"] for tn in turns for sg in jd._segs(tn, store)}
        return any(not jd._placed_key(store.get("placements") or {}, jd._unit_key(u[0], u[1]), live)
                   for u in jd.plan_units({"turns": turns}, store))


class GateMemo(_Gate):
    def test_derived_once_then_served_while_the_parse_and_the_store_stand(self):
        turns, store = self._turns(), self._view()
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            a = km._nudge_placement_gate(SID, turns, store)
            b = km._nudge_placement_gate(SID, turns, store)
            c = km._nudge_placement_gate(SID, self._turns(), self._view())   # the same cached parse and view
        self.assertEqual((a, b, c), (True, True, True), "an unplaced ended segment: the planner queue is not empty")
        self.assertEqual(pu.call_count, 1, "derived once; the two later calls were served")
        self.assertEqual(km._NUDGE_GATE_STATS, {"served": 2, "derived": 1})

    def test_the_answer_is_the_direct_derivation_before_and_after_the_placement_lands(self):
        turns, store = self._turns(), self._view()
        self.assertEqual(km._nudge_placement_gate(SID, turns, store), self._direct(turns, store))
        # the planner places the segment's work unit: the store moves, the gate re-derives and now reads clear
        seg_id = next(u[0] for u in jd.plan_units({"turns": turns}, store) if u[1] == "work")
        self.store["placements"] = {seg_id: SID + ":g1"}
        self.store["seq"] = 2
        self._save_store()
        turns2, store2 = self._turns(), self._view()
        self.assertIsNot(store2, store, "a moved store is a new shared view")
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            got = km._nudge_placement_gate(SID, turns2, store2)
        self.assertEqual(pu.call_count, 1, "the store's stat moved: derived again")
        self.assertEqual(got, self._direct(turns2, store2))
        self.assertFalse(got, "every unit placed: the queue is empty")

    def test_a_transcript_append_re_derives(self):
        km._nudge_placement_gate(SID, self._turns(), self._view())
        self._write(self.recs + [uline(T0 + 600, "and the cap?", "u2", "a1"),
                                 aline(T0 + 610, "two minutes", "a2", "u2")])
        os.utime(self.tpath, (NOW - 20, NOW - 20))
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, self._turns(), self._view())
        self.assertEqual(pu.call_count, 1, "a new parse: derived again")
        self.assertEqual(km._NUDGE_GATE_STATS["derived"], 2)

    def test_an_override_journal_write_re_derives(self):
        km._nudge_placement_gate(SID, self._turns(), self._view())
        (jd.STATE / "overrides").mkdir(exist_ok=True)
        (jd.STATE / "overrides" / (SID + ".jsonl")).write_text(json.dumps({"t": NOW, "op": "noop"}) + "\n")
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, self._turns(), self._view())
        self.assertEqual(pu.call_count, 1, "the journal is a store input: the key moved")

    def test_a_clear_boundary_re_derives(self):
        """_placed_key scopes its fuzzy match by the episode floor, read from the clears log: a boundary row
        appended there changes the gate's answer with neither the parse nor the store moving, so the log's
        stat is part of the key."""
        km._nudge_placement_gate(SID, self._turns(), self._view())
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        (jd.EPIDIR / (SID + ".jsonl")).write_text(json.dumps({"head": "u1", "fsid": SID, "t": T0}) + "\n"
                                                  + json.dumps({"head": "u9", "fsid": SID, "t": NOW - 60}) + "\n")
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, self._turns(), self._view())
        self.assertEqual(pu.call_count, 1, "the clears log is a gate input: the key moved")

    def test_a_placement_landing_between_the_read_and_the_derivation_is_never_pinned(self):
        """The maintainers' review of the first cut (2026-09-09): cycle N read the view, a judge published a
        placement, the gate stat'd the NEW file and cached the OLD bytes' answer under it, and cycle N+1 served
        'planner-queue' until the store next moved. The store half of the key is the view object itself now,
        and the answer is cached only while that view is still the current one."""
        turns, view_old = self._turns(), self._view()
        seg_id = next(u[0] for u in jd.plan_units({"turns": turns}, view_old) if u[1] == "work")
        self.store["placements"] = {seg_id: SID + ":g1"}
        self.store["seq"] = 2
        self._save_store()                                   # the judge's publish lands between the read and the gate
        self.assertTrue(km._nudge_placement_gate(SID, turns, view_old), "derived from the old bytes: still unplanned")
        self.assertNotIn(SID, km._nudge_gate_memo, "not cached: the view it derived from is no longer current")
        view_new = self._view()
        self.assertIsNot(view_new, view_old)
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            got = km._nudge_placement_gate(SID, self._turns(), view_new)
        self.assertEqual(pu.call_count, 1, "re-derived against the current view")
        self.assertFalse(got, "the placement landed: the queue is empty")
        self.assertEqual(km._NUDGE_GATE_STATS, {"served": 0, "derived": 2})
        self.assertIs(km._nudge_gate_memo[SID][1], view_new)

    def test_a_store_that_is_not_the_shared_view_is_derived_every_time(self):
        turns = self._turns()
        fresh = jd.load_goals(SID)                           # a writer's mutable store, not the view
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, turns, fresh)
            km._nudge_placement_gate(SID, turns, fresh)
        self.assertEqual(pu.call_count, 2)
        self.assertNotIn(SID, km._nudge_gate_memo)

    def test_a_failed_derivation_is_never_cached_and_waves_nothing_through(self):
        turns, store = self._turns(), self._view()
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with patch.object(jd, "_segs", side_effect=RuntimeError("synthetic gate fault")), redirect_stderr(err):
            self.assertFalse(km._nudge_placement_gate(SID, turns, store))
            self.assertFalse(km._nudge_placement_gate(SID, turns, store))
        self.assertNotIn(SID, km._nudge_gate_memo, "a failed derivation is not an answer to serve")
        self.assertEqual(err.getvalue().count("auto-nudge placement gate"), 2, "loud every time, never silent")
        self.assertTrue(km._nudge_placement_gate(SID, turns, store), "the next good derivation answers")

    def test_a_parse_the_cache_does_not_hold_is_derived_every_time(self):
        turns, store = self._turns(), self._view()
        copied = list(turns)                              # not the cached object: no key to serve on
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, copied, store)
            km._nudge_placement_gate(SID, copied, store)
        self.assertEqual(pu.call_count, 2)
        self.assertNotIn(SID, km._nudge_gate_memo)

    def test_the_memo_is_bounded(self):
        turns, store = self._turns(), self._view()
        km._nudge_gate_memo.clear()
        for i in range(km._NUDGE_GATE_MEMO_MAX + 1):
            km._nudge_gate_memo["11111111-2222-3333-4444-%012d" % i] = (("k",), None, None, False)
        oldest = next(iter(km._nudge_gate_memo))
        before = len(km._nudge_gate_memo)
        km._nudge_placement_gate(SID, turns, store)    # a store past the bound evicts the oldest entry first
        self.assertEqual(len(km._nudge_gate_memo), before, "one out, one in: the memo never grows past its bound")
        self.assertNotIn(oldest, km._nudge_gate_memo)
        self.assertIn(SID, km._nudge_gate_memo)

    def test_the_counters_ride_the_perf_snapshot(self):
        km._nudge_placement_gate(SID, self._turns(), self._view())
        km._nudge_placement_gate(SID, self._turns(), self._view())
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(snap["memos"]["nudgeGate"], {"served": 1, "derived": 1})


    def test_served_by_the_turns_held_when_an_agent_view_is_the_newest_slot(self):
        """Review find (2026-09-11): the gate read the sid's NEWEST parse slot; an openSubagent handler on a socket
        thread can store the agent file's parse under the same sid between the walk's parse and the gate's read
        (the store keeps a slot per leaf), and the newest slot then held another tree, so the memo missed and the
        gate re-derived the whole session that cycle. The gate looks its entry up by the turns it holds."""
        turns, store = self._turns(), self._view()
        km._nudge_placement_gate(SID, turns, store)                         # derived once, memoized
        agent = self.tpath.parent / "subagents" / "agent-aaaa.jsonl"
        agent.parent.mkdir()
        agent.write_text("\n".join(json.dumps(r) for r in [uline(T0 + 100, "check the width", "s1"),
                                                             aline(T0 + 110, "checked", "s2", "s1")]) + "\n")
        jd.parsed_session(SID, [str(agent)], NOW)                           # the agent view: the sid's newest slot now
        self.assertEqual(jd._PARSE_CACHE[SID][2], str(agent), "the newest slot is the agent file's")
        self.assertIsNot(jd._PARSE_CACHE[SID][1].get("turns"), turns)
        with patch.object(jd, "plan_units", wraps=jd.plan_units) as pu:
            km._nudge_placement_gate(SID, turns, store)
        self.assertEqual(pu.call_count, 0, "served from the memo: the entry is found by the turns held, not the newest slot")
        self.assertEqual(km._NUDGE_GATE_STATS, {"served": 1, "derived": 1})


class WalkReadsTheSharedView(_Gate):
    def test_the_walk_reads_through_the_shared_view_and_never_a_fresh_load(self):
        """The walk's own read is the shared view; every writer downstream reloads at its write moment
        (_wake_goal's fresh load, _file_wake_answer's own load, the fire list's, the fire path's), so a fresh
        load per idle session per cycle bought nothing. A write through the view raises FrozenStoreError, files
        a frozen-store-write row and switches the cache off, so a writer that forgets the reload is refused
        and recorded instead of landing. The answered wake's filing is pinned by behaviour, not by source text,
        in tests/test_wake_answer_files_under_shared_view.py: a text pin on _wake_goal's load matched the due
        leg's reload and stayed green while the answered leg wrote through the view."""
        import inspect
        src = inspect.getsource(km._auto_nudge_session)
        self.assertIn("store, fault = jd.load_goals_shared_or_fault(sid)", src)
        self.assertNotIn("jd.load_goals_or_fault(sid)", src, "no fresh load for the walk's own read")
        self.assertIn("_nudge_placement_gate(sid, turns, store)", src, "the gate is the memoized one")
        self.assertIn("_nudge_fire_list(jd.load_goals(sid)", src, "the fire list judges a FRESH store, never the view")
        self.assertIn("_nodes = jd.load_goals(sid)", src, "the fire path re-reads fresh too")

    def test_the_interrupt_block_stand_check_reads_the_shared_view(self):
        import inspect
        src = inspect.getsource(km._intr_block_stands)
        self.assertIn("jd.load_goals_shared_or_fault(sid)", src, "a pure read")
        self.assertNotIn("jd.load_goals_or_fault(", src)
        for writer in ("_record_interrupt_block", "_lift_interrupt_block"):
            self.assertIn("jd.load_goals_or_fault(sid)", inspect.getsource(getattr(km, writer)),
                          "%s writes, so it loads fresh" % writer)


if __name__ == "__main__":
    unittest.main()
