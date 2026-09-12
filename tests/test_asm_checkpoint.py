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
    """A tree as JSON compares it: lazy scalars dropped once hydrated (the whole parse never carries them); a restored
    tree's pre-cut turns are built into plain turns first (em.plain_tree, T323 stage 4c)."""
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)                                  # where the lazy atoms ended: a restored tree's own fact (stage 4b)
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


class Harness(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        self.states, self.sent = None, []                       # what parse() hands the parser until write() sets them
        self._trees = {}                                         # path → the tree parse() last returned (doc() writes with it)
        self.fresh()
        em._ASM_CKPT_STATS.update(written=0, restored=0, fallbacks={}, skipped={}, hydratedBytes=0, hydratedAtoms=0, hydratedBy={})

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
        tree = em.parse_session(path, rompuuid=SID, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=self.states, postal_log=self.sent, now=NOW, asm_mode_out=modes)
        self._trees[path] = tree
        return tree

    def doc(self, path):
        """The leaf's document, written from its whole entry with the tree the last parse() of that path returned (the kernel
        hands the store's live tree, which gives the document its turns section: T323 stage 4c). A test that parsed the
        path through em.parse_session itself gets a document with no turns section (the atoms-only form)."""
        return em.asm_checkpoint_write(path, SID, tree=self._trees.get(path))

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


_KM = []


def kernel_module():
    """The kernel (and through it the judge, km.jd) loaded ONCE for this module: the judge module is one object for the
    whole test process, so a second load_source of it re-executes it under every test that already holds it."""
    if not _KM:
        os.environ.setdefault("ROMP_KERNEL_NO_OPEN", "1")
        _KM.append(load_source("romp_kernel_t323s4a", os.path.join(BIN, "romp-kernel")))
    return _KM[0]


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
                self.assertTrue(self.doc(path), "a document is written: %s" % em.asm_checkpoint_stats())
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
        restored, skipped = [], {}
        for name in G.SINGLE_FILE:
            with self.subTest(scenario=name):
                records, sent = G.SINGLE_FILE[name]
                recs = compacting_variant(records(), name[:6])
                path = self.write("variant-" + name, recs, sent=sent)
                whole = self.cold(path)
                self.fresh(); self.parse(path)
                em._ASM_CKPT_STATS["skipped"] = {}
                wrote = self.doc(path)
                if not wrote:
                    skipped[name] = dict(em.asm_checkpoint_stats()["skipped"])
                    continue
                got, modes, n_lazy = self.restored(path)
                self.assertEqual(modes, ["restore"], name)
                self.assertGreater(n_lazy, 0)
                self.assertEqual(got, whole, "restored and hydrated equals the whole parse: %s" % name)
                self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {})
                restored.append(name)
        self.assertEqual(sorted(restored), ["author_kinds", "broken_chain_kept", "clear_breaks_lineage", "compaction_atom",
                                            "compaction_broken_stitch", "eclipsed_branch_kept", "idle_atom", "manual_compact_detached",
                                            "multi_input_absorbed", "popall", "queued_new_turn", "retry_superseded", "rewind_off_path",
                                            "slash_command_turn"], "every single-file golden scenario, made to compact, writes and restores")
        self.assertEqual(skipped, {}, "none is unsplittable")

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
        self.assertTrue(self.doc(str(pb)), em.asm_checkpoint_stats())
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
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
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
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
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
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
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
            self.assertFalse(self.doc(path))
        finally:
            em._ASM_CKPT_CAP = saved
        self.assertEqual(em.asm_checkpoint_stats()["skipped"].get("oversize"), 1)
        self.assertFalse(em._asm_ckpt_file(path).exists())
        recs = records()
        first = recs[0]; first["timestamp"] = em.datetime.fromtimestamp(G.T0 + 10 ** 6, em.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z") if hasattr(em, "timezone") else "2099-01-01T00:00:00.000Z"
        path2 = self.write("valves-order", recs)
        self.fresh(); self.parse(path2)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path2)
        self.assertFalse(wrote, "a pre-cut record stamped after every tail record cannot be split off")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"unsplittable": 1})

    def _spoil_identity(self, path):
        _write_doc(path, dict(_doc(path), identity="0" * 40))


