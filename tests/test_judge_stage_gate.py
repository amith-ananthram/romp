#!/usr/bin/env python3
"""The judge tiers' EVIDENCE GATE (2026-09-07): the planner and closer skip a
session's per-pass run when nothing their decision path reads has changed since the run that last judged
it to completion. The four store-only tiers went on the same gate the same day: the unblocker (the parse
pair and the store trio), the grouper and consolidator (the store trio and cleared.jsonl, no parse), the
distiller (the store trio, the states file and this sid's stall records, no parse); the StoreTiers,
StoreReArms, StoreOwnWrites, StoreCompleteness, UnblockerHazard and DrainStaysUngated classes and the four
FsCompleteness checks below are its tests.

Why: every pass ran every discovered session in full (a parse, a store load, the unit walk, the closed-turn
walk, a rollup, an unconditional save), with about two of thirty-three sessions holding anything new per
pass on the live kernel; the planner and closer were more than half of an idle pass's CPU.

What exact means here, and what every test below protects (CLAUDE.md, cards move on new information):
the gate never withholds a verdict the ungated pass would have filed from NEW evidence. A run is skipped
only when every input the tier reads is identical by identity to what it last judged to completion:
the parse pair pinned with the parse BEFORE it is read (_frame_parse_key, so judged content can be newer
than the stamp, never older), the store trio (store, override journal, archive), the tier's side files
(captions, episodes, the death marker, the sdk reg's spawnedAt value, cleared.jsonl, this sid's stall
records, the LEAF stem's task store), and the one clock input (a background launch's deadline). A stage
that deferred, was paused, failed a call, had a reply rejected without a write, raised, or was cut sets
the completeness bit and leaves no stamp. The probes the design review ran, each a test here: a poke
mid-pass (a turn ending after the pass's first touch), an unpoked background task, a rewind and a cut, a
journal gesture, a nudge block, two concurrent writers, a restart (fresh process state), the death drain,
the archive path.

Accepted lag, recorded here as the design asks: under an open frame every PASS-THREAD caller of
parsed_session sees the pass-start world (a cache hit too), so a turn that ends after a pass's first touch is
judged next pass, whole. That is the frame's design (2026-07-21). The kernel's six pusher tick jobs that read
the judge parse (_interrupt_block_tick, _closer_pending, _awaiting_wake_outcomes, _deferral_sweep_tick,
_auto_nudge_session, _clear_done_working_notes) are NOT pass threads (jd._pass_frame; review find,
2026-09-08): they read the live world every cycle, so the frame costs them nothing, and the gate adds no lag
beyond the producer's 3 s backstop for the clock input.

PRIVATE synthetic sids (goal-minting fixtures never share the placeholder sid: its override journal is
replayed on every load), invented text, a notes-api with web/api sessions; the journals are removed in
tearDown."""
import builtins
import io
import json
import os
import re
import shutil
import tempfile
import time
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
jd = load_source("romp_judge_stage_gate", os.path.join(BIN, "romp-judge"))
em = jd.em

SID = "aaaaaaaa-1111-2222-3333-444444444444"      # private synthetic sids, never the shared placeholder
SID2 = "bbbbbbbb-1111-2222-3333-444444444444"
SID3 = "dddddddd-1111-2222-3333-444444444444"     # a sid with no session of its own (signature-level cases)
T0 = 1781100000
NOW = T0 + 5000
MINT = '{"ops":[{"why":"x","do":"mint","text":"Goal"}]}'
EMPTY_CLOSE = '{"done": [], "block": []}'
HOLD_ALL = '{"verdicts":[]}'
LIFT_ONE = '{"verdicts":[{"n":1,"do":"lift","why":"the port was named two messages later"}]}'
MIRROR_WHY = "declared in the agent's own to-do list"          # the mirror top's mint reason (_title_mirror_tops)
STORE_TIERS = ("unblock", "group", "consolidate", "distill")
ALL_TIERS = ("plan", "close") + STORE_TIERS                     # run_triage's order


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def tool_use_line(t, uuid, parent, tool_id, name, inp):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}]}}


def tool_result_line(t, uuid, parent, tool_id, text):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def boundary(t, uuid, parent):
    """A compaction boundary record: the event model opens a trigger-less turn on it."""
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent}


TWO_TURNS = [uline(T0, "task A", "u1"), aline(T0 + 30, "did A", "a1", "u1"),
             uline(T0 + 100, "task B", "u2", "a1"), aline(T0 + 130, "did B", "a2", "u2")]


class _Gate(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td)                    # clears the stamps, the value memos and the counters
        jd.end_pass_frame(True)                      # belt: never inherit a frame a crashed test left open
        for c in (jd._PARSE_CACHE, jd._CHAIN_MEMO, jd._BG_SCAN_CACHE, jd._RECON_MEMO, jd._gone_memo):
            c.clear()
        self.cdir = self.td / "launchdir"; self.cdir.mkdir()
        self.proj = self.td / "projects"
        self.pdir = self.proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        self.pdir.mkdir(parents=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        self.claude = self.td / "claude-config"; (self.claude / "tasks").mkdir(parents=True)
        self._env = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)   # the task store, resolved at call time
        self._saved = (jd.PROJECTS, jd.plan_llm, jd.closer_llm, jd.group_llm, jd.opener_llm, jd._PENDING_CUT_FN,
                       jd._judge_run_impl, jd._rewound_away, jd._judge_run, jd._fileset_key, jd._close_turn,
                       jd._STAGE_STAMP_MAX, jd.load_goals, jd.save_goals)
        jd.PROJECTS = self.proj
        # store I/O spies: the stages reach load_goals/save_goals through the module globals, so a
        # counting wrapper over each sees every load and save a pass makes for the fixture's sids
        self.io = {"loads": 0, "saves": 0}
        real_load, real_save = jd.load_goals, jd.save_goals

        def counting_load(*a, **k):
            self.io["loads"] += 1
            return real_load(*a, **k)

        def counting_save(*a, **k):
            self.io["saves"] += 1
            return real_save(*a, **k)
        jd.load_goals, jd.save_goals = counting_load, counting_save
        self.plan_calls, self.close_calls = [], []
        jd.plan_llm = lambda text, menu, human=False, **kw: (self.plan_calls.append(text) or MINT)
        jd.opener_llm = lambda text, menu, **kw: (self.plan_calls.append(text) or MINT)
        jd.closer_llm = lambda tt, mt, *a, **k: (self.close_calls.append(tt) or EMPTY_CLOSE)
        jd.group_llm = lambda menu, judge="grouper": '{"ops":[]}'
        jd._PENDING_CUT_FN = None
        # the store tiers' helpers: hold every block, land every distill, title every mirror top; and a belt
        # under all of them, since no test here may reach the real model call
        self._saved_store = (jd.unblock_llm, jd.distill_llm, jd.brief_llm, jd.stall_llm, jd.mirror_title_llm,
                             jd.parsed_session)
        self.unblock_calls, self.distill_calls, self.title_calls = [], [], []
        jd.unblock_llm = lambda blocks, since, completed="": (self.unblock_calls.append(blocks) or HOLD_ALL)
        jd.distill_llm = lambda text, work, why, **kw: (self.distill_calls.append(text) or "Shipped the search endpoint.")
        jd.brief_llm = lambda text, work, owed, **kw: (self.distill_calls.append(text) or "Pick the port the api binds.")
        jd.stall_llm = lambda text, work, holding: (self.distill_calls.append(text) or "The build has not finished.")
        jd.mirror_title_llm = lambda subject, frame=None, user_ask=None: (self.title_calls.append(subject) or "Write the api tests")

        def no_model(*a, **k):
            raise AssertionError("a stage reached the real model call; patch the helper above the belt")
        jd._judge_run_impl = no_model
        jd._judge_ctx.paused, jd._judge_ctx.last_call_fail, jd._judge_ctx.stage_incomplete = False, None, False

    def tearDown(self):
        jd.end_pass_frame(True)
        (jd.PROJECTS, jd.plan_llm, jd.closer_llm, jd.group_llm, jd.opener_llm, jd._PENDING_CUT_FN,
         jd._judge_run_impl, jd._rewound_away, jd._judge_run, jd._fileset_key, jd._close_turn,
         jd._STAGE_STAMP_MAX, jd.load_goals, jd.save_goals) = self._saved
        (jd.unblock_llm, jd.distill_llm, jd.brief_llm, jd.stall_llm, jd.mirror_title_llm,
         jd.parsed_session) = self._saved_store
        if self._env is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._env
        for sid in (SID, SID2):
            try:
                (jd._overrides_dir() / (sid + ".jsonl")).unlink()   # a sid's journal never outlives its test
            except OSError:
                pass
        jd._judge_ctx.paused, jd._judge_ctx.last_call_fail, jd._judge_ctx.stage_incomplete = False, None, False
        shutil.rmtree(self.td, ignore_errors=True)

    # ── fixture helpers ──
    def _session(self, sid, recs=None, name="web"):
        path = self.pdir / (sid + ".jsonl")
        path.write_text("\n".join(json.dumps(r) for r in (TWO_TURNS if recs is None else recs)) + "\n")
        (jd.NAMES / sid).write_text("%s\t%s\t#abcdef\n" % (name, str(self.cdir)))
        return path

    def _append(self, path, *recs):
        with open(path, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _states_row(self, sid, t, state):
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.STATESDIR / (sid + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": t, "state": state}) + "\n")

    def _pass(self, now=NOW, tiers=("plan", "close")):
        """One gated pass over the fixture: the named tiers in run_triage's order under one frame."""
        jd._discover_cache.clear()                   # discover's list is cached behind a dir fingerprint, not `now`
        runners = {"plan": jd.run_plan, "close": jd.run_close, "unblock": jd.run_unblock, "group": jd.run_group,
                   "consolidate": jd.run_consolidate, "distill": jd.run_distill}
        own = jd.begin_pass_frame()
        try:
            for t in ALL_TIERS:
                if t in tiers:
                    runners[t](now=now)
        finally:
            jd.end_pass_frame(own)

    def _converge(self, now=NOW, limit=6, tiers=ALL_TIERS):
        """Passes over `tiers` until every one skips: the working pass, the follow-on run that finds nothing
        to write (the tier's own publish re-armed it once), then the skip. Leaves the counters zeroed."""
        for _ in range(limit):
            self._reset()
            self._pass(now, tiers=tiers)
            if all(self._st(t)["ran"] == 0 for t in tiers):
                self._reset()
                return
        self.fail("the fixture did not converge in %d passes" % limit)

    def _st4(self, key="ran"):
        """The four store tiers' `key` counters, in STORE_TIERS order."""
        return tuple(self._st(t)[key] for t in STORE_TIERS)

    def _block(self, sid, nid, t, why="Which port should the api bind?"):
        """A kernel-side nudge block on `nid` at `t`: the journal row plus a publish through save_goals."""
        store = jd.load_goals(sid)
        jd.append_block(sid, nid, "nudge", why, t)
        jd.record_verdict(store, store["nodes"][nid], "nudge", "block", t, why=why)
        jd.rollup_status(store, False)
        jd.save_goals(sid, store)

    def _mirror_top(self, sid, text, n=90):
        """An untitled to-do MIRROR top (the agent's own TaskCreate subject, verbatim): the one node shape
        that makes _title_mirror_tops call the model before the distiller's todo is built."""
        store = jd.load_goals(sid)
        nid = "%s:g%d" % (sid, n)
        store["nodes"][nid] = {"id": nid, "text": text, "parentId": None, "why": MIRROR_WHY, "t": T0 + 300,
                               "mt": T0 + 300, "log": [], "trail": [], "nodeComplete": False, "cleared": False}
        store["status"][nid] = "working"
        jd.save_goals(sid, store)
        return nid

    def _st(self, tier):
        return dict(jd._TIER_STATS[tier])

    def _reset(self):
        for d in jd._TIER_STATS.values():
            for k in d:
                d[k] = 0

    def _stamp(self, tier, sid=SID):
        return jd._STAGE_STAMP.get((tier, sid))

    def _rows(self, err):
        """The judge-errors rows of kind `err` (the file exists once any row was logged)."""
        return [r for r in (json.loads(l) for l in open(jd.ERRORS) if l.strip()) if r.get("err") == err]

    def _tops(self, sid=SID):
        store = jd.load_goals(sid)
        return sorted((nd for nd in store["nodes"].values() if nd["parentId"] is None), key=lambda nd: nd["t"])


class Convergence(_Gate):
    def test_two_idle_passes_run_once_then_skip_with_no_store_io(self):
        # the working pass places and sweeps; the tier's own publish re-arms it once (the stamp holds the
        # pre-run identity); the follow-on run makes no call and writes nothing; the third pass skips both
        # tiers, loads and saves nothing for the sid, and still stamps pass_done (a skip is a completed
        # no-op pass: the kernel's wedged-reviver bound reads that watermark)
        self._session(SID)
        self._pass()
        self.assertEqual(len(self.plan_calls), 2, "two ended turns: two work units placed")
        self.assertEqual(len(self.close_calls), 2, "and two turns swept")
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (1, 1))
        self.assertEqual((self._st("plan")["stamped"], self._st("close")["stamped"]), (1, 1),
                         "complete runs stamp what they judged")
        self._reset()
        self._pass()                                                    # the idle follow-on: runs, no calls, no write
        self.assertEqual((len(self.plan_calls), len(self.close_calls)), (2, 2), "no model call")
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (1, 1),
                         "the tiers' own publishes re-armed them once")
        self._reset()
        for t in ("plan", "close"):                                     # the earlier passes' watermarks go: the skip
            self.assertIsNotNone(jd._PASS_DONE.pop((t, SID), None), t)  #  must write its own, not inherit one
        io0 = dict(self.io)
        self._pass()                                                    # the skip
        io1 = dict(self.io)
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (0, 0))
        self.assertEqual((self._st("plan")["skipped"], self._st("close")["skipped"]), (1, 1))
        self.assertEqual((io1["loads"] - io0["loads"], io1["saves"] - io0["saves"]), (0, 0),
                         "a skipped session costs no store load and no save")
        self.assertIsNotNone(jd.pass_watermark("plan", SID), "a skip stamps pass_done")
        self.assertIsNotNone(jd.pass_watermark("close", SID))
        self.assertEqual((len(self.plan_calls), len(self.close_calls)), (2, 2))

    def test_a_restart_is_a_full_walk(self):
        # the stamps are process state: a kernel restart mid-pass loses them and the first pass after boot
        # judges every session, as before the gate
        self._session(SID)
        self._converge()
        jd._STAGE_STAMP.clear()                                         # what a restart does
        self._pass()
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (1, 1))
        self.assertEqual((len(self.plan_calls), len(self.close_calls)), (2, 2), "nothing new: no call either way")

    def test_counters_add_up(self):
        self._session(SID)
        self._pass(); self._pass(); self._pass()
        for t in ("plan", "close"):
            s = self._st(t)
            self.assertEqual(s["ran"], s["stamped"] + s["bypassed"] + s["incomplete"], t)
        ts = jd.tier_stats()
        self.assertEqual(set(ts), set(jd.GATED_TIERS) | {"stamps"})
        self.assertEqual(ts["stamps"], 2, "one stamp per (tier, sid)")


