#!/usr/bin/env python3
"""The courier skips a session whose inputs have not moved since a scan that found nothing to place, and
reads the ones it scans through the shared read-only view (2026-09-09).

Measured on the maintainer's box: run_courier loaded every session's goal store with the writer's loader on
every triage pass, 1172 goal loads a pass across 18 sessions, 83% of the judge tier thread's samples. The
skip key is every input the per-session scan reads, taken before the store read (the chain-memo rule): the
parse cache's key bound to the session object, the store file's key with its journal's and archive's, the
episode log's key and the transcript path. Pins: unchanged inputs skip after a scan that placed nothing; a
moved store, a fresh parse or an episode boundary un-skips that session alone; a write landing during the
scan is seen next pass; three sessions with one moved place exactly what the ungated pass places; a parse
the cache does not hold is never skipped; the scan asks the writer's loader for nothing; the counters. A raise
inside one session's scan is that session's pass-crash row: the other sessions place, the rows its unfinished scan
queued are dropped and theirs are kept, it is not recorded, and the tiers after the courier run (CourierScanCrash).

Synthetic fixtures only: placeholder sids, invented text, hostname TESTHOST; a temp root per test."""
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
jd = load_source("romp_judge_courier_skip", os.path.join(BIN, "romp-judge"))

A = "11111111-2222-3333-4444-777777777701"
B = "11111111-2222-3333-4444-777777777702"
C = "11111111-2222-3333-4444-777777777703"
SENDER = "aaaaaaaa-bbbb-cccc-dddd-777777777700"
T0 = 1781100000
DELEGATING = '{"verdict": "delegating", "goal": 0, "text": "check the subnet layout"}'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class _World(unittest.TestCase):
    SIDS = (A, B, C)

    def setUp(self):
        self._rooted_saved = jd._delegate_user_rooted
        jd._delegate_user_rooted = lambda *a, **k: True       # chain-rooted minting is orthogonal here
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.GOALARCHDIR, jd.PCACHE,
                      jd.MESSAGES, jd.ERRORS, jd.courier_llm)
        jd.courier_llm = lambda *a, **k: DELEGATING
        self._make_world()

    def tearDown(self):
        self._drop_world()
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.CAPDIR, jd.ARCHDIR, jd.GOALARCHDIR, jd.PCACHE,
         jd.MESSAGES, jd.ERRORS, jd.courier_llm) = self.saved
        jd._delegate_user_rooted = self._rooted_saved

    def _make_world(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        munged = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        self.proj_dir = proj / munged
        self.proj_dir.mkdir(parents=True)
        names = td / "names"; names.mkdir()
        for i, sid in enumerate(self.SIDS):
            (names / sid).write_text("worker%d\t%s\t#abcdef\n" % (i, str(cdir)))
        tl = td / "timeline"; tl.mkdir()
        jd.NAMES, jd.PROJECTS = names, proj
        jd.GOALDIR = td / "goals"
        jd.CAPDIR, jd.ARCHDIR, jd.PCACHE = td / "captions", td / "archive", td / "pcache"
        jd.GOALARCHDIR = td / "goals-archive"           # the goal archive, one of the skip key's inputs, under this root too
        jd.MESSAGES = tl / "messages.jsonl"
        jd.ERRORS = td / "judge-errors.jsonl"
        jd.MESSAGES.write_text("")
        self.recs = {sid: [] for sid in self.SIDS}
        self.mid_of = {sid: [] for sid in self.SIDS}   # the message ids delivered to each session, in order
        self.mids = 0
        self._reset_memos()
        jd._COURIER_SEEN.clear()
        for k in jd._COURIER_STATS:
            jd._COURIER_STATS[k] = 0
        for sid in self.SIDS:
            self.deliver(sid, T0 + 10 * self.SIDS.index(sid))

    def _drop_world(self):
        for sid in self.SIDS:
            try:
                (jd.EPIDIR / (sid + ".jsonl")).unlink()
            except OSError:
                pass
            try:
                (jd._overrides_dir() / (sid + ".jsonl")).unlink()
            except OSError:
                pass
        jd._COURIER_SEEN.clear()
        self._reset_memos()
        self.td.cleanup()

    @staticmethod
    def _reset_memos():
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._episode_memo.clear()
        jd._shared_clear()

    def deliver(self, sid, t):
        """One peer message (a declared delegate) lands in `sid`'s transcript, with its postal row."""
        self.mids += 1
        mid = "%d.%05d_%05d.TESTHOST" % (T0, self.mids, self.mids)
        self.mid_of[sid].append(mid)
        with jd.MESSAGES.open("a") as f:
            f.write(json.dumps({"t": t - 5, "ev": "sent", "id": mid, "from": "sender", "from_id": SENDER,
                                "to_id": sid, "kind": "delegate",
                                "body": "check the subnet layout for box %d" % self.mids}) + "\n")
        n = len(self.recs[sid]) // 2 + 1
        parent = self.recs[sid][-1]["uuid"] if self.recs[sid] else None
        self.recs[sid] += [uline(t, "check the subnet layout for box %d\n<!-- romp-msg-id: %s -->\n"
                                    "<!-- romp-msg-kind: delegate -->" % (self.mids, mid), "u%d" % n, parent),
                           aline(t + 30, "Looking at box %d now." % self.mids, "a%d" % n, "u%d" % n)]
        p = self.proj_dir / (sid + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in self.recs[sid]) + "\n")
        os.utime(p, (t + 60, t + 60))                       # a distinct mtime per write: the parse key moves
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        return mid

    def run_pass(self, now=T0 + 200):
        """One courier pass; returns the gate counters' deltas (scanned, skipped, recorded)."""
        before = jd.courier_skip_stats()
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._postal_from_memo["key"] = None
        jd.run_courier(now=now)
        after = jd.courier_skip_stats()
        return tuple(after[k] - before[k] for k in ("scanned", "skipped", "recorded"))

    def stores(self):
        return {sid: json.loads((jd.GOALDIR / (sid + ".json")).read_text()) for sid in self.SIDS}

    @staticmethod
    def shape(store):
        """The decisions a pass makes for a session: its placements and its planted peer nodes."""
        planted = sorted((nd.get("text"), (nd.get("origin") or {}).get("msgId"))
                         for nd in store["nodes"].values() if isinstance(nd.get("origin"), dict))
        return (store["placements"], planted)


