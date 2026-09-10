"""The kernel side of the shared read-only goal-store cache (kernel/judge.py load_goals_shared).

kernel/judge.py's load_goals_shared serves one deep-frozen parsed store per file version; this module pins
WHICH kernel sites load through it (the pusher's read-only sites) and which deliberately do not (every
writer, the probe-then-write tick jobs and the feed's snapshot read), and drives the builders
over synthetic stores to show the cache in effect: each store parsed once per version across builds, the
frozen guard reaching a wired site without taking the frame down, the compaction sweep's eviction, and
one session's failed chat build no longer aborting the whole push. Synthetic fixtures only: placeholder
sids, invented goal text, transcript paths that do not exist (a lane with no transcript still builds)."""
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SIDS = ["66666666-1111-4222-8333-44444444440%d" % i for i in range(3)]   # private to this module (synthetic)
NOW = 1781100000
T0 = NOW - 3600


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def _aline(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def _tm():
    """One live tmux entry, every key the feed builder reads."""
    return {"state": "ready", "color": "#888888", "since": NOW - 60, "model": "", "effort": "",
            "context": None, "backend": "tmux"}

# The pusher-side READ-ONLY sites, wired (kernel.py). Each reads nodes / status / seams / confirming / log
# rows and hands nothing to rollup_status, record_verdict or save_goals (audited 2026-09-06; the deep
# freeze would raise if one did). Five sit inside a per-session catch of their own and take the shared view
# directly; the three builders (four sites) read it through the per-session store-fault boundary
# (load_goals_shared_or_fault), so one session's unreadable store costs that session's goal-derived data
# and files one row per fault episode, never the frame.
WIRED = {"_open_top_goal": 1, "_deferral_sweep_tick": 1, "_session_stamp_read": 1, "_owned_yield_why": 1,
         "_msg_sum_scan_session": 1,
         "_bg_placed_tops": 1}   # the placed-launch memo: the shared view, or the store the caller hands in
WIRED_BOUNDARY = {"build_feed": 1, "build_session": 2, "build_timeline": 1}
# TWO-PHASE: the awaiting-lift job takes one shared PROBE (through the boundary: a fault forgets the gate so
# the next tick retries) and one writer load only when the probe found a lift due (jd.load_goals_or_fault,
# the same boundary around the writer's loader); the decision body (_lift_decisions) loads nothing and
# writes nothing.
TWO_PHASE = {"_lift_spent_awaiting": (1, 1)}
# NOT wired, on purpose: the feed's pass snapshot has its own memo (_feed_goals stays on the writer's
# loader, bare or behind load_goals_or_fault).
UNWIRED = ("_feed_goals",)


class WiringPins(unittest.TestCase):
    def test_the_read_only_pusher_sites_load_through_the_shared_cache(self):
        for name, n in WIRED.items():
            src = inspect.getsource(getattr(km, name))
            self.assertEqual(src.count("jd.load_goals_shared("), n, "%s: shared loads" % name)
            self.assertEqual(src.count("jd.load_goals(") + src.count("jd.load_goals_or_fault("), 0,
                             "%s: no writer-style load left" % name)
        for name, n in WIRED_BOUNDARY.items():
            src = inspect.getsource(getattr(km, name))
            self.assertEqual(src.count("jd.load_goals_shared_or_fault("), n, "%s: shared loads through the boundary" % name)
            self.assertEqual(src.count("jd.load_goals(") + src.count("jd.load_goals_or_fault("), 0,
                             "%s: the boundary reads the shared view, not the writer's loader" % name)

    def test_the_feeds_pass_snapshot_stays_on_load_goals(self):
        for name in UNWIRED:
            src = inspect.getsource(getattr(km, name))
            self.assertEqual(src.count("jd.load_goals_shared(") + src.count("jd.load_goals_shared_or_fault("), 0,
                             "%s: not wired" % name)
            self.assertGreaterEqual(src.count("jd.load_goals(") + src.count("jd.load_goals_or_fault("), 1,
                                    "%s: still the writer's loader, bare or behind the boundary" % name)

    def test_the_awaiting_lift_probes_the_shared_view_and_loads_the_writers_copy_once(self):
        for name, (shared, writer) in TWO_PHASE.items():
            src = inspect.getsource(getattr(km, name))
            self.assertEqual(src.count("jd.load_goals_shared_or_fault("), shared, "%s: the phase-1 probe" % name)
            self.assertEqual(src.count("jd.load_goals_or_fault("), writer, "%s: the phase-2 writer load" % name)
            self.assertEqual(src.count("jd.load_goals_shared(") + src.count("jd.load_goals("), 0,
                             "%s: no bare load outside the boundary" % name)

    def test_the_lifts_decision_body_loads_nothing_and_writes_nothing(self):
        # every rule of the lift is decided here, on whichever store the caller hands in (the shared view
        # in phase 1, the writer's copy in phase 2); the verdict gate is read through jd.may_apply only
        src = inspect.getsource(km._lift_decisions)
        for needle in ("jd.load_goals(", "jd.load_goals_shared(", "jd.load_goals_or_fault(",
                       "jd.load_goals_shared_or_fault(", "record_verdict(", "save_goals(",
                       "rollup_status(", "_drop_auto_nudge_rec("):
            self.assertEqual(src.count(needle), 0, "_lift_decisions: %s" % needle)
        self.assertEqual(src.count("jd.may_apply("), 4,
                         "the read-only gate, once per arm: rolled-up, peer-superseded, empty-registry, cited-return")
        # ...and it is the gate record_verdict consults before it appends, so a decision here is a
        # record_verdict that would have returned True (LiftGate's floor cases run the two side by side)
        self.assertIn("may_apply(", inspect.getsource(km.jd.record_verdict),
                      "record_verdict asks may_apply: phase 1 decides through the gate phase 2 files through")

    def test_bg_placed_tops_keys_on_objects_not_on_a_stat(self):
        # the per-version map is keyed on the parse and store OBJECTS in hand (a stat taken after the
        # read can describe a version the read did not see); no stat is taken here. The one presence
        # check (os.path.exists on the store file, an absent store answering nothing without a parse or
        # a load) is not a key and is allowed.
        src = inspect.getsource(km._bg_placed_tops)
        self.assertEqual(src.count(".stat()"), 0)
        self.assertEqual(src.count("os.stat("), 0)

    def test_the_compaction_sweep_evicts_the_caches_absent_paths(self):
        src = inspect.getsource(km._compact_goal_stores)
        self.assertIn("jd._disk_memo_evict_absent()", src)
        self.assertIn("jd._shared_evict_absent()", src)
        # ...and, for the two memos holding PARSED stores, the entries of stores no discovered session owns
        # (review find, 2026-09-08: neither had a cap)
        self.assertIn("jd._shared_evict_unowned(", src)
        self.assertIn("_goals_memo_evict_unowned(", src)


class SharedViewInBuilds(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))         # clears the cache and lifts any earlier off switch
        self.saved = {nm: getattr(km, nm) for nm in ("_timeline_sessions", "_derive_judging_marks")}
        km._lanes_memo.clear()                        # a lane the timeline memo holds never reaches the spy below
        for i, sid in enumerate(SIDS):
            s = {"rompUuid": sid, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
                 "placements": {}, "status": {}}
            jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Goal %d" % i}], [])
            jd.rollup_status(s, session_closed=False)
            jd.save_goals(sid, s)
        km._timeline_sessions = lambda now, tmux, live_only=False: [
            {"sid": sid, "name": "s%d" % i, "path": os.path.join(self.td.name, "no-such-transcript-%d" % i)}
            for i, sid in enumerate(SIDS)]
        # the compaction sweep evicts the entries of stores no DISCOVERED session owns, so the three synthetic
        # stores must be discovered for their entries to survive a sweep (review find, 2026-09-08)
        self.saved_discover = jd.discover
        self.discovered = list(SIDS)
        jd.discover = lambda now, window=None, forks=True: [(sid, "/dev/null", None, "s%d" % i)
                                                            for i, sid in enumerate(SIDS) if sid in self.discovered]
        self.stats0 = jd.shared_store_stats()

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        jd.discover = self.saved_discover
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _delta(self, key):
        return jd.shared_store_stats()[key] - self.stats0[key]

    def _errors(self):
        try:
            return [json.loads(l) for l in jd.ERRORS.read_text().splitlines() if l.strip()]
        except FileNotFoundError:
            return []

    def _transcript(self, sid, recs):
        p = Path(self.td.name) / (sid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km._parse_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        return str(p)

    def _private_loads(self):
        """Count the writer's loader per sid while the context runs: the wired sites must never reach it."""
        seen, o_load = [], jd.load_goals
        jd.load_goals = lambda fsid: (seen.append(fsid), o_load(fsid))[1]
        self.addCleanup(setattr, jd, "load_goals", o_load)
        return seen

    def test_two_chat_builds_parse_the_store_once(self):
        # build_session's two loads (the seam-aware seg ids, the ledger tree) take the shared view: two
        # builds of one tab parse its store once, and the writer's loader is never asked for it.
        sid = SIDS[0]
        tpath = self._transcript(sid, [_uline(NOW - 500, "start the next piece", "u1"),
                                       _aline(NOW - 480, "Done.", "a1", "u1")])
        sess = [{"sid": sid, "name": "s0", "anchor": None, "path": tpath, "mtime": NOW}]
        private = self._private_loads()
        with mock.patch.object(km, "_sessions", lambda now, window=None, forks=True: list(sess)):
            m1 = km.build_session(sid, NOW, {})
            m2 = km.build_session(sid, NOW, {})
        self.assertTrue(m1 and m1["ledger"]["tree"], "premise: the tab's ledger tree shows the goal")
        self.assertNotIn(sid, private, "the writer's loader was never asked for the tab's store")
        self.assertEqual(self._delta("miss"), 1, "one parse across two builds")
        self.assertGreaterEqual(self._delta("hit"), 3, "the first build's second load and both of the second's are hits")
        self.assertEqual(json.dumps(m1["ledger"]), json.dumps(m2["ledger"]))

    def test_the_feeds_peer_origin_badge_reads_the_shared_peer_view(self):
        # build_feed's card loop reads a delegation-origin badge's liveness from the PEER's store through the
        # shared boundary (load_goals_shared_or_fault), never the writer's loader.
        a, b = SIDS[0], SIDS[1]
        s = jd.load_goals(b)
        s["nodes"][b + ":g1"]["origin"] = {"peer": a, "goalId": a + ":g1"}
        jd.save_goals(b, s)
        sessions = [{"sid": x, "name": "s%d" % i, "path": "/nonexistent/%s.jsonl" % x, "anchor": 0, "mtime": 0}
                    for i, x in enumerate((a, b))]
        shared, o_shared = [], jd.load_goals_shared_or_fault
        jd.load_goals_shared_or_fault = lambda fsid: (shared.append(fsid), o_shared(fsid))[1]
        self.addCleanup(setattr, jd, "load_goals_shared_or_fault", o_shared)
        with mock.patch.object(km, "_alive_sessions", lambda now, tm: list(sessions)), \
             mock.patch.object(km, "_warm_fleet_bg", lambda now: None):
            cards = {c["itemId"]: c for c in km.build_feed(NOW, {a: _tm(), b: _tm()})["asks"]}
        self.assertIn(a, shared, "the peer's store was read through the shared boundary")
        self.assertTrue(cards[b + ":g1"]["origin"]["live"], "the peer's goal is open: the badge reads live")

    def test_the_message_summary_scan_holds_the_shared_view(self):
        # _msg_sum_scan_session hands the store to _segs_seam for the seam-aware seg ids: the shared
        # read-only view, which apply_seams only reads.
        sid = SIDS[0]
        tpath = self._transcript(sid, [_uline(NOW - 500, "start the next piece", "u1"),
                                       _aline(NOW - 480, "Done.", "a1", "u1")])
        jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (jd.CAPDIR / (sid + ".jsonl")).write_text(json.dumps(
            {"id": "u1", "grain": "segment", "t": NOW - 500, "caption": "Starting the next piece"}) + "\n")
        seen, o_segs = [], km._segs_seam
        km._segs_seam = lambda turn, store: (seen.append(store), [])[1]
        self.addCleanup(setattr, km, "_segs_seam", o_segs)
        km._msg_sum_scan_session(sid, tpath, NOW)
        self.assertTrue(seen, "the scan reached the seg loop")
        self.assertTrue(all(isinstance(st, jd.FrozenStore) for st in seen), "the shared view, not a private load")
        self.assertEqual(self._delta("miss"), 1)

    def test_two_timeline_builds_parse_each_store_once(self):
        fills = []
        o_freeze = jd._freeze_store
        jd._freeze_store = lambda store, fsid=None: (fills.append(1), o_freeze(store, fsid))[1]
        try:
            tl1 = km.build_timeline(NOW, {}, with_bars=True)
            tl2 = km.build_timeline(NOW, {}, with_bars=True)
        finally:
            jd._freeze_store = o_freeze
        self.assertEqual(len(fills), len(SIDS), "one parse per store across two full builds")
        self.assertEqual(self._delta("miss"), len(SIDS))
        self.assertGreaterEqual(self._delta("hit"), len(SIDS), "the second build's loads are all hits")
        self.assertEqual(self._delta("poisoned"), 0, "the build wrote nothing into the shared views")
        self.assertEqual(sorted(l["id"] for l in tl1["sessions"]), sorted(SIDS))
        self.assertEqual(json.dumps(tl1["turns"]), json.dumps(tl2["turns"]), "same inputs, same frame")

    def test_the_store_a_wired_site_works_on_is_the_frozen_shared_view(self):
        seen, raised = [], []

        def spy(sid, caps, goals, seg_ends=None):
            seen.append(goals)
            for attempt in (lambda: goals["status"].__setitem__("x", "y"),
                            lambda: goals["nodes"][sid + ":g1"]["log"].append({"kind": "done"}),
                            lambda: goals["nodes"][sid + ":g1"].__setitem__("text", "edited")):
                try:
                    attempt()
                except jd.FrozenStoreError:
                    raised.append(1)
            return self.saved["_derive_judging_marks"](sid, caps, goals, seg_ends)
        km._derive_judging_marks = spy
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tl = km.build_timeline(NOW, {}, with_bars=True)
        self.assertEqual(len(seen), len(SIDS))
        # The FIRST lane holds the shared view and every nested write on it raises; the first raise switches
        # the cache off for the process, so the lanes after it are served private, mutable load_goals stores
        # (the fallback) — their writes land on a throwaway copy and nothing shared is touched.
        self.assertIsInstance(seen[0], jd.FrozenStore, "the wired site holds the shared view")
        self.assertEqual(len(raised), 3, "every nested write on the shared view raised")
        self.assertTrue(all(type(g) is dict for g in seen[1:]), "later lanes take the fallback (private stores)")
        self.assertEqual(len(tl["sessions"]), len(SIDS), "the frame still shipped, every lane in it")
        rows = [r for r in self._errors() if r["err"] == "frozen-store-write"]
        self.assertEqual(len(rows), 1, "one loud row, naming the writing site")
        self.assertIn(os.path.basename(__file__), rows[0]["note"])
        self.assertEqual(jd.shared_store_stats()["off"], 1, "the cache is off for the process")
        self.assertEqual(self._delta("poisoned"), 3)
        self.assertEqual(self._delta("fallback"), len(SIDS) - 1)
        self.assertNotIn("x", seen[0]["status"])         # nothing landed on the shared object
        self.assertEqual(seen[0]["nodes"][seen[0]["rompUuid"] + ":g1"]["text"], "Goal 0")
        self.assertEqual(seen[0]["nodes"][seen[0]["rompUuid"] + ":g1"]["log"], [])
        self.assertEqual(jd.load_goals(SIDS[1])["nodes"][SIDS[1] + ":g1"]["text"], "Goal 1",
                         "a write on a fallback store reached no file")
        # the board keeps rendering: the next build's loads take load_goals (private, mutable) and succeed
        km._derive_judging_marks = self.saved["_derive_judging_marks"]
        km.build_timeline(NOW, {}, with_bars=True)
        self.assertEqual(self._delta("fallback"), 2 * len(SIDS) - 1)

    def test_open_top_goal_reads_the_shared_view_and_answers_after_a_write_attempt(self):
        sid = SIDS[0]
        self.assertEqual(km._open_top_goal(sid), sid + ":g1")
        self.assertEqual(self._delta("miss"), 1)
        self.assertEqual(km._open_top_goal(sid), sid + ":g1")
        self.assertEqual(self._delta("hit"), 1)
        with self.assertRaises(jd.FrozenStoreError):
            jd.load_goals_shared(sid)["status"][sid + ":g1"] = "completed"
        self.assertEqual(km._open_top_goal(sid), sid + ":g1", "still answers with the cache off")
        self.assertEqual(self._delta("fallback"), 1)

    def test_the_compaction_sweep_evicts_a_removed_stores_entry(self):
        for sid in SIDS:
            jd.load_goals_shared(sid)
        self.assertEqual(jd.shared_store_stats()["entries"], len(SIDS))
        os.unlink(jd.GOALDIR / (SIDS[0] + ".json"))
        km._compact_goal_stores()
        self.assertEqual(jd.shared_store_stats()["entries"], len(SIDS) - 1)
        self.assertEqual(self._delta("evict"), 1)

    def test_the_compaction_sweep_evicts_the_entries_of_stores_no_discovered_session_owns(self):
        # The cache had no cap: a store's view stayed resident for the process once read, so a board's whole
        # history of stores sat in memory (review find, 2026-09-08). The sweep drops the entries of stores no
        # session in the discover set owns; a later read of one is a miss that refills it.
        for sid in SIDS:
            jd.load_goals_shared(sid)
        self.assertEqual(jd.shared_store_stats()["entries"], len(SIDS))
        km._compact_goal_stores()
        self.assertEqual(jd.shared_store_stats()["entries"], len(SIDS), "every store is owned: nothing evicted")
        self.assertEqual(self._delta("evict"), 0)
        self.discovered[:] = [SIDS[0]]                     # the other two sessions left the discover window
        km._compact_goal_stores()
        self.assertEqual(jd.shared_store_stats()["entries"], 1, "the unowned stores' entries are gone")
        self.assertEqual(self._delta("evict"), 2)
        miss0 = self._delta("miss")
        jd.load_goals_shared(SIDS[1])                      # read again: one miss refills it
        self.assertEqual(self._delta("miss"), miss0 + 1)
        self.assertEqual(jd.shared_store_stats()["entries"], 2)


class PushSurvivesOneFailedChatBuild(unittest.TestCase):
    """A chat build that raises used to abort the whole push (the cycle-level "push build:" catch returns
    before the feed and the timeline are built). One session's build now fails alone: its frame is skipped
    this cycle, the other sessions' frames and the timeline still go out, and stderr names it, ONCE per
    fault episode, with a dashboard bell row beside the stderr line, so the pane that stopped updating is
    not a silent degrade and a build that fails every cycle is not a traceback every cycle (review find,
    2026-09-08). The episode is the fault text: a repeat says nothing, a different fault is a new episode,
    and a build that succeeds ends it."""
    STUBS = ("NAMES", "_tmux_sessions", "_live_names", "_chat_tab_sessions", "build_session",
             "_cached_feed", "_cached_timeline", "build_timeline", "_fleet_view_sig", "_comments_frame",
             "_retry_parked_creates")
    A, B = SIDS[1], SIDS[2]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        names = Path(self.tmp) / "names"
        names.mkdir()
        (names / self.A).write_text("web\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        (names / self.B).write_text("api\t/proj/TESTHOST/app\t#1EA1EB\twhite\n")
        self.tx = {}
        for sid in (self.A, self.B):
            self.tx[sid] = Path(self.tmp) / (sid + ".jsonl")
            self.tx[sid].write_text('{"type": "user"}\n')
        self.saved = {nm: getattr(km, nm) for nm in self.STUBS}
        self.saved_state = (km.jd.STATE, dict(km._built_chat), dict(km._prev_chat_events),
                            dict(km._prev_chat_ledger), list(km._last_tab_order))
        km.NAMES = names
        km.jd.STATE = Path(self.tmp) / "state"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._tmux_sessions = lambda: {}
        km._live_names = lambda tm: {"web": self.A, "api": self.B}
        km._chat_tab_sessions = lambda now, tmux: [
            {"sid": sid, "name": nm, "path": str(self.tx[sid]), "anchor": sid}
            for sid, nm in ((self.A, "web"), (self.B, "api"))]
        km.build_session = self._build_session
        km._cached_feed = lambda now, tmux, sig, connect=False: {"working": [], "awaiting": [], "now": now}
        km._cached_timeline = lambda now, tmux, sig, connect=False: {"turns": {}, "judging": [], "messages": [],
                                                                      "now": now}
        km.build_timeline = lambda now, tmux, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, tmux: {"probe": 1}
        km._comments_frame = lambda sid, tmux: None
        km._retry_parked_creates = lambda: None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        self.saved_bell = list(km._SYNC_NOTICES)          # the dashboard bell ring the fault reaches
        del km._SYNC_NOTICES[:]
        km._chat_build_faults.clear()                     # no fault episode carried in from another test
        self.fail_with = "synthetic: this session's chat build fails"
        self.built = []
        self.chat_frames, self.tl_frames = [], []
        self.chat = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: self.chat_frames.append(json.loads(s))}
        self.tl = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: self.tl_frames.append(json.loads(s))}

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        st, bc, pe, pl, lo = self.saved_state
        km.jd.STATE = st
        km._built_chat.clear(); km._built_chat.update(bc)
        km._prev_chat_events.clear(); km._prev_chat_events.update(pe)
        km._prev_chat_ledger.clear(); km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo
        km._SYNC_NOTICES[:] = self.saved_bell

    def _build_session(self, sid, now, tmux):
        self.built.append(sid)
        if sid == self.A and self.fail_with:
            raise RuntimeError(self.fail_with)
        return {"type": "session", "id": sid, "name": "api", "events": [{"uuid": "e1", "type": "user"}],
                "ledger": None, "status": {"state": "waiting"}, "color": None}

    def test_the_other_sessions_frames_and_the_timeline_still_go_out(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._push([self.chat, self.tl])
        self.assertEqual(sorted(self.built), sorted([self.A, self.B]), "both builds were attempted")
        sessions = [f for f in self.chat_frames if f.get("type") == "session"]
        self.assertEqual([f["id"] for f in sessions], [self.B], "the surviving session's frame went out")
        self.assertIn("bars", [f["type"] for f in self.tl_frames], "the push went on to the timeline")
        self.assertIn("push build: chat %s" % self.A[:8], err.getvalue(), "stderr names the failed build")
        self.assertIn("synthetic: this session's chat build fails", err.getvalue())
        self.assertNotIn(self.A, km._built_chat, "no cache entry for the failed build")
        self.assertIn(self.B, km._built_chat)
        # ...and the dashboard hears it (review find, 2026-09-08): one bell row of the kind a state file that
        # cannot be read wears, naming the session and the fault, so the user is not left reading the kernel
        # log to learn why one pane stopped updating
        rows = list(km._SYNC_NOTICES)
        self.assertEqual(len(rows), 1, "one bell row for the fault: %r" % rows)
        self.assertEqual(rows[0]["kind"], "refused")
        self.assertFalse(rows[0]["ok"], "a fault, not a sync that landed")
        self.assertIn("web", rows[0]["text"], "the row names the session")
        self.assertIn("RuntimeError: " + self.fail_with, rows[0]["text"], "...and the fault")

    def test_a_build_that_keeps_failing_the_same_way_is_said_once_until_it_changes_or_succeeds(self):
        def push():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km._push([self.chat, self.tl])
            return err.getvalue().count("push build: chat %s" % self.A[:8])
        self.assertEqual(push(), 1, "the first cycle says it")
        self.assertEqual(push(), 0, "the second cycle, same fault: not again")
        self.assertEqual(push(), 0)
        self.assertEqual(len(km._SYNC_NOTICES), 1, "one episode, one bell row")
        self.fail_with = "synthetic: a different fault on the same session"
        self.assertEqual(push(), 1, "a different fault is a new episode")
        self.assertEqual(len(km._SYNC_NOTICES), 2)
        self.assertIn("a different fault", km._SYNC_NOTICES[-1]["text"])
        self.fail_with = ""                                   # the build succeeds: the episode is over
        self.assertEqual(push(), 0)
        self.assertNotIn(self.A, km._chat_build_faults, "a build that succeeds ends the fault episode")
        self.assertIn(self.A, km._built_chat, "…and its frame is cached like any other")
        self.fail_with = "synthetic: this session's chat build fails"
        km._built_chat.pop(self.A, None)                      # the input moved: the build runs again and fails again
        self.assertEqual(push(), 1, "the same fault after a success is a new episode, said anew")
        self.assertEqual(len(km._SYNC_NOTICES), 3)


if __name__ == "__main__":
    unittest.main()