class ATurnEndingMidPass(_Gate):
    def test_a_turn_ending_after_the_first_touch_is_judged_next_pass_whole(self):
        # the review's hazard: a pass thread (the index tier's captioner, say) touches the session while its
        # last turn is OPEN; the turn's final record and the idle row land; the gated planner and closer check the gate.
        # The transcript component is the pair the pass PINNED at that first touch, which equals the stamp
        # the converged run left, so the mid-pass check is a SKIP (no stat of the grown file, no run), the
        # stamps keep the pre-append pair, and the next pass runs both stages over the ended turn, whole.
        # A gate reading the live key at its own moment would run here over the frozen (pre-append) parse
        # and bypass, or stamp the post-append key over a world that never judged the ended turn.
        path = self._session(SID, [uline(T0, "task A", "u1"), aline(T0 + 30, "did A", "a1", "u1"),
                                   uline(T0 + 100, "task B", "u2", "a1"),
                                   aline(T0 + 110, "starting on B", "a2", "u2", stop="tool_use")])
        self._converge()
        own = jd.begin_pass_frame()
        try:
            jd.parsed_session(SID, [str(path)], NOW)                    # the index tier's first touch, turn open
            pre = jd._frame["keys"][("parse", SID)]
            self._append(path, aline(T0 + 140, "did B", "a3", "a2"))
            self._states_row(SID, T0 + 141, "idle")
            jd.run_plan(now=NOW)
            jd.run_close(now=NOW)
            for t in ("plan", "close"):
                self.assertEqual((self._st(t)["ran"], self._st(t)["skipped"]), (0, 1),
                                 "%s: the pinned pair matches the stamp, so the mid-pass check skips (a live-key "
                                 "gate would run and bypass instead)" % t)
        finally:
            jd.end_pass_frame(own)
        for t in ("plan", "close"):
            st = self._stamp(t)
            self.assertIsNotNone(st, "%s: the converged run's stamp stands" % t)
            self.assertEqual(st[0][0][1], jd._pair_key(pre), "%s: the stamp holds the PRE-append pair" % t)
        self._reset()
        self._pass()
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (1, 1),
                         "the next pass runs both stages: the live pair differs from the stamped one")
        self.assertIn("did B", " ".join(self.plan_calls), "the planner judged the ended turn's work")
        turns = jd.parsed_session(SID, [str(path)], NOW)["turns"]
        self.assertTrue(turns[-1]["ended"])
        self.assertIn(turns[-1]["id"], jd.load_goals(SID).get("closedTurns") or [], "the closer swept it")


class TheCut(_Gate):
    def test_a_cut_arming_between_the_pin_and_the_parse_withholds_the_stamp(self):
        # the user's cut rule (2026-09-07): the parse runs under the LIVE cut, and a cut that arms after
        # the gate pinned makes the served pair differ from the pinned one, so the run is not stamped
        # (bypassed) and the sid stays due. Then: with the cut standing, the cut world converges and is
        # stamped under the cut; the cut clearing with no file change re-arms both tiers, and the
        # previously cut turn is judged. The invariant: no verdict the ungated pass would file is lost.
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        before = self._stamp("plan")
        own = jd.begin_pass_frame()
        try:
            pinned, _cut, _fr = jd._frame_parse_key(SID, [str(path)])   # the gate's pin (also run_plan's)
            self.assertEqual(pinned[1], "")
            jd._PENDING_CUT_FN = lambda fsid: "a2"                       # a bare rollback arms: u3/a3 abandoned
            jd.run_plan(now=NOW)
            jd.run_close(now=NOW)
        finally:
            jd.end_pass_frame(own)
        self.assertEqual(self._st("plan")["ran"], 1, "the transcript grew: the planner ran")
        self.assertEqual(self._st("plan")["bypassed"], 1, "but the served cut differs from the pinned one: no stamp")
        self.assertEqual(self._stamp("plan"), before, "the old stamp stands (it describes the last COMPLETE run)")
        self.assertNotIn("task C", " ".join(self.plan_calls), "the planner judged the cut world: the tail is not planned")
        self._reset()
        self._pass()                                                    # the cut world, pinned and parsed alike
        self.assertEqual(self._st("plan")["stamped"], 1, "a pin and a parse under the same cut stamp")
        self.assertEqual(self._stamp("plan")[0][0][1][1], "a2", "the stamp holds the cut")
        self._reset()
        self._pass()
        self.assertEqual((self._st("plan")["skipped"], self._st("close")["skipped"]), (1, 1))
        jd._PENDING_CUT_FN = None                                        # the rollback dissolves: no file change
        self._reset()
        self._pass()
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (1, 1),
                         "the cut clearing re-arms both tiers with no file change")
        self.assertIn("task C", " ".join(self.plan_calls), "and the un-cut tail is judged")

    def test_a_rewind_pending_defers_without_a_stamp_and_a_durable_rewind_retires(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        jd._rewound_away = lambda fsid, p, uuid: "pending"
        self._pass()
        self.assertIsNone(self._stamp("plan") if self._st("plan")["stamped"] else None)
        self.assertEqual(self._st("plan")["incomplete"], 1, "a pending rewind defers the unit: no stamp")
        self._reset()
        self._pass()
        self.assertEqual(self._st("plan")["ran"], 1, "still due")
        self.assertEqual(self._st("plan")["incomplete"], 1)
        calls = []

        def durable_then_live(fsid, p, uuid):
            calls.append(uuid)
            return "durable" if len(calls) == 1 else False              # the unit's check; the plan-sync's is live
        jd._rewound_away = durable_then_live
        self._reset()
        self._pass()
        self.assertEqual(self._st("plan")["stamped"], 1, "a durable rewind retires the unit: a complete run")
        store = jd.load_goals(SID)
        retired = [k for k, v in store["placements"].items() if v is None]
        self.assertTrue(retired, "the unit is retired, not planned")
        self.assertNotIn("task C", " ".join(self.plan_calls))

    def test_the_unit_loop_pending_leg_alone_leaves_no_stamp(self):
        # the unit loop's own pending mark, isolated: the latest segment is trigger-less (a compaction
        # boundary), so the plan-sync stand-down cannot fire, and the deferred unit is the only reason the
        # run is incomplete. The rewind-pending case above trips both marks at once.
        path = self._session(SID, TWO_TURNS + [boundary(T0 + 300, "cb1", "a2")])
        self._converge()
        store = jd.load_goals(SID)
        segs = [sg for t in jd.parsed_session(SID, [str(path)], NOW)["turns"] for sg in jd._segs(t, store)]
        self.assertIsNone(max(segs, key=lambda sg: sg.get("t") or 0).get("trigger"), "fixture: no trigger on the latest segment")
        seg2 = next(sg for sg in segs if sg.get("trigger") == "u2")
        del store["placements"][seg2["id"]]                            # turn 2's unit falls due again
        jd.save_goals(SID, store)
        jd._rewound_away = lambda fsid, p, uuid: "pending"
        n_calls = len(self.plan_calls)
        self._reset()
        self._pass(tiers=("plan",))
        st = self._st("plan")
        self.assertEqual((st["ran"], st["incomplete"], st["stamped"]), (1, 1, 0), "the deferred unit alone voids the stamp")
        self.assertEqual(len(self.plan_calls), n_calls, "deferred before any model call")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertEqual([r["err"] for r in rows if "rewind" in str(r.get("err"))], ["rewind-stand-down-pending"],
                         "the unit loop's own row, and no other stand-down")

    def test_the_plan_sync_stand_down_alone_leaves_no_stamp(self):
        # the plan-sync stand-down's mark, isolated: a clear re-arms the planner with no new unit, so the unit
        # loop checks nothing, and the latest segment's trigger reads as rewound away. A clear is the cleared.jsonl
        # row plus the journal row append_clear writes before the save (2026-09-09): the gate keys cleared.jsonl,
        # the planner's own change gate inside _plan_session (_plan_key) keys the journal, and both must move
        # for the run to reach the sync; the row names a node this store does not hold, so the replay skips it
        self._session(SID)
        self._converge()
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": SID3 + ":g1", "op": "clear", "t": NOW}) + "\n")
        jd.append_clear(SID, SID3 + ":g1", "user", "cleared from the feed", NOW)
        jd._rewound_away = lambda fsid, p, uuid: "pending"
        self._reset()
        self._pass(tiers=("plan",))
        st = self._st("plan")
        self.assertEqual((st["ran"], st["incomplete"], st["stamped"]), (1, 1, 0), "the skipped sync alone voids the stamp")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertEqual([r["err"] for r in rows if "rewind" in str(r.get("err"))], ["rewind-stand-down"],
                         "the plan-sync's own row, and no unit-loop row")


class ARollbackArmingDuringTheCall(_Gate):
    def test_the_apply_time_pending_leg_leaves_no_stamp(self):
        # the review's second finding (2026-09-07): a bare rollback arming DURING the planner's model call
        # reaches apply_plan_guarded's pending leg past the unit loop's own check; the leg defers the unit
        # with no write, so the run must mark itself incomplete or the stamp holds the deferred unit until
        # an unrelated re-arm. The latest segment is trigger-less (a compaction boundary) so the plan-sync
        # stand-down, the other pending mark, cannot fire: the mark seen here is the apply leg's own.
        path = self._session(SID, TWO_TURNS + [boundary(T0 + 300, "cb1", "a2")])
        self._converge()
        store = jd.load_goals(SID)
        segs = [s for t in jd.parsed_session(SID, [str(path)], NOW)["turns"] for s in jd._segs(t, store)]
        self.assertIsNone(max(segs, key=lambda s: s.get("t") or 0).get("trigger"), "fixture: the latest segment has no trigger")
        seg2 = next(s for s in segs if s.get("trigger") == "u2")
        self.assertIn(seg2["id"], store["placements"], "fixture: turn 2's unit was placed")
        del store["placements"][seg2["id"]]                            # turn 2's unit falls due again
        jd.save_goals(SID, store)
        real = jd.plan_llm

        def arm_during_call(text, menu, human=False, **kw):
            jd._PENDING_CUT_FN = lambda fsid: "a1"                        # the rollback arms while the model thinks
            return real(text, menu, human=human, **kw)
        jd.plan_llm = arm_during_call
        self._reset()
        self._pass(tiers=("plan",))
        jd.plan_llm = real
        s = self._st("plan")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "deferred at apply time: no stamp")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertEqual([r["err"] for r in rows if "rewind" in str(r.get("err"))], ["rewind-stand-down-pending"],
                         "the apply leg's own row, and no other stand-down")
        self.assertNotIn(seg2["id"], jd.load_goals(SID)["placements"], "deferred: no write, the key stays absent")
        jd._PENDING_CUT_FN = None                                       # the rollback dissolves: no file change
        self._reset()
        self._pass(tiers=("plan",))
        self.assertEqual(self._st("plan")["ran"], 1, "still due: the deferred run left no stamp")
        self.assertIn(seg2["id"], jd.load_goals(SID)["placements"], "and the unit is placed")
        self.assertEqual(self._st("plan")["stamped"], 1)


