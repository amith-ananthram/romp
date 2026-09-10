"""The placed-launch memo behind the awaiting lift and the feed's background-task classification:
_bg_placed_tops answers {launch tool_use id: owning top} from the transcript segment holding the launch
and the goal store's placement for it.

The memo is keyed on OBJECT identity, not on a stat: _parse returns one object per transcript version and
jd.load_goals_shared one FrozenStore per store version, so (parse object, store object) IS the version pair,
and a per-sid map answers every launch id asked so far under that pair (the lift asks with every task id,
the feed with the live ids, often none: one map answers both, and the feed's empty ask never evicts the
lift's fill). Placements are read through a per-store index (_placement_index) that gives exactly what
jd._placement_of's scan gives, for the four suffixes the walk tried. A store that is not the shared cache's
FrozenStore (a writer's private copy, no file, the cache off) is computed on and never published. Entries
are evicted when a session with no live ids asks after its transcript was re-parsed (the pinned parse is
stale) and when its sid leaves the alive set. SYNTHETIC fixtures only: placeholder sids, invented goal text
and prompts."""
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
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_bgtops", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "77777777-1111-4222-8333-444444444401"   # private to this module (synthetic)
NOW = 1781100000
T0 = NOW - 3600
TOP_A, SUB_A, TOP_B = SID + ":gA", SID + ":gA1", SID + ":gB"


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _prompt(uuid, t, text):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": None,
            "message": {"role": "user", "content": text}}


def _dispatch(tid, t):
    """The assistant tool_use block that dispatched a background agent: the record a launch's segment
    is resolved from."""
    return {"type": "assistant", "timestamp": _iso(t), "uuid": "d" + tid, "parentUuid": None,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Agent",
                 "input": {"description": "an investigation", "prompt": "look", "run_in_background": True}}]}}


def _ack(tid, t):
    return {"type": "user", "timestamp": _iso(t), "uuid": "u" + tid, "parentUuid": None,
            "toolUseResult": {"status": "async_launched", "description": "an investigation"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                     "content": "launched"}]}}


def _drift(seg_id, t="999"):
    """The same segment as recorded by another consumer: a different middle t (see jd._seg_key)."""
    parts = seg_id.split(":")
    return parts[0] + ":" + t + ":" + parts[-1]


def _node(nid, parent=None):
    return {"id": nid, "text": "a goal", "parentId": parent, "nodeComplete": False, "blocked": False,
            "cleared": False, "trail": [], "t": T0, "mt": T0, "log": []}


