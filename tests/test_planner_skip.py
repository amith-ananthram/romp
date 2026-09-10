#!/usr/bin/env python3
"""The planner skips a session whose inputs have not moved since a pass that had nothing to do (2026-09-09).

Measured on the maintainer's box: the planner worker pool was 37% of the process's samples, re-deriving every
session's segmentation, plan units and placement normalization on every pass. The gate keys each session on
every input the pass reads, taken before the store read: the parse cache's key bound to the session object,
the store with its journal and archive, the episode log, the leaf's task-store files, the reg file and the
transcript path, the captions file and each running background launch's expiry under the pass clock; it records
only when the pass placed nothing, collected no unit, left the store's key unchanged and was complete (the
evidence gate's bit). Pins: unchanged inputs
skip after a pass that had nothing to do; a moved store, a fresh parse, a task-store change alone (under the
session's own sid, and under its forked leaf's), a reg change alone, a prompt caption landing (which heals a
floor title) and a running launch crossing its deadline (which completes a done focus top) each un-skip that
session; the expiry term holds while a launch is inside its ceiling, and for a launch with no recorded ceiling
however far the clock runs; the settle reads the wall clock when no pass clock is handed in; a write landing
during the pass is seen next pass; three sessions with one moved leave placements and nodes byte-identical to
the ungated passes (two fresh worlds); a parse the cache does not hold is never skipped, nor is an expiry view
that cannot be computed; a pass that stood down is planned again, not recorded; a rebound root forgets; the counters.

Synthetic sids and text; a temp state root, a temp Claude config root for the task store; the planner's model
calls are stubs, deterministic, so two worlds agree byte for byte."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_planner_skip", os.path.join(BIN, "romp-judge"))

A = "11111111-2222-3333-4444-555555555501"
B = "11111111-2222-3333-4444-555555555502"
C = "11111111-2222-3333-4444-555555555503"
LEAF = "11111111-2222-3333-4444-555555555511"     # the transcript A's /clear forks to (its reg's lastSid)
NOW = 1781100000
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class _World(unittest.TestCase):
    SIDS = (A, B, C)
    PROMPTS = {A: "wire up the reconnect banner", B: "retire the request cap", C: "fix the mobile tab width"}

    def setUp(self):
        self.saved = (jd.STATE, jd.NAMES, jd.PROJECTS, jd.plan_llm, jd.opener_llm, jd.group_llm, jd._judge_run,
                      os.environ.get("CLAUDE_CONFIG_DIR"))
        jd.opener_llm = lambda text, menu, sibling_num=None, **kw: '{"ops":[{"why":"a new ask","do":"mint","text":"Ship: %s"}]}' % text.split("\n")[0][:40]
        jd.plan_llm = lambda *a, **kw: '{"ops":[{"why":"a new ask","do":"mint","text":"Ship: %s"}]}' % str(a[0] if a else "").split("\n")[0][:40]
        jd.group_llm = lambda menu, **kw: '{"ops":[]}'
        jd._judge_run = lambda *a, **kw: "{}"
        self._make_world()

    def tearDown(self):
        self._drop_world()
        (jd.STATE, jd.NAMES, jd.PROJECTS, jd.plan_llm, jd.opener_llm, jd.group_llm, jd._judge_run, cfg) = self.saved
        jd._rebind_state(jd.STATE)
        if cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = cfg

    def _make_world(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        jd._rebind_state(td / "state")
        self.cfg = td / "claude"
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = self.cfg / "projects"
        self.proj_dir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        self.proj_dir.mkdir(parents=True)
        names = td / "names"; names.mkdir()
        for i, sid in enumerate(self.SIDS):
            (names / sid).write_text("worker%d\t%s\t#abcdef\n" % (i, str(cdir)))
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.recs = {sid: [] for sid in self.SIDS}
        self._reset()
        for sid in self.SIDS:
            self.prompt(sid, T0 + 10 * self.SIDS.index(sid))

    def _drop_world(self):
        self._reset()
        self.td.cleanup()

    @staticmethod
    def _reset():
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._episode_memo.clear(); jd._shared_clear()
        jd._PLANNER_SEEN.clear(); jd._lastsid_memo.clear(); jd._BG_SCAN_CACHE.clear()
        for k in jd._PLANNER_STATS:
            jd._PLANNER_STATS[k] = 0
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None

    def prompt(self, sid, t, text=None):
        """A human prompt and its reply land in `sid`'s transcript: one plan unit's worth."""
        n = len(self.recs[sid]) // 2 + 1
        parent = self.recs[sid][-1]["uuid"] if self.recs[sid] else None
        text = text or self.PROMPTS[sid]
        self.recs[sid] += [uline(t, text, "u%d" % n, parent), aline(t + 30, "Done: %s." % text, "a%d" % n, "u%d" % n)]
        p = self.proj_dir / (sid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in self.recs[sid]) + "\n")
        os.utime(p, (t + 60, t + 60))                       # a distinct mtime per write: the parse key moves
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None

    def run_pass(self, now=NOW):
        """One planner pass; returns the gate counters' deltas (planned, skipped, recorded)."""
        before = jd.planner_skip_stats()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd.run_plan(now=now)
        after = jd.planner_skip_stats()
        return tuple(after[k] - before[k] for k in ("planned", "skipped", "recorded"))

    def stores(self):
        return {sid: json.loads((jd.GOALDIR / (sid + ".json")).read_text()) for sid in self.SIDS}

    def settle(self):
        """Plan until every session has nothing to do and is recorded; then a pass skips all three."""
        for _ in range(4):
            p, s, r = self.run_pass()
            if p == 0:
                break
        self.assertEqual(self.run_pass(), (0, 3, 0), "settled: all three skipped")