class ReArms(_Gate):
    """Each input re-arms exactly its tiers and only its sid."""

    def _rearms(self, plan, close, msg):
        self._reset()
        self._pass()
        self.assertEqual((self._st("plan")["ran"], self._st("close")["ran"]), (plan, close), msg)

    def test_transcript_and_states_re_arm_both(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        self._rearms(1, 1, "a transcript append")
        self._converge()
        self._states_row(SID, T0 + 300, "idle")
        self._rearms(1, 1, "a states row (the states file is in the parse key)")

    def test_store_journal_and_archive_re_arm_both(self):
        self._session(SID)
        self._converge()
        store = jd.load_goals(SID)
        top = self._tops()[0]
        store["nodes"][top["id"]]["text"] = "Renamed by a kernel-side writer"
        jd.save_goals(SID, store)
        self._rearms(1, 1, "a save_goals publish (a rename: new identity)")
        self._converge()
        jd.append_override(SID, top["id"], "resolve", NOW + 1)          # the user's gesture: the journal only
        self._rearms(1, 1, "a journal append with no store write")
        self.assertEqual(jd.load_goals(SID)["status"].get(top["id"]), "completed", "the replayed resolve took")
        self._converge()
        jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
        self._rearms(1, 1, "an archive write")

    def test_a_nudge_block_re_arms_both(self):
        # the kernel's nudge block: a journal row plus a publish through save_goals
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        store = jd.load_goals(SID)
        jd.append_block(SID, top["id"], "nudge", "Which port should the api bind?", NOW + 2)
        jd.record_verdict(store, store["nodes"][top["id"]], "nudge", "block", NOW + 2, why="Which port should the api bind?")
        jd.rollup_status(store, False)
        jd.save_goals(SID, store)
        self._rearms(1, 1, "a nudge block")
        self.assertEqual(jd.load_goals(SID)["status"].get(top["id"]), "blocked")

    def test_captions_and_episodes_re_arm_the_planner_only(self):
        self._session(SID)
        self._converge()
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.CAPDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"id": "seg-x#p", "caption": "Ship the notes-api search"}) + "\n")
        self._rearms(1, 0, "a captions append")
        self._converge()
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        with open(jd.EPIDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"head": "u1", "fsid": SID, "t": T0}) + "\n")
        self._rearms(1, 0, "an episodes row")

    def test_the_death_marker_and_spawned_at_re_arm_both_and_a_reg_rewrite_re_arms_nothing(self):
        self._session(SID)
        self._converge()
        jd._write_death_marker(SID, {"t": T0 + 500, "by": "probe", "endedAt": T0 + 500})   # finalized: no epilogue
        self._rearms(1, 1, "a death marker")
        self._converge()
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"spawnedAt": T0 + 600, "model": "sonnet"}))
        self._rearms(1, 1, "a spawnedAt change (a revival)")
        self._converge()
        reg.write_text(json.dumps({"spawnedAt": T0 + 600, "model": "opus", "pushNote": "x"}))
        self._rearms(0, 0, "a reg rewrite that keeps spawnedAt (a model pick, a push note)")
        reg.write_text(json.dumps({"spawnedAt": T0 + 700, "model": "opus"}))
        self._rearms(1, 1, "the next revival")

    def test_a_reg_appearing_or_vanishing_re_arms_both_through_sdk_owned(self):
        # _sdk_owned (the reg's existence) is its own input: the parse authors the composer's input as the
        # human only for an SDK session, so a reg with no spawnedAt still re-arms when it appears or goes
        self._session(SID)
        self._converge()
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"model": "sonnet"}))                 # no spawnedAt: only the existence changes
        self.assertIsNone(jd._reg_spawned_at(SID), "premise: the value memo reads None either way")
        self._rearms(1, 1, "a reg appearing")
        self._converge()
        reg.unlink()
        self._rearms(1, 1, "a reg vanishing")

    def test_the_task_store_re_arms_the_planner_under_the_leaf_stem(self):
        # a to-do item created, then flipped in place, under the LEAF stem (what _sync_declared_plan reads:
        # a /clear fork lane's leaf is not the romp sid); a dir under the romp sid on a fork lane is not read
        path = self._session(SID)
        self._converge()
        d = self.claude / "tasks" / SID                                  # stem == sid here: the plain lane
        plan0, close0 = jd._stage_sig("plan", SID, str(path)), jd._stage_sig("close", SID, str(path))
        d.mkdir()
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "write the api tests", "status": "pending"}))
        plan1 = jd._stage_sig("plan", SID, str(path))
        self.assertNotEqual(plan1, plan0, "a to-do item created moves the planner's signature")
        self.assertEqual(jd._stage_sig("close", SID, str(path)), close0, "the closer does not read the task store")
        self._reset()
        self._pass()
        self.assertEqual(self._st("plan")["ran"], 1, "the planner runs (and mirrors the open item, which re-arms the closer)")
        self.assertTrue(any(nd.get("agentTask") for nd in jd.load_goals(SID)["nodes"].values()), "the mirror landed")
        self._converge()
        plan2 = jd._stage_sig("plan", SID, str(path))
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "write the api tests", "status": "completed"}))
        os.utime(d / "1.json", ns=(time.time_ns() + 5_000_000, time.time_ns() + 5_000_000))
        self.assertNotEqual(jd._stage_sig("plan", SID, str(path)), plan2, "an item flipped in place moves it too")
        self._reset()
        self._pass()
        self.assertEqual(self._st("plan")["ran"], 1)
        # the leaf-stem rule at the signature level, on a fork-lane path whose stem is not the sid
        fork = self.pdir / "cccccccc-1111-2222-3333-444444444444.jsonl"
        fork.write_text(json.dumps(uline(T0 + 900, "after the clear", "u9")) + "\n")
        s1 = jd._stage_sig("plan", SID, str(fork))
        (self.claude / "tasks" / fork.stem).mkdir()
        (self.claude / "tasks" / fork.stem / "1.json").write_text(json.dumps({"id": "1", "subject": "x", "status": "pending"}))
        s2 = jd._stage_sig("plan", SID, str(fork))
        self.assertNotEqual(s1, s2, "the LEAF stem's task store is in the planner's signature")
        (d / "2.json").write_text(json.dumps({"id": "2", "subject": "y", "status": "pending"}))
        self.assertEqual(jd._stage_sig("plan", SID, str(fork)), s2, "the romp sid's dir is not read on a fork lane")

    def test_cleared_rows_re_arm_both_and_a_second_sid_stays_stamped(self):
        self._session(SID)
        self._session(SID2, name="api")
        self._converge()
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": SID2 + ":g1", "op": "clear", "t": NOW}) + "\n")
        self._rearms(2, 2, "a cleared.jsonl row re-arms every session's planner and closer (whole-file identity)")
        self._converge()
        self._append(self.pdir / (SID2 + ".jsonl"), uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        self._rearms(1, 1, "the second sid's append")
        self.assertIsNotNone(self._stamp("plan", SID))
        self.assertEqual(self._st("plan")["skipped"], 1, "the first sid skipped")

    def test_value_inputs_are_read_by_value_not_by_file_identity(self):
        # the reg's spawnedAt and this sid's stall records are VALUE inputs: a rewrite in place that keeps
        # the file's (inode, mtime_ns, size) must still be seen. The kernel publishes both files by rename,
        # so identity moves there; a test fixture (or another writer) rewriting in place within one
        # mtime tick does not, and an identity memo served the previous content (17 distiller tests went
        # red under xdist for it, 2026-09-07)
        self._session(SID)
        an = jd.STATE / "auto-nudge.json"
        an.write_text(json.dumps({"enabled": False, "deferred": {SID2 + ":g1": {"at": NOW, "why": "waiting on the closer", "sid": SID2}}}))
        st = os.stat(an)
        self.assertEqual(jd._stall_slice(SID), ())
        body = json.dumps({"enabled": False, "deferred": {SID + ":g1": {"at": NOW, "why": "waiting on the closer", "sid": SID}}})
        self.assertEqual(len(body), st.st_size, "fixture: the rewrite keeps the size (the sids have one length)")
        an.write_text(body)
        os.utime(an, ns=(st.st_atime_ns, st.st_mtime_ns))              # ...and the mtime: identity unchanged
        self.assertEqual(os.stat(an).st_ino, st.st_ino, "fixture: written in place")
        self.assertEqual(jd._stall_slice(SID), ((SID + ":g1", "waiting on the closer", NOW),),
                         "the slice follows the content, not the identity")
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"spawnedAt": 1781100600}))
        st = os.stat(reg)
        self.assertEqual(jd._reg_spawned_at(SID), 1781100600)
        reg.write_text(json.dumps({"spawnedAt": 1781100700}))          # same length, a revival
        os.utime(reg, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(jd._reg_spawned_at(SID), 1781100700, "the value follows the content, not the identity")

    def test_a_stall_record_for_this_sid_re_arms_and_another_sids_does_not(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        an = jd.STATE / "auto-nudge.json"
        an.write_text(json.dumps({"enabled": False, "deferred": {SID2 + ":g1": {"at": NOW, "why": "the closer has not settled the turn", "sid": SID2}}}))
        self._rearms(0, 0, "another sid's stall record")
        an.write_text(json.dumps({"enabled": False, "deferred": {top["id"]: {"at": NOW, "why": "the closer has not settled the turn", "sid": SID}}}))
        self._rearms(1, 1, "this sid's stall record (rollup_status reads it to retire a stall warn)")

    def test_the_parse_component_names_the_candidate_files(self):
        # two lanes with byte-identical content and equal (mtime, size) rows have one fileset key; the
        # signature carries the candidate paths beside it, so a path swap cannot match a stamp
        x, y = self.pdir / "x-lane.jsonl", self.pdir / "y-lane.jsonl"
        x.write_text("\n".join(json.dumps(r) for r in TWO_TURNS) + "\n")
        shutil.copy2(x, y)
        self.assertEqual(jd._fileset_key([str(x)]), jd._fileset_key([str(y)]), "fixture: one fileset key for both")
        jd.end_pass_frame(True)                                         # no frame: the live pair
        self.assertNotEqual(jd._stage_sig("plan", SID3, str(x)), jd._stage_sig("plan", SID3, str(y)),
                            "the candidate files are named in the signature")

    @unittest.skipIf(os.geteuid() == 0, "root ignores mode bits")
    def test_an_unlistable_task_dir_bypasses_the_gate(self):
        # task_store_fp raises on a dir that exists and cannot be listed (as task_store_plan does), so the
        # gate runs the stage and stamps nothing; a fingerprint that swallowed the error would read as
        # "no store" and match the stamp of a run that saw none
        path = self._session(SID)
        self._converge()
        d = self.claude / "tasks" / SID
        d.mkdir()
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "write the api tests", "status": "pending"}))
        os.chmod(d, 0)
        try:
            jd.end_pass_frame(True)
            self.assertEqual(jd._gate_check("plan", SID, str(path), NOW), (False, None),
                             "run, and stamp nothing: the signature could not be computed")
        finally:
            os.chmod(d, 0o700)


class ConcurrentWriters(_Gate):
    def test_a_kernel_side_publish_during_the_stage_is_never_skipped_over(self):
        # a second writer publishes while the planner holds its store across a model call: the planner's
        # stamp holds the pre-run identity, so the next pass re-reads the store (the other writer's work
        # rebased in) and stamps only when nothing moves
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        real = jd.plan_llm

        def plan_and_race(text, menu, human=False, **kw):
            side = jd.load_goals(SID)                                   # the nudge tick, on its own thread
            side["nodes"][SID + ":side"] = {"id": SID + ":side", "text": "Answer the port question", "parentId": None,
                                            "nodeComplete": False, "blocked": True, "cleared": False, "trail": [],
                                            "t": NOW, "log": []}
            jd.save_goals(SID, side)
            return real(text, menu, human=human, **kw)
        jd.plan_llm = plan_and_race
        self._pass()
        jd.plan_llm = real
        self.assertIn(SID + ":side", jd.load_goals(SID)["nodes"], "the racing publish survived the planner's save")
        self._reset()
        self._pass()
        self.assertEqual(self._st("plan")["ran"], 1, "the identity moved under the stamp: the planner runs again")
        self._reset()
        self._pass()
        self.assertEqual((self._st("plan")["skipped"], self._st("close")["skipped"]), (1, 1))