class PlacedTops(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd._rebind_state(Path(self.td.name))          # clears the shared cache and lifts any earlier off switch
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.saved = {nm: getattr(km, nm) for nm in ("_alive_sessions", "_mark_views_dirty")}
        self.path = os.path.join(self.td.name, SID + ".jsonl")
        # two prompts, two segments: t1 dispatched under the first, t2 and t3 under the second
        self._transcript([_prompt("p1", T0, "start the first investigation"), _dispatch("t1", T0 + 10),
                          _ack("t1", T0 + 11),
                          _prompt("p2", T0 + 100, "and now the second one"), _dispatch("t2", T0 + 110),
                          _ack("t2", T0 + 111), _dispatch("t3", T0 + 120), _ack("t3", T0 + 121)])
        km._task_seg_cache.clear(); km._BG_TOPS_CACHE.clear(); km._PLACEMENT_IDX.clear()
        ps = km._parse(self.path, SID, NOW)
        segs = km._seg_of_tool_uses(ps, {}, ["t1", "t2", "t3"])
        self.assertEqual(set(segs), {"t1", "t2", "t3"}, "precondition: every dispatch resolves to a segment")
        self.seg1, self.seg2 = segs["t1"], segs["t2"]
        self.assertEqual(segs["t3"], self.seg2, "t2 and t3 share the second segment")
        km._task_seg_cache.clear()                    # the precondition's walk must not warm the test
        # placements recorded under DRIFTED ids (another consumer's parse of the same segments)
        self._save({_drift(self.seg1): SUB_A, _drift(self.seg2): TOP_B})
        self.s0 = self._stats()

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        km._task_seg_cache.clear(); km._BG_TOPS_CACHE.clear(); km._PLACEMENT_IDX.clear()
        jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _transcript(self, recs):
        """Records chained through parentUuid: the parse walks the transcript graph from its leaf."""
        for prev, rec in zip(recs, recs[1:]):
            rec["parentUuid"] = prev["uuid"]
        with open(self.path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")

    def _save(self, placements, extra_nodes=()):
        nodes = {TOP_A: _node(TOP_A), SUB_A: _node(SUB_A, TOP_A), TOP_B: _node(TOP_B)}
        for nd in extra_nodes:
            nodes[nd["id"]] = nd
        jd.save_goals(SID, {"rompUuid": SID, "seq": 1, "placementsV": jd.PLACEMENTS_V, "nodes": nodes,
                            "placements": dict(placements), "status": {}})

    def _stats(self):
        return {"bg": km._bg_tops_report(), "sh": jd.shared_store_stats(), "io": jd.goal_io_stats()}

    def _delta(self, block, key):
        return self._stats()[block][key] - self.s0[block][key]

    def _spy(self, name):
        """Count the calls of jd.<name> for the rest of the test; the real function still runs."""
        calls, real = [], getattr(jd, name)
        setattr(jd, name, lambda fsid: (calls.append(fsid), real(fsid))[1])
        self.addCleanup(setattr, jd, name, real)
        return calls

    # ---- one map for the lift's all-ids ask and the feed's live-ids ask ----
    def test_the_lifts_all_ids_and_the_feeds_live_ids_share_one_read_and_one_walk(self):
        writer = self._spy("load_goals")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2", "t3"]),
                         {"t1": TOP_A, "t2": TOP_B, "t3": TOP_B}, "sub-goal placement resolves to its top")
        self.assertEqual((self._delta("bg", "miss"), self._delta("bg", "walk"), self._delta("bg", "idx_build"),
                          self._delta("bg", "resolve")), (1, 1, 1, 3))
        self.assertEqual(self._delta("sh", "miss"), 1, "the store parsed once, into the shared cache")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t2"]), {"t2": TOP_B}, "the feed's live subset")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2", "t3"]), {"t1": TOP_A, "t2": TOP_B, "t3": TOP_B})
        self.assertEqual((self._delta("bg", "hit"), self._delta("bg", "walk"), self._delta("bg", "idx_build")), (2, 1, 1),
                         "both later asks were answered from the map: no second walk, no second index")
        self.assertEqual(self._delta("sh", "hit"), 2, "...and the two shared reads were cache hits")
        self.assertEqual(writer, [], "the writer's loader was never asked")
        self.assertEqual(self._delta("io", "loads"), 0, "no writer-style load anywhere")
        self.assertEqual(km._bg_tops_report()["entries"], 1)
        self.assertIsInstance(km._BG_TOPS_CACHE[SID][1], jd.FrozenStore, "the entry is keyed on the shared view")

    def test_the_feeds_live_ids_ask_then_the_lifts_all_ids_ask_fill_one_map_incrementally(self):
        # the other order: the feed asks first with the live ids, then the lift with every id the
        # transcript records. The lift's ask resolves only the ids the map does not hold yet, under
        # the same (parse, store) pair, and the feed's next ask is a hit on the widened map
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t2"]), {"t2": TOP_B})
        self.assertEqual((self._delta("bg", "miss"), self._delta("bg", "resolve"), self._delta("bg", "walk")), (1, 1, 1))
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2", "t3"]), {"t1": TOP_A, "t2": TOP_B, "t3": TOP_B})
        self.assertEqual((self._delta("bg", "miss"), self._delta("bg", "resolve"), self._delta("bg", "walk")), (2, 3, 2),
                         "only the two ids the map lacked were resolved, with one walk for them")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t2"]), {"t2": TOP_B})
        self.assertEqual(self._delta("bg", "hit"), 1, "the feed's next ask is answered from the widened map")
        self.assertEqual(km._bg_tops_report()["entries"], 1)

    def test_a_fill_publishes_a_new_tuple_and_never_writes_into_the_old_map(self):
        # the pusher and the handler threads both run this: a reader holding the entry it read keeps a
        # consistent (parse, store, map), so a fill that widens the map copies it and publishes a new
        # tuple under the same pair, and the old tuple is left as its holder read it
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        ent1 = km._BG_TOPS_CACHE[SID]
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2"]), {"t1": TOP_A, "t2": TOP_B})
        ent2 = km._BG_TOPS_CACHE[SID]
        self.assertIsNot(ent2, ent1, "a fill publishes a new tuple")
        self.assertIsNot(ent2[2], ent1[2], "...holding a new map")
        self.assertEqual(set(ent1[2]), {"t1"}, "the old map is as its holder read it")
        self.assertEqual(set(ent2[2]), {"t1", "t2"})
        self.assertTrue(ent2[0] is ent1[0] and ent2[1] is ent1[1], "the same (parse, store) pair")

    def test_an_unresolvable_launch_is_absent_and_the_walk_is_counted_negative(self):
        out = km._bg_placed_tops(SID, self.path, ["t1", "t-never-dispatched"])
        self.assertEqual(out, {"t1": TOP_A}, "a launch no segment holds is not in the answer (awaited-conservative)")
        self.assertEqual((self._delta("bg", "walk"), self._delta("bg", "walk_neg")), (1, 1))
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t-never-dispatched"]), {"t1": TOP_A})
        self.assertEqual((self._delta("bg", "hit"), self._delta("bg", "walk")), (1, 1),
                         "the None answer is in the map under this (parse, store): no walk repeats it")

    # ---- the index equals the four-suffix scan ----
    def test_the_indexed_lookup_equals_the_four_suffix_scan(self):
        placements = {"u:100:h1": "n1",                    # plain
                      "u:200:h2#live": "n2",               # a live-twin key
                      "u:300:h3#p": None,                  # a RETIRED planned placement (None-valued)
                      "u:301:h3#d": "n3",                  # ...and its decision-phase twin
                      "u:400:h4": None, "u:401:h4": "n4",  # two drifted ids for one segment: first wins
                      "odd-key": "n5",                     # a non-conforming id passes through _seg_key
                      "u:500:h6#d": "n6"}
        idx = km._placement_index(placements)
        probes = ["u:100:h1", "u:150:h1", "u:200:h2", "u:250:h2", "u:300:h3", "u:350:h3", "u:400:h4",
                  "u:401:h4",                           # the exact key that is NOT first in seg-key order
                  "u:402:h4", "odd-key", "u:500:h6", "u:600:none", "u:300:h3#p", "u:301:h3#d"]
        for seg in probes:
            for suf in ("", "#live", "#p", "#d"):
                self.assertEqual(km._placed_via_index(placements, idx, seg + suf),
                                 jd._placement_of(placements, seg + suf), "%s%s: the scan's own answer" % (seg, suf))
            scan = next((v for v in (jd._placement_of(placements, seg + suf)
                                     for suf in ("", "#live", "#p", "#d")) if v), None)
            via = None
            for suf in ("", "#live", "#p", "#d"):
                v = km._placed_via_index(placements, idx, seg + suf)
                if v:
                    via = v
                    break
            self.assertEqual(via, scan, "%s: the first truthy suffix match, as the walk picked it" % seg)
        self.assertEqual(idx["u:h4"], None, "setdefault: the first drifted key's value stands, None included")
        self.assertEqual(jd._placement_of(placements, "u:402:h4"), None, "...exactly as the scan answers")

    # ---- versions: publishes, journal appends, a new parse ----
    def test_a_save_goals_publish_moves_the_version_and_rebuilds_the_index_once(self):
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self._save({_drift(self.seg1): TOP_B, _drift(self.seg2): TOP_B})   # the closer re-places t1's segment
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_B}, "the new version's placement")
        self.assertEqual((self._delta("bg", "miss"), self._delta("bg", "idx_build")), (2, 2))
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_B})
        self.assertEqual((self._delta("bg", "hit"), self._delta("bg", "idx_build")), (1, 2), "one index per version")
        self.assertEqual(km._bg_tops_report()["entries"], 1, "one entry per sid, the old version's replaced")

    def test_a_journal_append_moves_the_version(self):
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        jd.append_override(SID, TOP_B, "resolve", NOW)     # a user click: the journal grew, the store did not
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self.assertEqual(self._delta("bg", "miss"), 2, "a new store object: recomputed, same answer")
        self.assertEqual(self._delta("bg", "walk"), 1, "the launch's segment is a positive: no second walk")

    def test_a_new_parse_object_busts_the_map(self):
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self.assertEqual(self._delta("bg", "hit"), 1)
        with open(self.path, "a") as f:                    # the transcript grew: _parse returns a new object
            f.write(json.dumps(_prompt("p3", T0 + 200, "one more thing")) + "\n")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self.assertEqual(self._delta("bg", "miss"), 2, "the parse moved: recomputed under the new pair")
        self.assertEqual(self._delta("bg", "idx_build"), 1, "...on the same store object: the index stands")
        self.assertEqual(km._bg_tops_report()["entries"], 1)

    # ---- the two races a stat key loses: identity, not a stat ----
    def test_a_publish_between_a_read_and_the_ask_is_served_for_the_object_in_hand_then_fresh(self):
        """A stat key taken after the read would file, for a publish landing between the two, the new
        version's identity with the old placements, served until the store next moves (and the lift's
        time window then claims another card's launch as its own). Keyed on the object, the answer is
        the object's, and the next real read is a new object."""
        f1 = jd.load_goals_shared(SID)                     # V1: t1's segment under SUB_A (top TOP_A)
        self._save({_drift(self.seg1): TOP_B, _drift(self.seg2): TOP_B})   # V2 published under it
        saved = jd.load_goals_shared
        jd.load_goals_shared = lambda fsid: f1              # a reader still holding V1 asks
        try:
            self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A}, "V1's answer, for V1")
        finally:
            jd.load_goals_shared = saved
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_B},
                         "the real read is V2, a new object: V2's placement, not V1's memo")
        self.assertEqual(self._delta("bg", "miss"), 2)

    def test_a_same_identity_rewrite_is_served_fresh(self):
        """A rewrite that keeps the inode, the byte count AND the mtime_ns (a compare_miss in the shared
        cache) still moves the object: the new placement is served, where a stat key would have held."""
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        gp = jd.GOALDIR / (SID + ".json")
        old = gp.stat()
        doc = json.loads(gp.read_text())
        doc["placements"] = {_drift(self.seg1): TOP_B, _drift(self.seg2): TOP_B}   # same key lengths, other tops
        data = json.dumps(doc, separators=(",", ":")).encode()
        pad = old.st_size - len(data)
        self.assertGreaterEqual(pad, 0)
        gp.write_bytes(data + b" " * pad)               # same inode, same byte count
        os.utime(gp, ns=(old.st_atime_ns, old.st_mtime_ns))
        new = gp.stat()
        self.assertEqual((new.st_ino, new.st_mtime_ns, new.st_size), (old.st_ino, old.st_mtime_ns, old.st_size))
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_B})
        self.assertEqual(self._delta("sh", "compare_miss"), 1, "the shared cache saw the bytes move")

    # ---- what is never published ----
    def test_a_writer_copy_is_computed_on_and_never_published(self):
        w = jd.load_goals(SID)
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2"], store=w), {"t1": TOP_A, "t2": TOP_B})
        self.assertEqual(km._bg_tops_report()["entries"], 0, "a private copy is not a version: no entry")
        self.assertNotIn(SID, km._PLACEMENT_IDX, "...and no index keyed on it")
        self.assertEqual(self._delta("bg", "idx_build"), 1, "the index was built for the call")
        self.assertEqual(self._delta("sh", "miss") + self._delta("sh", "hit"), 0, "the caller's store: no shared read")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {"t1": TOP_A})
        self.assertEqual(km._bg_tops_report()["entries"], 1, "the shared view's answer is published")
        self.assertEqual(self._delta("bg", "walk"), 1, "the positives learned on the writer copy served the shared read")

    def test_no_store_file_answers_nothing_and_publishes_nothing(self):
        # a session with live tasks and no goal store yet (a new session, every render): one presence
        # check, and neither a parse nor a load of either kind (the shared loader hands an absent file to
        # load_goals, so reaching it would cost a fresh-store build per call)
        os.unlink(jd.GOALDIR / (SID + ".json"))
        km._parse_cache.pop(self.path, None)
        writer, shared = self._spy("load_goals"), self._spy("load_goals_shared")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1"]), {})
        self.assertEqual(km._bg_tops_report()["entries"], 0)
        self.assertEqual((writer, shared, self._delta("sh", "absent")), ([], [], 0), "no load of any kind")
        self.assertNotIn(self.path, km._parse_cache, "no parse either")
        self.assertEqual(self._delta("bg", "miss"), 0)

    # ---- eviction ----
    def test_an_empty_live_set_keeps_the_lifts_fill_while_the_parse_is_current(self):
        # the lift asks with every task id; the feed's classification asks with the LIVE ids, none here
        # (every task returned): that ask must not evict what the lift filled under the same parse
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2", "t3"]), {"t1": TOP_A, "t2": TOP_B, "t3": TOP_B})
        self.assertEqual((self._delta("bg", "idx_build"), self._delta("bg", "walk")), (1, 1))
        saved = km._tmux_sessions
        km._tmux_sessions = lambda: {SID: {"state": "idle", "bgTasks": []}}   # live, no live tasks
        try:
            self.assertEqual(km._awaiting_task_descs(SID, self.path), [])
        finally:
            km._tmux_sessions = saved
        self.assertEqual(km._bg_tops_report()["entries"], 1, "the empty ask kept the fill: the parse is current")
        self.assertEqual(km._bg_placed_tops(SID, self.path, ["t1", "t2", "t3"]), {"t1": TOP_A, "t2": TOP_B, "t3": TOP_B})
        self.assertEqual((self._delta("bg", "hit"), self._delta("bg", "idx_build"), self._delta("bg", "walk")), (1, 1, 1),
                         "the lift's next ask is a hit: no index rebuilt, no transcript re-walked")

    def test_empty_tids_evict_the_sessions_entries_once_the_pinned_parse_is_stale(self):
        km._bg_placed_tops(SID, self.path, ["t1"])
        self.assertEqual((km._bg_tops_report()["entries"], SID in km._PLACEMENT_IDX), (1, True))
        self.assertEqual(km._bg_placed_tops(SID, self.path, []), {})
        self.assertEqual(km._bg_tops_report()["entries"], 1, "the pinned parse is the current one: kept")
        with open(self.path, "a") as f:                    # the transcript grew and a build re-parsed it
            f.write(json.dumps(_prompt("p3", T0 + 200, "one more thing")) + "\n")
        km._parse(self.path, SID, NOW)
        self.assertEqual(km._bg_placed_tops(SID, self.path, []), {})
        self.assertEqual((km._bg_tops_report()["entries"], SID in km._PLACEMENT_IDX), (0, False),
                         "no live launches and a stale pinned parse: the parse and index are released")

    def test_the_lifts_end_of_tick_prune_evicts_a_sid_that_left_the_alive_set(self):
        km._bg_placed_tops(SID, self.path, ["t1"])
        self.assertEqual(km._bg_tops_report()["entries"], 1)
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": self.path}]
        km._mark_views_dirty = lambda *a, **k: None
        km._lift_spent_awaiting(NOW, {})                   # alive (dormant here): the entry stays
        self.assertEqual(km._bg_tops_report()["entries"], 1)
        km._alive_sessions = lambda now, tmux: []
        km._lift_spent_awaiting(NOW, {})
        self.assertEqual((km._bg_tops_report()["entries"], SID in km._PLACEMENT_IDX), (0, False))

    # ---- /perf ----
    def test_perf_reports_the_memo(self):
        km._bg_placed_tops(SID, self.path, ["t1"])
        km._bg_placed_tops(SID, self.path, ["t1"])
        rep = km._PERF_STATS.snapshot()["memos"]["bgTops"]
        self.assertEqual(set(rep), {"hit", "miss", "resolve", "walk", "walk_neg", "idx_build", "entries"})
        self.assertEqual(rep["entries"], 1)
        for k in ("hit", "miss", "resolve", "walk", "idx_build"):
            self.assertEqual(rep[k], km._bg_tops_stats[k], k)
        self.assertGreaterEqual(rep["hit"], 1)


if __name__ == "__main__":
    unittest.main()
