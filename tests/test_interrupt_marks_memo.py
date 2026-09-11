#!/usr/bin/env python3
"""_interrupt_marks on the pusher thread: the author gate and the memo.

The interrupt tick, the nudge tick and build_feed's badge each tallied a session's user atoms every
cycle: a flatten, then a per-atom is_interrupt_record text join (most of the cost, and most user
records are tool_result-only harness lines that can never be an interrupt record or a human prompt).

The gate drops the atoms the file adapter marked text-less (author None) before the text scan; exact,
because author_of returns None on no other shape. The memo keys on the parse object's identity plus
the machineCut stamp, per (sid, parse family).

Synthetic fixtures only: placeholder UUIDs, invented prompt text, hostname-free paths.
"""
import json
import os
import tempfile
import threading
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
em = load_source("romp_event_model_imm", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_imm", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_imm", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_imm", os.path.join(BIN, "romp_sdk_backend.py"))
jd = km.jd

NOW = 1781100000
# A sid of this module's own (the goal-store fixture rule, 2026-08-24): the override journal is
# per-sid and process-shared across every kernel test copy.
SID = "11111111-2222-3333-4444-777777777777"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def uatom(t, text, author="human"):
    return {"type": "user", "t": t, "author": author, "message": {"role": "user", "content": text}}


def intr(t):
    return uatom(t, "[Request interrupted by user]", author="human")


def tool_result_atom(t):
    """The file adapter's shape for a tool_result-only harness line: author None, no text block."""
    return {"type": "user", "t": t, "author": None,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                     "content": "ok"}]}}


class _Counter:
    """Wrap a module attribute with a call counter; restore on close."""

    def __init__(self, mod, name):
        self.mod, self.name, self.orig, self.calls = mod, name, getattr(mod, name), []

        def wrapped(*a, **k):
            self.calls.append((a, k))
            return self.orig(*a, **k)
        setattr(mod, name, wrapped)

    def close(self):
        setattr(self.mod, self.name, self.orig)