class Completeness(_Gate):
    """A stage that did not finish leaves no stamp, so the sid stays due."""

    def test_an_empty_or_whitespace_reply_keeps_the_planner_due_without_a_parse_fail(self):
        # the stripped-reply case patches _judge_run_impl, not plan_llm: plan_llm strips the reply, so a
        # whitespace reply reaches the stage as "" and the belt in _judge_run marks the stage incomplete
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        jd.plan_llm = self._saved[1]                                    # the real planner helper, over the belt
        for reply in ("", "   "):
            jd._judge_run_impl = lambda *a, **k: reply
            self._reset()
            self._pass(tiers=("plan",))
            self.assertEqual(self._st("plan")["ran"], 1)
            self.assertEqual(self._st("plan")["incomplete"], 1, "reply %r: the call failed, the stage is incomplete" % reply)
            self.assertEqual(self._st("plan")["stamped"], 0)
            self.assertFalse(jd.load_goals(SID).get("parseFails"), "no parse try burned on a failed call")
        self.assertIsNotNone(self._stamp("plan"), "the earlier complete run's stamp stands")

    def test_a_failed_closer_call_and_a_rejected_reply_keep_the_closer_due(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        jd.closer_llm = lambda *a, **k: ""                              # a cut: the call failed
        self._pass()
        self.assertEqual((self._st("close")["ran"], self._st("close")["incomplete"]), (1, 1))
        self._reset()
        jd.closer_llm = lambda *a, **k: "not a verdict at all"          # a parse reject under the cap
        self._pass()
        self.assertEqual((self._st("close")["ran"], self._st("close")["incomplete"]), (1, 1))
        self._reset()
        jd.closer_llm = lambda tt, mt, *a, **k: EMPTY_CLOSE             # a served reply
        self._pass()
        self.assertEqual(self._st("close")["stamped"], 1)

    def test_a_raising_stage_stamps_nothing(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        before = self._stamp("close")

        def boom(*a, **k):
            raise RuntimeError("closer down")
        jd._close_turn = boom
        self._reset()
        self._pass()
        self.assertEqual(self._stamp("close"), before, "no stamp from a raised run")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertTrue(any(r.get("err") == "pass-crash" for r in rows), "the crash is logged, as before")
        s = self._st("close")
        self.assertEqual((s["ran"], s["incomplete"]), (1, 1), "a raised run counts as incomplete")
        self.assertEqual(s["ran"], s["stamped"] + s["bypassed"] + s["incomplete"], "the identity romp perf reads holds through a crash")

    def test_a_vanished_candidate_runs_the_stage_and_stamps_nothing(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        before = self._stamp("plan")

        def vanished(files):
            raise OSError("a candidate vanished between the exists() and the stat")
        jd._fileset_key = vanished
        self._pass(tiers=("plan",))
        self.assertEqual((self._st("plan")["ran"], self._st("plan")["bypassed"]), (1, 1))
        self.assertEqual(self._stamp("plan"), before)
        self.assertIn("task C", " ".join(self.plan_calls), "the stage ran over the live world")

    def test_the_belt_marks_an_empty_reply_incomplete(self):
        # _judge_run is a belt over _judge_run_impl: an empty, whitespace or None reply marks the running
        # stage incomplete; a served reply leaves the bit alone. Every stage site also marks its own failed
        # call, so no pass-level case isolates the belt; this one drives it directly.
        for reply in ("", "   ", None, "ok"):
            jd._judge_run_impl = lambda *a, **k: reply
            jd._judge_ctx.stage_incomplete = False
            self.assertEqual(jd._judge_run("sonnet", "sys", "user"), reply)
            self.assertEqual(jd._judge_ctx.stage_incomplete, reply != "ok", "reply %r" % (reply,))

    def test_gated_resets_the_completeness_bit_per_run(self):
        # the bit is thread state: a run that left it set on a pool thread must not void the NEXT session's
        # stamp on the same thread, so _gated clears it before each run
        path = self._session(SID)
        self._reset()
        jd._judge_ctx.stage_incomplete = True                           # left by an earlier session's run
        out = jd._gated("group", lambda f, p, n: 7, SID, str(path), NOW, ("sig",), settle=False, parse=False)
        self.assertEqual(out, 7)
        self.assertEqual(jd._STAGE_STAMP[("group", SID)], (("sig",), None), "stamped: the stale bit was reset")
        self.assertEqual((self._st("group")["stamped"], self._st("group")["incomplete"]), (1, 0))

    def test_the_planners_failed_call_site_alone_marks_the_run(self):
        # the work-run's failed-call `continue` marks the run on its own: plan_llm is patched ABOVE the
        # belt (the belt never sees the call), so the site's mark is the only one in play
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        jd.plan_llm = lambda text, menu, human=False, **kw: ""
        self._reset()
        self._pass(tiers=("plan",))
        st = self._st("plan")
        self.assertEqual((st["ran"], st["incomplete"], st["stamped"]), (1, 1, 0), "the failed call alone voids the stamp")
        self.assertFalse(jd.load_goals(SID).get("parseFails"), "no parse try burned on a failed call")

    @unittest.skipIf(os.geteuid() == 0, "root ignores mode bits")
    def test_a_side_file_stat_that_fails_runs_the_stage_without_a_stamp(self):
        # _ident treats ABSENCE as None and lets every other OSError propagate: a stat that fails for
        # another reason (here a permission bit on the marker directory) must not read as "absent", or it
        # would match the stamp of a run that saw no marker and skip the session
        path = self._session(SID)
        jd.GONEDIR.mkdir(parents=True, exist_ok=True)
        self._converge()
        os.chmod(jd.GONEDIR, 0)
        try:
            jd.end_pass_frame(True)
            self.assertEqual(jd._gate_check("plan", SID, str(path), NOW), (False, None),
                             "run, and stamp nothing: a failed stat is not an absent file")
        finally:
            os.chmod(jd.GONEDIR, 0o700)

    def test_a_stamping_failure_is_not_a_failed_pass(self):
        # the stamp is written after the stage's work landed; a failure computing it (the clock input
        # here) counts the run bypassed, logs one gate-stamp row, and is never a pass-crash: pass_done is
        # stamped and the planner's placements stand
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        real = jd._settle_not_before

        def boom(fsid, p, now):
            raise RuntimeError("the clock input could not be read")
        jd._settle_not_before = boom
        jd._PASS_DONE.pop(("plan", SID), None)
        self._reset()
        try:
            self._pass(tiers=("plan",))
        finally:
            jd._settle_not_before = real
        st = self._st("plan")
        self.assertEqual((st["ran"], st["bypassed"], st["stamped"]), (1, 1, 0))
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertEqual([r["err"] for r in rows if r.get("err") in ("gate-stamp", "pass-crash")], ["gate-stamp"])
        self.assertEqual([r["judge"] for r in rows if r.get("err") == "gate-stamp"], ["planner"],
                         "the row wears the judge's name like every other row (review find, 2026-09-08: it carried "
                         "the tier key, which no reader of the log joins on)")
        self.assertIsNotNone(jd.pass_watermark("plan", SID), "the pass over the sid completed")
        self.assertIn("task C", " ".join(self.plan_calls), "and the stage's work landed")


class TheClock(_Gate):
    def _monitor_fixture(self):
        # turn 1: a non-persistent Monitor launched with a 60 s timeout_ms, its ack, then the turn ends: the
        # launch is running with a deadline (its t + 60); the closer stamps the wait on that turn's top.
        # turn 2: task A, done by the closer; its top is the last placement, so it is the FOCUS whose settle
        # waits on the hold (rollup_status: settled = not the focus, or the session settled).
        launch_t = T0 + 100
        recs = [uline(T0 + 90, "watch the deploy log", "u1"),
                tool_use_line(launch_t, "a1", "u1", "toolu_m1", "Monitor", {"command": "tail -f deploy.log", "timeout_ms": 60000}),
                tool_result_line(launch_t + 1, "r1", "a1", "toolu_m1", "Monitor started"),
                aline(launch_t + 5, "Watching the deploy log in the background.", "a2", "r1"),
                uline(T0 + 200, "task A", "u3", "a2"), aline(T0 + 230, "did A", "a3", "u3")]
        path = self._session(SID, recs)

        def closer(tt, mt, *a, **k):
            self.close_calls.append(tt)
            if "did A" in tt:
                return '{"done": [{"goal": 1, "why": "task A shipped"}], "block": []}'
            return '{"done": [], "block": [], "awaiting": [{"goal": 1, "why": "the deploy log is still being watched"}]}'
        jd.closer_llm = closer
        return path, launch_t + 60.0 + 120.0                            # the expiry: deadline + grace

    def test_the_stamp_carries_the_next_expiry_and_the_run_is_due_once_the_clock_passes_it(self):
        path, expiry = self._monitor_fixture()
        now0 = int(expiry) - 100
        self._converge(now0)
        watch, g1 = self._tops()                                        # the monitor's top, then task A's
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][g1["id"]]["nodeComplete"], "premise: the closer filed task A done")
        self.assertTrue(store["nodes"][watch["id"]].get("awaitingWhy"), "premise: the closer stamped the wait")
        self.assertIn(g1["id"], store.get("confirming") or [], "the settle is held: the monitor is awaited")
        self.assertEqual(self._stamp("plan")[1], expiry, "the planner's stamp carries the launch's expiry")
        self.assertEqual(self._stamp("close")[1], expiry)
        self._reset()
        self._pass(int(expiry))                                         # at the instant: not yet expired
        self.assertEqual((self._st("plan")["skipped"], self._st("close")["skipped"]), (1, 1),
                         "skipped at the expiry instant (em._bg_expired reads now > expiry)")
        self._reset()
        self._pass(int(expiry) + 1)
        self.assertEqual(self._st("plan")["due_clock"], 1, "past the expiry the planner runs on the clock alone")
        self.assertEqual(self._st("plan")["ran"], 1)
        store = jd.load_goals(SID)
        self.assertEqual(store["status"].get(g1["id"]), "completed", "the expired wait released the settle")
        self.assertIsNone(self._stamp("plan")[1], "no future expiry left: the stamp's not-before is None")

    def test_a_complete_run_at_the_expiry_instant_keeps_the_clock(self):
        # the review's boundary case (2026-09-07): run_triage's now is int(time.time()) and transcript
        # timestamps are whole seconds, so a pass lands in the expiry's own second routinely. At that
        # instant the launch has not expired (em._bg_expired is strict), the run holds the settle, and the
        # stamp must still carry the expiry: a stamp with no clock would skip every later pass while the
        # ungated pass at now > expiry releases the hold and completes the top.
        path, expiry = self._monitor_fixture()
        self.assertEqual(expiry, int(expiry), "fixture: an integral expiry (launch t + timeout + grace)")
        self._converge(int(expiry) - 100)
        watch, g1 = self._tops()
        with open(jd.STATE / "cleared.jsonl", "a") as f:               # an unrelated re-arm in the expiry's second
            f.write(json.dumps({"id": "cccccccc-1111-2222-3333-444444444444:g7", "op": "clear", "t": int(expiry)}) + "\n")
        self._reset()
        self._pass(int(expiry))
        self.assertEqual(self._st("plan")["ran"], 1, "re-armed: the planner ran at the instant")
        self.assertEqual(self._st("plan")["stamped"], 1, "a complete run")
        self.assertIn(g1["id"], jd.load_goals(SID).get("confirming") or [], "at the instant the wait still holds")
        self.assertEqual(self._stamp("plan")[1], expiry, "the stamp keeps the expiry that equals now")
        self.assertEqual(self._stamp("close")[1], expiry)
        self._reset()
        self._pass(int(expiry) + 1)
        self.assertEqual((self._st("plan")["ran"], self._st("plan")["due_clock"]), (1, 1), "past it: run on the clock")
        self.assertEqual(jd.load_goals(SID)["status"].get(g1["id"]), "completed", "the released settle completes the top")
        self.assertIsNone(self._stamp("plan")[1])

    def test_the_store_tiers_stamps_carry_no_clock_input(self):
        # settle=False for the four store tiers: they consult the settle on write paths only, so their stamps
        # hold no not-before and a background launch's expiry makes none of them due on the clock (the
        # planner's stamp, on the same fixture, does carry it)
        path, expiry = self._monitor_fixture()
        self._converge(int(expiry) - 100)
        self.assertEqual(self._stamp("plan")[1], expiry, "premise: the planner's stamp carries the expiry")
        for t in STORE_TIERS:
            self.assertIsNone(self._stamp(t)[1], "%s: no clock input in the stamp" % t)
        self._reset()
        self._pass(int(expiry) + 1, tiers=STORE_TIERS)                 # the planner NOT run: no publish re-arms them
        self.assertEqual(self._st4("due_clock"), (0, 0, 0, 0), "past the expiry no store tier is due on the clock")
        self.assertEqual(self._st4("ran"), (0, 0, 0, 0))
        self.assertEqual(self._st4("skipped"), (1, 1, 1, 1))

    def test_the_expiry_helper_and_the_predicate_agree(self):
        self.assertEqual(em._bg_expiry_t({"deadline": 1000.0}), 1120.0)
        self.assertEqual(em._bg_expiry_t({"deadline": 1000.0, "deadlineSrc": "hook"}), 1005.0, "hook grace 5")
        self.assertIsNone(em._bg_expiry_t({"id": "x"}), "no deadline: never expires by the clock")
        for t in ({"deadline": 1000.0}, {"deadline": 1000.0, "deadlineSrc": "hook"}):
            x = em._bg_expiry_t(t)
            self.assertFalse(em._bg_expired(t, x), "at the instant: not expired")
            self.assertTrue(em._bg_expired(t, x + 0.001), "past it: expired")
        self.assertFalse(em._bg_expired({"id": "x"}, 1e12))

    def test_not_before_is_the_earliest_future_non_ghost_expiry(self):
        path, expiry = self._monitor_fixture()
        self.assertEqual(jd._settle_not_before(SID, str(path), expiry - 10), expiry)
        self.assertEqual(jd._settle_not_before(SID, str(path), expiry), expiry,
                         "at the instant the launch has not expired yet (em._bg_expired is strict), so it stays on the stamp")
        self.assertIsNone(jd._settle_not_before(SID, str(path), expiry + 1), "past it nothing lies ahead")
        reg = jd.STATE / "sdk" / (SID + ".json")
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"spawnedAt": T0 + 400}))            # a CLI spawned after the launch: a ghost
        self.assertIsNone(jd._settle_not_before(SID, str(path), expiry - 10),
                          "a ghost launch's expiry can change no verdict, so it arms no clock")


class DeathDrain(_Gate):
    def _dead(self):
        # a session outside the discover window with a pending death marker: run_close reaches it only
        # through the death drain
        path = self._session(SID)
        self._converge()
        later = int(time.time()) + 49 * 3600                            # the transcript falls out of the 48 h window
        jd._discover_cache.clear()                                      # (the list is cached behind a dir fingerprint)
        self.assertFalse([s for s in jd.discover(later) if s[0] == SID], "premise: not discovered")
        jd._discover_cache.clear()
        return path, later

    def test_a_pending_marker_finalizes_on_the_first_drain_run_and_the_sid_then_skips(self):
        path, later = self._dead()
        jd._write_death_marker(SID, {"t": T0 + 1000, "by": "probe"})
        self._reset()
        self._pass(later, tiers=("close",))
        self.assertEqual(self._st("close")["ran"], 1, "the drain's sid runs through the gate")
        m = json.loads((jd.GONEDIR / (SID + ".json")).read_text())
        self.assertIn("endedAt", m, "the settled dead store finalized its marker")
        self.assertEqual((self._st("close")["ran"], self._st("close")["stamped"]), (1, 1),
                         "a complete drain run stamps (the drain's sids carry a signature)")
        self.assertIsNotNone(self._stamp("close"))
        self._reset()
        self._pass(later + 1, tiers=("close",))
        self.assertEqual((self._st("close")["ran"], self._st("close")["skipped"]), (0, 0),
                         "a finalized marker leaves the drain: the sid is not listed at all")
        self.assertIsNone(self._stamp("close"), "...and its stamp is evicted with it")

    def test_a_marker_superseded_by_a_newer_states_row_retires(self):
        path, later = self._dead()
        jd._write_death_marker(SID, {"t": T0 + 1000, "by": "probe"})
        self._states_row(SID, T0 + 2000, "idle")                        # a revival's row, newer than the marker
        self._pass(later, tiers=("close",))
        m = json.loads((jd.GONEDIR / (SID + ".json")).read_text())
        self.assertTrue(m.get("superseded"))

    def test_a_cut_walk_leaves_the_marker_pending_and_the_sid_due(self):
        path, later = self._dead()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))

        def dead_call(*a, **k):
            jd._judge_ctx.last_call_fail = {"note": "the model CLI died with no output (exit -14)",
                                            "model": "sonnet", "kill": True}
            return ""
        jd.closer_llm = dead_call
        self._pass()                                                    # the planner places task C; the closer's call dies
        self.assertEqual(self._st("close")["incomplete"], 1, "premise: the cut walk is incomplete while alive too")
        jd._write_death_marker(SID, {"t": T0 + 1000, "by": "probe"})
        self._reset()
        self._pass(later, tiers=("close",))
        self.assertEqual((self._st("close")["ran"], self._st("close")["incomplete"]), (1, 1))
        m = json.loads((jd.GONEDIR / (SID + ".json")).read_text())
        self.assertNotIn("endedAt", m, "a cut walk never finalizes the marker")
        self._reset()
        self._pass(later + 1, tiers=("close",))
        self.assertEqual(self._st("close")["ran"], 1, "still due: the cut run stamped nothing, so there is no stamp to match")


class Bounds(_Gate):
    def test_rebind_empties_the_stamps_and_eviction_follows_discover(self):
        self._session(SID)
        self._session(SID2, name="api")
        self._converge()
        self.assertEqual(len(jd._STAGE_STAMP), len(ALL_TIERS) * 2, "one stamp per gated tier per sid")
        (jd.NAMES / SID2).unlink()                                      # the session leaves discover
        jd._namefp_memo.clear()
        self._pass(tiers=ALL_TIERS)
        self.assertEqual({k for k in jd._STAGE_STAMP}, {(t, SID) for t in ALL_TIERS}, "the gone sid's stamps evicted")
        jd._rebind_state(self.td)
        self.assertEqual(jd._STAGE_STAMP, {}, "a new root is a new world")

    def test_the_cap_clears(self):
        self._session(SID)
        self._session(SID2, name="api")
        jd._STAGE_STAMP_MAX = 1
        self._pass()
        self.assertEqual(len(jd._STAGE_STAMP), 1, "a wholesale clear at the cap: one full walk next pass")

    def test_an_unframed_runner_never_stamps(self):
        # no frame, no stamp: the served pair the frame records is what makes a stamp exact, so a runner
        # outside a pass frame (romp-judge --plan, a bare run_plan) runs every session and counts it
        # bypassed; the same runner under a frame stamps
        self._session(SID)
        jd.end_pass_frame(True)
        jd._discover_cache.clear()
        jd.run_plan(now=NOW)
        st = self._st("plan")
        self.assertEqual((st["ran"], st["bypassed"], st["stamped"]), (1, 1, 0))
        self.assertIsNone(self._stamp("plan"), "no frame: nothing stamped")
        self._reset()
        self._pass(tiers=("plan",))
        self.assertEqual(self._st("plan")["stamped"], 1, "under a frame the same work stamps")

    def test_a_muted_sid_leaves_the_parse_tiers_and_an_unmute_costs_one_full_run(self):
        # session-flags.json is in no signature on purpose (the inventory above GATED_TIERS): _hidden_from_feed
        # filters run_plan's, run_close's and run_unblock's lists before any stage runs, so a muted sid is not
        # listed and the post-pool eviction drops its stamps for those three tiers; the other three runners do
        # not filter on it and keep skipping it. An unmute lists the sid again with no stamp to match: one
        # full run (a load, the walk, a stamp), then the skips resume (review find, 2026-09-08: stated, untested)
        self._session(SID)
        self._session(SID2, name="api")
        self._converge()
        flags = jd.STATE / "session-flags.json"
        flags.write_text(json.dumps({SID2: {"hideFromFeed": True}}))     # the timeline's mute checkbox
        self._reset()
        self._pass(tiers=ALL_TIERS)
        self.assertEqual({k for k in jd._STAGE_STAMP if k[1] == SID2},
                         {(t, SID2) for t in ("group", "consolidate", "distill")},
                         "the muted sid's planner, closer and unblocker stamps are evicted; the store tiers keep theirs")
        for t in ("plan", "close", "unblock"):
            self.assertEqual((self._st(t)["ran"], self._st(t)["skipped"]), (0, 1), "%s: the muted sid is not listed" % t)
        for t in ("group", "consolidate", "distill"):
            self.assertEqual((self._st(t)["ran"], self._st(t)["skipped"]), (0, 2), "%s: both sids skip" % t)
        flags.write_text(json.dumps({}))                                  # the unmute
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=ALL_TIERS)
        for t in ("plan", "close", "unblock"):
            self.assertEqual((self._st(t)["ran"], self._st(t)["stamped"], self._st(t)["skipped"]), (1, 1, 1),
                             "%s: the unmuted sid runs once, in full, and stamps; the other sid skips" % t)
        for t in ("group", "consolidate", "distill"):
            self.assertEqual((self._st(t)["ran"], self._st(t)["skipped"]), (0, 2), "%s: nothing changed for either sid" % t)
        self.assertGreater(self.io["loads"] - io0["loads"], 0, "a full run loads the store")
        self.assertEqual({k for k in jd._STAGE_STAMP if k[1] == SID2}, {(t, SID2) for t in ALL_TIERS})
        self._reset()
        self._pass(tiers=ALL_TIERS)
        self.assertTrue(all(self._st(t)["ran"] == 0 for t in ALL_TIERS), "and the skips resume")


