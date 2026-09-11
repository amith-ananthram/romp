#!/usr/bin/env python3
"""T323 stage 4b (2026-09-11): the chat build's RENDER FLOOR and PAGE renderer. A restored parse's pre-cut turns are
lazy (stage 4a); the chat build renders from the turn holding the assembly cut (the floor) and the older history is
rendered on demand, a page of turns at a time, for a proto-2 client's loadOlder / loadAround / loadNewer. Pinned here:
the pages from turn 0 to the floor, concatenated with the floor'd list, equal the WHOLE build's events byte for byte,
at every page size (so at every page boundary), with notes interleaved between the turns; a page hydrates its own
turns only and the floor'd build hydrates the tail's; the floor drops to 0 while a proto-1 client is connected and
climbs back when it leaves (the fold entry rebuilt, the prefix released); every event's uuid is unique within a
list. Synthetic transcripts only (the stage 4a served fixture's builder and the golden compaction scenarios)."""
import contextlib
import io
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
        self.saved = (km._sessions, km._live_map)
        km._sessions = lambda now, **kw: list(self.rows)
        km._live_map = lambda: {}
        self.fresh()

    def tearDown(self):
        km._sessions, km._live_map = self.saved
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
        with em._MAT_LOCK:                                                # the lazy index's LRU and counters (stage 4c)
            em._MAT_LRU.clear()
            em._ASM_INDEX_STATS.update(materialized=0, materializedBy={}, resident=0, evictions=0, restoredTurns=0)

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
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, tree=km._parse(self.leaf, SID, NOW)), em.asm_checkpoint_stats())   # the
        #                                                                  store's tree gives the turns section (stage 4c)

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
        # a page boundary of the PARSE's turns (each compact boundary record is its own turn, so prompt 96 is not turn 96):
        # the first multiple of 16 whose turn starts a clean second after the turn before ends
        pturns = km._parse(self.leaf, SID, NOW)["turns"]
        self.boundary = next(b for b in (96, 80, 112, 64) if (pturns[b - 1].get("end") or 0) < int(pturns[b]["t"]) - 1)
        rows = [{"t": int(t_of[3]) + 30, "retriesRecovered": 2},
                {"t": int(t_of[16]) - 1, "retriesGaveUp": 5, "errorKind": "overloaded"},     # a page boundary (16-turn pages)
                {"t": int(t_of[32]) - 1, "effortApplied": "high"},
                {"t": int(t_of[48]) - 1, "cmdGesture": "/compact"},
                {"t": int(t_of[64]) - 1, "orphanReply": {"uuid": "orph-1", "text": "a reply the transcript never kept"}},
                {"t": int(t_of[7]) + 5, "orphanReply": {"uuid": "orph-2", "text": kept_text}},
                {"t": int(t_of[80]) - 1, "effortApplied": "low"},
                {"t": int(pturns[self.boundary]["t"]) - 1, "orphanReply": {"uuid": "orph-3", "text": kept_text}}]   # RENDERED, at a page boundary
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
        self.assertEqual([e.get("orphanOf") for e in orphaned], ["orph-2", "orph-3"], "near texts dedup; a copy far in time does not")
        self.assertRegex(orphaned[1]["uuid"], r"^orphan:\d+:\d+$", "a rendered note's key: orphan:<t>:<n>, the record uuid on orphanOf")
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
        # the rendered orphan note at the boundary of the 96th turn: a page's FIRST event; a window lands on it by its key,
        # the pages around it equal the whole, and a walk older from it crosses the boundary (round 2, item 6)
        onote = orphaned[1]
        ow = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": onote["uuid"]}, NOW)
        self.assertNotIn("missing", ow); self.assertIn(onote["uuid"], [e["uuid"] for e in ow["events"]])
        wu = [e["uuid"] for e in whole]
        full_index = wu.index(onote["uuid"])
        b16 = self.boundary
        self.assertEqual(self.pages(m["floor"], 16)[full_index]["uuid"], onote["uuid"])
        page_at = km._chat_history_page(SID, b16, b16 + 16, NOW)
        self.assertEqual(page_at[0]["uuid"], onote["uuid"], "the note is the boundary page's FIRST event")
        before = km._chat_history_page(SID, b16 - 16, b16, NOW)
        self.assertFalse(before[-1].get("orphaned"), "the page before it ends clean")
        turns_b = km._parse(self.leaf, SID, NOW)["turns"]
        first_atom = next(a["uuid"] for a in turns_b[b16]["atoms"] if a.get("uuid"))
        ob0 = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": first_atom}, NOW)
        self.assertNotIn(onote["uuid"], [e["uuid"] for e in ob0["events"]], "older than the boundary turn's first atom excludes the note")
        ob = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": onote["uuid"]}, NOW)
        self.assertNotIn("missing", ob); self.assertTrue(ob["events"], "older than the note: the pages before its boundary")
        older = [e["uuid"] for e in ob["events"]]
        self.assertEqual(older[len(self.head_cards):] + [onote["uuid"]], wu[:full_index + 1], "one contiguous run across the boundary, from the head")
        self.assertFalse(ob["more"])
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