class AuthorNoneAtomsAreSkipped(unittest.TestCase):
    """A user atom carrying author None (author_of's answer for a text-less record) is dropped before
    the text scan; no tally changes. The file adapter OMITS the key for that answer rather than writing
    it (pinned below), so the gate's reach on disk-parsed sessions is nil and a key-less atom is still
    scanned: the SDK live tail carries texty user atoms without the key."""

    def test_a_text_less_author_none_atom_never_changes_the_marks(self):
        base = [uatom(T0, "wire the thing"), intr(T0 + 60)]
        with_tr = [uatom(T0, "wire the thing"), tool_result_atom(T0 + 30), intr(T0 + 60),
                   tool_result_atom(T0 + 70), tool_result_atom(T0 + 80)]
        self.assertEqual(km._interrupt_marks_atoms(with_tr), km._interrupt_marks_atoms(base))
        # …and past the stop: a genuine stop with only harness lines after it is still the user's
        self.assertEqual(km._interrupt_marks_atoms(with_tr), (T0 + 60, T0))

    def test_a_text_less_atom_between_the_stop_and_the_notice_stays_wedge(self):
        # the forward scan read such an atom as wedge before; dropping it reads the same notice
        atoms = [uatom(T0, "wire the thing"), intr(T0 + 60), tool_result_atom(T0 + 61),
                 uatom(T0 + 62, sb.BOOT_RESUME_NUDGE, "romp")]
        self.assertEqual(km._interrupt_marks_atoms(atoms), (0, T0),
                         "the machine cut is still named by the notice past the harness line")
        atoms = [uatom(T0, "wire the thing"), intr(T0 + 60), tool_result_atom(T0 + 61)]
        self.assertEqual(km._interrupt_marks_atoms(atoms, cut_t=T0 + 64, cut_cause="restart"), (0, T0),
                         "the stamp still settles an inconclusive scan that ends in harness lines")

    def test_a_tool_result_only_atom_is_never_text_scanned(self):
        c = _Counter(km.em, "is_interrupt_record")
        try:
            atoms = [uatom(T0, "wire the thing")] + [tool_result_atom(T0 + i) for i in range(1, 40)] + \
                    [intr(T0 + 60), tool_result_atom(T0 + 61)]
            km._interrupt_marks_atoms(atoms)
            self.assertEqual(len(c.calls), 2, "only the two authored atoms reach is_interrupt_record")
        finally:
            c.close()

    def test_an_atom_without_an_author_key_is_still_classified(self):
        # an SDK live-tail atom (msg_to_atom) carries no author key at all and may carry text; a merged
        # live interrupt record must count the moment it is merged, so absence is never a skip
        live_stop = {"type": "user", "t": T0 + 90,
                     "message": {"role": "user", "content": "[Request interrupted by user]"}}
        atoms = [uatom(T0, "wire the thing"), live_stop]
        self.assertEqual(km._interrupt_marks_atoms(atoms), (T0 + 90, T0))

    def test_the_file_adapter_omits_the_key_for_a_text_less_record(self):
        # the premise the docstring states: a tool_result-only line parses to a user atom with NO
        # author key (not author None), so the gate above meets nothing on a disk-parsed session
        td = tempfile.mkdtemp()
        path = os.path.join(td, SID + ".jsonl")
        recs = [uline(T0, "wire the thing", "u1"),
                {"type": "assistant", "timestamp": iso(T0 + 5), "uuid": "a1", "parentUuid": "u1",
                 "message": {"role": "assistant", "stop_reason": "tool_use",
                             "content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {}}]}},
                {"type": "user", "timestamp": iso(T0 + 6), "uuid": "u2", "parentUuid": "a1",
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                          "content": "ok"}]}}]
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        try:
            sess = em.parse_session(path, rompuuid=SID, now=NOW)
        finally:
            os.remove(path); os.rmdir(td)
        users = [a for t in sess["turns"] for a in t.get("atoms") or [] if a.get("type") == "user"]
        by_uuid = {a.get("uuid"): a for a in users}
        self.assertIn("u2", by_uuid, "the tool_result-only line is a user atom of its own")
        self.assertNotIn("author", by_uuid["u2"], "…with the author key omitted, not None")
        self.assertEqual(by_uuid["u1"].get("author"), "human")

    def test_author_none_means_no_text_in_the_file_adapter(self):
        # the exactness claim, checked against author_of itself: across every promptSource the
        # adapter sees, a None author is returned ONLY for a content list with no text block
        texty = [{"type": "text", "text": "hello there"}]
        stop = [{"type": "text", "text": "[Request interrupted by user]"}]
        tr_only = [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}]
        for blocks in (texty, stop, tr_only, []):
            for ps in (None, "typed", "queued", "sdk", "system"):
                for sdk_human in (False, True):
                    a = em.author_of(blocks, ps, {}, sdk_human)
                    if a is None:
                        self.assertEqual(em._text_of(blocks), "",
                                         "author None with text present would make the gate inexact (%r %r)" % (blocks, ps))
                    if em._text_of(blocks):
                        self.assertIsNotNone(a, "every branch on a non-empty text returns an author (%r)" % ps)


