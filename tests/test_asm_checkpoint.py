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


def _last_uuid(recs):
    return next((r["uuid"] for r in reversed(recs) if r.get("uuid")), None)


def compacting_variant(recs, tag):
    """A golden scenario's records followed by a compaction (the CLI's shape: the boundary anchored on the last record, its
    summary child, the conversation chaining on) and two more turns: the original scenario becomes the pre-cut part, so its
    every atom kind (forks, rewinds, clears, absorbed attachments, skill atoms, command output, postal authors) is restored
    from the document and compared with the whole parse."""
    t1 = max((em.parse_z(r.get("timestamp")) or 0) for r in recs if r.get("timestamp")) + 600
    b, sm = "b_%s" % tag, "s_%s" % tag
    more = [G.compact_line(t1, b, _last_uuid(recs)),
            G.compact_summary_line(t1 + 1, sm, b),
            G.uline(t1 + 10, "after the compaction, what remains?", "u_%s_1" % tag, sm),
            G.aline(t1 + 20, "the cap and the retry budget remain", "a_%s_1" % tag, "u_%s_1" % tag, stop="end_turn"),
            G.uline(t1 + 30, "then close them out", "u_%s_2" % tag, "a_%s_1" % tag),
            G.aline(t1 + 40, "closing both", "a_%s_2" % tag, "u_%s_2" % tag, stop="end_turn")]
    return list(recs) + more


def _doc(path):
    """The leaf's assembly document, decoded (stored gzipped)."""
    import gzip
    return json.loads(gzip.decompress(em._asm_ckpt_file(path).read_bytes()))