class FsCompleteness(_Gate):
    """The loud guard for a missing input: wrap the filesystem for one idle run of each stage over a
    two-session fixture (a task store, captions, episodes, a finalized death marker and an sdk reg present)
    and hold every path touched under the state root, the transcript directory and the task store against
    the tier's signature file set plus a fixed allowlist. A stage that starts reading a file the signature
    does not carry fails here."""

    # what a stage may touch beyond its signature: the model-call gate's two files (a skip on either is a ""
    # call, so the belt marks the run incomplete) and the errors log it writes to. session-flags.json is NOT
    # here: no stage reads it (the runners filter their lists on it before any stage runs; Bounds has the
    # muted-sid test), so a stage that started to would fail here (review find, 2026-09-08)
    ALLOW_NAMES = {"usage.json", "retry-paused.json", "judge-errors.jsonl"}

    def _touched(self, fn):
        seen = set()

        def note(p):
            if isinstance(p, (str, bytes, os.PathLike)):
                seen.add(os.path.abspath(os.fsdecode(p)))
        reals = {(os, "stat"): os.stat, (os, "lstat"): os.lstat, (os, "open"): os.open, (os, "scandir"): os.scandir,
                 (os, "listdir"): os.listdir, (builtins, "open"): builtins.open, (io, "open"): io.open}

        def wrap(real):
            def w(p, *a, **k):
                note(p)
                return real(p, *a, **k)
            return w
        for (mod, name), real in reals.items():
            setattr(mod, name, wrap(real))
        try:
            fn()
        finally:
            for (mod, name), real in reals.items():
                setattr(mod, name, real)
        return seen

    def _fixture(self):
        self._session(SID)
        self._session(SID2, name="api")
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (jd.CAPDIR / (SID + ".jsonl")).write_text(json.dumps({"id": "seg#p", "caption": "Ship the search"}) + "\n")
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        (jd.EPIDIR / (SID + ".jsonl")).write_text(json.dumps({"head": "u1", "fsid": SID, "t": T0}) + "\n")
        jd._write_death_marker(SID2, {"t": T0 + 500, "by": "probe", "endedAt": T0 + 500})
        reg = jd.STATE / "sdk" / (SID + ".json"); reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"spawnedAt": T0 - 10}))
        d = self.claude / "tasks" / SID; d.mkdir()
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "write the api tests", "status": "pending"}))
        self._states_row(SID, T0 + 131, "idle")
        (jd.STATE / "cleared.jsonl").write_text("")
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"enabled": False, "deferred": {}}))
        self._converge()

    def _allowed(self, tier, sid, path):
        ident, value = jd._sig_inputs(tier, sid, str(path))
        allowed = {os.path.abspath(str(p)) for p in ident + value}
        if tier in jd.PARSE_TIERS:
            cands, states, key_files = jd._parse_key_files(sid, [str(path)])
            allowed |= {os.path.abspath(str(p)) for p in key_files + [states, jd.MESSAGES]}
        # the grouper, consolidator and distiller carry NO parse pair, so a transcript read on their idle
        # path is exactly what this test must catch: the transcript stays out of their allowed set
        allowed |= {os.path.abspath(str(jd.STATE / n)) for n in self.ALLOW_NAMES}
        return allowed

    def _check(self, tier, stage, prep=None):
        self._fixture()
        if prep is not None:
            prep()
            self._converge()
        for sid in (SID, SID2):
            path = self.pdir / (sid + ".jsonl")
            own = jd.begin_pass_frame()                                 # a fresh frame: the parse hits the filesystem
            try:
                touched = self._touched(lambda: stage(sid, str(path), NOW))
            finally:
                jd.end_pass_frame(own)
            allowed = self._allowed(tier, sid, path)
            roots = (str(jd.STATE), str(self.pdir), str(self.claude / "tasks"))
            scratch = (str(jd.JUDGE_SCRATCH), str(jd.NAMES), str(self.claude / "tasks" / Path(path).stem))
            stray = sorted(p for p in touched
                           if p.startswith(roots) and p not in allowed and not os.path.isdir(p)
                           and not p.startswith(scratch))
            self.assertEqual(stray, [], "%s read files its signature does not carry for %s" % (tier, sid))

    def test_the_planners_idle_reads_are_all_in_its_signature(self):
        self._check("plan", jd._plan_session)

    def test_the_closers_idle_reads_are_all_in_its_signature(self):
        self._check("close", jd._close_session)

    def test_the_unblockers_idle_reads_are_all_in_its_signature(self):
        # with a blocked candidate whose examine is current (the block postdates every ended turn), so the
        # run reaches the parse and stops at `due` empty: the parse and the store, nothing else
        self._check("unblock", jd._unblock_session,
                    prep=lambda: self._block(SID, self._tops()[0]["id"], T0 + 150))

    def test_the_groupers_idle_reads_are_all_in_its_signature(self):
        self._check("group", jd._group_session)

    def test_the_consolidators_idle_reads_are_all_in_its_signature(self):
        self._check("consolidate", jd._consolidate_session)

    def test_the_distillers_idle_reads_are_all_in_its_signature(self):
        # the distiller's idle run, as the design review defines it: no untitled mirror top and an empty
        # todo; a transcript or peer-store read here would be a signature hole
        self._check("distill", jd._distill_session)


class StoreTiers(_Gate):
    """The four store-only tiers on the gate: two idle passes run once, then skip with no store I/O."""

    def test_two_idle_passes_run_once_then_skip_with_no_store_io(self):
        self._session(SID)
        self._pass(tiers=("plan", "close"))                            # the planner mints two tops, the closer sweeps
        self._reset()
        for t in STORE_TIERS:
            jd._PASS_DONE.pop((t, SID), None)                           # so the run path's own pass_done shows
        self._pass(tiers=STORE_TIERS)
        self.assertEqual(self._st4("ran"), (1, 1, 1, 1), "first pass: every store tier runs")
        self.assertEqual(self._st4("stamped"), (1, 1, 1, 1), "each ran to completion")
        self.assertEqual(self._st4("skipped"), (0, 0, 0, 0))
        for t in STORE_TIERS:
            self.assertIsNotNone(jd.pass_watermark(t, SID), "%s: a completed run stamps pass_done" % t)
        # the grouper and the consolidator each recorded their signature on first sight (groupedSig,
        # consolidatedSig: a store-level write, no relink): those publishes moved the store identity after
        # the unblocker's stamp and their own, and before the distiller's, which keyed on the final identity
        self._reset()
        self._pass(tiers=STORE_TIERS)
        self.assertEqual(self._st4("ran"), (1, 1, 1, 0), "the publishes re-armed the tiers stamped before them, once")
        self.assertEqual(self._st4("skipped"), (0, 0, 0, 1))
        self._reset()
        for t in STORE_TIERS:                                           # the earlier passes' watermarks go: the skip
            self.assertIsNotNone(jd._PASS_DONE.pop((t, SID), None), t)  #  must write its own, not inherit one
        io0 = dict(self.io)
        self._pass(tiers=STORE_TIERS)
        io1 = dict(self.io)
        self.assertEqual(self._st4("ran"), (0, 0, 0, 0), "the third pass skips every store tier")
        self.assertEqual(self._st4("skipped"), (1, 1, 1, 1))
        self.assertEqual((io1["loads"] - io0["loads"], io1["saves"] - io0["saves"]), (0, 0),
                         "a skipped session costs no store load and no save")
        for t in STORE_TIERS:
            self.assertIsNotNone(jd.pass_watermark(t, SID), "%s: a skip stamps pass_done" % t)
        self.assertEqual(self.unblock_calls, [], "no blocked goal, no unblocker call")

    def test_counters_add_up_over_the_store_tiers(self):
        self._session(SID)
        self._converge()
        for t in STORE_TIERS:
            self.assertIsNotNone(self._stamp(t), t)
        stats = jd.tier_stats()
        for t in STORE_TIERS:
            s = stats[t]
            self.assertEqual(s["ran"], s["stamped"] + s["bypassed"] + s["incomplete"], t)
        self.assertGreaterEqual(stats["stamps"], 6, "one stamp per tier for the sid")

    def test_an_unframed_pass_stamps_the_store_only_tiers_and_bypasses_the_parse_tiers(self):
        # the rule split (_gated): a parse tier's stamp needs the frame's served pair, so without a frame it
        # runs and bypasses; a store-only tier has no frame-pinned component (the gate stats every input
        # itself), so it stamps with or without one, and its next unframed run skips
        self._session(SID)
        self._converge()
        store = jd.load_goals(SID)
        top = self._tops()[0]
        store["nodes"][top["id"]]["text"] = "Renamed by a kernel-side writer"
        jd.save_goals(SID, store)                                       # re-arms all six tiers by identity
        runners = {"plan": jd.run_plan, "close": jd.run_close, "unblock": jd.run_unblock, "group": jd.run_group,
                   "consolidate": jd.run_consolidate, "distill": jd.run_distill}
        jd.end_pass_frame(True)
        self._reset()
        jd._discover_cache.clear()
        for t in ALL_TIERS:
            runners[t](now=NOW)                                         # no begin_pass_frame anywhere
        for t in ("plan", "close", "unblock"):
            st = self._st(t)
            self.assertEqual((st["ran"], st["bypassed"], st["stamped"]), (1, 1, 0), "%s: no frame, no stamp" % t)
        for t in ("group", "consolidate", "distill"):
            st = self._st(t)
            self.assertEqual((st["ran"], st["bypassed"], st["stamped"]), (1, 0, 1), "%s: stamps without a frame" % t)
        for _ in range(3):                                              # the store tiers' own writes re-arm once or twice
            self._reset()
            jd._discover_cache.clear()
            for t in ("group", "consolidate", "distill"):
                runners[t](now=NOW)
            if all(self._st(t)["ran"] == 0 for t in ("group", "consolidate", "distill")):
                break
            for t in ("group", "consolidate", "distill"):
                self.assertEqual(self._st(t)["bypassed"], 0, "%s: an unframed run that completes stamps" % t)
        for t in ("group", "consolidate", "distill"):
            self.assertEqual((self._st(t)["ran"], self._st(t)["skipped"]), (0, 1), "%s: the unframed stamp is honoured" % t)