class CourierSkip(_World):
    def test_unchanged_inputs_skip_after_a_scan_that_placed_nothing(self):
        self.assertEqual(self.run_pass(), (3, 0, 0), "first pass: every session scanned, rows to place, none recorded")
        placed = self.stores()
        self.assertTrue(all(any(isinstance(nd.get("origin"), dict) for nd in st["nodes"].values())
                            for st in placed.values()), "a recipient top planted in each session")
        self.assertEqual(self.run_pass(), (3, 0, 3), "second pass: scanned, nothing to place, all three recorded")
        self.assertEqual(self.run_pass(), (0, 3, 0), "third pass: nothing moved, all three skipped")
        self.assertEqual(self.run_pass(), (0, 3, 0))
        self.assertEqual(self.stores(), placed, "a skipped pass writes nothing")

    def _settled(self):
        self.run_pass(); self.run_pass()
        self.assertEqual(self.run_pass(), (0, 3, 0))

    def test_a_moved_store_un_skips_that_session_only(self):
        self._settled()
        p = jd.GOALDIR / (A + ".json")
        d = json.loads(p.read_text()); d["seq"] = d.get("seq", 0) + 1
        p.write_text(json.dumps(d))
        self.assertEqual(self.run_pass(), (1, 2, 1), "A's store moved: A scanned and re-recorded, B and C skipped")

    def test_a_journal_write_un_skips(self):
        self._settled()
        jd._overrides_dir().mkdir(parents=True, exist_ok=True)
        with (jd._overrides_dir() / (B + ".jsonl")).open("a") as f:
            f.write(json.dumps({"op": "resolve", "id": B + ":gX", "t": T0 + 150}) + "\n")
        self.assertEqual(self.run_pass(), (1, 2, 1), "B's override journal moved: B alone scanned")

    def test_a_fresh_parse_un_skips(self):
        self._settled()
        p = self.proj_dir / (C + ".jsonl")
        recs = self.recs[C] + [uline(T0 + 120, "and the gateway?", "u9", self.recs[C][-1]["uuid"]),
                               aline(T0 + 130, "10.0.0.1", "a9", "u9")]
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(p, (T0 + 190, T0 + 190))
        self.assertEqual(self.run_pass(), (1, 2, 1), "C's transcript grew: C alone scanned")

    def test_an_episode_boundary_un_skips(self):
        self._settled()
        jd.EPIDIR.mkdir(parents=True, exist_ok=True)
        (jd.EPIDIR / (A + ".jsonl")).write_text(json.dumps({"t": T0 - 100, "kind": "seed"}) + "\n"
                                                + json.dumps({"t": T0 + 150, "kind": "clear"}) + "\n")
        self.assertEqual(self.run_pass(), (1, 2, 1), "A's episode log moved: A alone scanned")

    def test_a_write_landing_during_the_scan_is_seen_next_pass(self):
        # INTERLEAVED WRITE: the key is taken before the store read. A row appended to A's journal while the
        # scan reads A's store leaves the recorded key behind the file, so the next pass scans A again.
        self.run_pass()                                   # places
        real = jd.load_goals_shared
        landed = []

        def read_then_write(fsid):
            store = real(fsid)
            if fsid == A and not landed:
                landed.append(True)
                jd._overrides_dir().mkdir(parents=True, exist_ok=True)
                with (jd._overrides_dir() / (A + ".jsonl")).open("a") as f:
                    f.write(json.dumps({"op": "resolve", "id": A + ":gX", "t": T0 + 160}) + "\n")
            return store
        jd.load_goals_shared = read_then_write
        try:
            self.assertEqual(self.run_pass(), (3, 0, 3), "the recording pass, with A's write landing mid-scan")
        finally:
            jd.load_goals_shared = real
        self.assertTrue(landed)
        self.assertEqual(self.run_pass(), (1, 2, 1), "A is scanned again: its recorded key predates the write")
        self.assertEqual(self.run_pass(), (0, 3, 0))

    def test_three_sessions_one_moved_place_exactly_what_the_ungated_pass_places(self):
        def scenario(gated):
            self.run_pass()                               # places the first three
            self.run_pass()                               # records all three
            self.deliver(B, T0 + 150)                     # B moves: a second message
            if not gated:
                jd._COURIER_SEEN.clear()                  # the ungated pass scans every session
            counts = self.run_pass(now=T0 + 300)
            return counts, {sid: self.shape(st) for sid, st in self.stores().items()}
        gated_counts, gated = scenario(True)
        self._drop_world(); self._make_world()
        ungated_counts, ungated = scenario(False)
        self.assertEqual(gated_counts, (1, 2, 0), "gated: B scanned with rows to place, A and C skipped")
        self.assertEqual(ungated_counts, (3, 0, 2), "ungated: all scanned, A and C recorded")
        self.assertEqual(gated, ungated, "the same placements and planted nodes either way")
        self.assertEqual(len(gated[B][1]), 2, "B's second delegate planted")

    def test_a_placed_delegate_without_its_link_keeps_the_session_scanned_until_the_repair_lands(self):
        # PLANNER-FIRST PLACEMENT: A's delegate segment sits under a plain top with no courier link, and no
        # sender tracks the message yet. The repair's other input is the SENDER's store (_handoff_backref),
        # outside A's key, so A is never recorded while the link is missing; once the sender's tracking node
        # exists, the next pass attaches the link through a writer load, and only then does A settle.
        path = self.proj_dir / (A + ".jsonl")
        mid_a = self.mid_of[A][0]
        session = jd.parsed_session(A, [str(path)], T0 + 200)
        fresh = jd.load_goals(A)
        seg = next(sg for tn in session["turns"] for sg in jd._segs(tn, fresh) if (jd._seg_peer(sg) or ("",))[0])
        top = {"id": A + ":g1", "text": "Look after the subnet", "parentId": None, "nodeComplete": False,
               "blocked": False, "cleared": False, "trail": [], "t": T0}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (A + ".json")).write_text(json.dumps(
            {"rompUuid": A, "seq": 1, "lastNode": top["id"], "closedTurns": [], "nodes": {top["id"]: top},
             "placements": {seg["id"]: top["id"]}, "status": {top["id"]: "working"}}))
        self._reset_memos()
        self.run_pass()                                   # B and C place; A has nothing pending but an open repair
        self.assertEqual(self.run_pass(), (3, 0, 2), "B and C recorded; A stays scanned: its link is missing")
        self.assertEqual(self.run_pass(), (1, 2, 0), "A alone, every pass, while no sender tracks the message")
        self.assertNotIn("links", jd.load_goals(A)["nodes"][top["id"]])
        # the sender's tracking node appears (a names entry and a transcript make the sender discoverable, the way
        # _handoff_backref finds sender boards; its store carries the handoff)
        (jd.NAMES / SENDER).write_text("sender\t%s\t#abcdef\n" % str(Path(self.td.name) / "launchdir"))
        (self.proj_dir / (SENDER + ".jsonl")).write_text(json.dumps(uline(T0 - 100, "hand the subnet check to worker0", "s1"))
                                                         + "\n" + json.dumps(aline(T0 - 90, "Delegated.", "s2", "s1")) + "\n")
        snd = jd.load_goals(SENDER)
        snd["nodes"][SENDER + ":g1"] = {"id": SENDER + ":g1", "text": "delegated to worker0", "parentId": None,
                                        "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                                        "t": T0, "handoff": {"peer": A, "msgId": mid_a}}
        snd["status"][SENDER + ":g1"] = "working"
        jd.save_goals(SENDER, snd)
        private, o_load = [], jd.load_goals
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        try:
            self.assertEqual(self.run_pass(), (2, 2, 1), "A and the now-discoverable sender scanned, B and C skipped; "
                                                         "the sender records, A does not yet (its store just moved)")
        finally:
            jd.load_goals = o_load
        self.assertIn(A, private, "the repair took a writer load for A")
        links = jd.load_goals(A)["nodes"][top["id"]].get("links") or []
        self.assertEqual([l.get("msgId") for l in links], [mid_a], "the link attached")
        self.assertEqual(self.run_pass(), (1, 3, 1), "A's store moved with the link: scanned once more and recorded; "
                                                     "B, C and the sender skipped")
        self.assertEqual(self.run_pass(), (0, 4, 0), "and now every session is settled")

    def test_a_delegate_filed_quiet_has_no_repair_to_wait_for_and_is_recorded(self):
        # An UNROOTED dispatch files "fyi" (the recipient gets no standalone top): the placement is not a
        # node, so no courier link can ever attach and there is no repair to keep the session scanned.
        # Exactly the worker sessions that receive team dispatches; they must settle and skip like any other.
        jd._delegate_user_rooted = lambda *a, **k: False
        self.assertEqual(self.run_pass(), (3, 0, 0), "first pass: every delegate filed quiet")
        for sid in self.SIDS:
            st = jd.load_goals(sid)
            self.assertIn("fyi", set(st["placements"].values()), "the placement is the fyi mark, not a node")
            self.assertFalse(any(isinstance(nd.get("origin"), dict) for nd in st["nodes"].values()), "no top planted")
        self.assertEqual(self.run_pass(), (3, 0, 3), "second pass: nothing to place, no repair open, all recorded")
        self.assertEqual(self.run_pass(), (0, 3, 0), "third pass: all skipped")

    def test_a_segment_placed_under_a_shifted_key_is_not_queued_and_the_session_records(self):
        # DRIFT (measured live, 2026-09-09): a peer segment whose parse t shifted after its placement was
        # recorded is no exact member of placements, so the scan queued it every pass; the placement loop
        # then loaded the store (a writer load per row per pass) and dropped the row through _placed_key
        # unwritten. Every such session stayed unrecorded, and the courier's loads per pass did not fall.
        # The scan applies the same drift-tolerant rule: no row, no load, the session records and skips.
        path = self.proj_dir / (A + ".jsonl")
        session = jd.parsed_session(A, [str(path)], T0 + 200)
        fresh = jd.load_goals(A)
        seg = next(sg for tn in session["turns"] for sg in jd._segs(tn, fresh) if (jd._seg_peer(sg) or ("",))[0])
        sid, t, texthash = seg["id"].rsplit(":", 2)
        shifted = "%s:%d:%s" % (sid, int(t) + 7, texthash)          # the same segment, recorded seven seconds later
        self.assertTrue(jd._placed_key({shifted: "x"}, seg["id"]), "premise: the drift-tolerant rule matches")
        top = {"id": A + ":g1", "text": "Look after the subnet", "parentId": None, "nodeComplete": False,
               "blocked": False, "cleared": False, "trail": [], "t": T0,
               "origin": {"peer": SENDER, "goalId": SENDER + ":g1", "msgId": self.mid_of[A][0]}}
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (A + ".json")).write_text(json.dumps(
            {"rompUuid": A, "seq": 1, "lastNode": top["id"], "closedTurns": [], "nodes": {top["id"]: top},
             "placements": {shifted: top["id"]}, "status": {top["id"]: "working"}}))
        self._reset_memos()
        private, o_load = [], jd.load_goals
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        try:
            self.assertEqual(self.run_pass(), (3, 0, 1), "A records at once: nothing queued; B and C place")
        finally:
            jd.load_goals = o_load
        self.assertNotIn(A, private, "no writer load for A: its segment never reached the placement loop")
        self.assertEqual(self.run_pass(), (2, 1, 2), "A skipped; B and C record")
        self.assertEqual(self.run_pass(), (0, 3, 0))
        self.assertEqual(json.loads((jd.GOALDIR / (A + ".json")).read_text())["placements"], {shifted: top["id"]},
                         "A's store untouched: the drifted placement stands as recorded")

    def test_a_parse_the_cache_does_not_hold_is_never_skipped(self):
        real = jd.parsed_session
        jd.parsed_session = lambda fsid, paths, now: dict(real(fsid, paths, now))   # a copy: not the cache's object
        try:
            self.run_pass(); self.run_pass()
            self.assertEqual(self.run_pass(), (3, 0, 0), "unkeyed: scanned every pass, never recorded")
        finally:
            jd.parsed_session = real

    def test_the_scan_asks_the_writers_loader_for_nothing(self):
        self.run_pass()                                   # places (writers, legitimately)
        private, o_load = [], jd.load_goals
        jd.load_goals = lambda fsid: (private.append(fsid), o_load(fsid))[1]
        try:
            self.assertEqual(self.run_pass(), (3, 0, 3))
        finally:
            jd.load_goals = o_load
        self.assertEqual(private, [], "a scan with nothing to place reads only the view")

    def test_the_counters(self):
        self.assertEqual(set(jd.courier_skip_stats()), {"skipped", "scanned", "recorded"})
        s = jd.courier_skip_stats(); s["skipped"] = 99
        self.assertNotEqual(jd.courier_skip_stats()["skipped"], 99, "a copy")

    def test_a_rebound_root_forgets_the_seen_keys(self):
        self._settled()
        self.assertTrue(jd._COURIER_SEEN)
        saved = jd.STATE
        other = tempfile.TemporaryDirectory()
        try:
            jd._rebind_state(Path(other.name))
            self.assertEqual(jd._COURIER_SEEN, {})
        finally:
            jd._rebind_state(saved)
            other.cleanup()