class _MemoHarness(unittest.TestCase):
    """A real transcript with a genuine stop, a goal store, a names entry and a rebound judge state
    (jd._rebind_state, so STATESDIR follows and a states append re-keys jd.parsed_session — a fixture
    that rebinds jd.STATE alone never exercises the new-object case)."""

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
        self._reset()
        km._write_auto_nudge({"enabled": True, "nudged": {}, "intrBlocked": {}})
        recs = [uline(T0, "wire up the reconnect banner", "u1"),
                aline(T0 + 20, "on it", "a1", "u1", "tool_use"),
                uline(T0 + 60, "[Request interrupted by user]", "u2", "a1")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        g = {"id": SID + ":g1", "text": "Ship the reconnect banner", "parentId": None, "nodeComplete": False,
             "blocked": False, "cleared": False, "trail": [], "t": T0}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": 1, "lastNode": g["id"], "closedTurns": [], "nodes": {g["id"]: g},
             "placements": {}, "status": {g["id"]: "working"}}))
        self.live = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}

    def _reset(self):
        km._parse_cache.clear()
        km._machine_cut_cache.clear()
        km._autonudge_cache.clear()
        km._pending_ops.clear()
        km._intr_marks_memo.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        (jd.STATE, jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATESDIR, km.NAMES, jd.CLOSER_ON) = self.saved
        self._reset()
        self.td.cleanup()

    def _judge_turns(self):
        return jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]

    def _stamp(self, t, cause="restart"):
        with open(jd.STATESDIR / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": t, "machineCut": cause}) + "\n")

    @staticmethod
    def _stats():
        return dict(km._intr_marks_memo_stats)


class MemoHitsAndMisses(_MemoHarness):
    def test_two_calls_on_the_same_turns_scan_once(self):
        turns = self._judge_turns()
        c = _Counter(km.em, "is_interrupt_record")
        try:
            first = km._interrupt_marks(turns, SID, family="judge")
            n = len(c.calls)
            self.assertGreater(n, 0, "the first call scanned the atoms")
            s0 = self._stats()
            self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), first)
            self.assertEqual(len(c.calls), n, "the same list object with the same stamp: no scan")
            self.assertEqual(self._stats()["hit"], s0["hit"] + 1)
            self.assertEqual(self._stats()["miss"], s0["miss"])
        finally:
            c.close()
        self.assertEqual(first, (T0 + 60, T0), "a genuine stop, no notice, no stamp")

    def test_a_new_list_recomputes(self):
        turns = self._judge_turns()
        km._interrupt_marks(turns, SID, family="judge")
        s0 = self._stats()
        self.assertEqual(km._interrupt_marks(list(turns), SID, family="judge"), (T0 + 60, T0))
        self.assertEqual(self._stats()["miss"], s0["miss"] + 1, "equal content, new identity → recompute")

    def test_a_moved_machine_cut_stamp_recomputes_on_the_same_object(self):
        turns = self._judge_turns()
        self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), (T0 + 60, T0))
        s0 = self._stats()
        self._stamp(T0 + 64)                            # the backend's own record: that stop was romp's cut
        self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), (0, T0),
                         "the SAME turns object, a moved stamp → recomputed, and the stop is now the cut's")
        self.assertEqual(self._stats()["miss"], s0["miss"] + 1)

    def test_a_new_parse_of_an_appended_transcript_is_a_new_object(self):
        # the identity key's standing assumption: the parsers allocate fresh lists, never extend in place
        t1 = self._judge_turns()
        d1 = km._parse(str(self.tpath), SID, NOW)["turns"]
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(T0 + 200, "keep going with plan B", "u3", "u2")) + "\n")
        self.assertIsNot(self._judge_turns(), t1, "jd.parsed_session re-keyed on the transcript append")
        self.assertIsNot(km._parse(str(self.tpath), SID, NOW)["turns"], d1, "…and so did the kernel parse")
        # …and a states append re-keys the judge parse too (STATESDIR is rebound in this harness)
        t2 = self._judge_turns()
        self._stamp(T0 + 64)
        self.assertIsNot(self._judge_turns(), t2, "a states append is a new parse object")

    def test_results_equal_the_unmemoized_function_on_the_fixtures(self):
        cases = [
            [uatom(T0, "wire the thing"), intr(T0 + 60), uatom(T0 + 61, sb.BOOT_RESUME_NUDGE, "romp")],
            [uatom(T0, "wire the thing"), intr(T0 + 60)],
            [uatom(T0, "wire the thing"), intr(T0 + 10), uatom(T0 + 11, sb.BOOT_RESUME_NUDGE, "romp"),
             intr(T0 + 30)],
            [uatom(T0, "wire the thing"), uatom(T0 + 60, "[Request interrupted by user for tool use]"),
             intr(T0 + 63), uatom(T0 + 80, sb.BOOT_RESUME_NUDGE, "romp")],
            [uatom(T0, "wire the thing"), uatom(T0 + 60, "[Request interrupted by user for tool use]"),
             intr(T0 + 63)],
        ]
        for atoms in cases:
            turns = [{"atoms": atoms}]
            pure = km._interrupt_marks_atoms(atoms)
            self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), pure)
            self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), pure, "the hit answers the same")
        # with the stamp on disk the memoized read equals the pure read GIVEN that stamp
        self._stamp(T0 + 64)
        atoms = cases[4]
        turns = [{"atoms": atoms}]
        self.assertEqual(km._interrupt_marks(turns, SID, family="judge"),
                         km._interrupt_marks_atoms(atoms, cut_t=T0 + 64, cut_cause="restart"))
        self.assertEqual(km._interrupt_marks(turns, SID, family="judge"), (0, T0))

    def test_at_most_one_entry_per_sid_and_family(self):
        turns = self._judge_turns()
        for _ in range(5):
            km._interrupt_marks(list(turns), SID, family="judge")
            km._interrupt_marks(list(turns), SID, family="display")
        keys = [k for k in km._intr_marks_memo if k[0] == SID]
        self.assertEqual(sorted(keys), [(SID, "display"), (SID, "judge")])

    def test_sid_less_and_family_less_calls_bypass_the_memo(self):
        turns = self._judge_turns()
        km._interrupt_marks(turns, "", family="judge")
        km._interrupt_marks(turns, SID)
        km._interrupt_marks(turns)
        km._interrupt_suppresses_nudge(turns, SID)
        self.assertEqual(km._intr_marks_memo, {}, "no family or no sid: computed, never stored")


