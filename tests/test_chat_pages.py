#!/usr/bin/env python3
"""T323 stage 4b (2026-09-11): the chat build's RENDER FLOOR and PAGE renderer. A restored parse's pre-cut turns are
lazy (stage 4a); the chat build renders from the turn holding the assembly cut (the floor) and the older history is
rendered on demand, a page of turns at a time, for a proto-2 client's loadOlder / loadAround / loadNewer. Pinned here:
the pages from turn 0 to the floor, concatenated with the floor'd list, equal the WHOLE build's events byte for byte,
at every page size (so at every page boundary), with notes interleaved between the turns; a page hydrates its own
turns only and the floor'd build hydrates the tail's; the floor drops to 0 while a proto-1 client is connected and
climbs back when it leaves (the fold entry rebuilt, the prefix released); every event's uuid is unique within a
list. Synthetic transcripts only (the stage 4a served fixture's builder and the golden compaction scenarios)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_t323s4b", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em
sys.path.insert(0, HERE)
import test_event_model_golden as G                                  # noqa: E402  the synthetic scenario builders
from test_asm_checkpoint_served import transcript                    # noqa: E402  the 4a served fixture's transcript builder

SID = "aaaaaaaa-4444-4222-8333-444444444444"
NOW = 1781200000


def _strip(events):
    return json.loads(json.dumps(events, default=lambda o: "<unserializable>"))


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved_state = jd.STATE
        jd._rebind_state(self.td / "state")
        for d in ("states", "goals", "sdk", "checkpoints"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        self.proj = self.td / "proj"; self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.rows = [{"sid": SID, "name": "web", "path": self.leaf, "mtime": NOW, "anchor": SID}]
        self.saved = (km._sessions, km._tmux_sessions)
        km._sessions = lambda now, **kw: list(self.rows)
        km._tmux_sessions = lambda: {}
        self.fresh()

    def tearDown(self):
        km._sessions, km._tmux_sessions = self.saved
        km._live_scope.chat_floor0 = None
        em.set_checkpoint_dir(None)
        jd._rebind_state(self.saved_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        em._ASM_CKPT_STATS.update(written=0, restored=0, fallbacks={}, skipped={}, hydratedBytes=0, hydratedAtoms=0, hydratedBy={})
        with km._chat_fold_lock:
            km._chat_fold.clear()
        for name in ("_PARSE_CACHE",):                                    # the one parse store (stage 2): a restart empties it
            getattr(jd, name).clear()
        km._parse_mode.clear()
        km._built_chat.clear() if hasattr(km, "_built_chat") else None
        km._prev_chat_events.clear()
        km._RENDER_FLOOR.clear()
        with km._page_lock:
            km._PAGE_CACHE.clear(); km._PAGE_STATS.update(hits=0, misses=0, evictions=0, pages=0, bytes=0, renderMs=0.0)
        km._live_scope.chat_floor0 = None

    def write(self, recs):
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))

    def whole(self):
        """The fully hydrated build (a proto-1 client's), from a fresh process with no document."""
        self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            m = km.build_session(SID, NOW, {}, floor=0)
        finally:
            em._CKPT_DIR_FN = saved
        self.head_cards = _strip(m.get("headCards") or [])
        return _strip(m["events"])[len(self.head_cards):]                # the transcript's events: the head cards ride top-level

    def document(self):
        self.fresh()
        km.build_session(SID, NOW, {}, floor=0)                          # a whole parse, then its document
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID), em.asm_checkpoint_stats())

    def restored(self):
        """A fresh process with the document: the floor'd build (the pusher's, no proto-1 client)."""
        self.fresh(); modes = []
        km._live_scope.chat_floor0 = False
        try:
            m = km.build_session(SID, NOW, {})
        finally:
            km._live_scope.chat_floor0 = None
        return m

    def pages(self, floor, size):
        out = []
        for lo in range(0, floor, size):
            out += km._chat_history_page(SID, lo, min(lo + size, floor), NOW)
        return _strip(out)


