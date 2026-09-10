#!/usr/bin/env python3
"""The sender-board walk behind the courier's link repair (_handoff_backref) is built once per state of its
inputs and served while they stand (2026-09-09).

Every call walked every discovered session's goal store with the writer's loader, and the courier asked it
for each placed delegate whose link was missing, on every triage pass. The map now answers every message id
from one walk over the read-only view, keyed on the discover order and each sender store's file key with
its journal's and archive's, taken before the reads. Pins: built once then served; a sender store write
re-derives, including one the file clock cannot see; a journal row re-derives; a fleet change re-derives;
a write landing during the build is seen next call; the first sender in discover order wins; a completed
handoff is no backref; a sender store that cannot be read is skipped for that call through the per-session
boundary (one store-unreadable row per fault episode, the other senders answer, the map is not published
while a sender is missing from it) and any other raise out of a sender's read stays loud; a rebound root
forgets; the counters.

Synthetic sids and stores under a temp root; discover is a stub, so no transcripts are needed."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_backref_memo", os.path.join(BIN, "romp-judge"))

S1 = "11111111-2222-3333-4444-999999999901"
S2 = "11111111-2222-3333-4444-999999999902"
S3 = "11111111-2222-3333-4444-999999999903"
S4 = "11111111-2222-3333-4444-999999999904"
R1 = "11111111-2222-3333-4444-999999999911"   # recipients: their stores hold the placements the courier repairs
R2 = "11111111-2222-3333-4444-999999999912"
M1, M2, M3 = "1781100000.00001_00001.TESTHOST", "1781100000.00002_00002.TESTHOST", "1781100000.00003_00003.TESTHOST"
M4 = "1781100000.00004_00004.TESTHOST"
T0 = 1781100000


def _node(nid, text, mid=None, complete=False):
    nd = {"id": nid, "text": text, "parentId": None, "nodeComplete": complete, "blocked": False, "cleared": False,
          "trail": [], "t": T0}
    if mid:
        nd["handoff"] = {"peer": "peer", "msgId": mid}
    return nd


class _Memo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_root, self.saved_discover = jd.STATE, jd.discover
        jd._rebind_state(Path(self.td.name))
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        # three senders in discover order, so a sender AFTER a faulted one is covered by the fault tests
        self.fleet = [(S1, "/nonexistent/s1.jsonl", None, "s1"), (S2, "/nonexistent/s2.jsonl", None, "s2"),
                      (S3, "/nonexistent/s3.jsonl", None, "s3")]
        jd.discover = lambda now: list(self.fleet)
        self.write(S1, {S1 + ":g1": _node(S1 + ":g1", "delegated to worker0", M1)})
        self.write(S2, {S2 + ":g1": _node(S2 + ":g1", "delegated to worker1", M2)})
        self.write(S3, {S3 + ":g1": _node(S3 + ":g1", "delegated to worker3", M3)})
        self._reset()

    def tearDown(self):
        jd.discover = self.saved_discover
        jd._rebind_state(self.saved_root)
        self._reset()
        self.td.cleanup()

    @staticmethod
    def _reset():
        jd._BACKREF_MEMO["slot"] = None
        for k in jd._BACKREF_STATS:
            jd._BACKREF_STATS[k] = 0
        jd._shared_clear()

    def write(self, sid, nodes, mtime=None, placements=None):
        p = jd.GOALDIR / (sid + ".json")
        p.write_text(json.dumps({"rompUuid": sid, "seq": 1, "lastNode": None, "closedTurns": [], "nodes": nodes,
                                 "placements": placements or {}, "status": {k: "working" for k in nodes}}))
        if mtime is not None:
            os.utime(p, (mtime, mtime))

    def counting(self):
        """Wrap the view's loader; returns the list of sids read."""
        reads, real = [], jd.load_goals_shared
        jd.load_goals_shared = lambda fsid: (reads.append(fsid), real(fsid))[1]
        self.addCleanup(lambda: setattr(jd, "load_goals_shared", real))
        return reads

    def faulting(self, sid, exc):
        """Wrap the view's loader so `sid`'s read raises `exc` and every other read is real. The boundary
        resolves the loader's name at call time, so the patch is what it reads. The store FILE is untouched:
        its inode, mtime and size stand, so the walk's key does not move between the fault and the heal."""
        real = jd.load_goals_shared

        def loader(fsid):
            if fsid == sid:
                raise exc
            return real(fsid)
        jd.load_goals_shared = loader
        self.addCleanup(lambda: setattr(jd, "load_goals_shared", real))
        return lambda: setattr(jd, "load_goals_shared", real)

    @staticmethod
    def rows():
        """(fsid, err) of every judge-errors row filed under the temp root."""
        if not jd.ERRORS.exists():
            return []
        return [(r["fsid"], r["err"]) for r in (json.loads(l) for l in jd.ERRORS.read_text().splitlines() if l.strip())]