class PlannerSkip(_World):
    def test_unchanged_inputs_skip_after_a_pass_that_had_nothing_to_do(self):
        p, s, r = self.run_pass()
        self.assertEqual((p, s), (3, 0), "first pass: every session planned")
        stores = self.stores()
        self.assertTrue(all(st["nodes"] for st in stores.values()), "each prompt minted its top")
        self.assertTrue(all(len(st["placements"]) >= 1 for st in stores.values()))
        # a pass that has nothing left to do records; the next one skips
        for _ in range(3):
            p, s, r = self.run_pass()
            if r == 3:
                break
        self.assertEqual(r, 3, "every session recorded after a pass with nothing to do")
        self.assertEqual(self.run_pass(), (0, 3, 0))
        self.assertEqual(self.stores(), stores, "a skipped pass writes nothing")

    def test_a_moved_store_un_skips_that_session_only(self):
        self.settle()
        p = jd.GOALDIR / (A + ".json")
        d = json.loads(p.read_text()); d["seq"] = d.get("seq", 0) + 1
        p.write_text(json.dumps(d))
        planned, skipped, recorded = self.run_pass()
        self.assertEqual((planned, skipped), (1, 2), "A's store moved: A planned, B and C skipped")

    def test_a_fresh_parse_un_skips_and_the_new_prompt_is_placed(self):
        self.settle()
        self.prompt(B, T0 + 500, "and add the retry hint")
        planned, skipped, recorded = self.run_pass()
        self.assertEqual((planned, skipped), (1, 2), "B's transcript grew: B alone planned")
        st = self.stores()[B]
        self.assertEqual(len([n for n in st["nodes"].values() if n.get("parentId") is None]), 2, "the second ask minted")

    def test_a_task_store_change_alone_un_skips(self):
        self.settle()
        d = self.cfg / "tasks" / C
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "check the width on iOS", "status": "pending"}))
        self.assertEqual(self.run_pass()[0:2], (1, 2), "C's task store appeared: C alone planned")
        self.settle()
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "check the width on iOS", "status": "completed"}))
        os.utime(d / "1.json", (NOW + 5, NOW + 5))
        self.assertEqual(self.run_pass()[0:2], (1, 2), "a task's status moved: C alone planned")

    def test_a_reg_change_alone_un_skips(self):
        self.settle()
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (A + ".json")).write_text(json.dumps({"sid": A, "name": "worker0", "cwd": "/tmp", "auth": "login"}))
        self.assertEqual(self.run_pass()[0:2], (1, 2), "A's reg appeared: A alone planned")

    def test_a_pass_that_stood_down_is_not_recorded(self):
        # the evidence gate's completeness bit (_judge_ctx.stage_incomplete, reset by _gated before every run): a
        # deferral without a write, or a side file that exists and did not read, marks the pass incomplete. A
        # recorded incomplete pass would skip the session until an input moved, which a permission bit never does
        self.settle()
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (A + ".json")).write_text(json.dumps({"sid": A, "name": "worker0", "cwd": "/tmp", "auth": "login"}))
        real = jd._latch_ask_anchors

        def stand_down(fsid, session, store):
            jd._judge_ctx.stage_incomplete = True          # what a deferral or an unreadable side file does
            return real(fsid, session, store)
        jd._latch_ask_anchors = stand_down
        try:
            self.assertEqual(self.run_pass(), (1, 2, 0), "A planned with nothing to do, but incomplete: not recorded")
            self.assertNotIn(A, jd._PLANNER_SEEN)
            self.assertEqual(self.run_pass(), (1, 2, 0), "still incomplete: planned again, still not recorded")
        finally:
            jd._latch_ask_anchors = real
        self.assertEqual(self.run_pass(), (1, 2, 1), "complete with nothing to do: recorded")
        self.assertEqual(self.run_pass(), (0, 3, 0), "and skipped")

    def test_a_write_landing_during_the_pass_is_seen_next_pass(self):
        # INTERLEAVED WRITE: the key is taken before the store read; a journal row lands while the pass reads
        # A's store, so the key A records predates the file, and the next pass plans A again.
        self.settle()
        self.prompt(A, T0 + 600, "and the dark theme")            # A has work again: this pass plans it
        real = jd.load_goals
        landed = []

        def read_then_write(fsid):
            store = real(fsid)
            if fsid == A and not landed:
                landed.append(True)
                jd._overrides_dir().mkdir(parents=True, exist_ok=True)
                with (jd._overrides_dir() / (A + ".jsonl")).open("a") as f:
                    f.write(json.dumps({"op": "resolve", "id": A + ":gX", "t": NOW}) + "\n")
            return store
        jd.load_goals = read_then_write
        try:
            self.run_pass()                                    # plans A (units) with the write landing mid-pass
            counts = self.run_pass()                           # nothing to do now; A's recording pass
        finally:
            jd.load_goals = real
        self.assertTrue(landed)
        self.assertEqual(counts[0:2], (1, 2), "A planned again: the key it would have recorded predates the write")
        for _ in range(3):                                     # the replayed row may move the store once more
            counts = self.run_pass()
            if counts[0] == 0:
                break
        self.assertEqual(counts, (0, 3, 0), "A recorded with the post-write key, then skipped")

    def test_three_sessions_one_moved_leave_placements_and_nodes_identical_to_the_ungated_passes(self):
        def scenario(gated):
            self.settle()
            self.prompt(B, T0 + 500, "and add the retry hint")  # B moves
            if not gated:
                jd._PLANNER_SEEN.clear()
            counts = self.run_pass(now=NOW + 100)
            if not gated:
                jd._PLANNER_SEEN.clear()
            self.run_pass(now=NOW + 200)
            shape = {sid: json.dumps({"nodes": st["nodes"], "placements": st["placements"], "status": st["status"]},
                                     sort_keys=True) for sid, st in self.stores().items()}
            return counts, shape
        gated_counts, gated = scenario(True)
        self._drop_world(); self._make_world()
        ungated_counts, ungated = scenario(False)
        self.assertEqual(gated_counts[0:2], (1, 2), "gated: B planned, A and C skipped")
        self.assertEqual(ungated_counts[0:2], (3, 0), "ungated: all planned")
        self.assertEqual(gated, ungated, "byte-identical nodes, placements and status either way")

    def test_a_parse_the_cache_does_not_hold_is_never_skipped(self):
        real = jd.parsed_session
        jd.parsed_session = lambda fsid, paths, now: dict(real(fsid, paths, now))
        try:
            for _ in range(3):
                self.run_pass()
            self.assertEqual(self.run_pass(), (3, 0, 0), "unkeyed: planned every pass, never recorded")
        finally:
            jd.parsed_session = real

    def test_a_rebound_root_forgets_the_table(self):
        self.settle()
        self.assertTrue(jd._PLANNER_SEEN)
        other = tempfile.TemporaryDirectory()
        try:
            jd._rebind_state(Path(other.name))
            self.assertEqual(jd._PLANNER_SEEN, {})
        finally:
            jd._rebind_state(Path(self.td.name) / "state")
            other.cleanup()

    def test_the_counters(self):
        self.assertEqual(set(jd.planner_skip_stats()), {"skipped", "planned", "recorded"})
        s = jd.planner_skip_stats(); s["skipped"] = 99
        self.assertNotEqual(jd.planner_skip_stats()["skipped"], 99)

    def test_a_task_store_change_under_the_forked_leaf_un_skips(self):
        # A is an SDK session that /cleared: its reg names LEAF as the current transcript, so discover hands the
        # planner the leaf file under A's stable sid and the declared-plan sync reads tasks/<LEAF>/, never
        # tasks/<A>/. A's anchor transcript stays a parse candidate; the fork's null-rooted head drops it.
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (A + ".json")).write_text(json.dumps(
            {"sid": A, "name": "worker0", "cwd": str(Path(self.td.name) / "launchdir"), "lastSid": LEAF}))
        leaf = self.proj_dir / (LEAF + ".jsonl")
        recs = [uline(T0 + 100, "wire up the reconnect banner after the clear", "f1"),
                aline(T0 + 130, "Done: the banner reconnects.", "f2", "f1")]
        leaf.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(leaf, (T0 + 160, T0 + 160))
        self.assertEqual(self.run_pass()[0:2], (3, 0))
        self.assertEqual(jd._PARSE_CACHE[A][1]["leafFsid"], LEAF, "A parses its forked leaf")
        self.assertTrue(any(n.get("parentId") is None for n in self.stores()[A]["nodes"].values()), "A's ask minted")
        self.settle()
        d = self.cfg / "tasks" / LEAF
        d.mkdir(parents=True)
        (d / "1.json").write_text(json.dumps({"id": "1", "subject": "check the width on iOS", "status": "pending"}))
        self.assertEqual(self.run_pass()[0:2], (1, 2), "the leaf's task store appeared: A alone planned")
        self.assertTrue(any((n.get("agentTask") or {}).get("key") == "1" for n in self.stores()[A]["nodes"].values()),
                        "the to-do is mirrored from the leaf's store")

    def test_a_prompt_caption_landing_heals_a_floor_title_on_a_recorded_session(self):
        # The planner answers C's work-run with a lone skip: a human message never vanishes, so the hard guard
        # places it on the coerce floor wearing the verbatim head (no caption exists yet to title it from).
        # The prompt caption the index tier appends later is the ONLY input that moves; the heal must see it.
        stub = jd.plan_llm
        jd.plan_llm = (lambda text, *a, **kw:
                       '{"ops":[{"do":"skip","why":"nothing to file"}]}' if self.PROMPTS[C] in text else stub(text, *a, **kw))
        self.settle()
        floor = [n for n in self.stores()[C]["nodes"].values() if n.get("why") == jd._COERCE_WHY]
        self.assertEqual(len(floor), 1, "C's prompt landed on the coerce floor")
        nd = floor[0]
        self.assertEqual(nd["text"], jd._seg_label(nd["quote"]), "precondition: the floor node wears the verbatim head")
        seg = nd["trail"][0]
        jd.append_caption(C, seg + "#p", "prompt", NOW, "Fix the mobile tab width")
        self.assertEqual(self.run_pass()[0:2], (1, 2), "C's captions file appeared: C alone planned")
        self.assertEqual(self.stores()[C]["nodes"][nd["id"]]["text"], "Fix the mobile tab width", "retitled from the caption")
        self.settle()

    def _launch_transcript(self, sid, tid, name, inp, ack):
        """`sid`'s turn dispatches one background launch (tool_use `tid`: `name` with `inp`), the harness acks it
        with plain text (no <task-notification>, so em._bg_step keeps it running) and the turn ends."""
        recs = [uline(T0, self.PROMPTS[sid], "u1"),
                {"type": "assistant", "timestamp": iso(T0 + 20), "uuid": "m1", "parentUuid": "u1",
                 "message": {"role": "assistant", "stop_reason": None, "content": [
                     {"type": "tool_use", "id": tid, "name": name, "input": inp}]}},
                {"type": "user", "timestamp": iso(T0 + 21), "uuid": "r1", "parentUuid": "m1",
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": ack}]}},
                aline(T0 + 30, "Done: it is running.", "a1", "r1")]
        p = self.proj_dir / (sid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(p, (T0 + 90, T0 + 90))

    def _monitor_transcript(self, sid):
        """A Monitor: its timeout_ms is a recorded ceiling. em._bg_step registers the deadline at T0 + 20 + 3600
        and em._bg_expired reads the crossing 120 s later, at NOW + 140."""
        self._launch_transcript(sid, "toolu_mon1", "Monitor",
                                {"timeout_ms": 3600000, "persistent": False, "description": "watch the build"},
                                "monitoring started")

    def _bash_transcript(self, sid):
        """A backgrounded Bash: no recorded ceiling, so em._bg_expired never expires it."""
        self._launch_transcript(sid, "toolu_bash1", "Bash", {"command": "./serve-dev.sh", "run_in_background": True},
                                "Command running in background")

    def test_a_running_launch_crossing_its_deadline_re_plans_the_session_and_its_done_top_completes(self):
        self._monitor_transcript(A)
        self.assertEqual(self.run_pass()[0:2], (3, 0))
        top = next(nid for nid, n in self.stores()[A]["nodes"].items() if n.get("parentId") is None)
        sp = jd.GOALDIR / (A + ".json")                     # the done verdict, filed as a closer files it
        d = json.loads(sp.read_text())
        d["nodes"][top]["nodeComplete"] = True
        d["nodes"][top].setdefault("log", []).append({"ev_t": T0 + 40, "src": "closer", "kind": "done",
                                                      "why": "shipped", "at": T0 + 40})
        d["lastNode"] = top
        sp.write_text(json.dumps(d))
        self.settle()
        self.assertEqual(self.stores()[A]["status"][top], "working",
                         "precondition: complete and in focus, held by the running watch under the pass clock")
        self.assertEqual(self.run_pass(now=NOW + 100), (0, 3, 0), "the clock moved, no launch crossed: all skipped")
        self.assertEqual(self.run_pass(now=NOW + 200)[0:2], (1, 2), "the watch crossed its deadline: A alone planned")
        self.assertEqual(self.stores()[A]["status"][top], "completed", "the hold released and the top settled")

    def test_a_launch_with_no_ceiling_leaves_a_recorded_session_skipped(self):
        # The alternative to a key term, "never record while a launch runs", would plan this session on every
        # pass: a backgrounded Bash has no recorded ceiling, so its expiry is a stable False and the term never
        # moves, however far the clock runs.
        self._bash_transcript(A)
        self.assertEqual(self.run_pass()[0:2], (3, 0))
        self.settle()
        path = str(self.proj_dir / (A + ".jsonl"))
        self.assertEqual(jd._bg_expiry_key(path, NOW), (("toolu_bash1", False),), "running, no ceiling")
        self.assertEqual(jd._bg_expiry_key(path, NOW + 86400), jd._bg_expiry_key(path, NOW), "a day later: unchanged")
        self.assertEqual(self.run_pass(now=NOW + 200), (0, 3, 0), "the clock moved, nothing crossed: all skipped")
        self.assertEqual(self.run_pass(now=NOW + 86400), (0, 3, 0), "a day later: still skipped")

    def test_the_settle_reads_the_wall_clock_when_no_pass_clock_is_handed_in(self):
        # Every caller but the planner asks with no clock (the closer, the unblocker, the A/B path): the default
        # is the wall clock, read per call outside the scan cache. The clock is frozen, never slept.
        self._monitor_transcript(A)
        path = str(self.proj_dir / (A + ".jsonl"))
        saved = jd.time.time
        try:
            jd.time.time = lambda: NOW + 200
            self.assertEqual(jd._bg_unresolved(path), [], "past the ceiling under the wall clock: expired")
            self.assertEqual([t["id"] for t in jd._bg_unresolved(path, T0 + 25)], ["toolu_mon1"],
                             "a clock handed in outranks the wall clock")
            jd.time.time = lambda: NOW
            self.assertEqual([t["id"] for t in jd._bg_unresolved(path)], ["toolu_mon1"],
                             "inside the ceiling under the wall clock: still running")
        finally:
            jd.time.time = saved

    def test_an_expiry_view_that_cannot_be_computed_is_never_skipped(self):
        self.settle()
        real = jd.em.scan_bg_tasks_cached
        a_path = str(self.proj_dir / (A + ".jsonl"))

        def raising(path, cache, want_all=False):
            if str(path) == a_path:
                raise OSError("the transcript cannot be read")
            return real(path, cache, want_all)
        self.assertEqual(jd._bg_expiry_key(a_path, NOW), jd._bg_expiry_key(a_path, NOW), "a readable view is stable")
        jd.em.scan_bg_tasks_cached = raising
        try:
            self.assertNotEqual(jd._bg_expiry_key(a_path, NOW), jd._bg_expiry_key(a_path, NOW),
                                "a view that cannot be computed never equals a recorded one")
            for _ in range(3):
                self.assertEqual(self.run_pass()[0:2], (1, 2), "A planned every pass, never skipped")
        finally:
            jd.em.scan_bg_tasks_cached = real


if __name__ == "__main__":
    unittest.main()