def restart_seam_records():
    """A machine-cut seam in the pre-cut history: a prompt, a reply cut by a kernel restart (the CLI's stop record, its null
    settle, romp's resume notice), then the resumed reply; more turns; then a compaction and two turns after it, so the
    seam lies in the pages. The notice is what stamps the marker's cause and drops the settle (_stamp_interrupt_causes),
    and it lands turns after the marker: a page ending at the marker's turn reads it through its fill turns."""
    t0 = NOW - 86400
    recs, parent = [], None
    for i in range(12):
        t = t0 + i * 120
        recs.append(G.uline(t, "step %d, please" % i, "u_seam_%d" % i, parent))
        recs.append(G.aline(t + 30, "step %d done" % i, "a_seam_%d" % i, "u_seam_%d" % i, stop="end_turn"))
        parent = "a_seam_%d" % i
    t = t0 + 12 * 120
    recs.append(G.uline(t, "now the long step", "u_cut", parent))
    recs.append(G.aline(t + 20, "starting the long step", "a_cut", "u_cut", stop="end_turn"))
    recs.append(G.uline(t + 40, "[Request interrupted by user]", "u_stop", "a_cut", ps=None))
    recs.append(G.aline(t + 41, "No response requested.", "a_settle", "u_stop", stop="end_turn"))
    recs.append(G.uline(t + 60, "[romp] The romp kernel " + km.INTR_RESTART_SIG + " this session's in-flight turn; pick it up where it "
                        "stopped.<!-- romp-injected --><!-- romp-system -->", "u_notice", "a_settle", ps=None))
    recs.append(G.aline(t + 90, "resuming the long step", "a_resume", "u_notice", stop="end_turn"))
    parent = "a_resume"
    for i in range(12, 20):
        t = t0 + (i + 1) * 120
        recs.append(G.uline(t, "step %d, please" % i, "u_seam_%d" % i, parent))
        recs.append(G.aline(t + 30, "step %d done" % i, "a_seam_%d" % i, "u_seam_%d" % i, stop="end_turn"))
        parent = "a_seam_%d" % i
    return compacting_variant(recs, "seam")


class RestartSeam(Harness):
    def test_a_machine_cut_seam_in_the_pages_keeps_its_cause_and_no_page_repeats_an_event(self):
        """Round 2, item 5: the settle a restart seam drops shifted every index after it, so a page kept its fill turn's first
        event and the next page repeated it; and no golden carried an interrupt marker."""
        self.write(restart_seam_records())
        whole = self.whole()
        marker = [e for e in whole if e.get("interruptMarker")]
        self.assertEqual(len(marker), 1); self.assertEqual(marker[0].get("interruptCause"), "restart", "the notice names the cause")
        self.assertEqual([e for e in whole if e.get("interruptSettle")], [], "a machine cut shows no settle line")
        self.assertIn("a_settle", marker[0].get("settleUuids") or [], "the dropped settle's uuid still answers on the seam")
        self.document()
        m = self.restored()
        floor = m["floor"]; self.assertGreater(floor, 0)
        tail = _strip(m["events"])
        for size in (1, 2, 16):
            with self.subTest(page_turns=size):
                paged = self.pages(floor, size)
                self.assertEqual(paged + tail, whole, "the seam through pages of %d turns" % size)
                keys = [e.get("key") or e["uuid"] for e in paged + tail]
                self.assertEqual(len(keys), len(set(keys)), "no event twice on the wire")


def _fake_self(path):
    """A connect handler whose peer speaks through the stubbed _ws_recv (tests/test_chat_skeleton_reconnect.py's shape)."""
    class FakeSelf:
        headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        rfile = io.BytesIO(); wfile = io.BytesIO()
        connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
        close_connection = False
        def send_response(self, *a): pass
        def send_header(self, *a): pass
        def end_headers(self): pass
    FakeSelf.path = path
    for name in ("_dispatch_ws", "_push_one"):                            # the handler's own arms, on the fake
        setattr(FakeSelf, name, getattr(km.Handler, name))
    return FakeSelf()