class SkillLoadCarry(Harness):
    def test_a_pre_cut_skill_load_is_reported_from_a_restored_tree(self):
        """The harness's own skill load (T333: a bare-named <skill-format> wrapper the emit skips, reported as skillLoads for
        the judges' anchor stamp) before the cut rides the document's carry: the restored tree reports it as the whole
        parse does, with no body read."""
        skill = "notes-review"
        wrapper = ("<command-message>%s</command-message>\n<command-name>%s</command-name>\n"
                   "<skill-format>true</skill-format>" % (skill, skill))
        t0 = NOW - 7200
        recs = [G.uline(t0, "Add retries to the notes-api client", "u1", None),
                dict(G.uline(t0 + 1, wrapper, "u2", "u1"), isMeta=True),
                dict(G.uline(t0 + 1, "# Notes review reference\n\nHow to review notes.", "u3", "u2"), isMeta=True),
                G.aline(t0 + 60, "Adding the retry loop to the client.", "a1", "u3", stop="tool_use"),
                G.aline(t0 + 400, "Wrote the retry loop with a test.", "a2", "a1", stop="end_turn")]
        path = self.write("skill-load", compacting_variant(recs, "skl"))
        whole = self.cold(path)
        self.assertEqual(whole["skillLoads"], {"u2": skill}, "the whole parse reports the wrapper")
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        self.assertEqual(tree["skillLoads"], {"u2": skill}, "restored from the carry, before any hydration")
        em.hydrate(tree, SID)
        self.assertEqual(_strip(tree), whole)


class WriteValves(Harness):
    def _whole(self, name="valve"):
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write(name, records(), sent=sent)
        self.fresh(); self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        return path

    def test_a_reconstruction_that_would_not_reproduce_the_ids_writes_nothing(self):
        """Review find (E): the identity is the whole parse's; a document whose lazy reconstruction would not reproduce
        it is not written, counted. Driven by answering the reconstruction's hash differently from the whole's."""
        path = self._whole()
        real, calls = em._pre_tree_identity, []
        def identity(atoms, rompuuid):
            calls.append(len(atoms))
            h = real(atoms, rompuuid)
            return h if len(calls) == 1 else "0" * len(h)          # the whole's hash, then a reconstruction that differs
        em._pre_tree_identity = identity
        try:
            self.assertFalse(self.doc(path))
        finally:
            em._pre_tree_identity = real
        self.assertEqual(len(calls), 2, "the whole parse's tree and the reconstruction were both hashed")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1})
        self.assertFalse(em._asm_ckpt_file(path).exists(), "no document")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1})
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "the failure is memoized for this entry and cut")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"reconstruction": 1}, "counted again, nothing rebuilt")
        self.assertEqual(em._ASM_CACHE[next(iter(em._ASM_CACHE))].get("docSkip", (None, None))[1], "reconstruction")

    def test_a_transient_failure_is_not_memoized_against_the_cut(self):
        """Review find (third round): a stat, offsets or write failure is a blip, not a property of the cut; memoizing it left
        the session without a document until its next compaction. It is counted and tried again at the next settle."""
        path = self._whole("blip")
        real = em.record_offsets
        em.record_offsets = lambda fp, base: None                   # a record landing between the parse and the offsets
        try:
            self.assertFalse(self.doc(path))
        finally:
            em.record_offsets = real
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"offsets": 1})
        self.assertIsNone(em._ASM_CACHE[next(iter(em._ASM_CACHE))].get("docSkip"), "not memoized")
        self.assertTrue(self.doc(path), "the next settle writes")

    def test_a_whole_entry_writes_its_document_once(self):
        """Review find (H): a fold appends after the cut and changes nothing before it, so the settles after the first
        write skip the build (`written`), until the entry is replaced."""
        path = self._whole("once")
        self.assertTrue(self.doc(path))
        st = em._asm_ckpt_file(path).stat()
        self.assertFalse(self.doc(path))
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"written": 1})
        self.assertEqual(em._asm_ckpt_file(path).stat().st_mtime_ns, st.st_mtime_ns, "the document was not rewritten")
        em._asm_ckpt_file(path).unlink()
        self.assertTrue(self.doc(path), "a missing document is written again from the same entry")


