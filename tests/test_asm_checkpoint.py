#!/usr/bin/env python3
"""T323 stage 4a (2026-09-11): the assembly checkpoint. A document per leaf transcript records everything before the
cut (the turn holding the last compaction boundary) as identities and record locations, never bodies, plus the
carried emit state and the pre-cut graph facts; a fresh process verifies it, rebuilds the pre-cut turns as lazy atoms,
reads the leaf from the cut's byte offset only, parses that tail through a seeded adapter and proves the prefix by a
hash of its ids. Pinned here over every golden scenario that holds a compaction: the restored tree, hydrated, equals
the whole parse's byte for byte (turn ids, segment ids, atom uuids and bodies); folds after the restore keep equal;
a compaction landing after the document demotes to a whole parse; a rewrite under the cut's guard, a wrong version, a
moved session and a corrupt document each fall back loudly, counted; a body read before hydration is loud; the bytes
the restore reads are the document, the cut's guard and the tail. Synthetic transcripts only (the golden builders)."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
import sys
sys.path.insert(0, HERE)
import test_event_model_golden as G   # noqa: E402  the synthetic scenario builders

SID = G.SID
NOW = G.NOW
COMPACTING = [n for n in G.SINGLE_FILE if any(r.get("subtype") == "compact_boundary" for r in G.SINGLE_FILE[n][0]())]


def _strip(tree):
    """A tree as JSON compares it: lazy scalars dropped once hydrated (the whole parse never carries them)."""
    t = json.loads(json.dumps(tree, default=lambda o: "<unserializable>"))
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        self.fresh()
        em._ASM_CKPT_STATS.update(written=0, restored=0, fallbacks={}, skipped={}, hydratedBytes=0, hydratedAtoms=0)

    def tearDown(self):
        em.set_checkpoint_dir(None)
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side: every reader entry, assembly entry, hydrated body and pending restore gone."""
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

    def write(self, name, records, states=None, sent=None):
        d = self.td / name                                      # a directory per scenario: no stale document at a reused path
        d.mkdir(exist_ok=True)
        p = d / (SID + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.states = G.IDLE_STATES if name == "idle_atom" else states
        self.sent = sent or []
        return str(p)

    def parse(self, path, modes=None):
        return em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=self.states, postal_log=self.sent, now=NOW, asm_mode_out=modes)

    def cold(self, path):
        self.fresh()
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None
        try:
            return _strip(self.parse(path))
        finally:
            em._CKPT_DIR_FN = saved

    def restored(self, path):
        """A fresh process parsing with the document in place: the tree hydrated, its mode, and the lazy count; the bytes
        the leaf cost BEFORE hydration are kept in self.read_before."""
        self.fresh()
        modes = []
        tree = self.parse(path, modes)
        self.read_before = em.read_bytes_report().get(path, 0)
        n_lazy = sum(1 for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None)
        em.hydrate(tree, SID)
        return _strip(tree), modes, n_lazy

    @staticmethod
    def after(records, dt):
        """A time past every stamp in `records` by `dt` seconds (an append must not regress the fold's timestamp gate)."""
        return max(em.parse_z(r.get("timestamp")) or 0 for r in records if r.get("timestamp")) + dt