class StoreReArms(_Gate):
    """Each input re-arms exactly the store tiers that read it (the runs, in STORE_TIERS order)."""

    def _rearms(self, expect, msg):
        self._reset()
        self._pass(tiers=STORE_TIERS)
        self.assertEqual(self._st4("ran"), expect, msg)

    def test_a_store_publish_a_journal_append_and_an_archive_write_re_arm_all_four(self):
        self._session(SID)
        self._converge()
        store = jd.load_goals(SID)
        top = self._tops()[0]
        store["nodes"][top["id"]]["text"] = "Renamed by a kernel-side writer"
        jd.save_goals(SID, store)
        self._rearms((1, 1, 1, 1), "a save_goals publish (a rename: new identity)")
        self._converge()
        jd.append_override(SID, top["id"], "resolve", NOW + 1)           # the user's gesture: the journal only
        self._rearms((1, 1, 1, 1), "a journal append with no store write")
        self.assertEqual(jd.load_goals(SID)["status"].get(top["id"]), "completed", "the replayed resolve took")
        self._converge()
        jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
        self._rearms((1, 1, 1, 1), "an archive write: in the grouper's and consolidator's signatures too "
                                   "(compaction rewrites it with no journal row)")

    def test_a_cleared_row_re_arms_the_grouper_and_consolidator_only(self):
        self._session(SID)
        self._converge()
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": SID2 + ":g1", "op": "clear", "t": NOW}) + "\n")
        self._rearms((0, 1, 1, 0), "a cleared.jsonl row (whole-file identity): the two tiers whose candidate "
                                   "forests read the view-cleared set")

    def test_a_transcript_append_re_arms_the_unblocker_only(self):
        path = self._session(SID)
        self._converge()
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        self._rearms((1, 0, 0, 0), "a transcript append: the parse pair is the unblocker's key and no one else's")
        self.assertEqual(self.unblock_calls, [], "no blocked goal: the re-armed run made no call")

    def test_a_states_row_re_arms_the_distiller_and_the_unblocker_not_the_grouper_or_consolidator(self):
        self._session(SID)
        self._converge()
        self._states_row(SID, T0 + 300, "picker")
        self._rearms((1, 0, 0, 1), "a states row entering picker: the distiller reads the states file, and the "
                                   "unblocker's parse key names it")

    def test_a_stall_record_for_this_sid_re_arms_the_distiller_and_another_sids_does_not(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        an = jd.STATE / "auto-nudge.json"
        an.write_text(json.dumps({"enabled": False, "deferred": {SID2 + ":g1": {"at": NOW, "why": "the closer has not settled the turn", "sid": SID2}}}))
        self._rearms((0, 0, 0, 0), "another sid's stall record")
        an.write_text(json.dumps({"enabled": False, "deferred": {top["id"]: {"at": NOW, "why": "the closer has not settled the turn", "sid": SID}}}))
        self._rearms((0, 0, 0, 1), "this sid's stall record: the staller owes the card a note")
        self.assertIsNotNone(jd.load_goals(SID)["nodes"][top["id"]].get("stallSummary"), "and wrote it")


class StoreOwnWrites(_Gate):
    """A tier's own publish re-arms it once (the stamp holds the pre-run identity); the follow-on run finds
    nothing to write and stamps; the third pass skips."""

    def test_the_unblockers_lift_re_arms_it_once_then_it_skips(self):
        path = self._session(SID)
        self._converge()
        top = self._tops()[0]
        self._block(SID, top["id"], T0 + 150)
        self._append(path, uline(T0 + 200, "the api binds 8080", "u3", "a2"), aline(T0 + 230, "noted", "a3", "u3"))
        jd.unblock_llm = lambda blocks, since, completed="": (self.unblock_calls.append(blocks) or LIFT_ONE)
        self._reset()
        self._pass(tiers=("unblock",))
        self.assertEqual(len(self.unblock_calls), 1, "one examine over the new turn")
        self.assertNotEqual(jd.load_goals(SID)["status"].get(top["id"]), "blocked", "the lift filed")
        self.assertEqual((self._st("unblock")["ran"], self._st("unblock")["stamped"]), (1, 1))
        self._reset()
        self._pass(tiers=("unblock",))
        self.assertEqual((self._st("unblock")["ran"], self._st("unblock")["stamped"]), (1, 1),
                         "the own publish re-armed it once; the follow-on run found nothing due")
        self.assertEqual(len(self.unblock_calls), 1, "and made no call")
        self._reset()
        self._pass(tiers=("unblock",))
        self.assertEqual((self._st("unblock")["ran"], self._st("unblock")["skipped"]), (0, 1))

    def test_a_titling_re_arms_the_distiller_once_and_the_third_pass_skips(self):
        # the design review's cross-tier convergence probe: _title_mirror_tops titles a mirror top and the
        # caller saves, so the distiller's own publish re-arms it once; the follow-on run reads no
        # transcript, writes nothing and stamps; the third pass skips
        self._session(SID)
        self._converge()
        nid = self._mirror_top(SID, "add tests+docs for the search endpoint")
        parses = []
        real = self._saved_store[5]
        jd.parsed_session = lambda *a, **k: (parses.append(a[0]) or real(*a, **k))
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(self.title_calls, ["add tests+docs for the search endpoint"])
        nd = jd.load_goals(SID)["nodes"][nid]
        self.assertEqual((nd.get("text"), nd.get("declaredSubject")), ("Write the api tests", "add tests+docs for the search endpoint"))
        self.assertTrue(nd.get("titledT"))
        self.assertEqual((self._st("distill")["ran"], self._st("distill")["stamped"]), (1, 1), "the titling run completed")
        self._reset()
        io0 = dict(self.io)
        parses.clear()
        self._pass(tiers=("distill",))
        io1 = dict(self.io)
        self.assertEqual((self._st("distill")["ran"], self._st("distill")["stamped"]), (1, 1),
                         "the own publish re-armed it once; the follow-on run stamps")
        self.assertEqual(parses, [], "no transcript read on the idle path")
        self.assertEqual(io1["saves"] - io0["saves"], 0, "nothing written")
        self.assertEqual(len(self.title_calls), 1, "titled once, never re-derived")
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual((self._st("distill")["ran"], self._st("distill")["skipped"]), (0, 1))


class StoreCompleteness(_Gate):
    """A store-tier run that did not finish leaves no stamp, so the sid stays due."""

    def _blocked_with_a_new_turn(self):
        path = self._session(SID)
        self._converge()
        top = self._tops()[0]
        self._block(SID, top["id"], T0 + 150)
        self._append(path, uline(T0 + 200, "the api binds 8080", "u3", "a2"), aline(T0 + 230, "noted", "a3", "u3"))
        return top

    def test_a_stripped_unblocker_reply_keeps_the_sid_due_without_a_strike(self):
        # patches _judge_run_impl, not unblock_llm: the helper strips its reply, so "   " reaches the stage as
        # "" and takes the no-write return (a truthy raw would take the parse-strike path instead)
        top = self._blocked_with_a_new_turn()
        jd.unblock_llm = self._saved_store[0]                            # the real helper, over the belt
        for reply in ("", "   "):
            jd._judge_run_impl = lambda *a, **k: reply
            self._reset()
            io0 = dict(self.io)
            self._pass(tiers=("unblock",))
            s = self._st("unblock")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "reply %r" % reply)
            self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
            self.assertFalse(jd.load_goals(SID).get("unblockFails"), "no strike burned on a failed call")
        jd.unblock_llm = lambda blocks, since, completed="": ""         # the helper itself returned nothing, above
        self._reset()                                                    # the belt: the stage's own mark decides
        io0 = dict(self.io)
        self._pass(tiers=("unblock",))
        s = self._st("unblock")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "an empty helper reply")
        self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
        jd.unblock_llm = self._saved_store[0]
        jd._judge_run_impl = lambda *a, **k: LIFT_ONE
        self._reset()
        self._pass(tiers=("unblock",))
        self.assertEqual(self._st("unblock")["stamped"], 1, "a served reply completes the run")
        self.assertNotEqual(jd.load_goals(SID)["status"].get(top["id"]), "blocked")

    def test_a_stripped_grouper_or_consolidator_reply_keeps_the_sid_due_without_a_strike(self):
        self._session(SID)
        self._pass(tiers=("plan", "close"))                            # two open tops: the grouper has a menu
        store = jd.load_goals(SID)
        store.pop("groupedSig", None)                                    # the planner groups inline after each placement
        jd.save_goals(SID, store)                                        # and recorded the set already: re-open the gate
        jd.group_llm = self._saved[3]                                    # the real helper, over the belt
        for reply in ("", "   "):
            jd._judge_run_impl = lambda *a, **k: reply
            self._reset()
            io0 = dict(self.io)
            self._pass(tiers=("group",))
            s = self._st("group")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "grouper, reply %r" % reply)
            self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
            self.assertFalse(jd.load_goals(SID).get("groupFails"), "no strike")
        jd.group_llm = lambda menu, judge="grouper": ""                  # the helper itself returned nothing, above
        self._reset()                                                    # the belt: the stage's own mark decides
        io0 = dict(self.io)
        self._pass(tiers=("group",))
        s = self._st("group")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "grouper, an empty helper reply")
        self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
        jd.group_llm = self._saved[3]
        jd._judge_run_impl = lambda *a, **k: '{"ops":[]}'
        self._reset()
        self._pass(tiers=("group",))
        self.assertEqual(self._st("group")["stamped"], 1, "a served reply completes the run (groupedSig written)")
        # the consolidator: two COMPLETED tops (resolved, and the focus top's pending settle forced through:
        # a done verdict on the last node exports as confirming until the session settles, and the
        # consolidator's menu is the completed status alone), then the same probe over that menu
        for top in self._tops():
            jd.append_override(SID, top["id"], "resolve", NOW + 1)
        store = jd.load_goals(SID)
        for top in self._tops():
            store["status"][top["id"]] = "completed"
        store["confirming"] = []
        jd.save_goals(SID, store)
        self.assertEqual(len(jd._consolidate_tops(jd.load_goals(SID))), 2, "fixture: the consolidator has a menu")
        for reply in ("", "   "):
            jd._judge_run_impl = lambda *a, **k: reply
            self._reset()
            io0 = dict(self.io)
            self._pass(tiers=("consolidate",))
            s = self._st("consolidate")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "consolidator, reply %r" % reply)
            self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
            self.assertFalse(jd.load_goals(SID).get("consolidateFails"), "no strike")
        jd.group_llm = lambda menu, judge="grouper": ""
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("consolidate",))
        s = self._st("consolidate")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "consolidator, an empty helper reply")
        self.assertEqual(self.io["saves"] - io0["saves"], 0, "no save")
        jd.group_llm = self._saved[3]
        jd._judge_run_impl = lambda *a, **k: '{"ops":[]}'
        self._reset()
        self._pass(tiers=("consolidate",))
        self.assertEqual(self._st("consolidate")["stamped"], 1)

    def test_a_paused_title_call_keeps_the_distiller_due(self):
        self._session(SID)
        self._converge()
        nid = self._mirror_top(SID, "add tests+docs for the search endpoint")
        jd.mirror_title_llm = self._saved_store[4]                       # the real helper, over the belt

        def paused(*a, **k):
            jd._judge_ctx.paused = True                                  # what the real call does under retry-paused.json
            return ""
        jd._judge_run_impl = paused
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "a pause-skipped titling leaves the sid due")
        self.assertEqual(self.io["saves"] - io0["saves"], 0)
        self.assertFalse(jd.load_goals(SID)["nodes"][nid].get("titledT"), "not stamped: the next pass retries")
        jd.mirror_title_llm = paused                                     # the helper itself pause-skipped, above the
        self._reset()                                                    # belt: _title_mirror_tops' own mark decides
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "a pause-skip above the belt")
        self.assertFalse(jd.load_goals(SID)["nodes"][nid].get("titledT"))
        jd.mirror_title_llm = self._saved_store[4]

        def served(*a, **k):
            jd._judge_ctx.paused = False
            return "Write the api tests"
        jd._judge_run_impl = served
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(self._st("distill")["stamped"], 1)
        self.assertEqual(jd.load_goals(SID)["nodes"][nid].get("text"), "Write the api tests")

    def test_a_paused_stall_brief_or_brief_retry_call_keeps_the_distiller_due(self):
        # the distiller's three other paused continues, each reached with the helper patched ABOVE the belt so
        # the stage's own mark is the one that decides: the staller (a stall record for this sid), the briefer
        # (a blocked top owed one decision) and the briefer's shortfall retry (two blocked nodes under one top:
        # the draft covers one, the corrective retry is pause-skipped)
        self._session(SID)
        self._converge()
        top = self._tops()[0]["id"]

        def paused(*a, **k):
            jd._judge_ctx.paused = True
            return ""
        an = jd.STATE / "auto-nudge.json"
        an.write_text(json.dumps({"enabled": False, "deferred": {top: {"at": NOW, "why": "the closer has not settled the turn", "sid": SID}}}))
        jd.stall_llm = paused
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "the staller's paused continue marks the run")
        nd = jd.load_goals(SID)["nodes"][top]
        self.assertIsNone(nd.get("stallSummary"))
        self.assertFalse(nd.get("stallFails"), "a pause-skip is not a strike")
        jd._judge_ctx.paused = False
        an.write_text(json.dumps({"enabled": False, "deferred": {}}))    # the stall ends; the top is blocked instead
        self._block(SID, top, T0 + 150)
        jd.brief_llm = paused
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "the briefer's paused continue marks the run")
        nd = jd.load_goals(SID)["nodes"][top]
        self.assertIsNone(nd.get("blockSummary"))
        self.assertFalse(nd.get("briefFails"), "a pause-skip is not a strike")
        jd._judge_ctx.paused = False
        store = jd.load_goals(SID)                                       # a second owed decision under the same top
        kid = SID + ":g91"
        store["nodes"][kid] = {"id": kid, "text": "Choose the api's auth scheme", "parentId": top, "why": "x",
                               "t": T0 + 300, "mt": T0 + 300, "log": [], "trail": [], "nodeComplete": False, "cleared": False}
        jd.save_goals(SID, store)
        self._block(SID, kid, T0 + 160, why="Which auth scheme should the api use?")
        shortfalls = []

        def draft_then_paused_retry(text, work, owed, **kw):
            shortfalls.append(kw.get("shortfall"))
            if kw.get("shortfall") is not None:                          # the corrective retry: pause-skipped
                jd._judge_ctx.paused = True
                return ""
            return "Pick the port the api binds."                        # one paragraph for two owed decisions
        jd.brief_llm = draft_then_paused_retry
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual(shortfalls, [None, (1, 2)], "the draft, then the retry naming the shortfall")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "the retry's paused continue marks the run")
        self.assertIsNone(jd.load_goals(SID)["nodes"][top].get("blockSummary"), "the brief stays owed")
        jd._judge_ctx.paused = False
        jd.brief_llm = lambda text, work, owed, **kw: "Pick the port the api binds.\n\nUse bearer tokens."
        self._converge(tiers=("distill",))
        self.assertTrue(jd.load_goals(SID)["nodes"][top].get("blockSummary"), "unpaused, the brief lands and the tier settles")

    def test_a_paused_distill_call_keeps_the_sid_due_and_a_failed_one_writes_the_strike(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        jd.append_override(SID, top["id"], "resolve", NOW + 1)           # completed: a summary is owed

        def paused(*a, **k):
            jd._judge_ctx.paused = True
            return ""
        jd.distill_llm = paused
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0), "the paused continue marks the run")
        nd = jd.load_goals(SID)["nodes"][top["id"]]
        self.assertIsNone(nd.get("summary"))
        self.assertFalse(nd.get("distillFails"), "a pause-skip is not a strike")
        jd._judge_ctx.paused = False
        jd.distill_llm = lambda *a, **k: ""                              # a real failure, past the belt
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(jd.load_goals(SID)["nodes"][top["id"]].get("distillFails"), 1, "the strike is written")
        self.assertEqual(self._st("distill")["ran"], 1)
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(self._st("distill")["ran"], 1, "the strike's publish re-armed the tier by identity")
        self.assertEqual(jd.load_goals(SID)["nodes"][top["id"]].get("distillFails"), 2, "and it retried")
        jd.distill_llm = lambda *a, **k: "Shipped the search endpoint."
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(jd.load_goals(SID)["nodes"][top["id"]].get("summary"), "Shipped the search endpoint.")
        self._converge(tiers=("distill",))

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_store_that_does_not_read_never_stamps(self):
        # a goals file that exists and cannot be read RAISES out of load_goals (never an empty fallback): every
        # tier dies at its load, _gated counts the run incomplete, the runner files its pass-crash row and
        # nothing is written, so the sid stays due until the file reads. Nothing on disk moves when a
        # permission bit or a descriptor failure clears, so a stamp here would skip the session for good
        self._session(SID)
        self._converge()
        gp = jd.GOALDIR / (SID + ".json")
        good = gp.read_bytes()
        os.chmod(gp, 0)
        try:
            os.utime(gp, ns=(os.stat(gp).st_atime_ns, os.stat(gp).st_mtime_ns + 1_000_000_000))   # re-arm: chmod moves ctime only
            for _ in range(2):
                self._reset()
                self._pass(tiers=ALL_TIERS)
                for t in ALL_TIERS:
                    s = self._st(t)
                    self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                                     "%s: a load that raises counts the run incomplete, so the sid stays due" % t)
            rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
            self.assertEqual(sum(r.get("err") == "pass-crash" for r in rows), 2 * len(ALL_TIERS),
                             "one pass-crash row per tier per pass, as for any raised run")
        finally:
            os.chmod(gp, 0o644)
        self.assertEqual(gp.read_bytes(), good, "no tier wrote")
        self._reset()
        self._pass(tiers=ALL_TIERS)
        self.assertEqual(tuple(self._st(t)["stamped"] for t in ALL_TIERS), (1,) * len(ALL_TIERS),
                         "readable again: every tier runs to completion and stamps")

    def test_a_store_that_does_not_parse_is_moved_aside_and_the_fresh_store_is_judged(self):
        # bytes that do not parse are quarantined aside by load_goals (<file>.corrupt-<stamp>, one
        # store-quarantined row) and the fresh store is then what the path holds, so the tiers run to
        # completion over it and stamp: no run is judged from a view the files contradict, and no tier crashes
        self._session(SID)
        self._converge()
        gp = jd.GOALDIR / (SID + ".json")
        gp.write_text("{ not the store")
        self._reset()
        self._pass(tiers=STORE_TIERS)
        self.assertEqual(self._st4("ran"), (1, 1, 1, 1))
        self.assertEqual(self._st4("incomplete"), (0, 0, 0, 0), "the fresh store is the files' content: no mark")
        aside = sorted(jd.GOALDIR.glob(SID + ".json.corrupt-*"))
        self.assertEqual(len(aside), 1, "the bad bytes were moved aside once")
        self.assertEqual(aside[0].read_text(), "{ not the store")
        rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
        self.assertEqual(sum(r.get("err") == "store-quarantined" for r in rows), 1)
        self.assertEqual(sum(r.get("err") == "pass-crash" for r in rows), 0, "no tier crashed")
        self._converge(tiers=STORE_TIERS)                                # and the fresh store converges like any other

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_journal_never_stamps(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        jd.append_override(SID, top["id"], "resolve", NOW + 1)
        self._converge()
        jp = jd._overrides_dir() / (SID + ".jsonl")
        os.chmod(jp, 0)
        try:
            os.utime(jp, ns=(os.stat(jp).st_atime_ns, os.stat(jp).st_mtime_ns + 1_000_000_000))   # re-arm: chmod moves ctime only
            self._reset()
            self._pass(tiers=STORE_TIERS)
            self.assertEqual(self._st4("ran"), (1, 1, 1, 1))
            self.assertEqual(self._st4("incomplete"), (1, 1, 1, 1), "_replay_overrides' unreadable branch marks the run")
            self.assertEqual(self._st4("stamped"), (0, 0, 0, 0))
            rows = [json.loads(l) for l in open(jd.ERRORS) if l.strip()]
            self.assertTrue(any(r.get("err") == "history-unreadable" for r in rows), "the loud row stays per pass")
        finally:
            os.chmod(jp, 0o644)
        self._reset()
        self._pass(tiers=STORE_TIERS)
        self.assertEqual(self._st4("stamped"), (1, 1, 1, 1), "readable again: complete runs stamp")

    def _focus(self):
        store = jd.load_goals(SID)
        f = store.get("lastNode")
        while f and store["nodes"][f].get("parentId") is not None:
            f = store["nodes"][f]["parentId"]
        return store["nodes"][f]

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_states_file_never_stamps(self):
        # the review's reproduction: the gate stats the states file into the distiller's signature, then the
        # stage's open fails. Before the fix the run answered "no live prompt", completed and stamped, and
        # the brief owed to the parked session waited until the file moved for an unrelated reason
        self._session(SID)
        self._converge()
        self._states_row(SID, T0 + 300, "picker")
        sp = jd.STATESDIR / (SID + ".jsonl")
        os.chmod(sp, 0)
        try:
            self._reset()
            self._pass(tiers=("distill",))
            s = self._st("distill")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                             "a states read that fails after a good stat marks the run incomplete")
            self.assertEqual(len(self._rows("states-unreadable")), 1, "one loud row")
            self._reset()
            self._pass(tiers=("distill",))
            self.assertEqual((self._st("distill")["ran"], self._st("distill")["incomplete"]), (1, 1), "still due")
            self.assertEqual(len(self._rows("states-unreadable")), 1, "one row per failure episode, not per pass")
        finally:
            os.chmod(sp, 0o644)
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["stamped"]), (1, 1), "readable again, nothing on disk moved: the run happens and stamps")
        self.assertTrue(self._focus().get("blockSummary"), "the parked session's brief landed")
        self._states_row(SID, T0 + 400, "picker")                        # a new picker run re-arms the distiller
        os.chmod(sp, 0)
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(len(self._rows("states-unreadable")), 2, "a second episode opens")
        sp.unlink()                                                      # removed while the episode is open
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(self._st("distill")["stamped"], 1, "no states file: absent is a real state, the run completes and stamps")
        self._states_row(SID, T0 + 500, "picker")
        os.chmod(sp, 0)
        try:
            self._reset()
            self._pass(tiers=("distill",))
            self.assertEqual(len(self._rows("states-unreadable")), 3, "recreated unreadable: absence ended the episode, a third row")
        finally:
            os.chmod(sp, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_cleared_file_never_stamps(self):
        self._session(SID)
        self._converge()
        cp = jd.STATE / "cleared.jsonl"
        with open(cp, "a") as f:                                         # a row: the grouper re-arms and reads it
            f.write(json.dumps({"id": SID2 + ":g1", "op": "clear", "t": NOW}) + "\n")
        os.chmod(cp, 0)
        try:
            self._reset()
            self._pass(tiers=("group",))
            s = self._st("group")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                             "a cleared.jsonl read that fails after a good stat marks the run incomplete")
            self.assertEqual(len(self._rows("cleared-unreadable")), 1)
        finally:
            os.chmod(cp, 0o644)
        self._reset()
        self._pass(tiers=("group",))
        self.assertEqual((self._st("group")["ran"], self._st("group")["stamped"]), (1, 1), "readable again: the run stamps")
        with open(cp, "a") as f:                                         # another row re-arms the grouper
            f.write(json.dumps({"id": SID2 + ":g2", "op": "clear", "t": NOW + 1}) + "\n")
        os.chmod(cp, 0)
        self._reset()
        self._pass(tiers=("group",))
        self.assertEqual(len(self._rows("cleared-unreadable")), 2, "a second episode opens")
        cp.unlink()                                                      # removed while the episode is open
        self._reset()
        self._pass(tiers=("group",))
        self.assertEqual(self._st("group")["stamped"], 1, "no cleared.jsonl: absent is a real state, the run completes and stamps")
        with open(cp, "a") as f:
            f.write(json.dumps({"id": SID2 + ":g3", "op": "clear", "t": NOW + 2}) + "\n")
        os.chmod(cp, 0)
        try:
            self._reset()
            self._pass(tiers=("group",))
            self.assertEqual(len(self._rows("cleared-unreadable")), 3, "recreated unreadable: absence ended the episode, a third row")
        finally:
            os.chmod(cp, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_stall_file_never_stamps(self):
        # the by-value input: the signature's own read fails too, and an empty slice would EQUAL the last
        # good one whenever the records were empty, so the gate would skip a session over a file it cannot
        # see. The signature read is strict (raises), so the gate runs the stage without a stamp (bypassed);
        # the stage's own failed read marks the run; one row per failure episode either way
        self._session(SID)
        self._converge()
        an = jd.STATE / "auto-nudge.json"
        rec = {"enabled": False, "deferred": {SID + ":g999": {"why": "the build has not finished", "at": T0 + 10}}}
        an.write_text(json.dumps(rec))                 # a record for this sid moves the slice: the distiller is due
        os.chmod(an, 0)
        try:
            for _ in range(2):
                self._reset()
                self._pass(tiers=("distill",))
                s = self._st("distill")
                self.assertEqual((s["ran"], s["bypassed"], s["stamped"]), (1, 1, 0),
                                 "the stage ran (no skip over an unreadable input) and did not stamp")
                self.assertEqual(len(self._rows("stall-unreadable")), 1, "one row per failure episode, not per pass")
        finally:
            os.chmod(an, 0o644)
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual((self._st("distill")["ran"], self._st("distill")["stamped"]), (1, 1), "readable again: the run stamps")
        an.write_text("{ not a document")              # exists, unparseable: the same shape, a new episode
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["bypassed"], s["stamped"]), (1, 1, 0), "an unparseable file is not a real state")
        self.assertEqual(len(self._rows("stall-unreadable")), 2, "a new failure episode: a second row")
        an.write_text(json.dumps(rec))                 # the same records again: nothing the last complete run did not judge
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual((self._st("distill")["ran"], self._st("distill")["skipped"]), (0, 1), "the stamp survived the episode")
        an.write_text("{ not a document")              # a third episode opens ...
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(len(self._rows("stall-unreadable")), 3)
        an.unlink()                                    # ... and the file is removed while it is open: absent is a real state
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(self._st("distill")["stamped"], 1, "no stall file: an empty slice, the run completes and stamps")
        an.write_text("{ not a document")              # recreated unparseable: absence ended the episode
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(len(self._rows("stall-unreadable")), 4, "a fourth row")

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_stall_read_failing_after_a_good_signature_read_marks_the_run(self):
        # the other half of the strict rule: the signature's read succeeded (the file was readable at gate
        # time), so the run carries a signature; the stage's own, non-strict stalled_facts read then fails
        # (the mode flips between the two reads, here under the mirror-top titling that precedes it). That
        # read marks the run incomplete on its own, so no stamp; a _read_failed that fired only under
        # strict would let the run stamp over a file it never saw
        self._session(SID)
        self._converge()
        an = jd.STATE / "auto-nudge.json"
        an.write_text(json.dumps({"enabled": False, "deferred": {}}))  # readable when the gate reads it
        self._mirror_top(SID, "add tests+docs for the search endpoint")   # titled BEFORE the stage's stall read

        def title_then_flip(subject, frame=None, user_ask=None):
            os.chmod(an, 0)                                             # the mode flips between the two reads
            return "Write the api tests"
        jd.mirror_title_llm = title_then_flip
        try:
            self._reset()
            self._pass(tiers=("distill",))
            s = self._st("distill")
            self.assertEqual((s["ran"], s["bypassed"], s["incomplete"], s["stamped"]), (1, 0, 1, 0),
                             "a signature was computed, and the stage's own failed read voided the stamp")
            self.assertEqual(len(self._rows("stall-unreadable")), 1, "one loud row")
        finally:
            os.chmod(an, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_rebind_starts_the_failure_episodes_afresh(self):
        # _rebind_state clears the per-path failure episodes with the rest of the per-root state: the same
        # path string after a rebind is a new episode and logs again
        self._session(SID)
        self._converge()
        self._states_row(SID, T0 + 300, "picker")
        sp = jd.STATESDIR / (SID + ".jsonl")
        os.chmod(sp, 0)
        try:
            self._reset()
            self._pass(tiers=("distill",))
            self.assertEqual(len(self._rows("states-unreadable")), 1)
            jd._rebind_state(self.td)                                   # the SAME root: the path string is unchanged
            self._reset()
            self._pass(tiers=("distill",))
            self.assertEqual(len(self._rows("states-unreadable")), 2, "the rebind ended the episode: a new row")
        finally:
            os.chmod(sp, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_cleared_file_vanishing_between_the_stat_and_the_read_ends_the_episode(self):
        # the race the stat-before-read order admits: the gate stat'd the file and the stage's read finds it
        # gone. Absent is a real state, so an open episode ends there (as an absent stat ends it), and a file
        # recreated unreadable logs a new row; a scan failure read as unreadable would keep the episode open
        # and swallow that row. Fault-injected at the scan seam, since the window is a race
        self._session(SID)
        self._converge()
        cp = jd.STATE / "cleared.jsonl"
        with open(cp, "a") as f:
            f.write(json.dumps({"id": SID2 + ":g1", "op": "clear", "t": NOW}) + "\n")
        os.chmod(cp, 0)
        self._reset()
        self._pass(tiers=("group",))
        self.assertEqual(len(self._rows("cleared-unreadable")), 1, "premise: an episode is open")
        os.chmod(cp, 0o644)
        real = jd._view_cleared_scan

        def vanish_then_scan(path_s):
            os.unlink(path_s)                                           # gone between the stat and the read
            return real(path_s)                                         # FileNotFoundError, as the read of a vanished file
        jd._view_cleared_scan = vanish_then_scan
        try:
            self._reset()
            self._pass(tiers=("group",))
        finally:
            jd._view_cleared_scan = real
        self.assertEqual(len(self._rows("cleared-unreadable")), 1, "a vanished file is not a failed read")
        with open(cp, "a") as f:
            f.write(json.dumps({"id": SID2 + ":g2", "op": "clear", "t": NOW + 1}) + "\n")
        os.chmod(cp, 0)
        try:
            self._reset()
            self._pass(tiers=("group",))
            self.assertEqual(len(self._rows("cleared-unreadable")), 2, "recreated unreadable: the vanish ended the episode")
        finally:
            os.chmod(cp, 0o644)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_a_states_file_vanishing_between_the_stat_and_the_read_ends_the_episode(self):
        # the states twin of the case above, at _live_prompt_since's scan seam
        self._session(SID)
        self._converge()
        self._states_row(SID, T0 + 300, "picker")
        sp = jd.STATESDIR / (SID + ".jsonl")
        os.chmod(sp, 0)
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(len(self._rows("states-unreadable")), 1, "premise: an episode is open")
        os.chmod(sp, 0o644)
        real = jd._live_prompt_since_scan

        def vanish_then_scan(path_s):
            os.unlink(path_s)
            return real(path_s)
        jd._live_prompt_since_scan = vanish_then_scan
        try:
            self._reset()
            self._pass(tiers=("distill",))
        finally:
            jd._live_prompt_since_scan = real
        self.assertEqual(len(self._rows("states-unreadable")), 1, "a vanished file is not a failed read")
        self._states_row(SID, T0 + 400, "picker")
        os.chmod(sp, 0)
        try:
            self._reset()
            self._pass(tiers=("distill",))
            self.assertEqual(len(self._rows("states-unreadable")), 2, "recreated unreadable: the vanish ended the episode")
        finally:
            os.chmod(sp, 0o644)

    # ── the stage-site marks, one test per site, each forcing the early return past the _judge_run belt ──
    def test_the_unblockers_failed_call_site_marks_the_run(self):
        self._blocked_with_a_new_turn()
        jd.unblock_llm = lambda blocks, since, completed="": ""        # the helper, not the belt: the site alone marks
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("unblock",))
        s = self._st("unblock")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertEqual(self.io["saves"] - io0["saves"], 0)

    def test_the_groupers_failed_call_site_marks_the_run(self):
        self._session(SID)
        self._pass(tiers=("plan", "close"))
        store = jd.load_goals(SID)
        store.pop("groupedSig", None)
        jd.save_goals(SID, store)
        jd.group_llm = lambda menu, judge="grouper": ""
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("group",))
        s = self._st("group")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertEqual(self.io["saves"] - io0["saves"], 0)
        self.assertFalse(jd.load_goals(SID).get("groupFails"))

    def test_the_consolidators_failed_call_site_marks_the_run(self):
        self._session(SID)
        self._converge()
        store = jd.load_goals(SID)
        for top in self._tops():
            store["status"][top["id"]] = "completed"
        store["confirming"] = []
        jd.save_goals(SID, store)
        jd.group_llm = lambda menu, judge="grouper": ""
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("consolidate",))
        s = self._st("consolidate")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertEqual(self.io["saves"] - io0["saves"], 0)
        self.assertFalse(jd.load_goals(SID).get("consolidateFails"))

    def test_the_titlers_paused_site_marks_the_run(self):
        self._session(SID)
        self._converge()
        nid = self._mirror_top(SID, "add tests+docs for the search endpoint")

        def paused_title(subject, frame=None, user_ask=None):
            jd._judge_ctx.paused = True
            return ""
        jd.mirror_title_llm = paused_title
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertEqual(self.io["saves"] - io0["saves"], 0)
        self.assertFalse(jd.load_goals(SID)["nodes"][nid].get("titledT"))

    def test_the_stallers_paused_site_marks_the_run(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"deferred": {top["id"]: {"why": "the build has not finished", "at": T0 + 100}}}))

        def paused_stall(text, work, holding):
            jd._judge_ctx.paused = True
            return ""
        jd.stall_llm = paused_stall
        self._reset()
        io0 = dict(self.io)
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertEqual(self.io["saves"] - io0["saves"], 0)
        self.assertIsNone(jd.load_goals(SID)["nodes"][top["id"]].get("stallSummary"))

    def test_the_briefers_paused_shortfall_retry_marks_the_run(self):
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        store = jd.load_goals(SID)
        kid = "%s:g%d" % (SID, 91)                                        # a sub-goal: two blocked nodes make owed a list
        store["nodes"][kid] = {"id": kid, "text": "pick the test database", "parentId": top["id"], "t": T0 + 120,
                               "mt": T0 + 120, "log": [], "trail": [], "nodeComplete": False, "cleared": False}
        store["status"][kid] = "working"
        jd.save_goals(SID, store)
        self._block(SID, top["id"], T0 + 150, why="Which port should the api bind?")
        self._block(SID, kid, T0 + 160, why="Which database should the tests use?")
        calls = []

        def brief(text, work, owed, frame=None, user_ask=None, shortfall=None):
            calls.append(shortfall)
            if shortfall:                                                # the retry is skipped under the pause
                jd._judge_ctx.paused = True
                return ""
            return "One paragraph for two owed decisions."
        jd.brief_llm = brief
        self._reset()
        self._pass(tiers=("distill",))
        s = self._st("distill")
        self.assertEqual(calls, [None, (1, 2)], "two owed decisions, one paragraph: the corrective retry ran")
        self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0))
        self.assertIsNone(jd.load_goals(SID)["nodes"][top["id"]].get("blockSummary"), "the brief is still owed")