class PagesEqualTheWhole(Harness):
    def test_pages_and_the_floored_list_equal_the_whole_build_at_every_page_size(self):
        recs = transcript(NOW - 86400, turns=120, compact_every=25)
        self.write(recs)
        whole = self.whole()
        self.assertGreater(len(whole), 200)
        self.document()
        m = self.restored()
        floor = m["floor"]
        self.assertGreater(floor, 0, "a restored parse renders from the cut")
        tail = _strip(m["events"])
        self.assertLess(len(tail), len(whole))
        self.assertEqual(tail, whole[len(whole) - len(tail):], "the floor'd list is the whole build's tail")
        for size in (1, 3, 7, 16, 64):
            with self.subTest(page_turns=size):
                self.assertEqual(self.pages(floor, size) + tail, whole, "pages of %d turns plus the tail equal the whole" % size)
        self.assertGreater(km._PAGE_STATS["misses"], 0)

    def test_a_page_hydrates_its_own_turns_and_the_floored_build_the_tails(self):
        recs = transcript(NOW - 86400, turns=120, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedAtoms"], 0, "the floor'd build reads no pre-cut body: %s" % st["hydratedBy"])
        floor = m["floor"]
        page = km._chat_history_page(SID, 0, 5, NOW)
        st = em.asm_checkpoint_stats()
        atoms_in = sum(len(t["atoms"]) for t in em.parse_session(self.leaf, rompuuid=SID, candidate_files=[self.leaf], states=None, postal_log=[], now=NOW)["turns"][0:5])
        self.assertGreater(st["hydratedAtoms"], 0)
        self.assertLessEqual(st["hydratedAtoms"], atoms_in + km._PAGE_FILL_TURNS * 4, "the page's turns and its fill turns, no more: %s" % st["hydratedBy"])
        self.assertLessEqual(set(st["hydratedBy"]), {"build_session", "_atom_md"}, "the page's reshape and its own text set: %s" % st["hydratedBy"])
        self.assertGreater(len(page), 0)
        self.assertLess(floor, len(em.parse_session(self.leaf, rompuuid=SID, candidate_files=[self.leaf], states=None, postal_log=[], now=NOW)["turns"]))

    def test_every_golden_compaction_scenario_pages_equal_its_whole(self):
        for name in ("compaction_atom", "compaction_broken_stitch", "manual_compact_detached"):
            with self.subTest(scenario=name):
                records, _ = G.SINGLE_FILE[name]
                self.write(records())
                whole = self.whole()
                self.document()
                m = self.restored()
                floor = m["floor"]
                tail = _strip(m["events"])
                for size in (1, 2, 16):
                    self.assertEqual(self.pages(floor, size) + tail, whole, "%s at %d turns per page" % (name, size))


class UniqueUuids(Harness):
    def test_every_event_carries_a_uuid_unique_within_the_list(self):
        recs = transcript(NOW - 86400, turns=60, compact_every=25)
        self.write(recs)
        whole = self.whole()
        uuids = [e.get("uuid") for e in whole]
        self.assertTrue(all(uuids), "every event carries a uuid")
        self.assertEqual(len(uuids), len(set(uuids)), "unique within the list")
        self.document(); m = self.restored()
        got = self.pages(m["floor"], 16) + _strip(m["events"])
        uu = [e.get("uuid") for e in got]
        self.assertEqual(len(uu), len(set(uu)))


class RenderFloor(Harness):
    def test_the_floor_outlives_the_hydration_of_every_pre_cut_atom(self):
        """The judges' first pass over a fresh store hydrates every pre-cut atom (their unit text); the lazy markers go
        with the bodies. The floor is the parse's cutTurn, recorded before that, so the next build still renders from
        the cut (a scan of the markers would have found none and dropped to turn 0: every proto-2 client re-based)."""
        recs = transcript(NOW - 86400, turns=120, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()
        cut = m["floor"]; self.assertGreater(cut, 0)
        tree = em.parse_session(self.leaf, rompuuid=SID, candidate_files=[self.leaf], states=None, postal_log=[], now=NOW)
        self.assertEqual(tree.get("cutTurn"), cut)
        em.hydrate(tree, SID)                                             # what the judges' first pass does
        self.assertEqual(sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None), 0, "no marker left")
        km._live_scope.chat_floor0 = False
        try:
            m2 = km.build_session(SID, NOW + 1, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(m2["floor"], cut, "the floor stands after the hydration")
        self.assertEqual([e["uuid"] for e in m2["events"]], [e["uuid"] for e in m["events"]])


    def test_the_floor_drops_while_a_proto1_client_is_connected_and_climbs_back_when_it_leaves(self):
        recs = transcript(NOW - 86400, turns=90, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()
        cut = m["floor"]; self.assertGreater(cut, 0)
        km._live_scope.chat_floor0 = True                                  # a proto-1 client is connected
        try:
            m0 = km.build_session(SID, NOW, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(m0["floor"], 0, "the whole transcript for the index client")
        self.assertGreater(len(m0["events"]), len(m["events"]))
        self.assertEqual(km._RENDER_FLOOR[SID], 0)
        self.assertEqual(km._chat_fold_last_info().get("why"), "floor", "the fold demoted on the floor moving")
        km._live_scope.chat_floor0 = False                                 # it left
        try:
            m1 = km.build_session(SID, NOW, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(m1["floor"], cut, "the floor climbs back at the next build")
        self.assertEqual(_strip(m1["events"]), _strip(m["events"]))
        self.assertEqual(km._chat_fold_last_info().get("why"), "floor")
        fe = km._chat_fold_get(SID)
        self.assertIsNotNone(fe)
        self.assertEqual(fe["rf"], cut, "the fold entry holds the prefix from the floor: the turn-0 prefix is released")
        self.assertLess(len(fe["events"]), len(m0["events"]))


def _client(proto=2):
    sent = []
    return {"send": lambda s: sent.append(json.loads(s)), "sent": {}, "proto": proto, "echat": {}}, sent


class Proto2Wire(Harness):
    """The uuid-anchored send path and the history requests, over a restored session."""

    def _restored_tail(self):
        recs = transcript(NOW - 86400, turns=200, compact_every=25)     # ~400 events, the cut near turn 175
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        return whole, m

    def test_a_first_send_is_the_tail_with_head_unknown_and_a_later_send_a_uuid_anchored_delta(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        self.assertEqual(sent[-1]["type"], "session")
        f = sent[-1]
        self.assertEqual((f["proto"], f["headKnown"], f["headTotal"]), (2, False, None), "the head is not reached: no count")
        self.assertNotIn("headFrom", f)
        self.assertEqual(f["events"], m["events"][-km.WIRE_TAIL:])
        self.assertEqual((f["firstUuid"], f["lastUuid"]), (f["events"][0]["uuid"], f["events"][-1]["uuid"]))
        self.assertEqual(c["echat"][SID], {"first": f["firstUuid"], "last": f["lastUuid"], "detached": False})
        # an append: the list grows by two events, the diff finds the old length
        m2 = dict(m); m2["events"] = list(m["events"]) + [{"kind": "user", "md": "more", "uuid": "u_new"}, {"kind": "assistant", "md": "ok", "uuid": "a_new"}]
        km._send_chat_locked(c, m2, None, len(m["events"]), False)
        d = sent[-1]
        self.assertEqual(d["type"], "chatTail")
        self.assertEqual((d["afterUuid"], [e["uuid"] for e in d["events"]]), (f["lastUuid"], ["u_new", "a_new"]))
        self.assertEqual(c["echat"][SID]["last"], "a_new")
        # a change inside the held window: from the event two before the end
        m3 = dict(m2); evs3 = list(m2["events"]); evs3[-2] = dict(evs3[-2], md="edited"); m3["events"] = evs3
        km._send_chat_locked(c, m3, None, len(evs3) - 2, False)
        d = sent[-1]
        self.assertEqual((d["type"], d["afterUuid"], len(d["events"])), ("chatTail", evs3[-3]["uuid"], 2))
        # a change AT the client's first resident event (the floor'd list fits the tail whole: index 0): a full frame again
        km._send_chat_locked(c, m3, None, 0, False)
        self.assertEqual(sent[-1]["type"], "session")
        self.assertEqual(sent[-1]["firstUuid"], evs3[0]["uuid"])
        # a fork: the held uuids are gone from the new list
        m4 = dict(m); m4["events"] = [{"kind": "user", "md": "x", "uuid": "z1"}, {"kind": "assistant", "md": "y", "uuid": "z2"}]
        km._send_chat_locked(c, m4, None, 0, False)
        self.assertEqual((sent[-1]["type"], sent[-1]["headKnown"], sent[-1]["headTotal"]), ("session", False, None))

    def test_a_client_holding_pages_before_the_floor_still_gets_deltas(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        c["echat"][SID] = {"first": whole[3]["uuid"], "last": m["events"][-1]["uuid"], "detached": False}   # pages walked to the head
        m2 = dict(m); m2["events"] = list(m["events"]) + [{"kind": "user", "md": "more", "uuid": "u_new2"}]
        km._send_chat_locked(c, m2, None, len(m["events"]), False)
        self.assertEqual((sent[-1]["type"], sent[-1]["afterUuid"], [e["uuid"] for e in sent[-1]["events"]]),
                         ("chatTail", m["events"][-1]["uuid"], ["u_new2"]), "a run that begins before the floor'd list is caught up from its last")

    def test_a_trailing_overlay_card_that_vanishes_does_not_break_the_base(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        m1 = dict(m); m1["events"] = list(m["events"]) + [{"kind": "apiError", "uuid": "apiError", "text": "x", "status": 500}]
        km._send_chat_locked(c, m1, None, 0, False)
        self.assertEqual(c["echat"][SID]["last"], m["events"][-1]["uuid"], "the base ends on the last transcript event, not the notice")
        km._send_chat_locked(c, m1, None, len(m1["events"]), False)                  # nothing changed: an empty suffix
        self.assertEqual((sent[-1]["type"], sent[-1]["events"]), ("chatTail", []))
        m2 = dict(m); m2["events"] = list(m["events"]) + [{"kind": "user", "md": "next", "uuid": "u_next"}]   # the notice gone, a record appended
        km._send_chat_locked(c, m2, None, len(m["events"]), False)
        d = sent[-1]
        self.assertEqual((d["type"], d["afterUuid"], [e["uuid"] for e in d["events"]]), ("chatTail", m["events"][-1]["uuid"], ["u_next"]))

    def test_a_detached_client_gets_no_delta_and_a_fresh_base_reattaches(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        c["echat"][SID] = {"first": whole[10]["uuid"], "last": whole[30]["uuid"], "detached": True}
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent, [], "a detached client is sent nothing")
        c["echat"].pop(SID)                                              # needFull's reset, or a reconnect's ready
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent[-1]["type"], "session")
        self.assertFalse(c["echat"][SID]["detached"])

    def test_a_proto1_client_keeps_the_index_frames(self):
        whole, m = self._restored_tail()
        c, sent = _client(proto=1)
        km._live_scope.chat_floor0 = True
        try:
            m0 = km.build_session(SID, NOW, {})
        finally:
            km._live_scope.chat_floor0 = None
        km._send_chat_locked(c, m0, None, 0, False)
        f = sent[-1]
        self.assertEqual(f["type"], "session"); self.assertNotIn("proto", f)
        self.assertGreater(len(m0["events"]), km.WIRE_TAIL, "the whole list is longer than the wire tail")
        self.assertEqual((f["headFrom"], f["headTotal"]), (len(m0["events"]) - km.WIRE_TAIL, len(m0["events"])))
        self.assertIsInstance(c["echat"][SID], tuple)

    def test_load_older_by_uuid_walks_to_the_head_and_the_pages_equal_the_whole(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        resident = list(sent[-1]["events"])
        oldest = resident[0]["uuid"]
        steps = 0
        while True:
            r = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": oldest}, NOW)
            self.assertEqual((r["type"], r["beforeUuid"]), ("chatHead", oldest))
            self.assertNotIn("missing", r)
            resident = r["events"] + resident
            steps += 1
            if not r["more"]:
                break
            oldest = resident[0]["uuid"]
            self.assertLess(steps, 50)
        self.assertEqual(_strip(resident), whole, "the pages walked back to the head concatenate to the whole build")
        self.assertGreaterEqual(steps, 2)

    def test_load_around_lands_a_deep_anchor_in_one_reply_and_load_newer_walks_back_to_the_tail(self):
        whole, m = self._restored_tail()
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        anchor = whole[7]["uuid"]                                        # deep in the pre-cut history
        self.assertNotIn(anchor, {e["uuid"] for e in sent[-1]["events"]})
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW)
        self.assertEqual((r["type"], r["anchor"]), ("chatWindow", anchor))
        uu = [e["uuid"] for e in r["events"]]
        self.assertIn(anchor, uu)
        self.assertEqual(r["moreBefore"], False, "the window reached the head")
        self.assertTrue(r["moreAfter"], "and not the tail: the client is detached")
        base = r["_base"]; self.assertTrue(base["detached"])
        c["echat"][SID] = base
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent[-1]["type"], "session", "no delta reached the detached client")
        sent.clear()
        # scroll forward through loadNewer until the tail
        newest = r["events"][-1]["uuid"]; held = list(r["events"]); steps = 0
        while True:
            n = km._chat_history_reply(SID, {"type": "loadNewer", "id": SID, "after": newest}, NOW)
            self.assertEqual((n["type"], n["afterUuid"]), ("chatMore", newest))
            held = held + n["events"]; steps += 1
            if not n["more"]:
                break
            newest = held[-1]["uuid"]
            self.assertLess(steps, 50)
        self.assertEqual(_strip(held), whole, "the window walked forward to the tail equals the whole")
        self.assertFalse(n["_base"]["detached"], "re-attached at the tail")
        # a missing anchor answers honestly
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": "no-such-uuid"}, NOW)
        self.assertTrue(r.get("missing")); self.assertEqual(r["events"], [])

    def test_the_pages_cache_counts_and_bounds(self):
        whole, m = self._restored_tail()
        floor = m["floor"]
        km._chat_history_page(SID, 0, min(16, floor), NOW)
        km._chat_history_page(SID, 0, min(16, floor), NOW)
        st = km._PAGE_STATS
        self.assertEqual((st["misses"], st["hits"]), (1, 1))
        self.assertGreater(st["bytes"], 0); self.assertEqual(st["pages"], 1)
        saved = km._PAGE_CACHE_MAX
        km._PAGE_CACHE_MAX = 2
        try:
            for lo in range(0, min(floor, 48), 16):
                km._chat_history_page(SID, lo, min(lo + 16, floor), NOW)
            self.assertLessEqual(km._PAGE_STATS["pages"], 2)
            self.assertGreaterEqual(km._PAGE_STATS["evictions"], 1)
        finally:
            km._PAGE_CACHE_MAX = saved


if __name__ == "__main__":
    unittest.main()