def _write_doc(path, d):
    import gzip
    em._asm_ckpt_file(path).write_bytes(gzip.compress(json.dumps(d).encode("utf-8")))


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
                doc = _doc(path)
                self.assertGreater(len(doc["atoms"]), 0, "the cut leaves atoms before it")
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], "the assembly came from the document: %s" % em.asm_checkpoint_stats())
                self.assertGreater(n_lazy, 0, "the pre-cut atoms were lazy before hydration")
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse")
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})
                size = os.path.getsize(path)
                self.assertLess(self.read_before, size, "the leaf was not read whole before hydration: %d of %d bytes" % (self.read_before, size))

    def test_every_golden_scenario_made_to_compact_restores_identical(self):
        """Review find (F): only the three natively compacting scenarios were restored. Every single-file golden scenario
        gets a compaction appended, so its atoms (forks, rewinds, a /clear, absorbed attachments, skill atoms, command
        output, postal authors, eclipsed and broken chains) are the pre-cut part restored from a document."""
        for name in G.SINGLE_FILE:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                recs = compacting_variant(records(), name[:6])
                path = self.write("variant-" + name, recs, sent=sent)
                whole = self.cold(path)
                self.fresh(); self.parse(path)
                wrote = em.asm_checkpoint_write(path, SID)
                if not wrote:
                    self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"unsplittable": 1},
                                     "the only reason not to write is an order the cut cannot split: %s" % em.asm_checkpoint_stats())
                    em._ASM_CKPT_STATS["skipped"] = {}
                    continue
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], name)
                self.assertGreater(n_lazy, 0)
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse: %s" % name)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})

    def test_a_two_file_lineage_made_to_compact_restores_identical(self):
        """The resume-lineage scenario (two files, a recorded resume fork) with a compaction in the leaf: the prior file is
        wholly before the cut, witnessed by its stat and never read at restore."""
        d = self.td / "lineage"; d.mkdir()
        pa, pb = d / (G.FSID_A + ".jsonl"), d / (G.FSID_B + ".jsonl")
        recs_b = compacting_variant(G.scenario_resume_lineage_fileB(), "lin")
        pa.write_text("".join(json.dumps(r) + "\n" for r in G.scenario_resume_lineage_fileA()))
        pb.write_text("".join(json.dumps(r) + "\n" for r in recs_b))
        states = getattr(G, "RESUME_STATES", None)
        cands = [str(pa), str(pb)]

        def parse(modes=None):
            return em.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=cands, states=states,
                                    postal_log=[], now=NOW, asm_mode_out=modes)
        self.fresh(); saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None
        try:
            whole = _strip(parse())
        finally:
            em._CKPT_DIR_FN = saved
        self.fresh(); parse()
        self.assertTrue(em.asm_checkpoint_write(str(pb), SID), em.asm_checkpoint_stats())
        doc = _doc(str(pb))
        self.assertIn(G.FSID_A, doc["files"])
        self.fresh(); modes = []
        tree = parse(modes)
        self.assertEqual(modes, ["restore"])
        self.assertTrue(doc["files"][G.FSID_A].get("skip"), "the prior file is wholly before the cut")
        self.assertEqual(em.read_bytes_report().get(str(pa), 0), 0, "a prior file wholly before the cut is never read at restore: %s" % em.read_bytes_report())
        em.hydrate(tree, SID)                                   # hydration reads its atoms' records, in the prior file too
        self.assertGreater(em.read_bytes_report().get(str(pa), 0), 0, "hydration seeks into the prior file for its atoms")
        self.assertEqual(_strip(tree), whole)
        os.utime(pa, (NOW, NOW))                                # the prior file's stat moves: the lineage witness fails
        self.fresh(); modes = []
        tree = parse(modes)
        self.assertEqual(modes, ["full"])
        self.assertIn("lineage", em.asm_checkpoint_stats()["fallbacks"])

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
            ("version", lambda p: _write_doc(p, dict(_doc(p), av=99))),
            ("session", lambda p: _write_doc(p, dict(_doc(p), rompuuid="other"))),
            ("corrupt", lambda p: em._asm_ckpt_file(p).write_bytes(b"{nope")),
            ("guard", lambda p: self._rewrite_prefix(p)),
            ("identity", lambda p: self._spoil_identity(p)),
            ("shrunk", lambda p: self._shrink(p)),
            ("rewrite", lambda p: self._same_size_new_mtime(p)),
            ("inputs", lambda p: _write_doc(p, dict(_doc(p), cands=["/elsewhere/other.jsonl"]))),
            ("restore", lambda p: _write_doc(p, dict(_doc(p), records=[["bad"]]))),
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
        doc = _doc(path)
        pre_n = doc["files"][SID]["cut"][1]
        r = json.loads(lines[pre_n - 1]); r["message"] = {"role": r["message"].get("role", "user"), "content": "REWRITTEN under the guard " + "x" * 400}
        lines[pre_n - 1] = json.dumps(r) + "\n"                 # longer than before, so the size check passes and the guard decides
        lines.append(json.dumps(G.uline(self.after([json.loads(x) for x in lines], 100), "appended after the rewrite", "u_rw", None)) + "\n")
        with open(path, "w") as f:
            f.writelines(lines)

    def _shrink(self, path):
        cut_off = _doc(path)["files"][SID]["cut"][0]              # the file ends before the recorded cut: a shrink
        data = open(path, "rb").read()
        open(path, "wb").write(data[:max(0, cut_off - 1)])

    def _same_size_new_mtime(self, path):
        data = open(path, "rb").read()
        open(path, "wb").write(data)
        os.utime(path, (NOW + 7, NOW + 7))

    def test_the_write_valves_are_counted(self):
        """Review find (K): a document past the cap is not written; a tree whose chronological order the cut cannot split
        (a pre-cut record stamped after the tail) is not written; both counted, and the session parses whole."""
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("valves", records())
        self.fresh(); self.parse(path)
        saved = em._ASM_CKPT_CAP
        em._ASM_CKPT_CAP = 10
        try:
            self.assertFalse(em.asm_checkpoint_write(path, SID))
        finally:
            em._ASM_CKPT_CAP = saved
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("oversize"), 1)
        self.assertFalse(em._asm_ckpt_file(path).exists())
        recs = records()
        first = recs[0]; first["timestamp"] = em.datetime.fromtimestamp(G.T0 + 10 ** 6, em.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if hasattr(em, "timezone") else "2099-01-01T00:00:00.000Z"
        path2 = self.write("valves-order", recs)
        self.fresh(); self.parse(path2)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = em.asm_checkpoint_write(path2, SID)
        self.assertFalse(wrote, "a pre-cut record stamped after every tail record cannot be split off")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"unsplittable": 1})

    def _spoil_identity(self, path):
        _write_doc(path, dict(_doc(path), identity="0" * 40))