class RealArm(Harness):
    """The socket handler's own arms driven with frames (the ready, the history requests, needFull): the base the kernel
    keeps for the client is the real arm's, not a re-implementation's (round 3, D)."""

    def _drive(self, path, next_frame, alive_rows=True):
        got, sent = [], []
        real = (km._register_ws_client, km._ws_recv, km._mk_ws_send, km._alive_sessions)
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: next_frame(sent)
        km._mk_ws_send = lambda q, sock, client: (lambda s: sent.append(json.loads(s)))
        if alive_rows:
            km._alive_sessions = lambda now, tmux: list(self.rows)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(_fake_self(path))
        finally:
            km._register_ws_client, km._ws_recv, km._mk_ws_send, km._alive_sessions = real
            with km._clients_lock:
                for c in got:
                    if c in km._clients:
                        km._clients.remove(c)
        self.assertEqual(len(got), 1)
        return got[0], sent

    @staticmethod
    def _frame(msg):
        return (0x1, json.dumps(msg).encode("utf-8"), True)

    def test_the_registration_reads_the_redials_protocol(self):
        """Round 2 item 4 / round 3 D: the dial term's proto lands on the client at the handshake."""
        for path, want in (("/ws?app=chat&delta=1&iid=p1&active=%s&reconnect=1&proto=2" % SID, 2),
                           ("/ws?app=chat&delta=1&iid=p1&active=%s&reconnect=1&proto=1" % SID, 1),
                           ("/ws?app=chat&delta=1&iid=p1&active=%s&reconnect=1" % SID, None),
                           ("/ws?app=chat&delta=1&iid=p1&active=%s&proto=2" % SID, None)):   # not a redial: the ready says
            c, _ = self._drive(path, lambda sent: (0x8, b"", True), alive_rows=False)
            self.assertEqual(c.get("proto"), want, path)
            self.assertIsInstance(c.get("t0"), (int, float), "the registration is stamped for the ready wait")

    def test_a_return_to_live_keeps_the_runs_first_edge_and_a_window_into_the_walked_part_stays_attached(self):
        """Round 3, A: deep link, loadNewer into the list (still detached), Return to live (needFull reattach), a deep link
        into the walked part, then a tail change reaches the client as a chatTail. Before the fix the re-attach frame
        re-based the kernel's first to the wire tail's while the client's merged run kept its older first."""
        recs = transcript(NOW - 86400, turns=600, compact_every=150)      # ~300 events after the cut: longer than the wire tail
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        evs = m["events"]; list_keys = {e.get("key") or e["uuid"] for e in evs}
        deep = whole[10]["uuid"]
        run = []                                                          # what the page holds, as the frames build it
        state = {"step": "ready", "fulls": 0}

        def next_frame(sent):
            fresh = sent[state.get("seen", 0):]                            # the frames since the last request (the caps frame and
            state["seen"] = len(sent)                                     #  the tab strip ride the same socket: rarely the last one)

            def newest(kind):
                return next((f for f in reversed(fresh) if f.get("type") == kind and f.get("id") == SID), None)
            if state["step"] == "ready":
                state["step"] = "window"; return self._frame({"type": "ready", "proto": 2})
            if state["step"] == "window":
                if newest("session") is not None:
                    state["fulls"] += 1; state["step"] = "newer"
                    return self._frame({"type": "loadAround", "id": SID, "uuid": deep})
            elif state["step"] == "newer":
                w = newest("chatWindow"); mo = newest("chatMore") if w is None else None
                if w is not None:
                    run[:] = list(w["events"])
                    self.assertTrue(w["moreAfter"])
                if mo is not None:
                    run.extend(mo["events"])
                    self.assertTrue(mo["more"], "the walk stops inside the list, still detached")
                if w is not None or mo is not None:
                    if any((e.get("key") or e["uuid"]) in list_keys for e in run):
                        state["step"] = "keys"
                        return self._frame({"type": "reattachKeys", "id": SID, "keys": [e.get("key") or e["uuid"] for e in run[-512:]]})
                    return self._frame({"type": "loadNewer", "id": SID, "after": run[-1].get("key") or run[-1]["uuid"]})
            elif state["step"] == "keys":
                state["step"] = "reattach"
                return self._frame({"type": "needFull", "id": SID, "why": "reattach"})   # Return to live, the keys ahead of it
            elif state["step"] == "reattach":
                f = newest("session")
                if f is not None:
                    state["fulls"] += 1; state["step"] = "walked"
                    # the client merges the frame into its run (they overlap: the run's newest is inside the frame)
                    fk = {e.get("key") or e["uuid"] for e in f["events"]}
                    self.assertTrue(any((e.get("key") or e["uuid"]) in fk for e in run), "the frame overlaps the held run")
                    inside = run[len(run) // 3]["uuid"]                 # into the walked pages, far from the tail
                    return self._frame({"type": "loadAround", "id": SID, "uuid": inside})
            elif state["step"] == "walked":
                w = newest("chatWindow")
                if w is not None:
                    state["window"] = w; state["step"] = "done"
            return (0x8, b"", True)
        c, sent = self._drive("/ws?app=chat&delta=1&iid=p2&active=%s" % SID, next_frame)
        self.assertEqual(state["step"], "done", "the sequence ran to its end: %s" % state)
        self.assertEqual(state["fulls"], 2)
        base = c["echat"][SID]
        hc = {h["uuid"] for h in self.head_cards}
        first_event = next(e for e in run if e["uuid"] not in hc)         # the window reached the head: its cards ride first (B)
        self.assertEqual(base["first"], first_event.get("key") or first_event["uuid"], "the kernel's base keeps the run's older first edge")
        self.assertFalse(base["detached"])
        w = state["window"]
        self.assertTrue(w["connected"], "a window into the walked part overlaps the run: attached on both ends")
        self.assertTrue(w["moreAfter"])
        n = len(sent)
        km._send_chat_locked(c, m, None, len(evs) - 1, False)             # the next tail change
        self.assertEqual(len(sent), n + 1); self.assertEqual(sent[-1]["type"], "chatTail", "a delta, not silence")

    def test_a_window_reaching_the_head_keys_the_base_on_the_first_event_and_the_walk_to_the_tail_reattaches(self):
        """Round 3, B: the head cards ride a window that reaches turn 0; the base's first is the first EVENT's key, which
        places in a turn, so the walk to the tail re-attaches and the next tail change is a delta."""
        recs = transcript(NOW - 86400, turns=200, compact_every=25)
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        self.assertTrue(self.head_cards, "the fixture has head cards")
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        w = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": whole[0]["uuid"]}, NOW, base=c["echat"][SID])
        self.assertFalse(w["moreBefore"]); self.assertEqual(w["events"][0]["uuid"], self.head_cards[0]["uuid"], "the head cards ride first")
        self.assertEqual(w["_base"]["first"], whole[0]["uuid"], "the base's first is the first EVENT, not a head card")
        c["echat"][SID] = w.pop("_base")
        held = list(w["events"])
        for _ in range(200):
            r = km._chat_history_reply(SID, {"type": "loadNewer", "id": SID, "after": held[-1].get("key") or held[-1]["uuid"]}, NOW, base=c["echat"][SID])
            held += r["events"]
            b = r.pop("_base"); b["first"] = c["echat"][SID]["first"] if b.pop("keepFirst", False) else b["first"]
            c["echat"][SID] = b
            if not r["more"]:
                break
        self.assertFalse(c["echat"][SID]["detached"], "back at the tail")
        n = len(sent)
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent[-1]["type"], "chatTail"); self.assertEqual(len(sent), n + 1)
        # a window back into the head part now overlaps the run [turn 0 .. the tail]: attached
        w2 = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": whole[4]["uuid"]}, NOW, base=c["echat"][SID])
        self.assertTrue(w2["connected"])