class KernelOverRestored(Harness):
    """The kernel's and the judges' body readers over a restored tree: every consumer the audit named hydrates what it
    reads, so the same answers come from the restored tree as from the whole parse, with no LazyBodyRead."""

    @classmethod
    def setUpClass(cls):
        cls.km = kernel_module()
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
            "mids": [km._seg_mids(seg) for seg in segs],                       # T358: the stored ids, or the markers'
            "humanFloor": km._human_turn_floor(tree),
            "txSets": (lambda s_: (s_[0], s_[1], s_[4]))(km._merge_tx_sets(tree, SID + "-" + str(id(tree)))),
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
                self.assertTrue(self.doc(path))
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
        self.fresh(); whole = self.parse(path); self.doc(path)
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
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        self.assertEqual(em.declared_plan(tree), em.declared_plan(whole))
        self.assertEqual(em.declared_plan(tree), [], "this scenario declares no plan")
        self.assertEqual((em.asm_checkpoint_stats()["hydratedAtoms"], em.asm_checkpoint_stats()["hydratedBy"]), (0, {}),
                         "review find (1): a session with no plan hydrates nothing for the planner's fold (it asked for the whole tree)")

    def test_the_declared_plan_hydrates_only_the_task_call_and_its_result(self):
        t0 = NOW - 7200
        recs = [G.uline(t0, "plan the retry work", "u1", None),
                G.aline(t0 + 10, "", "a1", "u1", tools=("TaskCreate",), stop="tool_use"),
                G.trline(t0 + 11, "tu_a1_0", "r1", "a1", content="Task #7 created"),
                G.aline(t0 + 20, "the plan is filed", "a2", "r1", stop="end_turn"),
                G.uline(t0 + 100, "now do it", "u2", "a2"),
                G.aline(t0 + 130, "done with the first step", "a3", "u2", stop="end_turn")]
        path = self.write("plan", compacting_variant(recs, "pln"))
        self.fresh(); whole = self.parse(path); plan = em.declared_plan(whole)
        self.assertEqual([t["key"] for t in plan], ["7"], "the whole parse folds the declared step")
        self.assertTrue(self.doc(path), em.asm_checkpoint_stats())
        self.fresh(); modes = []
        tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        self.assertEqual(em.declared_plan(tree), plan)
        st = em.asm_checkpoint_stats()
        self.assertEqual(st["hydratedAtoms"], 2, "the TaskCreate call and its result, nothing else: %s" % st["hydratedBy"])
        self.assertEqual(sorted(st["hydratedBy"]), ["declared_plan"])
        self.assertEqual(sorted(a["uuid"] for a in em.plan_atoms(tree)), ["a1", "r1"])


class ConcurrentHydration(Harness):
    def test_two_threads_hydrating_the_same_atoms_read_each_record_once(self):
        """CI find (2026-09-11): the judges' unit text and the frame's markdown hydrate the same restored atoms at a boot
        from two threads; both missed the memo and both read (7806 reads for 7800 atoms). The file's read stripe is held
        across a group and the memo re-checked under it, so each record is read once whoever asks first."""
        import threading
        records, sent = G.SINGLE_FILE["compaction_atom"]
        path = self.write("threads", records(), sent=sent)
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
        self.fresh(); modes = []
        tree = self.parse(path, modes); self.assertEqual(modes, ["restore"])
        lazy = [a for t in tree["turns"] for a in t["atoms"] if a.get("lazy") is not None]
        self.assertGreater(len(lazy), 0)
        copies = [[dict(a, lazy=dict(a["lazy"])) for a in lazy] for _ in range(4)]   # each thread its own atom dicts, same uuids
        em._ASM_CKPT_STATS.update(hydratedAtoms=0, hydratedBytes=0, hydratedBy={})
        gate, errors = threading.Barrier(4), []
        def run(atoms):
            try:
                gate.wait(5); em.hydrate(atoms, SID, by="thread")
            except Exception as e:                                # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=run, args=(c,)) for c in copies]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(em.asm_checkpoint_stats()["hydratedAtoms"], len(lazy), "one read per record across four threads")
        for c in copies:
            self.assertEqual([a["message"] for a in c], [a["message"] for a in copies[0]], "every thread holds the same bodies")


class ClearedSessionDocument(Harness):
    def test_the_per_file_rewound_walk_leaves_a_lineage_document_standing(self):
        """Review find (B): the one-file walk asked for the leaf's document with the leaf alone as its inputs, so a
        /cleared session's document (its inputs name the anchor too) counted an inputs fallback and was unlinked on
        every reconcile pass. The walk asks quietly and the document stands."""
        jd = kernel_module().jd                                # the judge through the module's one kernel load
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
        self.assertTrue(self.doc(str(leaf)), em.asm_checkpoint_stats())
        em._ASM_CKPT_STATS["fallbacks"] = {}
        saved = jd._sdk_owned
        jd._sdk_owned = lambda fsid: False
        try:
            rewound, fails = jd._per_file_rewound(SID, cands)     # the judges' walk itself, over the leaf and the anchor
        finally:
            jd._sdk_owned = saved
        self.assertIsInstance(rewound, set); self.assertEqual(fails, 0)
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"], {}, "a quiet ask: no fallback counted")
        self.assertTrue(em._asm_ckpt_file(str(leaf)).exists(), "the display's document stands")
        self.fresh(); modes = []
        em.parse_session(str(leaf), rompuuid=SID, candidate_files=cands, states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], "and the next parse restores from it")


class LazyBodies(Harness):
    def test_a_body_read_before_hydration_is_loud_and_hydration_counts(self):
        records, _ = G.SINGLE_FILE["compaction_atom"]
        path = self.write("compaction_atom", records())
        self.fresh(); self.parse(path); self.assertTrue(self.doc(path))
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