class BackrefMemo(_Memo):
    def test_built_once_then_served_while_the_inputs_stand(self):
        reads = self.counting()
        self.assertEqual(jd._handoff_backref(M1), (S1, S1 + ":g1"))
        self.assertEqual(jd._handoff_backref(M2), (S2, S2 + ":g1"))
        self.assertEqual(jd._handoff_backref("1781100000.00009_00009.TESTHOST"), ("", ""), "an untracked message")
        self.assertEqual(sorted(reads), sorted([S1, S2, S3]), "one walk: each sender store read once")
        self.assertEqual(jd._BACKREF_STATS, {"served": 2, "built": 1})

    def test_a_sender_store_write_re_derives_even_when_the_file_clock_does_not_move(self):
        jd._handoff_backref(M1)
        before = os.stat(jd.GOALDIR / (S2 + ".json"))
        self.write(S2, {S2 + ":g1": _node(S2 + ":g1", "delegated to worker1", M2),
                        S2 + ":g2": _node(S2 + ":g2", "delegated to worker2", M3)}, mtime=before.st_mtime)
        self.assertEqual(os.stat(jd.GOALDIR / (S2 + ".json")).st_mtime, before.st_mtime)
        self.assertEqual(jd._handoff_backref(M3), (S2, S2 + ":g2"), "the size moved: built again, the new handoff seen")
        self.assertEqual(jd._BACKREF_STATS["built"], 2)

    def test_a_journal_row_re_derives(self):
        jd._handoff_backref(M1)
        jd._overrides_dir().mkdir(parents=True, exist_ok=True)
        with (jd._overrides_dir() / (S1 + ".jsonl")).open("a") as f:
            f.write(json.dumps({"op": "resolve", "id": S1 + ":g1", "t": T0 + 10}) + "\n")
        jd._shared_clear()
        jd._handoff_backref(M1)
        self.assertEqual(jd._BACKREF_STATS["built"], 2, "the journal is a keyed input")

    def test_a_fleet_change_re_derives(self):
        jd._handoff_backref(M1)
        self.write(S4, {S4 + ":g1": _node(S4 + ":g1", "delegated to worker4", M4)})
        self.fleet.append((S4, "/nonexistent/s4.jsonl", None, "s4"))
        self.assertEqual(jd._handoff_backref(M4), (S4, S4 + ":g1"))
        self.assertEqual(jd._BACKREF_STATS["built"], 2)

    def test_a_write_landing_during_the_build_is_seen_next_call(self):
        # INTERLEAVED WRITE: the key is taken before the reads. S2 gains a handoff while the walk reads S1, so
        # the map the first call builds is cached under a key the store no longer has; the next call rebuilds.
        real = jd.load_goals_shared
        landed = []

        def read_then_write(fsid):
            st = real(fsid)
            if fsid == S1 and not landed:
                landed.append(True)
                self.write(S2, {S2 + ":g1": _node(S2 + ":g1", "delegated to worker1", M2),
                                S2 + ":g2": _node(S2 + ":g2", "delegated to worker2", M3)})
            return st
        jd.load_goals_shared = read_then_write
        try:
            first = jd._handoff_backref(M3)
        finally:
            jd.load_goals_shared = real
        self.assertTrue(landed)
        self.assertEqual(jd._BACKREF_STATS["built"], 1)
        second = jd._handoff_backref(M3)
        self.assertEqual(jd._BACKREF_STATS, {"served": 0, "built": 2}, "the write moved the key the second call took")
        self.assertEqual(second, (S2, S2 + ":g2"))
        self.assertIn(first, ((S2, S2 + ":g2"), (S3, S3 + ":g1")), "what the first walk saw, depending on the read order")

    def test_the_first_sender_in_discover_order_wins(self):
        self.write(S2, {S2 + ":g1": _node(S2 + ":g1", "delegated to worker1", M1)})   # both track M1
        self.assertEqual(jd._handoff_backref(M1), (S1, S1 + ":g1"))
        self.fleet.reverse()
        self.assertEqual(jd._handoff_backref(M1), (S2, S2 + ":g1"), "the order is part of the key and the answer")

    def test_a_completed_handoff_is_no_backref(self):
        self.write(S1, {S1 + ":g1": _node(S1 + ":g1", "delegated to worker0", M1, complete=True)})
        self.assertEqual(jd._handoff_backref(M1), ("", ""))

    def test_one_unreadable_sender_store_skips_that_sender_and_the_others_answer(self):
        """A sender store that exists and cannot be read is that SENDER's fault, contained to it: the senders
        before and after it in discover order answer, its own message is unanswered for this call, and the
        boundary files one store-unreadable row for the episode, however many lookups meet it.

        This test replaces the one that pinned the raise (the memo's first shape): one raising store failed the
        lookup for every message id and every recipient, so the courier filed a link-attach pass-crash row per
        placed unlinked delegate per pass and no link landed anywhere, while the walk the memo replaced had
        answered every sender before the fault. The per-session boundary (load_goals_shared_or_fault) is how
        every other reader of a goal store contains a fault; the walk reads through it now.

        The map is NOT published while a sender is missing from it: a fault moves no file key (a permission
        fix changes neither inode, mtime nor size), so a cached partial map would be served after the store
        reads again. The heal below restores the loader WITHOUT touching the file, so the key stands; only a
        withheld slot lets the healed sender answer."""
        restore = self.faulting(S2, OSError(13, "Permission denied"))
        self.assertEqual(jd._handoff_backref(M1), (S1, S1 + ":g1"), "the sender before the fault answers")
        self.assertEqual(jd._handoff_backref(M3), (S3, S3 + ":g1"), "the sender after the fault answers")
        self.assertEqual(jd._handoff_backref(M2), ("", ""), "the faulted sender's own message waits")
        self.assertIsNone(jd._BACKREF_MEMO["slot"], "a map missing a sender is not published")
        self.assertEqual(jd._BACKREF_STATS, {"served": 0, "built": 3}, "walked again per call while the fault lasts")
        self.assertEqual(self.rows(), [(S2, "store-unreadable")], "one row for the episode, not one per lookup")
        restore()
        self.assertEqual(jd._handoff_backref(M2), (S2, S2 + ":g1"), "healed with the key unmoved: the sender answers")
        self.assertIsNotNone(jd._BACKREF_MEMO["slot"], "a complete walk is published")
        self.assertEqual(jd._BACKREF_STATS, {"served": 0, "built": 4})
        self.assertEqual(jd._handoff_backref(M1), (S1, S1 + ":g1"))
        self.assertEqual(jd._BACKREF_STATS, {"served": 1, "built": 4}, "and served from then on")
        self.assertEqual(self.rows(), [(S2, "store-unreadable")], "the heal files nothing")

    def test_a_directory_at_a_sender_store_path_faults_that_sender_only(self):
        """The same rule under a REAL read fault, no loader patched: a directory at the store path opens and
        then raises on the read inside load_goals_shared, an OSError the boundary contains. Here the key does
        move on the heal (no regular file, then one), so this test pins the skip and the row; the patched-loader
        test above pins the withheld slot."""
        p = jd.GOALDIR / (S2 + ".json")
        os.remove(p)
        os.mkdir(p)
        self.assertEqual(jd._handoff_backref(M1), (S1, S1 + ":g1"))
        self.assertEqual(jd._handoff_backref(M3), (S3, S3 + ":g1"))
        self.assertEqual(jd._handoff_backref(M2), ("", ""))
        self.assertIsNone(jd._BACKREF_MEMO["slot"])
        self.assertEqual(self.rows(), [(S2, "store-unreadable")])
        os.rmdir(p)
        self.write(S2, {S2 + ":g1": _node(S2 + ":g1", "delegated to worker1", M2)})
        self.assertEqual(jd._handoff_backref(M2), (S2, S2 + ":g1"), "the file is back: the sender answers")
        self.assertIsNotNone(jd._BACKREF_MEMO["slot"])
        self.assertEqual(self.rows(), [(S2, "store-unreadable")], "the heal files nothing")

    def test_the_courier_gate_attaches_the_readable_senders_link_beside_a_faulted_sender(self):
        """The consequence for run_courier's link repair, through the functions it calls: two recipients each
        hold a planner-placed delegate with no courier link, one from S1 and one from S2, and S2's store is a
        directory. Before the boundary the walk raised out of _courier_link_wanted for BOTH recipients, so the
        courier filed a link-attach pass-crash row for each every pass and no link landed. Now S1's link lands
        on its recipient; S2's recipient is answered None (no attach, no raise: the courier gate keeps it
        scanned, the wait it already models for a sender that does not yet track the message); and the fault
        is one store-unreadable row for S2 across both recipients' lookups."""
        self.write(R1, {R1 + ":g1": _node(R1 + ":g1", "check the subnet")}, placements={"seg-1": R1 + ":g1"})
        self.write(R2, {R2 + ":g1": _node(R2 + ":g1", "rotate the logs")}, placements={"seg-2": R2 + ":g1"})
        p = jd.GOALDIR / (S2 + ".json")
        os.remove(p)
        os.mkdir(p)
        r1 = jd.load_goals(R1)
        self.assertEqual(jd._courier_link_wanted(r1, "seg-1", M1), (R1 + ":g1", S1, S1 + ":g1"))
        self.assertTrue(jd._attach_courier_link(r1, "seg-1", M1))
        self.assertEqual(jd.load_goals(R1)["nodes"][R1 + ":g1"]["links"],
                         [{"peer": S1, "goalId": S1 + ":g1", "msgId": M1}], "the readable sender's link landed")
        self.assertIsNone(jd._courier_link_wanted(jd.load_goals(R2), "seg-2", M2),
                          "the faulted sender's recipient waits: answered None where the walk raised before")
        self.assertEqual(self.rows(), [(S2, "store-unreadable")], "one row for the sender, none per recipient")

    def test_a_raise_that_is_not_a_read_fault_stays_loud(self):
        """The boundary contains OSError only, as every reader boundary does. Any other raise out of a sender's
        read (a journal row that parses as JSON but is not an event, a bug in a loader) is not a read fault: it
        leaves the walk as before, caches nothing, files no store-unreadable row, and reaches the courier's own
        catch instead of passing as a skipped sender."""
        self.faulting(S2, ValueError("a journal row that is not an event"))
        with self.assertRaises(ValueError):
            jd._handoff_backref(M1)
        self.assertIsNone(jd._BACKREF_MEMO["slot"], "nothing cached")
        self.assertEqual(self.rows(), [], "not a read fault: no row")
        self.assertEqual(jd._BACKREF_STATS, {"served": 0, "built": 0}, "the walk did not complete")

    def test_a_rebound_root_forgets_the_map(self):
        jd._handoff_backref(M1)
        self.assertIsNotNone(jd._BACKREF_MEMO["slot"])
        other = tempfile.TemporaryDirectory()
        try:
            jd._rebind_state(Path(other.name))
            self.assertIsNone(jd._BACKREF_MEMO["slot"])
        finally:
            jd._rebind_state(Path(self.td.name))
            other.cleanup()

    def test_the_counters_are_a_copy(self):
        s = jd.backref_memo_stats()
        self.assertEqual(set(s), {"served", "built"})
        s["built"] = 99
        self.assertNotEqual(jd.backref_memo_stats()["built"], 99)


if __name__ == "__main__":
    unittest.main()
