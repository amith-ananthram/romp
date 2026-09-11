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


def _last_uuid(recs):
    return next((r["uuid"] for r in reversed(recs) if r.get("uuid")), None)


def compacting_variant(recs, tag):
    """A golden scenario's records followed by a compaction and two more turns (the stage 4a harness's shape, copied here:
    importing that module re-executes the event model into this process and resets its registries)."""
    t1 = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + 600
    b, sm = "b_%s" % tag, "s_%s" % tag
    more = [G.compact_line(t1, b, _last_uuid(recs)),
            G.compact_summary_line(t1 + 1, sm, b),
            G.uline(t1 + 10, "after the compaction, what remains?", "u_%s_1" % tag, sm),
            G.aline(t1 + 20, "the cap and the retry budget remain", "a_%s_1" % tag, "u_%s_1" % tag, stop="end_turn"),
            G.uline(t1 + 30, "then close them out", "u_%s_2" % tag, "a_%s_1" % tag),
            G.aline(t1 + 40, "closing both", "a_%s_2" % tag, "u_%s_2" % tag, stop="end_turn")]
    return list(recs) + more


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

    def _with_notes(self, turns=120, compact_every=25):
        """The fixture with a model stamp on every reply and a states log of romp's notes between the turns: a retry
        recovered, a give-up, an effort change, a command gesture and two orphan replies (one the transcript kept: deduped;
        one it did not: rendered), several stamped one second before a page boundary's first atom."""
        recs = transcript(NOW - 86400, turns=turns, compact_every=compact_every)
        for r in recs:
            if r.get("type") == "assistant":
                r["message"]["model"] = "claude-test-1"
        self.write(recs)
        t_of = {}                                                          # turn index (typed prompts in order) -> its t
        k = 0
        for r in recs:
            if r.get("type") == "user" and not r.get("isCompactSummary") and r.get("promptSource") == "typed":
                t_of[k] = em.parse_z(r["timestamp"]); k += 1
        kept_text = next(r for r in recs if r.get("type") == "assistant")["message"]["content"][0]["text"]
        rows = [{"t": int(t_of[3]) + 30, "retriesRecovered": 2},
                {"t": int(t_of[16]) - 1, "retriesGaveUp": 5, "errorKind": "overloaded"},     # a page boundary (16-turn pages)
                {"t": int(t_of[32]) - 1, "effortApplied": "high"},
                {"t": int(t_of[48]) - 1, "cmdGesture": "/compact"},
                {"t": int(t_of[64]) - 1, "orphanReply": {"uuid": "orph-1", "text": "a reply the transcript never kept"}},
                {"t": int(t_of[7]) + 5, "orphanReply": {"uuid": "orph-2", "text": kept_text}},
                {"t": int(t_of[80]) - 1, "effortApplied": "low"}]
        (jd.STATE / "states" / (SID + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
        return recs

    def test_pages_with_notes_between_the_turns_equal_the_whole_and_the_walks_cross_them(self):
        """Review find N: the fixtures carried no notes, so the cursors, the note ordinals and the orphan dedup ran on empty
        inputs. Notes at page boundaries are a page's first event; the uuid walks resolve them (review find B)."""
        self._with_notes()
        whole = self.whole()
        kinds = [e.get("kind") for e in whole]
        for k in ("retried", "retryGaveUp", "effortApplied", "cmdGesture"):
            self.assertIn(k, kinds, k)
        # the parse synthesizes an orphan reply's atom from the same row when the transcript lacks the text
        # (event_model.synthesize_orphans), so orph-1's note is deduped against that atom, which sits at the note's time;
        # orph-2's text is turn 0's reply, far from the note's time, so under the near-window rule the note renders
        orphaned = [e for e in whole if e.get("orphaned")]
        self.assertEqual([e.get("orphanOf") for e in orphaned], ["orph-2"], "near texts dedup; a copy far in time does not")
        self.assertTrue(any(e.get("kind") == "assistant" and not e.get("orphaned") and (e.get("md") or "").startswith("a reply the transcript never kept") for e in whole))
        self.document()
        m = self.restored()
        floor = m["floor"]; tail = _strip(m["events"])
        for size in (1, 3, 7, 16, 64):
            with self.subTest(page_turns=size):
                self.assertEqual(self.pages(floor, size) + tail, whole, "notes interleaved the same way in pages of %d turns" % size)
        # the uuid walks cross the notes: older to the head from the tail, then around a note and forward to the tail
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        resident = list(sent[-1]["events"]); oldest = resident[0]["uuid"]
        for _ in range(100):
            r = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": oldest}, NOW)
            self.assertNotIn("missing", r, "a page's first event is a note here: it resolves by its second")
            resident = r["events"] + resident
            if not r["more"]:
                break
            oldest = resident[0].get("key") or resident[0]["uuid"]
        self.assertEqual(_strip(resident), self.head_cards + whole)
        note = next(e for e in whole if e.get("kind") == "retryGaveUp")
        w = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": note["uuid"]}, NOW)
        self.assertNotIn("missing", w); self.assertIn(note["uuid"], [e["uuid"] for e in w["events"]])
        held = list(w["events"])
        for _ in range(100):
            n = km._chat_history_reply(SID, {"type": "loadNewer", "id": SID, "after": held[-1].get("key") or held[-1]["uuid"]}, NOW)
            self.assertNotIn("missing", n)
            held = held + n["events"]
            if not n["more"]:
                break
        full = [e["uuid"] for e in self.head_cards + whole]
        self.assertEqual([e["uuid"] for e in held], full[full.index(held[0]["uuid"]):])

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
        for name in sorted(G.SINGLE_FILE):                                 # every single-file scenario made to compact (stage 4a)
            with self.subTest(scenario=name):
                records, _ = G.SINGLE_FILE[name]
                self.write(compacting_variant(records(), name[:6]) if name not in ("compaction_atom", "compaction_broken_stitch", "manual_compact_detached") else records())
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
        keys = [e.get("key") or e.get("uuid") for e in whole]
        self.assertEqual(len(keys), len(set(keys)), "the wire's keys are unique within the list")
        self.document(); m = self.restored()
        got = self.pages(m["floor"], 16) + _strip(m["events"])
        kk = [e.get("key") or e.get("uuid") for e in got]
        self.assertEqual(len(kk), len(set(kk)))
        # a record whose text and tool call are two events keeps its uuid on both (deep links land on the record) and
        # the second carries the key
        t0 = NOW - 7200
        recs = [G.uline(t0, "run it", "u1", None), G.aline(t0 + 10, "running", "a1", "u1", tools=("Bash",), stop="tool_use"),
                G.trline(t0 + 11, "tu_a1_0", "r1", "a1", content="ok"), G.aline(t0 + 20, "done", "a2", "r1", stop="end_turn")]
        self.write(recs)
        whole = self.whole()
        same = [e for e in whole if e.get("uuid") == "a1"]
        self.assertEqual(len(same), 2, [e.get("kind") for e in whole])
        self.assertEqual([e.get("key") for e in same], [None, "a1#2"])


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
        tree = km._parse(self.leaf, SID, NOW)                            # the STORE's tree, the one build_session reads
        self.assertEqual(tree.get("cutTurn"), cut)
        em.hydrate(tree, SID)                                             # what the judges' first pass does, to that tree
        self.assertEqual(sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None), 0, "no marker left")
        km._live_scope.chat_floor0 = False
        try:
            m2 = km.build_session(SID, NOW + 1, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(m2["floor"], cut, "the floor stands after the hydration")
        self.assertEqual([e["uuid"] for e in m2["events"]], [e["uuid"] for e in m["events"]])
        saved = tree.pop("cutTurn")                                       # cutTurn is load-bearing: without it the markers, now gone,
        try:                                                              #  would put the floor at turn 0
            km._live_scope.chat_floor0 = False
            m3 = km.build_session(SID, NOW + 2, {})
        finally:
            km._live_scope.chat_floor0 = None; tree["cutTurn"] = saved
        self.assertEqual(m3["floor"], 0, "the parse's cutTurn is what holds the floor")


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

    def test_the_base_rule_over_a_list_longer_than_the_wire_tail_and_a_floor_move(self):
        """Review find N: the earlier fixture's floor'd list fit the wire tail whole (pf 0). Here the tail is longer: a change
        just before the held first is a full frame, just after it a delta from the change; a floor move (an index client
        connecting) rebuilds the list from turn 0 and is a full frame."""
        recs = transcript(NOW - 86400, turns=600, compact_every=150)      # the cut near turn 450: ~300 events after it
        self.write(recs); self.document(); m = self.restored()
        evs = m["events"]; self.assertGreater(len(evs), km.WIRE_TAIL)
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        f = sent[-1]; pf = len(evs) - km.WIRE_TAIL
        self.assertEqual(f["firstUuid"], evs[pf].get("key") or evs[pf]["uuid"])
        m2 = dict(m); e2 = list(evs); e2[pf - 1] = dict(e2[pf - 1], md="edited before the held first"); m2["events"] = e2
        km._send_chat_locked(c, m2, None, pf - 1, False)
        self.assertEqual(sent[-1]["type"], "session", "a change before the held first: a full tail frame")
        m3 = dict(m); e3 = list(evs); e3[pf + 1] = dict(e3[pf + 1], md="edited inside"); m3["events"] = e3
        km._send_chat_locked(c, m3, None, pf + 1, False)
        d = sent[-1]
        self.assertEqual((d["type"], d["afterUuid"]), ("chatTail", evs[pf].get("key") or evs[pf]["uuid"]), "a change inside: a delta from it")
        self.assertEqual(len(d["events"]), len(evs) - pf - 1)
        km._live_scope.chat_floor0 = True                                  # an index client connected: the floor drops to 0
        try:
            m0 = km.build_session(SID, NOW + 1, {})
        finally:
            km._live_scope.chat_floor0 = None
        km._send_chat_locked(c, m0, None, 0, False)
        self.assertEqual((sent[-1]["type"], sent[-1]["floor"]), ("session", 0), "a floor move is a full frame")

    def test_a_detached_base_whose_edges_left_the_transcript_gets_a_full_frame(self):
        """Review find A: a detached client whose run is gone (a /clear, a fork, a rewind) was never sent anything again."""
        whole, m = self._restored_tail()
        c, sent = _client()
        c["echat"][SID] = {"first": "gone-1", "last": "gone-2", "detached": True}
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent[-1]["type"], "session", "both edges gone from the transcript: a full frame re-bases the client")
        self.assertFalse(c["echat"][SID]["detached"])
        c["echat"][SID] = {"first": whole[3]["uuid"], "last": whole[30]["uuid"], "detached": True}   # a live pre-floor run
        n = len(sent)
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(len(sent), n, "a detached run the transcript still holds gets no delta")

    def test_a_window_that_reaches_the_held_run_keeps_the_client_attached(self):
        """Review find G: a window overlapping the resident tail merges into one run through the live tail."""
        whole, m = self._restored_tail()
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        base = c["echat"][SID]
        anchor = whole[-len(m["events"]) - 3]["uuid"]                        # just before the floor'd list: the window reaches it
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW, base=base)
        self.assertTrue(r["connected"], "the window holds the client's first: one run through the tail")
        self.assertEqual((r["_base"]["detached"], r["_base"]["last"]), (False, base["last"]))

    def test_in_list_windows_are_turn_aligned_at_floor_zero_and_the_walks_meet_the_whole(self):
        """Review find J: slices of the floor'd list snap to turn boundaries, at floor 0 too (a whole parse, no document)."""
        recs = transcript(NOW - 86400, turns=300, compact_every=1000)     # no compaction: floor 0, ~600 events in the list
        self.write(recs)
        self.fresh(); km._live_scope.chat_floor0 = False
        try:
            m = km.build_session(SID, NOW, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertEqual(m["floor"], 0)
        evs = m["events"]
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        tix = km._turn_index_of_events(evs, turns)
        anchor = evs[len(evs) // 2]["uuid"]
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW)
        first, last = r["events"][0]["uuid"], r["events"][-1]["uuid"]
        pos = {e["uuid"]: i for i, e in enumerate(evs)}
        a, b = pos[first], pos[last]
        self.assertTrue(a == 0 or tix[a - 1] != tix[a], "the window starts at a turn's first event")
        self.assertTrue(b == len(evs) - 1 or tix[b + 1] != tix[b], "and ends at a turn's last event")
        held = list(r["events"])
        for _ in range(50):
            o = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": held[0].get("key") or held[0]["uuid"]}, NOW)
            held = o["events"] + held
            if not o["more"]:
                break
        for _ in range(50):
            n = km._chat_history_reply(SID, {"type": "loadNewer", "id": SID, "after": held[-1].get("key") or held[-1]["uuid"]}, NOW)
            held = held + n["events"]
            if not n["more"]:
                break
        self.assertEqual(_strip(held), _strip(evs), "both walks from the window meet the whole list")

    def test_the_fold_entry_carries_the_prefixs_key_counts(self):
        whole, m = self._restored_tail()
        km._live_scope.chat_floor0 = False
        try:
            km.build_session(SID, NOW + 1, {})                             # a second build seals a prefix
        finally:
            km._live_scope.chat_floor0 = None
        fe = km._chat_fold_get(SID)
        self.assertIsNotNone(fe); self.assertIn("keyCounts", fe)
        self.assertEqual(fe["keyCounts"], km._key_counts(fe["events"]))

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
        self.assertEqual(_strip(resident), self.head_cards + whole, "the pages walked back to the head, the head cards first, concatenate to the whole build")
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
        self.assertEqual(_strip(held), self.head_cards + whole, "the window walked forward to the tail equals the whole, the head cards first (the window reached the head)")
        self.assertIn("status", n, "back at the tail the reply carries the frame's status (no full frame needed)")
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


class HydrationRace(Harness):
    def test_an_atom_another_thread_finished_between_the_filter_and_the_read_is_skipped(self):
        """Review find C: the disk loop was guarded, the memo-hit path and _hydrate_one were not."""
        class Flaky(dict):                                                # answers the marker once (the filter), then none
            def __init__(self, *a, **k):
                super().__init__(*a, **k); self.n = 0
            def get(self, k, d=None):
                if k == "lazy":
                    self.n += 1
                    return super().get(k, d) if self.n <= 1 else None
                return super().get(k, d)
        a = Flaky({"uuid": "x1", "type": "user", "lazy": {"k": "u", "at": (0, 10)}, "session_id": SID})
        with em._ASM_CKPT_LOCK:
            em._HYDRATED["x1"] = ({"uuid": "x1", "message": {"role": "user", "content": "hi"}}, 10)   # a warm memo
        try:
            self.assertEqual(em.hydrate([a], SID), 1, "counted as filled, nothing raised")
        finally:
            with em._ASM_CKPT_LOCK:
                em._HYDRATED.pop("x1", None)
        em._hydrate_one({"uuid": "x2"}, {"uuid": "x2"})                   # a finished atom: a no-op, not a KeyError


if __name__ == "__main__":
    unittest.main()