class ReattachKeysArm(Harness):
    """1448's lows: a posted key list is held only for a session the client has a base for, and the map is bounded."""

    def test_unknown_sessions_are_ignored_and_the_map_is_bounded(self):
        c, sent = _client()
        c["echat"]["s-known"] = {"first": "a", "last": "b", "detached": False}
        arm = lambda msg: km.Handler._dispatch_ws(object.__new__(km.Handler), msg, c)
        arm({"type": "reattachKeys", "id": "s-unknown", "keys": ["k1"]})
        self.assertNotIn("reattachKeys", c, "a session this client holds no base for: dropped")
        arm({"type": "reattachKeys", "id": "s-known", "keys": ["k%d" % i for i in range(700)]})
        self.assertEqual(len(c["reattachKeys"]["s-known"]), km.REATTACH_KEYS, "the newest keys, bounded")
        for i in range(km.REATTACH_KEYS_CLIENTS + 3):
            sid = "s-%d" % i
            c["echat"][sid] = {"first": "a", "last": "b", "detached": False}
            arm({"type": "reattachKeys", "id": sid, "keys": ["k"]})
        self.assertEqual(len(c["reattachKeys"]), km.REATTACH_KEYS_CLIENTS, "the map is capped")
        self.assertNotIn("s-known", c["reattachKeys"], "…the oldest dropped first")
        self.assertIn("s-%d" % (km.REATTACH_KEYS_CLIENTS + 2), c["reattachKeys"])