class MemoAcrossTheCycle(_MemoHarness):
    def test_the_tick_and_build_feed_hit_on_the_second_cycle(self):
        km._parse(str(self.tpath), SID, NOW)                       # warm the display cache (_warm_fleet_bg)
        km._interrupt_block_tick(NOW, self.live)
        km.build_feed(NOW, self.live)
        self.assertEqual(sorted(k for k in km._intr_marks_memo if k[0] == SID),
                         [(SID, "display"), (SID, "judge")], "one entry per family after a cycle")
        s0 = self._stats()
        km._interrupt_block_tick(NOW, self.live)
        km.build_feed(NOW, self.live)
        s1 = self._stats()
        self.assertEqual(s1["miss"], s0["miss"], "the second cycle recomputes nothing")
        self.assertGreaterEqual(s1["hit"] - s0["hit"], 2, "the tick hit and build_feed hit")

    def test_the_families_do_not_evict_each_other(self):
        km._parse(str(self.tpath), SID, NOW)
        km._interrupt_block_tick(NOW, self.live)
        km.build_feed(NOW, self.live)
        judge = km._intr_marks_memo[(SID, "judge")]
        disp = km._intr_marks_memo[(SID, "display")]
        self.assertIs(judge[0], disp[0], "ONE parse object since stage 2 (the kernel and the judges share the store); still one slot per family")
        km._interrupt_block_tick(NOW, self.live)
        self.assertIs(km._intr_marks_memo[(SID, "display")], disp, "the tick left the display slot alone")
        km.build_feed(NOW, self.live)
        self.assertIs(km._intr_marks_memo[(SID, "judge")], judge, "build_feed left the judge slot alone")

    def test_a_sid_leaving_the_alive_set_loses_its_entries(self):
        km._parse(str(self.tpath), SID, NOW)
        km._interrupt_block_tick(NOW, self.live)
        km.build_feed(NOW, self.live)
        dead = ("11111111-2222-3333-4444-888888888888", "judge")
        km._intr_marks_memo[dead] = ([], (0.0, ""), (0, 0))
        km._interrupt_block_tick(NOW, self.live)
        self.assertNotIn(dead, km._intr_marks_memo, "a sid outside the alive set is swept")
        self.assertIn((SID, "judge"), km._intr_marks_memo, "…the alive one stays")
        s0 = self._stats()
        km._interrupt_block_tick(NOW, {})               # an empty live map is authoritative: nobody is alive
        self.assertEqual([k for k in km._intr_marks_memo if k[0] == SID], [],
                         "the session left the alive set: both of its entries are released")
        self.assertEqual(self._stats()["evict"], s0["evict"] + 2)

    def test_the_nudge_tick_reads_the_judge_entry_without_a_scan(self):
        # the third per-cycle caller: the nudge tick's interrupt gate reads the judge parse through the memo, so after
        # the interrupt tick has filled the (sid, "judge") entry the nudge tick text-scans no atom
        km._interrupt_block_tick(NOW, self.live)
        self.assertIn((SID, "judge"), km._intr_marks_memo)
        row = next(r for r in km._alive_sessions(NOW, self.live) if r["sid"] == SID)
        s0 = self._stats()
        c = _Counter(km.em, "is_interrupt_record")
        try:
            verdict = km._auto_nudge_session(row, NOW, self.live, {}, {}, alive_ids={SID})
        finally:
            c.close()
        self.assertEqual(verdict, "user-interrupt", "the gate ruled on the interrupt")
        self.assertEqual(len(c.calls), 0, "the nudge tick read the judge entry: no atom was text-scanned")
        s1 = self._stats()
        self.assertEqual(s1["hit"], s0["hit"] + 1); self.assertEqual(s1["miss"], s0["miss"])
        self.assertEqual(sorted(k for k in km._intr_marks_memo if k[0] == SID), [(SID, "judge")], "no second entry was minted")

    def test_the_cap_clears_the_memo(self):
        saved = km._INTR_MARKS_MEMO_MAX
        km._INTR_MARKS_MEMO_MAX = 4
        try:
            for i in range(4):
                km._intr_marks_memo[("11111111-2222-3333-4444-%012d" % i, "judge")] = ([], (0.0, ""), (0, 0))
            s0 = self._stats()
            km._interrupt_marks(self._judge_turns(), SID, family="judge")
            self.assertEqual(list(km._intr_marks_memo), [(SID, "judge")], "at the cap the memo is cleared whole")
            self.assertEqual(self._stats()["evict"], s0["evict"] + 4)
        finally:
            km._INTR_MARKS_MEMO_MAX = saved


class CounterBumpsAreAtomic(unittest.TestCase):
    """The memo's counters are bumped from the pusher thread and from connect-time builds on socket threads at once,
    so they go through _intr_marks_bump under a lock; a bare `+= 1` is a read-modify-write that loses increments.
    Under the GIL alone the loss is rare, so a dict whose reads yield the thread forces the interleaving."""

    def test_concurrent_bumps_lose_nothing(self):
        class _Yielding(dict):
            def get(self, k, d=None):
                v = dict.get(self, k, d); time.sleep(0); return v

            def __getitem__(self, k):
                v = dict.__getitem__(self, k); time.sleep(0); return v
        saved = km._intr_marks_memo_stats
        km._intr_marks_memo_stats = _Yielding(saved)
        try:
            s0 = km._intr_marks_memo_stats["hit"]

            def run():
                for _ in range(300):
                    km._intr_marks_bump("hit")
            ts = [threading.Thread(target=run, daemon=True) for _ in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
            self.assertFalse(any(t.is_alive() for t in ts))
            self.assertEqual(km._intr_marks_memo_stats["hit"] - s0, 8 * 300)
        finally:
            km._intr_marks_memo_stats = saved


if __name__ == "__main__":
    unittest.main()