class CourierScanCrash(_World):
    """A raise inside ONE session's scan is that session's pass-crash row, not the tier's (2026-09-09). The
    per-session boundary in run_courier covered the parse, the store read and the link attach; the settle, the
    episode floor and the segment walk ran outside it, so one poisoned session left run_courier before the
    write loop (no other session placed), left run_triage before the propagate, group, consolidate and distill
    passes, and left only a stderr traceback. Pins: the other sessions place in the crashing pass; one row for
    the crashed session with the scan note; the rows its half-walked scan queued are dropped, and only those;
    it is not recorded (scanned again next pass, placed once the poison lifts), and a record from an earlier
    scan is dropped; the tiers after the courier run.

    The poisoned session is C: discover walks the names directory sorted, so A's and B's scans run first and
    their rows are on the pending list when C's scan raises. Each poison records the order it saw and the
    assertions check that premise. With the FIRST session poisoned, dropping the crashed session's rows and
    dropping every session's rows are indistinguishable, and the second is the fault this boundary removes."""

    def _crash_rows(self):
        text = jd.ERRORS.read_text() if jd.ERRORS.exists() else ""
        return [r for r in (json.loads(l) for l in text.splitlines() if l.strip()) if r.get("err") == "pass-crash"]

    @staticmethod
    def _planted(store):
        return [nd for nd in store["nodes"].values() if isinstance(nd.get("origin"), dict)]

    def _poisoned_pass(self, attr, poison, now=T0 + 200):
        """One pass with jd.<attr> replaced by `poison`; restored whatever the pass does."""
        real = getattr(jd, attr)
        setattr(jd, attr, poison)
        try:
            return self.run_pass(now=now)
        finally:
            setattr(jd, attr, real)

    def _assert_c_scanned_after_a_and_b(self, order):
        self.assertEqual(list(dict.fromkeys(order)), [A, B, C],
                         "premise: C's scan follows A's and B's, so their rows are queued when it raises")

    def _assert_row_and_recovery(self, counts, order, planted_c):
        self._assert_c_scanned_after_a_and_b(order)
        self.assertEqual(counts, (3, 0, 0), "every session scanned; A and B have rows to place, C's scan died")
        self.assertTrue(self._planted(jd.load_goals(A)) and self._planted(jd.load_goals(B)),
                        "A and B placed in the pass whose scan of C raised: only C's queued rows went with the crash")
        self.assertEqual(self._planted(jd.load_goals(C)), [], "nothing placed from C's unfinished scan")
        rows = self._crash_rows()
        self.assertEqual([(r["judge"], r["fsid"]) for r in rows], [("courier", C)], "one row, C's")
        self.assertTrue(rows[0]["note"].startswith("scan: RuntimeError("), rows[0]["note"])
        self.assertNotIn(C, jd._COURIER_SEEN, "a scan that raised is not recorded: C is scanned again next pass")
        # the poison lifted: C places on the next pass and settles like the others
        self.assertEqual(self.run_pass(), (3, 0, 2), "C scanned with rows to place, A and B recorded")
        self.assertEqual(len(self._planted(jd.load_goals(C))), planted_c, "C's delegates planted once its scan completes")
        self.assertEqual(self.run_pass(), (1, 2, 1))
        self.assertEqual(self.run_pass(), (0, 3, 0))
        self.assertEqual(len(self._crash_rows()), 1, "no further rows once the scan completes")

    def test_a_raise_in_the_segment_walk_is_that_sessions_row_and_the_others_place(self):
        # C has TWO delegates and the walk raises on the second turn: the row the first turn queued must go
        # with the crash (or the write loop places from a walk that never finished, and C, never recorded,
        # re-attempts the rest every pass while the poison lasts), and A's and B's rows must stay.
        self.deliver(C, T0 + 100)
        real, order = jd._segs, []

        def poison(turn, store):
            order.append(store.get("rompUuid"))
            if order.count(C) == 2:
                raise RuntimeError("seam walk failed")
            return real(turn, store)
        counts = self._poisoned_pass("_segs", poison)
        self.assertEqual(order.count(C), 2, "premise: C's first turn walked, the second raised")
        self._assert_row_and_recovery(counts, order, planted_c=2)

    def test_a_raise_in_the_episode_floor_is_that_sessions_row_and_the_others_place(self):
        real, order = jd.episode_floor, []

        def poison(sid):
            order.append(sid)
            if sid == C:
                raise RuntimeError("episode log unreadable")
            return real(sid)
        self._assert_row_and_recovery(self._poisoned_pass("episode_floor", poison), order, planted_c=1)

    def test_a_raise_in_the_settle_is_that_sessions_row_and_the_others_place(self):
        # The settle reads the background-hold state (_awaiting_bg_hold, _bg_unresolved) and a raise there
        # propagates by design (_bg_expiry_key's docstring). The planner files it as the session's row; so
        # does the courier now.
        real, order = jd._session_settled, []

        def poison(fsid, path, session, store, now=None):
            order.append(fsid)
            if fsid == C:
                raise RuntimeError("background hold unreadable")
            return real(fsid, path, session, store, now)
        self._assert_row_and_recovery(self._poisoned_pass("_session_settled", poison), order, planted_c=1)

    def test_a_recorded_session_whose_rescan_raised_holds_no_stale_record(self):
        # C is recorded, a delivery moves its inputs, and the rescan raises. The record says C's last scan
        # found nothing to place, which is no longer so: it is dropped, as the rows-to-place arm drops it,
        # rather than kept under a key a later pass could match.
        self.run_pass(); self.run_pass()
        self.assertEqual(self.run_pass(), (0, 3, 0), "premise: all three recorded")
        self.assertIn(C, jd._COURIER_SEEN)
        self.deliver(C, T0 + 150)
        real = jd.episode_floor

        def poison(sid):
            if sid == C:
                raise RuntimeError("episode log unreadable")
            return real(sid)
        self.assertEqual(self._poisoned_pass("episode_floor", poison, now=T0 + 300), (1, 2, 0),
                         "C alone scanned (its inputs moved), and its scan died")
        self.assertNotIn(C, jd._COURIER_SEEN, "the earlier record is dropped, not kept under its stale key")
        self.assertEqual([(r["judge"], r["fsid"]) for r in self._crash_rows()], [("courier", C)])
        self.assertEqual(self.run_pass(now=T0 + 300), (1, 2, 0), "the poison lifted: C places its second delegate")
        self.assertEqual(len(self._planted(jd.load_goals(C))), 2)
        self.assertEqual(self.run_pass(now=T0 + 300), (1, 2, 1))
        self.assertEqual(self.run_pass(now=T0 + 300), (0, 3, 0))

    def test_the_tiers_after_the_courier_run_in_the_crashing_pass(self):
        # run_triage is one try/finally around the tier sequence: a raise leaving run_courier skipped the
        # propagate, group, consolidate and distill passes for every session until the poison lifted.
        ran, tiers = [], ("run_rewound_reconcile", "run_plan", "run_close", "run_unblock", "run_propagate",
                          "run_group", "run_consolidate", "run_distill")
        saved = {t: getattr(jd, t) for t in tiers}
        for t in tiers:
            setattr(jd, t, (lambda name: lambda *a, **k: ran.append(name))(t))
        real, order = jd._segs, []

        def poison(turn, store):
            order.append(store.get("rompUuid"))
            if order[-1] == C:
                raise RuntimeError("seam walk failed")
            return real(turn, store)
        jd._segs = poison
        try:
            jd._discover_cache["fp"] = None
            jd._discover_cache["result"] = None
            jd._postal_from_memo["key"] = None
            jd.run_triage(now=T0 + 200)
        finally:
            jd._segs = real
            for t, fn in saved.items():
                setattr(jd, t, fn)
        self._assert_c_scanned_after_a_and_b(order)
        self.assertIn("run_propagate", ran, "the tier after the courier ran in the crashing pass")
        after = (["run_propagate"] + (["run_group"] if jd.GROUPER_ON else []) + (["run_consolidate"] if jd.CONSOLIDATE_ON else [])
                 + (["run_distill"] if jd.DISTILLER_ON else []))
        self.assertEqual(ran[ran.index("run_propagate"):], after, "every tier after the courier ran, in order")
        self.assertTrue(self._planted(jd.load_goals(A)) and self._planted(jd.load_goals(B)), "A and B placed")
        self.assertEqual([(r["judge"], r["fsid"]) for r in self._crash_rows()], [("courier", C)])
        self.assertNotIn(C, jd._COURIER_SEEN)


if __name__ == "__main__":
    unittest.main()