class OrphanGate(Harness):
    """Round 2 item 11, executed: the fold's orphan demotion gate reads the note's own window."""

    def _sealed_note(self):
        """A rendered orphan note in the (soon sealed) last turn of a 200-turn transcript, its text turn 0's reply: the parse
        keeps the text far away, so the near-window rule renders the note; then a typed prompt opens turn 200, which seals
        turn 199 with the note into the fold's prefix. Returns (records, kept_text, the open prompt's uuid)."""
        recs = transcript(NOW - 86400, turns=200, compact_every=1000)     # no compaction: one fold over the whole list
        self.write(recs)
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        kept_text = next(r for r in recs if r.get("type") == "assistant")["message"]["content"][0]["text"]
        rows = [{"t": int(turns[199]["t"]) - 1, "orphanReply": {"uuid": "orph-g", "text": kept_text}}]
        (jd.STATE / "states" / (SID + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
        last = next(r for r in reversed(recs) if r.get("uuid"))
        t_last = em.parse_z(last["timestamp"])
        recs2 = recs + [G.uline(t_last + 60, "one more, please", "u_g200", last["uuid"])]
        self.write(recs2)
        m = km.build_session(SID, NOW, {}, floor=0)                       # turn 199 sealed, the note in it
        self.assertTrue(any(e.get("orphanOf") == "orph-g" for e in m["events"]), "the note rendered")
        fe = km._chat_fold_get(SID)
        self.assertIsNotNone(fe)
        self.assertTrue(any(isinstance(x, (list, tuple)) and x[1] == kept_text for x in fe["orphan_texts"]), "sealed as (t, text)")
        return recs2, kept_text, t_last

    def test_a_reply_landing_in_the_notes_window_retires_it_and_one_far_from_it_does_not(self):
        recs2, kept_text, t_last = self._sealed_note()
        # the far case: the open turn's reply says something else, then a later turn repeats the salvaged text
        far = recs2 + [G.aline(t_last + 90, "quite another reply", "a_g200", "u_g200", stop="end_turn"),
                       G.uline(t_last + 120, "and again", "u_g201", "a_g200"),
                       G.aline(t_last + 150, kept_text, "a_g201", "u_g201", stop="end_turn")]
        self.write(far)
        km.build_session(SID, NOW + 1, {}, floor=0)
        info = km._chat_fold_last_info()
        self.assertNotEqual(info.get("why"), "orphan", "a match two turns past the note's window is not its reply: %s" % info)
        self.assertEqual(info.get("fold"), 1, "the fold stands")
        # the near case: the reply landing in the turn right after the note's (its window) retires it
        self.fresh(); (jd.STATE / "states" / (SID + ".jsonl")).unlink()
        recs2, kept_text, t_last = self._sealed_note()
        near = recs2 + [G.aline(t_last + 90, kept_text, "a_g200", "u_g200", stop="end_turn")]
        self.write(near)
        km.build_session(SID, NOW + 1, {}, floor=0)
        self.assertEqual(km._chat_fold_last_info().get("why"), "orphan", "a new reply in the note's window retires it: a refold")


class KeyCounts(unittest.TestCase):
    """Round 2 item 13, executed: the sealed part is keyed before its key counts are memoized, else a uuid-less event in a
    just-sealed turn is missing from the next build's seen map and a later event of its kind gets the same key."""

    def test_counts_taken_after_the_pass_carry_the_uuid_less_events_key(self):
        prefix = [{"kind": "user", "uuid": "u0"}]
        sealed = [{"kind": "assistant", "uuid": "a0"}, {"kind": "todo"}]          # the overlay card has no uuid: the pass names it
        tail = [{"kind": "todo"}]
        events = prefix + sealed + tail
        p, b = len(prefix), len(prefix) + len(sealed)
        stale = km._key_counts(events[:b])                                       # the pre-fix order: counts before the pass
        self.assertNotIn("todo", stale)
        km._uniq_event_uuids(events[p:b], events[:p])                            # the commit's order now
        counts = km._key_counts(events[:b])
        self.assertEqual(counts.get("todo"), 1, "the sealed card's key is in the seen map")
        km._uniq_event_uuids(events[b:], events[:b], seen=counts)
        self.assertEqual(events[b:][0].get("key"), "todo#2", "the tail's card is a second of its kind")
        stale_tail = [{"kind": "todo"}]
        km._uniq_event_uuids(stale_tail, [], seen=stale)
        self.assertIsNone(stale_tail[0].get("key"), "with the stale counts the same key would have been minted twice")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        i = src.index('"keyCounts": _key_counts(events[:_b])')
        self.assertIn("_uniq_event_uuids(events[_pref_len:_b], events[:_pref_len],", src[i - 1200:i], "the pass precedes the commit")


class ZeroMaterialization(Harness):
    """T323 stage 4c: the chat's first open of a restored session builds no pre-cut atom. The floor'd build reads the turns
    before the floor through their own scalars (_cursors_before, _turn_index_of_events, _fold_tasks, the cut) and hydrates
    the tail's atoms only; a page then builds exactly its own turns' atoms."""

    def test_the_floored_build_builds_no_pre_cut_atom_and_a_page_builds_its_own_turns(self):
        recs = transcript(NOW - 86400, turns=120, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()                                               # fresh() zeroed the index's counters
        self.assertGreater(m["floor"], 0)
        tree = km._parse(self.leaf, SID, NOW)
        self.assertTrue(all(isinstance(tree["turns"][i], em.PreTurn) for i in range(m["floor"])), "the pre-cut turns are the index's")
        st = em.asm_index_stats()
        self.assertEqual(st["materialized"], 0, "the first open built no pre-cut atom: %s" % st["materializedBy"])
        page = km._chat_history_page(SID, 16, 32, NOW)
        self.assertTrue(page)
        st = em.asm_index_stats()
        built = sum(len(tree["turns"][i]["uuids"]) for i in range(16, 32 + km._PAGE_FILL_TURNS))
        self.assertLessEqual(st["materialized"], built, "a page builds its turns and its fill turns, no more: %s" % st["materializedBy"])
        self.assertGreater(st["materialized"], 0)
        r = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": m["events"][0]["uuid"]}, NOW)
        self.assertTrue(r["events"])


class EchoPlacement(Harness):
    """Review low 3: a stale echo stamped in the pre-cut history goes into the first post-cut turn, never into a restored
    pre-cut turn (its atoms are the document's, its spans written, and a synthetic turn among them would move the floor)."""

    def test_an_echo_before_the_cut_joins_the_first_tail_turn_and_the_pre_turns_stand(self):
        recs = transcript(NOW - 86400, turns=60, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()
        tree = km._parse(self.leaf, SID, NOW)
        cut = tree["cutTurn"]; turns = tree["turns"]
        early = turns[3]["t"] + 1                                          # inside a pre-cut turn's window
        gap = turns[cut - 1]["end"] + 1 if turns[cut]["t"] - turns[cut - 1]["end"] > 2 else turns[2]["end"] + 1   # a gap among pre-turns
        echoes = [{"type": "user", "uuid": "echo-1", "session_id": SID, "t": early, "_echo_text": "a note sent yesterday", "message": {"role": "user", "content": "a note sent yesterday"}},
                  {"type": "user", "uuid": "echo-2", "session_id": SID, "t": gap, "_echo_text": "another", "message": {"role": "user", "content": "another"}},
                  {"type": "user", "uuid": "echo-0", "session_id": SID, "t": turns[0]["t"] - 60, "_echo_text": "before everything", "message": {"role": "user", "content": "before everything"}}]
        out, placed = km._place_stale_echoes(turns, echoes)
        self.assertEqual(len(out), len(turns), "no synthetic turn among the pre-cut turns")
        for i in range(cut):
            self.assertIs(out[i], turns[i], "a pre-cut turn is untouched")
        self.assertEqual({p[0] for p in placed}, {cut}, "both echoes joined the first post-cut turn")
        self.assertEqual(sorted(a["uuid"] for a in out[cut]["atoms"] if a.get("_echo_text")), ["echo-0", "echo-1", "echo-2"], "the one ahead of every turn too")
        self.assertEqual(em.asm_index_stats()["materialized"], 0, "…and no pre-cut atom was built for it")


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


class FloorDecision(Harness):
    def test_which_clients_move_the_floor(self):
        """Round 2, item 4: a socket before its ready has no protocol and moves no floor; a redial carries the page's protocol
        on its dial term; a client stamped ready with none (an older shim's redial) is an index client."""
        f = km._chat_floor0_of
        self.assertFalse(f([]))
        self.assertFalse(f([{"proto": 2, "ready": True}]))
        self.assertFalse(f([{"proto": None, "ready": False}, {"proto": 2}]), "a socket whose ready has not arrived is not counted")
        self.assertTrue(f([{"proto": 1, "ready": True}]))
        self.assertTrue(f([{"proto": None, "ready": True}]), "ready with no protocol: an index client")
        self.assertTrue(f([{"proto": 2}, {"proto": 1}]))
        t = km._ws_clock()
        self.assertFalse(f([{"proto": None, "ready": False, "t0": t - 5}], now=t), "a socket five seconds old waits for its ready")
        self.assertTrue(f([{"proto": None, "ready": False, "t0": t - km.READY_WAIT_S - 1}], now=t),
                        "no protocol and no ready past the wait: an index client (a page whose ready was never answered)")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('_live_scope.chat_floor0 = _chat_floor0_of(_all_chat)', src)
        reg = src[src.index('            client["reconnect"] = True'):src.index('        _register_ws_client(client)')]
        self.assertIn('_rp = (q.get("proto") or [""])[0]', reg, "the redial's registration reads the protocol from its dial term")
        self.assertIn('client["proto"] = int(_rp)', reg)
        self.assertIn('?"&reconnect=1&proto="+readyProto:""', src, "the shim's dial term carries the protocol the bundle's ready declared")
        self.assertIn('if(m&&m.type==="ready"){bundleReady=true;readyProto=(m.proto===2?2:1);readyMsg=s;}', src)

    def test_a_connect_push_counts_every_connected_client_and_a_pre_ready_socket_moves_nothing(self):
        """Review find E, executed through _push: the decision reads km._clients, not the push's targets."""
        recs = transcript(NOW - 86400, turns=90, compact_every=25)
        self.write(recs)
        self.document()
        m = self.restored()
        cut = m["floor"]; self.assertGreater(cut, 0)
        saved_alive, saved_clients = km._alive_sessions, km._clients
        km._alive_sessions = lambda now, tmux: list(self.rows)
        try:
            c2, s2 = _client(proto=2); c2.update(app="chat", alive=True, ready=True, active=SID)
            c1, s1 = _client(proto=1); c1.update(app="chat", alive=True, ready=True)
            c0, s0 = _client(proto=None); c0.update(app="chat", alive=True, ready=False)   # a socket before its ready
            with km._clients_lock:
                km._clients = [c2, c0]
            km._push([c2], connect=True, live_map={})
            self.assertEqual(km._RENDER_FLOOR[SID], cut, "a proto-2 page and a pre-ready socket: the floor stands")
            f2 = next(x for x in s2 if x.get("type") == "session" and x.get("id") == SID)
            self.assertEqual(f2.get("proto"), 2)
            with km._clients_lock:
                km._clients = [c2, c1]                                    # an index client connects elsewhere
            km._push([c2], connect=False, live_map={})
            self.assertEqual(km._RENDER_FLOOR[SID], 0, "every connected client decides: the index client drops the floor")
            self.assertEqual(km._chat_fold_last_info().get("why"), "floor")
            with km._clients_lock:
                km._clients = [c2]
            km._push([c2], connect=False, live_map={})
            self.assertEqual(km._RENDER_FLOOR[SID], cut, "…and it climbs back when the index client leaves")
        finally:
            km._alive_sessions = saved_alive
            with km._clients_lock:
                km._clients = saved_clients


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
        pos = {e["uuid"]: i for i, e in enumerate(evs)}
        for d in (0, 1, 2, 3):                                            # both parities: the fixture's turns are two events each,
            anchor = evs[len(evs) // 2 + d]["uuid"]                       #  so one parity lands on a turn start by accident (round 2)
            r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW)
            first, last = r["events"][0]["uuid"], r["events"][-1]["uuid"]
            a, b = pos[first], pos[last]
            self.assertTrue(a == 0 or tix[a - 1] != tix[a], "the window starts at a turn's first event (anchor +%d)" % d)
            self.assertTrue(b == len(evs) - 1 or tix[b + 1] != tix[b], "and ends at a turn's last event (anchor +%d)" % d)
            self.assertIn(anchor, [e["uuid"] for e in r["events"]])
        anchor = evs[len(evs) // 2]["uuid"]
        r = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW)
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

    @staticmethod
    def _apply_base(c, reply):
        """What the handler does with a reply's _base (kernel.py, the history requests' arm): keepFirst / keepLast."""
        base = reply.pop("_base", None)
        old = c["echat"].get(SID)
        if base is None:
            return
        if base.pop("keepFirst", False):
            base["first"] = old.get("first") if isinstance(old, dict) else None
        if base.pop("keepLast", False):
            if not isinstance(old, dict):
                return
            base["last"], base["detached"] = old.get("last"), bool(old.get("detached"))
        c["echat"][SID] = base

    def test_load_older_advances_the_runs_first_edge_and_a_window_inside_the_walked_run_stays_attached(self):
        """Round 2, item 1: the base's first never moved with loadOlder, so loadAround's `connected` tested a STALE first: a
        window inside the walked run (holding neither the stale first nor the live tail) left the kernel detached while the
        page, holding the live tail, believed itself live: no deltas, ever."""
        recs = transcript(NOW - 86400, turns=600, compact_every=150)      # ~300 events after the cut: longer than the wire tail
        self.write(recs)
        whole = self.whole()
        self.document()
        m = self.restored()
        evs = m["events"]
        self.assertGreater(len(evs), km.WIRE_TAIL)
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        base0 = dict(c["echat"][SID])
        self.assertEqual(base0["first"], evs[len(evs) - km.WIRE_TAIL]["uuid"], "the first frame's base: the wire tail's first")
        self.assertNotEqual(base0["first"], evs[0]["uuid"])
        o1 = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": base0["first"]}, NOW, base=c["echat"][SID])
        self.assertIsNotNone(o1.get("_base")); self.assertTrue(o1["_base"].get("keepLast"))
        self._apply_base(c, o1)
        b1 = c["echat"][SID]
        self.assertEqual(b1["first"], evs[0]["uuid"], "the run's first edge advanced to the list's head")
        self.assertEqual((b1["last"], b1["detached"]), (base0["last"], False), "its last and its attachment unchanged")
        o2 = km._chat_history_reply(SID, {"type": "loadOlder", "id": SID, "before": b1["first"]}, NOW, base=c["echat"][SID])
        self._apply_base(c, o2)
        b2 = c["echat"][SID]
        pages = o2["events"]
        self.assertEqual(b2["first"], pages[0].get("key") or pages[0]["uuid"], "…and into the pages")
        run = pages + evs                                                 # what the page holds: one run through the live tail
        anchor = run[len(pages) // 2]["uuid"]                             # inside the walked pages: neither edge is in its window
        w = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": anchor}, NOW, base=c["echat"][SID])
        keys = {e.get("key") or e["uuid"] for e in w["events"]}
        self.assertNotIn(b2["first"], keys); self.assertNotIn(base0["first"], keys); self.assertNotIn(b2["last"], keys)
        self.assertTrue(w["moreAfter"], "the window ends before the tail")
        self.assertTrue(w["connected"], "…but lies inside the run the client holds through the live tail: attached")
        self.assertEqual((w["_base"]["first"], w["_base"]["last"], w["_base"]["detached"]), (b2["first"], b2["last"], False))
        self._apply_base(c, w)
        n = len(sent)
        km._send_chat_locked(c, m, None, len(evs) - 1, False)             # a change at the tail: a delta, not silence
        self.assertEqual(len(sent), n + 1); self.assertEqual(sent[-1]["type"], "chatTail")
        # a window that holds NO part of the run (a far anchor) still detaches the client, kernel and page agreeing
        far = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": whole[2]["uuid"]}, NOW, base=c["echat"][SID])
        self.assertFalse(far["connected"]); self.assertTrue(far["_base"]["detached"])
        # the re-attach's shared clause reads the client's OWN resident keys (reattachKeys), never the broadcast diff's change
        # index: the repair frame is a connect push over an unchanged build, so change_from is total there (T323 follow-up, M1)
        head_from = len(evs) - km.WIRE_TAIL
        run_keys = [e.get("key") or e["uuid"] for e in evs[:head_from + 10]]       # a run from the list's head into the frame
        c2, sent2 = _client()
        c2["echat"][SID] = {"first": evs[0]["uuid"], "last": "gone-after-a-fork", "detached": False, "reattach": True, "keys": run_keys[-512:]}
        km._send_chat_locked(c2, m, None, len(evs), False)               # an unchanged build: change_from == total
        self.assertEqual(sent2[-1]["type"], "session"); self.assertEqual(c2["echat"][SID]["first"], evs[0]["uuid"], "the older first kept")
        self.assertNotEqual(sent2[-1]["firstUuid"], evs[0]["uuid"])
        w2 = km._chat_history_reply(SID, {"type": "loadAround", "id": SID, "uuid": evs[3]["uuid"]}, NOW, base=c2["echat"][SID])
        self.assertTrue(w2["connected"], "a window below the frame after the re-attach stays connected: no divergence")
        c3, sent3 = _client()                                            # the fork cut every key the client holds: replaced (M2)
        c3["echat"][SID] = {"first": "dead-1", "last": "dead-2", "detached": False, "reattach": True, "keys": ["dead-1", "dead-9", "dead-2"]}
        km._send_chat_locked(c3, m, None, len(evs), False)
        self.assertEqual(c3["echat"][SID]["first"], sent3[-1]["firstUuid"], "no resident key: the base takes the frame's first")
        c3b, sent3b = _client()                                          # resident keys, all below the frame: no shared key either
        c3b["echat"][SID] = {"first": evs[0]["uuid"], "last": "gone", "detached": False, "reattach": True, "keys": run_keys[:head_from - 5]}
        km._send_chat_locked(c3b, m, None, len(evs), False)
        self.assertEqual(c3b["echat"][SID]["first"], sent3b[-1]["firstUuid"])
        c4, sent4 = _client()                                            # an older bundle sends no keys: the newest edge decides
        c4["echat"][SID] = {"first": evs[0]["uuid"], "last": evs[-1]["uuid"], "detached": False, "reattach": True}
        km._send_chat_locked(c4, m, None, len(evs), False)
        self.assertEqual(c4["echat"][SID]["first"], evs[0]["uuid"])
        c5, sent5 = _client()
        c5["echat"][SID] = {"first": evs[0]["uuid"], "last": "gone", "detached": False, "reattach": True}
        km._send_chat_locked(c5, m, None, len(evs), False)
        self.assertEqual(c5["echat"][SID]["first"], sent5[-1]["firstUuid"])
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('if base.pop("keepLast", False):', src, "the handler applies a loadOlder's first-edge advance")

    def test_a_detached_bases_note_keyed_edges_do_not_keep_it_alive_and_the_check_is_memoized(self):
        """Round 2, item 8: a note key resolves by time, to turn 0 on any newer transcript, so a run edged on a note read as
        alive after a /clear and the client never got its full frame."""
        whole, m = self._restored_tail()
        note_base = {"first": "orphan:%d:1" % (NOW - 90000), "last": "retried:%d:1" % (NOW - 89000), "detached": True}
        self.assertFalse(km._base_alive(SID, note_base, NOW), "note-keyed edges alone: not alive")
        atom_base = {"first": "orphan:%d:1" % (NOW - 90000), "last": whole[30]["uuid"], "detached": True}
        self.assertTrue(km._base_alive(SID, atom_base, NOW), "an atom-keyed edge the transcript holds: alive")
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        key = (SID, atom_base["first"], atom_base["last"])                # per (sid, edges), valid for one parse tree's identity
        hit = km._BASE_ALIVE_MEMO[key]
        ident = (id(turns), len(turns), turns[-1]["id"], turns[-1]["end"])
        self.assertEqual(hit, (ident, True))
        km._BASE_ALIVE_MEMO[key] = (ident, "memo")                       # a repeat with the same edges and parse reads the memo
        self.assertEqual(km._base_alive(SID, atom_base, NOW), "memo")
        km._BASE_ALIVE_MEMO[key] = ((id(turns), len(turns) + 1, turns[-1]["id"], turns[-1]["end"]), "stale")   # the same address, another
        self.assertIs(km._base_alive(SID, atom_base, NOW), True)                                              #  shape: re-read
        c, sent = _client()
        c["echat"][SID] = dict(note_base)
        km._send_chat_locked(c, m, None, len(m["events"]) - 1, False)
        self.assertEqual(sent[-1]["type"], "session", "the detached client edged on notes gets its full frame")
        km._forget_chat_positions(set())
        self.assertFalse([k for k in km._BASE_ALIVE_MEMO if k[0] == SID]); self.assertNotIn(SID, km._UUID_POS)

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
                    if self.n == 2:
                        super().pop("lazy", None)                         # another thread finished it: the marker is GONE, so a
                    return super().get(k, d) if self.n <= 1 else None    #  re-introduced a["lazy"] subscript raises (round 2)
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