class RestoredEqualsWhole(Harness):
    def test_every_compacting_golden_scenario_restores_identical(self):
        self.assertTrue(COMPACTING, "the golden set holds compaction scenarios")
        for name in COMPACTING:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                path = self.write(name, records(), sent=sent)
                whole = self.cold(path)
                self.fresh()
                self.parse(path)                                        # the whole parse the writer works from
                self.assertTrue(em.asm_checkpoint_write(path, SID), "a document is written: %s" % em.asm_checkpoint_stats())
                doc = json.loads(em._asm_ckpt_file(path).read_text())
                self.assertGreater(len(doc["atoms"]), 0, "the cut leaves atoms before it")
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], "the assembly came from the document: %s" % em.asm_checkpoint_stats())
                self.assertGreater(n_lazy, 0, "the pre-cut atoms were lazy before hydration")
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse")
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})
                size = os.path.getsize(path)
                self.assertLess(self.read_before, size, "the leaf was not read whole before hydration: %d of %d bytes" % (self.read_before, size))

    def test_appends_after_the_restore_fold_and_stay_equal(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = records()
        path = self.write("compaction_atom", recs)
        self.fresh(); self.parse(path); self.assertTrue(em.asm_checkpoint_write(path, SID))
        got, modes, _ = self.restored(path)
        last = recs[-1]
        t1 = self.after(recs, 100)
        more = [G.uline(t1, "and then the cap", "u_more", last.get("uuid")),
                G.aline(t1 + 10, "two minutes, as before", "a_more", "u_more", stop="end_turn")]
        with open(path, "a") as f:
            for r in more:
                f.write(json.dumps(r) + "\n")
        modes = []
        tree = self.parse(path, modes)
        em.hydrate(tree, SID)
        self.assertEqual(modes, ["fold"], "the appended records folded onto the restored entry")
        self.assertEqual(_strip(tree), self.cold(path))

    def test_a_compaction_after_the_document_demotes_to_a_whole_parse(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = records()
        path = self.write("compaction_atom", recs)
        self.fresh(); self.parse(path); self.assertTrue(em.asm_checkpoint_write(path, SID))
        self.restored(path)
        last = recs[-1]
        t1 = self.after(recs, 100)
        b = G.compact_line(t1, "b_new", last.get("uuid"))
        with open(path, "a") as f:
            f.write(json.dumps(b) + "\n")
            f.write(json.dumps(G.compact_summary_line(t1 + 1, "s_new", "b_new")) + "\n")
        modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["full"], "a new boundary in the tail demotes to a whole parse, as before")
        self.assertEqual(_strip(tree), self.cold(path))


class Fallbacks(Harness):
    def _armed(self, tag):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("compaction_atom-" + tag, records())
        self.fresh(); self.parse(path); self.assertTrue(em.asm_checkpoint_write(path, SID))
        return path

    def test_each_reason_falls_back_to_a_whole_parse_and_is_counted(self):
        for reason, spoil in (
            ("version", lambda p: em._asm_ckpt_file(p).write_text(json.dumps(dict(json.loads(em._asm_ckpt_file(p).read_text()), av=99)))),
            ("session", lambda p: em._asm_ckpt_file(p).write_text(json.dumps(dict(json.loads(em._asm_ckpt_file(p).read_text()), rompuuid="other")))),
            ("corrupt", lambda p: em._asm_ckpt_file(p).write_text("{nope")),
            ("guard", lambda p: self._rewrite_prefix(p)),
            ("identity", lambda p: self._spoil_identity(p)),
        ):
            with self.subTest(reason=reason):
                path = self._armed(reason)
                em._ASM_CKPT_STATS["fallbacks"] = {}
                spoil(path)
                whole = self.cold(path)
                got, modes, _ = self.restored(path)
                self.assertEqual(modes, ["full"], reason)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, whole)
                self.assertFalse(em._asm_ckpt_file(path).exists(), "the document that did not verify is gone")

    def _rewrite_prefix(self, path):
        """A rewrite under the cut's guard: the last pre-cut line changed and the file grown past its recorded size, so
        the size check passes and the guard bytes are what catch it."""
        lines = open(path).read().splitlines(keepends=True)
        doc = json.loads(em._asm_ckpt_file(path).read_text())
        pre_n = doc["files"][SID]["cut"][1]
        r = json.loads(lines[pre_n - 1]); r["message"] = {"role": r["message"].get("role", "user"), "content": "REWRITTEN under the guard " + "x" * 400}
        lines[pre_n - 1] = json.dumps(r) + "\n"                 # longer than before, so the size check passes and the guard decides
        lines.append(json.dumps(G.uline(self.after([json.loads(x) for x in lines], 100), "appended after the rewrite", "u_rw", None)) + "\n")
        with open(path, "w") as f:
            f.writelines(lines)

    def _spoil_identity(self, path):
        cp = em._asm_ckpt_file(path)
        d = json.loads(cp.read_text()); d["identity"] = "0" * 40; cp.write_text(json.dumps(d))


class LazyBodies(Harness):
    def test_a_body_read_before_hydration_is_loud_and_hydration_counts(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("compaction_atom", records())
        self.fresh(); self.parse(path); self.assertTrue(em.asm_checkpoint_write(path, SID))
        self.fresh()
        tree = self.parse(path)
        lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        self.assertTrue(lazy)
        with self.assertRaises(em.LazyBodyRead):
            em._text_of(em._content(lazy[0]["message"]))
        with self.assertRaises((em.LazyBodyRead, TypeError)):      # whichever the encoder trips first, a lazy body never
            json.dumps(tree)                                       #  leaves as an empty message
        n = em.hydrate(lazy, SID)
        self.assertEqual(n, len(lazy))
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedAtoms"], len([a for a in lazy]), "one read per lazy atom")
        self.assertGreater(st["hydratedBytes"], 0)
        self.assertEqual(em.hydrate(lazy, SID), 0, "nothing left to hydrate")
        json.dumps(tree)


if __name__ == "__main__":
    unittest.main()