class SignatureFileReads(_Gate):
    """The four signature files whose stage readers answered EMPTY on a read failure and let the run stamp
    (review find, 2026-09-08): the captions (_prompt_gist), the episode log (_episode_read), the death marker
    (_death_marker) and the archive (load_goal_archive). Each now goes through _read_failed like the states
    file, cleared.jsonl and the stall records: the run is marked incomplete, one row per failure episode, and
    the session stays due until the file reads. A stamp over an answer that never read the file would skip
    the session until the file moved, which a permission bit, an EMFILE or an EIO never makes it do."""

    def _coerced_top(self, sid, quote, seg, n=80):
        """A coerce-floor node still wearing its verbatim head as its title: the shape _heal_floor_titles
        retitles from the persisted prompt caption once one exists, reading captions/<sid>.jsonl every run
        until it does."""
        store = jd.load_goals(sid)
        nid = "%s:g%d" % (sid, n)
        store["nodes"][nid] = {"id": nid, "text": jd._seg_label(quote), "quote": quote, "parentId": None,
                               "why": jd._COERCE_WHY, "t": T0 + 300, "mt": T0 + 300, "log": [], "trail": [seg],
                               "nodeComplete": False, "cleared": False}
        store["status"][nid] = "working"
        jd.save_goals(sid, store)
        return nid

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_captions_file_never_stamps_the_planner(self):
        # the caption the heal waits for lands (a new identity: the planner runs), and the read fails after
        # the gate stat'd it. Before the fix the heal read '' and the run stamped, so the title healed only
        # when the file moved again; now the run is incomplete and the heal lands as soon as the file reads
        self._session(SID)
        quote = "Ship the search endpoint for the notes api"
        nid = self._coerced_top(SID, quote, "seg-x")
        self._converge()                                                 # no captions file yet: the heal reads nothing
        cp = jd.CAPDIR / (SID + ".jsonl")
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"id": "seg-x#p", "caption": "Ship the search"}) + "\n")
        os.chmod(cp, 0)
        try:
            self._reset()
            self._pass(tiers=("plan",))
            s = self._st("plan")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                             "the heal's read failed after a good stat: the run is incomplete, no stamp")
            self.assertEqual(len(self._rows("captions-unreadable")), 1, "one loud row")
            self.assertEqual(jd.load_goals(SID)["nodes"][nid]["text"], jd._seg_label(quote), "premise: not healed")
            self._reset()
            self._pass(tiers=("plan",))
            self.assertEqual((self._st("plan")["ran"], self._st("plan")["incomplete"]), (1, 1), "still due")
            self.assertEqual(len(self._rows("captions-unreadable")), 1, "one row per failure episode, not per pass")
        finally:
            os.chmod(cp, 0o644)
        self._converge(tiers=("plan",))                                  # readable, nothing moved: the run happens
        self.assertEqual(jd.load_goals(SID)["nodes"][nid]["text"], "Ship the search",
                         "the heal landed once the file read (a stamp over the failed read would have skipped it)")
        self.assertEqual(len(self._rows("captions-unreadable")), 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_episode_log_never_stamps_the_planner_and_is_not_memoized(self):
        # a /clear boundary at T0 + 50 makes task A pre-episode: the planner's retire reads the floor for
        # every new unit. After a restart (empty stamps, empty mtime memo) the read fails: before the fix the
        # run answered "no floor", stamped, AND memoized the empty read under the file's mtime, so the rows
        # stayed invisible until the log grew; now the run is incomplete and the next read sees the rows
        path = self._session(SID)
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        ep = jd.EPIDIR / (SID + ".jsonl")
        ep.write_text(json.dumps({"head": "u1", "fsid": SID, "t": T0}) + "\n"
                      + json.dumps({"head": "u2", "fsid": SID, "t": T0 + 50}) + "\n")
        self._converge()
        self.assertEqual(len(self.plan_calls), 1, "premise: task A predates the boundary and was retired; task B placed")
        self._append(path, uline(T0 + 200, "task C", "u3", "a2"), aline(T0 + 230, "did C", "a3", "u3"))
        jd._STAGE_STAMP.clear(); jd._episode_memo.clear()                # what a restart does
        os.chmod(ep, 0)
        try:
            self._reset()
            self._pass(tiers=("plan",))
            s = self._st("plan")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                             "the floor read failed after a good stat: the run is incomplete, no stamp")
            self.assertEqual(len(self._rows("episodes-unreadable")), 1, "one loud row")
            self.assertEqual(jd.episode_rows(SID), [], "while unreadable the log answers empty")
        finally:
            os.chmod(ep, 0o644)
        self.assertEqual(len(jd.episode_rows(SID)), 2,
                         "the failed read was not memoized under the file's mtime: the next read sees the rows")
        self._converge(tiers=("plan",))
        self.assertIsNotNone(self._stamp("plan"), "readable again: the planner completes and stamps")
        self.assertEqual(len(self._rows("episodes-unreadable")), 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_death_marker_never_stamps_the_closer(self):
        # _death_finalize reads the marker at the end of every _close_session (a finalized one is read and
        # left alone), so the marker is on the closer's idle path; another sid's cleared row re-arms the
        # closer without giving it anything to write
        self._session(SID)
        jd._write_death_marker(SID, {"t": T0 + 1000, "by": "probe", "endedAt": T0 + 1000})
        self._converge()
        mp = jd.GONEDIR / (SID + ".json")
        with open(jd.STATE / "cleared.jsonl", "a") as f:
            f.write(json.dumps({"id": SID2 + ":g1", "op": "clear", "t": NOW}) + "\n")
        os.chmod(mp, 0)
        try:
            self._reset()
            self._pass(tiers=("close",))
            s = self._st("close")
            self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                             "the marker read failed after a good stat: the run is incomplete, no stamp")
            self.assertEqual(len(self._rows("marker-unreadable")), 1, "one loud row")
            self._reset()
            self._pass(tiers=("close",))
            self.assertEqual((self._st("close")["ran"], self._st("close")["incomplete"]), (1, 1), "still due")
            self.assertEqual(len(self._rows("marker-unreadable")), 1, "one row per failure episode, not per pass")
        finally:
            os.chmod(mp, 0o644)
        self._reset()
        self._pass(tiers=("close",))
        self.assertEqual((self._st("close")["ran"], self._st("close")["stamped"]), (1, 1),
                         "readable again, nothing on disk moved: the run happens and stamps")
        self.assertEqual(len(self._rows("marker-unreadable")), 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-000 file")
    def test_an_unreadable_archive_never_stamps_any_tier(self):
        # every tier's signature carries the archive; load_goals reads it for a journaled restore row, so a
        # restore row puts the read on every tier's load. One failure episode, one row, six incomplete runs
        self._session(SID)
        self._converge()
        top = self._tops()[0]
        ap = jd.GOALARCHDIR / (SID + ".json")
        jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
        jd.append_restore(SID, {top["id"]: dict(jd.load_goals(SID)["nodes"][top["id"]])}, {top["id"]: "working"}, NOW)
        os.chmod(ap, 0)
        try:
            self._reset()
            self._pass(tiers=ALL_TIERS)
            for t in ALL_TIERS:
                s = self._st(t)
                self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                                 "%s: the archive read failed after a good stat: incomplete, no stamp" % t)
            self.assertEqual(len(self._rows("archive-unreadable")), 1, "one row for the episode, not one per tier")
            self._reset()
            self._pass(tiers=ALL_TIERS)
            for t in ALL_TIERS:
                s = self._st(t)
                self.assertEqual((s["ran"], s["incomplete"], s["stamped"]), (1, 1, 0),
                                 "%s: still due, and the shared archive memo did not serve the failed read" % t)
            self.assertEqual(len(self._rows("archive-unreadable")), 1)
        finally:
            os.chmod(ap, 0o644)
        self._converge()
        self.assertEqual(len(self._rows("archive-unreadable")), 1, "readable again: every tier completed and stamped")