class KernelOverRestored(Harness):
    """The kernel's and the judges' body readers over a restored tree: every consumer the audit named hydrates what it
    reads, so the same answers come from the restored tree as from the whole parse, with no LazyBodyRead."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("ROMP_KERNEL_NO_OPEN", "1")
        cls.km = load_source("romp_kernel_t323s4a", os.path.join(BIN, "romp-kernel"))
        cls.jd = cls.km.jd

    def answers(self, tree):
        km, jd = self.km, self.jd
        segs = [seg for t in tree["turns"] for seg in em.segments(t)]
        store = {"placements": {}, "nodes": {}, "seq": 0}
        return {
            "anchors": [km._seg_anchors(seg["atoms"]) for seg in segs],
            "jumps": [km._seg_jump(seg["atoms"]) for seg in segs],
            "prompts": [km._seg_prompt(seg) for seg in segs],
            "lastText": [km._seg_last_text(seg["atoms"]) for seg in segs],
            "prose": [km._atom_prose_chars(a) for t in tree["turns"] for a in t["atoms"]],
            "userTexts": [km._atom_user_texts(a) for t in tree["turns"] for a in t["atoms"] if a.get("type") == "user"],
            "progress": km._open_turn_progress(tree["turns"]),
            "tasks": km._fold_tasks(tree),
            "landed": [km._turn_landed(t) for t in tree["turns"]],
            "units": [(u[0], u[1]) for u in jd.plan_units(tree, store)],
            "unitText": [jd._unit_text(seg["atoms"]) for seg in segs],
            "asstWork": [jd._has_asst_work(seg["atoms"]) for seg in segs],
            "bgHold": jd._awaiting_bg_hold(SID, "", tree, store, now=NOW),
        }

    def test_kernel_and_judge_readers_answer_the_same_over_the_restored_tree(self):
        for name in COMPACTING:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                path = self.write(name, records(), sent=sent)
                self.fresh()
                whole = self.parse(path)
                cold = json.loads(json.dumps(self.answers(whole), default=str))
                self.assertTrue(em.asm_checkpoint_write(path, SID))
                self.fresh()
                modes = []
                tree = self.parse(path, modes)
                self.assertEqual(modes, ["restore"])
                self.assertTrue(any(a.get("lazy") is not None for t in tree["turns"] for a in t["atoms"]), "lazy atoms in play")
                got = json.loads(json.dumps(self.answers(tree), default=str))   # every reader hydrated what it needed
                self.assertEqual(got, cold)
                self.assertGreater(em.asm_checkpoint_stats()["hydratedAtoms"], 0, "the readers hydrated on demand")


class EventModelReaders(Harness):
    """Review find (C): the seam split and the declared plan read bodies inside the event model itself."""

    def _restored_with_doc(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        recs = compacting_variant(records(), "seam")
        path = self.write("em-readers", recs)
        self.fresh(); whole = self.parse(path); em.asm_checkpoint_write(path, SID)
        self.fresh(); tree = self.parse(path)
        self.assertTrue(any(a.get("lazy") is not None for t in tree["turns"] for a in t["atoms"]))
        return whole, tree

    def test_a_seam_split_inside_a_pre_cut_segment_hydrates(self):
        whole, tree = self._restored_with_doc()
        for w_turn, r_turn in zip(whole["turns"], tree["turns"]):
            for w_seg, r_seg in zip(em.segments(w_turn), em.segments(r_turn)):
                if len(w_seg["atoms"]) < 2:
                    continue
                t_split = w_seg["atoms"][0]["t"]
                r_split = em.split_segment(r_seg, t_split)            # reads the tail's bodies: hydrates, never raises
                w_split = em.split_segment(w_seg, t_split)
                if r_split is None or w_split is None:
                    self.assertEqual(r_split, w_split)
                    continue
                for part in r_split:
                    em.hydrate(part["atoms"], SID)
                self.assertEqual(_strip({"turns": [dict(r_split[0]), dict(r_split[1])]}), _strip({"turns": [dict(w_split[0]), dict(w_split[1])]}),
                                 "the seam split reads the same bodies")

    def test_the_declared_plan_over_a_restored_tree_hydrates(self):
        whole, tree = self._restored_with_doc()
        self.assertEqual(em.declared_plan(tree), em.declared_plan(whole))


class ClearedSessionDocument(Harness):
    def test_the_per_file_rewound_walk_leaves_a_lineage_document_standing(self):
        """Review find (B): the one-file walk asked for the leaf's document with the leaf alone as its inputs, so a
        /cleared session's document (its inputs name the anchor too) counted an inputs fallback and was unlinked on
        every reconcile pass. The walk asks quietly and the document stands."""
        jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
        d = self.td / "cleared"; d.mkdir()
        anchor = d / (SID + ".jsonl")
        leaf_sid = "77777777-2222-4333-8444-000000000777"
        leaf = d / (leaf_sid + ".jsonl")
        anchor.write_text("".join(json.dumps(r) + "\n" for r in G.SINGLE_FILE["queued_new_turn"][0]()))
        recs = compacting_variant(G.SINGLE_FILE["compaction_atom"][0](), "clr")
        leaf.write_text("".join(json.dumps(r) + "\n" for r in recs))
        cands = [str(leaf), str(anchor)]
        self.fresh()
        em.parse_session(str(leaf), rompuuid=SID, candidate_files=cands, states=None, postal_log=[], now=NOW)
        self.assertTrue(em.asm_checkpoint_write(str(leaf), SID), em.asm_checkpoint_stats())
        em._ASM_CKPT_STATS["fallbacks"] = {}
        rewound = em.file_rewound(leaf, rompuuid=SID, sdk_human=False)
        self.assertIsInstance(rewound, set)
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {}, "a quiet ask: no fallback counted")
        self.assertTrue(em._asm_ckpt_file(str(leaf)).exists(), "the display's document stands")
        self.fresh(); modes = []
        em.parse_session(str(leaf), rompuuid=SID, candidate_files=cands, states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], "and the next parse restores from it")


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