class UnblockerHazard(_Gate):
    def test_a_turn_ending_after_the_first_touch_is_judged_next_pass_by_the_unblocker(self):
        # the design review's hazard, on the unblocker: a tick job touches the session while its last turn is
        # OPEN and a top is blocked; the turn's final record and the idle row land; the gated unblocker runs
        # and finds nothing due under the pinned parse. Its stamp must hold the PRE-append pair, so the next
        # pass runs it over the ended turn and the lift files. Without the frame's parse-key pin
        # (_frame_parse_key) this test fails.
        path = self._session(SID)
        self._converge(tiers=("plan", "close"))
        top = self._tops()[0]
        self._block(SID, top["id"], T0 + 150)                            # after both ended turns
        self._append(path, uline(T0 + 200, "use port 8080 for the api", "u3", "a2"),
                     aline(T0 + 210, "starting on it", "a3", "u3", stop="tool_use"))
        jd.unblock_llm = lambda blocks, since, completed="": (self.unblock_calls.append(since) or LIFT_ONE)
        own = jd.begin_pass_frame()
        try:
            jd.parsed_session(SID, [str(path)], NOW)                     # the tick job's first touch, turn open
            pre = jd._frame["keys"][("parse", SID)]
            self._append(path, aline(T0 + 240, "bound to 8080", "a4", "a3"))
            self._states_row(SID, T0 + 241, "idle")
            jd.run_unblock(now=NOW)
        finally:
            jd.end_pass_frame(own)
        self.assertEqual(self.unblock_calls, [], "under the pinned parse the turn is open: nothing due, no call")
        st = self._stamp("unblock")
        self.assertIsNotNone(st, "the run completed and stamped")
        self.assertEqual(st[0][0][1], jd._pair_key(pre), "the stamp holds the PRE-append pair")
        self._reset()
        self._pass(tiers=("unblock",))
        self.assertEqual(self._st("unblock")["ran"], 1, "the live pair differs from the stamped one: the tier runs")
        self.assertEqual(len(self.unblock_calls), 1, "and examines the ended turn")
        self.assertIn("bound to 8080", self.unblock_calls[0])
        self.assertNotEqual(jd.load_goals(SID)["status"].get(top["id"]), "blocked", "the lift filed")


class DrainStaysUngated(_Gate):
    def test_the_drain_distills_an_absent_stuck_store_regardless_of_stamps(self):
        # a store no discovered session owns (no transcript for SID2) holding a completed top with a null
        # summary: _drain_undiscovered reaches it on every distill pass; it never holds a stamp to skip on
        self._session(SID)
        self._converge()
        nid = SID2 + ":g1"
        store = jd.load_goals(SID2)
        store["nodes"][nid] = {"id": nid, "text": "Ship the search", "parentId": None, "nodeComplete": True,
                               "cleared": False, "t": T0, "mt": T0 + 60, "trail": [],
                               "log": [{"kind": "done", "ev_t": T0 + 60, "at": T0 + 60, "src": "closer", "why": "landed"}]}
        store["status"][nid] = "completed"
        jd.save_goals(SID2, store)
        self._reset()
        self._pass(tiers=("distill",))
        self.assertEqual(jd.load_goals(SID2)["nodes"][nid].get("summary"), "",
                         "no transcript, no work: the sentinel, written by the ungated drain")
        self.assertIsNone(self._stamp("distill", SID2), "the drain leaves no stamp")
        self.assertEqual(self._st("distill")["ran"], 0, "the discovered sid skipped; the drain is not a gated run")


if __name__ == "__main__":
    unittest.main()
