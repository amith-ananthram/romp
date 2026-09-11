#!/usr/bin/env python3
"""T323 stage 3 (2026-09-11): the folds' checkpoints. One small JSON file per folded JSONL file records the reader's
prefix witness (offset past the last complete line, the guard bytes before it, the record count) and the state of every
fold_records fold whose cursor stood at that count; a fresh process verifies the guard on disk, reads only the tail
past the offset and resumes each fold from its recorded state. Pinned here: the codec round-trips every fold state
shape; fold-equals-full holds from the third start state (restore, then fold the tail) for the generic fold and for
every kernel fold that carries a checkpoint name; a whole reader touching the file first still lets the folds resume;
every fallback (rewrite under the guard, shrink, version, path, corrupt) reads whole, is counted per reason and equals
a cold fold; the bytes the reader pulls after a restore are the tail plus the guard; a fold behind the witness is left
out and cold-folds; the boot sweep removes checkpoints of vanished files; the kernel writes at a session's settle or
states-log move and never otherwise; /perf carries the counters. Hermetic: synthetic records under a temp root."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_t323s3", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000301"
TS0 = 1_800_000_000


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write(path, recs):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _append(path, *recs, torn=None):
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        if torn is not None:
            f.write(json.dumps(torn))                     # a final record without its newline
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 2))        # a new mtime even on a coarse-clock filesystem


def _user(text, uuid, parent, t, **kw):
    r = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed",
         "message": {"role": "user", "content": text}, "cwd": "/w/notes-api", "version": "2.1.0", "gitBranch": "web"}
    r.update(kw)
    return r


def _assistant(blocks, uuid, parent, t):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "cwd": "/w/notes-api",
            "version": "2.1.0", "gitBranch": "web", "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}}


def _tool_result(tid, uuid, parent, t, text="ok"):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def _bg_launch(tid, desc, uuid, parent, t):
    return _assistant([{"type": "tool_use", "id": tid, "name": "Bash",
                        "input": {"command": "sleep 1", "run_in_background": True, "description": desc}}], uuid, parent, t)


def _bg_note(tid, status, uuid, parent, t):
    note = ("<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>%s</tool-use-id>\n<status>%s</status>\n"
            "<summary>%s</summary>\n</task-notification>" % (tid, status, status))
    return _user(note, uuid, parent, t)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_state = jd.STATE
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        self.fresh_process()
        em._CKPT_STATS.update(restored=0, writes=0, swept=0, skippedFolds=0, fallbacks={}, restoredFolds={}, droppedRestores=0, oversizeFolds={})

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

    def fresh_process(self):
        """What a kernel restart does to the in-memory side: every reader entry, fold cursor, pending restore and
        byte counter gone; the checkpoint files on disk stay."""
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")   # the setter clears the pending restores and the seqs
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for c in list(em._FOLD_REG.values()):
            c.clear()
        with em._CKPT_LOCK:
            em._COLD_FOLDS.clear()                                # a process-wide set: a new process starts with none
        km._CKPT_SETTLE_SEEN.clear()

    def ckpt_files(self):
        d = jd.STATE / "checkpoints"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def doc(self, path):
        return json.loads(em._ckpt_file(path).read_text())


class Codec(Base):
    def test_every_fold_state_shape_round_trips(self):
        shapes = [
            {"tasks": {"toolu_1": {"status": "running", "desc": "x"}}, "order": ["toolu_1"], "dispatch": {}, "all": False, "done": {"toolu_0", "toolu_9"}},
            {"steps": [{"tool": "Bash", "desc": "ls", "ts": "2026-09-10T00:00:00.000Z"}], "calls": 1, "since": None, "last": "t"},
            {"launched": {"toolu_a": 1.5}, "settled": {"toolu_a"}},
            {"sent": {"m1": {"ev": "sent", "id": "m1"}}, "execd": {"m1": (1.0, None)}, "ended": {"m2"}},
            (12.5, "restart"), (None, False), ({"awaiting": True, "t": 3}, True),
            {"recoveries": [{"t": 1, "retries": 2}], "gaveups": [], "orphans": [], "efforts": [], "gestures": []},
            ([("hello", "2026-09-10T00:00:00Z"), ("", None)], 7),
            [(1, "working"), (2, "idle")],
            {1: "int keyed", (2, 3): "tuple keyed"},
            {"~set": "a plain key that looks like a tag"},
            set(), (), [], {}, None, True, 0, 1.25, "s",
        ]
        for st in shapes:
            enc = em._ckpt_encode(st)
            json.dumps(enc)                                       # JSON-able
            self.assertEqual(em._ckpt_decode(json.loads(json.dumps(enc))), st, repr(st))
            self.assertIs(type(em._ckpt_decode(enc)), type(st), repr(st))

    def test_an_unencodable_state_raises_type_error(self):
        with self.assertRaises(TypeError):
            em._ckpt_encode({"x": object()})


class GenericFold(Base):
    """em.fold_records with a checkpoint name over a plain JSONL file of {"n": i} records."""

    def setUp(self):
        super().setUp()
        self.p = str(self.td / "t.jsonl")
        self.cache = {}
        self.kinds = []

    @staticmethod
    def _step(st, o):
        return st + [o["n"]]

    def fold(self, name="t", cache=None):
        return em.fold_records(cache if cache is not None else self.cache, self.p, list, self._step, on=self.kinds.append, ckpt=name)

    def cold(self):
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None                                 # a plain whole fold, no checkpoint in play
        try:
            return em.fold_records({}, self.p, list, self._step)
        finally:
            em._CKPT_DIR_FN = saved

    def test_write_restore_fold_the_tail_equals_a_cold_fold_and_reads_only_the_tail(self):
        _write(self.p, [{"n": i} for i in range(3)])
        self.assertEqual(self.fold(), [0, 1, 2])
        self.assertEqual(self.kinds, ["refold"])
        self.assertIn(self.p, em.checkpoint_dirty())
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertNotIn(self.p, em.checkpoint_dirty(), "a write clears the dirty mark")
        d = self.doc(self.p)
        size0 = os.stat(self.p).st_size
        self.assertEqual((d["v"], d["count"], d["offset"], d["size"], d["seq"]), (1, 3, size0, size0, 1))
        self.assertEqual(d["folds"]["t"]["count"], 3)
        self.assertEqual(bytes.fromhex(d["guard"]), open(self.p, "rb").read()[-64:])
        self.fresh_process()
        _append(self.p, {"n": 3}, {"n": 4})
        self.assertEqual(self.fold(), [0, 1, 2, 3, 4], "restored state plus the tail equals a cold fold of the file")
        self.assertEqual(self.kinds[-1], "restore")
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE[self.p]
        self.assertEqual((ent[5], len(ent[4])), (3, 2), "a TAIL entry: base 3, two records held")
        read = em.read_bytes_report()[self.p]
        size1 = os.stat(self.p).st_size
        self.assertEqual(read, (size1 - size0) + min(64, size0) + min(64, size1),
                         "the tail, the guard check before it and the guard captured after it: nothing of the prefix")
        self.assertEqual(em.checkpoint_stats()["restored"], 1)
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        cp = em._ckpt_file(self.p)
        self.assertEqual(em.read_bytes_report()[str(cp)], cp.stat().st_size, "the checkpoint document's own read is counted")
        self.assertEqual(em.checkpoint_stats()["documentBytes"], cp.stat().st_size)
        self.assertEqual(self.fold(), [0, 1, 2, 3, 4]); self.assertEqual(self.kinds[-1], "hit")
        self.assertEqual(self.cache[self.p][0], 5, "the cursor's count is first, as every reader of it knew")
        self.assertEqual(self.cold(), [0, 1, 2, 3, 4])

    def test_an_unchanged_file_restores_with_no_tail_and_a_torn_tail_folds_provisionally(self):
        _write(self.p, [{"n": 0}, {"n": 1}])
        self.fold(); em.checkpoint_write(self.p)
        self.fresh_process()
        self.assertEqual(self.fold(), [0, 1]); self.assertEqual(self.kinds[-1], "restore")
        self.assertEqual(em.read_bytes_report()[self.p], 2 * min(64, os.stat(self.p).st_size), "the guard checked, then captured again: no content")
        _append(self.p, torn={"n": 2})
        self.assertEqual(self.fold(), [0, 1, 2], "the newline-less record is folded provisionally")
        self.assertEqual(self.cache[self.p][0], 2, "and not into the cursor")

    def test_a_whole_reader_first_then_the_fold_still_restores(self):
        _write(self.p, [{"n": 0}, {"n": 1}, {"n": 2}])
        self.fold(); em.checkpoint_write(self.p)
        size0 = os.stat(self.p).st_size
        self.fresh_process()
        _append(self.p, {"n": 3})
        recs = em._read_jsonl_incremental(self.p)                 # a whole reader (the parse) touches the file first
        self.assertEqual(len(recs), 4)
        self.assertEqual(self.fold(), [0, 1, 2, 3])
        self.assertEqual(self.kinds[-1], "restore", "the fold state came from the checkpoint after a guard check on disk")
        self.assertEqual(em.checkpoint_stats()["restored"], 1)
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        self.assertEqual(em.read_bytes_report()[self.p], os.stat(self.p).st_size + min(64, os.stat(self.p).st_size) + min(64, size0),
                         "the whole file and its guard capture (the whole reader's) plus the restore's guard check")

    def test_a_rewrite_under_the_guard_falls_back_loudly_and_equals_cold(self):
        _write(self.p, [{"n": i} for i in range(4)])
        self.fold(); em.checkpoint_write(self.p)
        self.fresh_process()
        _write(self.p, [{"n": 100 + i} for i in range(4)] + [{"n": 7}])   # same shape, other content, longer
        self.assertEqual(self.fold(), [100, 101, 102, 103, 7])
        self.assertEqual(self.kinds[-1], "refold")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {"guard": 1})
        self.assertFalse(em._ckpt_file(self.p).exists(), "the bad checkpoint is removed")
        self.assertEqual(self.fold(), self.cold())

    def test_a_shrunk_file_a_wrong_version_a_wrong_path_and_a_corrupt_document_each_fall_back(self):
        for reason, spoil in (
            ("shrunk", lambda: _write(self.p, [{"n": 0}])),
            ("version", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), v=99)))),
            ("path", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), path="/elsewhere/t.jsonl")))),
            ("corrupt", lambda: em._ckpt_file(self.p).write_text("{not json")),
            ("corrupt", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), offset="x")))),
            ("rewrite", lambda: (_write(self.p, [{"n": 5}, {"n": 6}, {"n": 7}]), os.utime(self.p, (TS0, TS0)))),
        ):
            with self.subTest(reason=reason):
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process()
                em._CKPT_STATS["fallbacks"] = {}
                self.fold(); em.checkpoint_write(self.p)
                self.fresh_process()
                spoil()
                got = self.fold()
                self.assertEqual(em.checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, self.cold(), reason)
                self.assertEqual(self.kinds[-1], "refold")

    def test_a_fold_behind_the_witness_is_left_out_and_cold_folds_while_the_other_restores(self):
        _write(self.p, [{"n": 0}, {"n": 1}])
        other = {}
        self.fold(); self.fold("u", other)
        _append(self.p, {"n": 2})
        self.fold()                                               # "t" stands at 3, "u" still at 2
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual(sorted(self.doc(self.p)["folds"]), ["t"], "only the fold at the witness is recorded")
        self.fresh_process()
        _append(self.p, {"n": 3})
        self.assertEqual(self.fold(), [0, 1, 2, 3]); self.assertEqual(self.kinds[-1], "restore")
        self.assertEqual(em.fold_records({}, self.p, list, self._step, on=self.kinds.append, ckpt="u"), [0, 1, 2, 3])
        self.assertEqual(self.kinds[-1], "refold", "a fold with no recorded state reads the whole file")
        with em._JSONL_CACHE_LOCK:
            self.assertEqual(em._JSONL_CACHE[self.p][5], 0, "the entry was upgraded to the whole file for it")
        self.assertEqual(self.fold(), [0, 1, 2, 3]); self.assertEqual(self.kinds[-1], "hit", "the restored fold still stands")

    def test_an_evicted_reader_entry_never_lets_a_rewritten_file_pass_as_an_append(self):
        """Review find (2026-09-11): a from-zero read after an eviction (the reader cache runs at its cap on a busy box) or
        a failure pop restarted the generation at 0 while the fold's cursor still held (count, 0, state): a file rewritten
        in place with as many records then read as a hit, one with more as an append of the NEW file's tail onto the OLD
        state, and the checkpoint written afterwards carried the wrong state under a clean witness. Every from-zero read
        takes a fresh process-wide generation now."""
        _write(self.p, [{"n": i} for i in range(3)])
        self.assertEqual(self.fold(), [0, 1, 2])
        for rewrite in ([{"n": 7}, {"n": 8}, {"n": 9}], [{"n": 4}, {"n": 5}, {"n": 6}, {"n": 3}]):   # same size, then longer
            with self.subTest(records=len(rewrite)):
                with em._JSONL_CACHE_LOCK:
                    em._JSONL_CACHE.pop(self.p, None)                 # evicted (or popped by a stat failure) under the fold
                _write(self.p, rewrite)
                self.assertEqual(self.fold(), [r["n"] for r in rewrite], "the rewritten file is folded from zero, never onto the old state")
                self.assertEqual(self.kinds[-1], "refold")
                self.assertTrue(em.checkpoint_write(self.p))
                self.assertEqual(self.fold(), self.cold())
                self.fresh_process()
                self.assertEqual(self.fold(), [r["n"] for r in rewrite], "and the checkpoint written after it is right")
                self.assertEqual(self.kinds[-1], "restore")

    def test_a_whole_reader_first_then_a_rewritten_file_falls_back_like_the_tail_path(self):
        """Review find (2026-09-11): the whole-reader-first restore checked the guard alone; the tail path also compared
        the document's size and mtime. Both verdicts come from one helper now: the same rewrite gets the same reason
        whichever reader came first, so /perf's fallback count is a witness."""
        for reason, rewrite in (("guard", [{"n": 100 + i} for i in range(4)] + [{"n": 7}]),          # longer, other prefix
                                ("rewrite", ([{"n": 5}, {"n": 6}, {"n": 7}], (TS0, TS0)))):         # same size, other mtime
            with self.subTest(reason=reason):
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process(); em._CKPT_STATS["fallbacks"] = {}
                self.fold(); em.checkpoint_write(self.p)
                self.fresh_process()
                if isinstance(rewrite, tuple):
                    _write(self.p, rewrite[0]); os.utime(self.p, rewrite[1])
                else:
                    _write(self.p, rewrite)
                recs = em._read_jsonl_incremental(self.p)          # the whole reader comes first
                self.assertEqual(len(recs), len(rewrite[0] if isinstance(rewrite, tuple) else rewrite))
                got = self.fold()
                self.assertEqual(em.checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, self.cold()); self.assertEqual(self.kinds[-1], "refold")
                self.assertFalse(em._ckpt_file(self.p).exists(), "the checkpoint that did not verify is gone")

    def test_two_threads_meeting_a_checkpointed_files_first_read_cost_one_read_and_lose_no_restore(self):
        """Review find (2026-09-11): the reader held its lock for the lookup and the store only, so a fold's tail restore
        (generation N, pending fold states under N) and a concurrent whole read (generation N+1, stored last) left the
        pending states orphaned: every other fold of the file refolded over a whole read, silently. Reads that pull bytes
        are serialized per path now and re-check the cache under the lock: the second thread waits and hits."""
        _write(self.p, [{"n": i} for i in range(200)])
        other = {}
        self.fold(); self.fold("u", other); em.checkpoint_write(self.p)
        size = os.stat(self.p).st_size
        for trial in range(8):
            with self.subTest(trial=trial):
                self.fresh_process(); other.clear()
                em._CKPT_STATS.update(restored=0, restoredFolds={}, droppedRestores=0, fallbacks={})
                gate = threading.Barrier(2)
                results, errors = {}, []

                def fold_first():
                    try:
                        gate.wait(5); results["fold"] = self.fold()
                    except Exception as e:                                # noqa: BLE001
                        errors.append(e)

                def whole_first():
                    try:
                        gate.wait(5); results["whole"] = len(em._read_jsonl_incremental(self.p))
                    except Exception as e:                                # noqa: BLE001
                        errors.append(e)
                ts = [threading.Thread(target=fold_first), threading.Thread(target=whole_first)]
                for th in ts:
                    th.start()
                for th in ts:
                    th.join(10)
                self.assertEqual(errors, [])
                self.assertEqual((results["fold"], results["whole"]), (list(range(200)), 200))
                self.assertEqual(em.fold_records(other, self.p, list, self._step, on=self.kinds.append, ckpt="u"), list(range(200)))
                self.assertEqual(self.kinds[-1], "restore", "the second fold of the file restores too: no orphaned pending states")
                st = em.checkpoint_stats()
                self.assertEqual((st["droppedRestores"], st["fallbacks"]), (0, {}))
                self.assertEqual(sorted(st["restoredFolds"]), ["t", "u"])
                read = em.read_bytes_report()[self.p]
                self.assertLessEqual(read, size + 4 * 64, "the file's content was read once, whichever thread went first (%d bytes for a %d-byte file)" % (read, size))

    def test_a_fold_whose_state_is_the_size_of_its_file_keeps_its_cursor_and_restarts_cold_over_the_tail(self):
        """Review finds (2026-09-11): a checkpoint carried every fold's state, and a state that grows with its file (the postal
        fold's map of every sent row) made the document a second copy of the file, read at every boot. A fold's encoded
        state past the cap is left out, counted per name; its CURSOR stays, so the next process starts that fold cold at the
        cut and steps the tail only (counted under coldFolds, said once) instead of reading the file whole at every restart;
        the bounded folds beside it restore whole."""
        _write(self.p, [{"n": i, "pad": "x" * 400} for i in range(400)])           # ~170 KB of records
        big = {}
        self.fold()                                                               # "t": a list of 400 ints, small
        em.fold_records(big, self.p, list, lambda st, o: st + [o], ckpt="big")    # "big": every record whole, over the cap
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual(sorted(d["folds"]), ["big", "t"], "the oversize fold's cursor is in the document")
        self.assertEqual(d["folds"]["big"], {"count": 400}, "without its state")
        self.assertLess(em._ckpt_file(self.p).stat().st_size, em._CKPT_FOLD_CAP, "and the document stays small")
        self.assertEqual(em.checkpoint_stats()["oversizeFolds"], {"big": 1})
        self.fresh_process(); big.clear()
        self.assertEqual(self.fold(), list(range(400))); self.assertEqual(self.kinds[-1], "restore")
        size = os.path.getsize(self.p)
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual(got, []); self.assertEqual(self.kinds[-1], "cold", "the oversize fold starts cold at the cut: an empty tail")
        self.assertEqual(em.checkpoint_stats()["coldFolds"], {"big": 1})
        self.assertLess(em.read_bytes_report().get(self.p, 0), size / 2, "the file was not read whole")
        _append(self.p, {"n": 400})
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual(got, [{"n": 400}], "and steps the records appended since"); self.assertEqual(self.kinds[-1], "append")
        # review find (2026-09-11, third round): the tail-only state is small now; a write must not record it as complete
        self.assertEqual(self.fold(), list(range(401)))               # the bounded fold steps to the witness too
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual(self.doc(self.p)["folds"]["big"], {"count": 401}, "a fold that began cold stays a cursor without state")
        self.assertEqual(em.checkpoint_stats()["coldWrites"], {"big": 1})
        self.assertIn("state", self.doc(self.p)["folds"]["t"], "the bounded fold beside it is written whole")
        self.fresh_process(); big.clear()
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual((got, self.kinds[-1]), ([], "cold"), "the third process restores it cold again, not as a complete state")
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("big", 0), 0)
        big.clear(); em._TRAILING_CACHE.clear()
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        em._ckpt_file(self.p).unlink()                                # no document to restore from: the fold reads the file whole
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual((len(got), self.kinds[-1]), (401, "refold"), "a whole refold completes the state")
        with em._CKPT_LOCK:
            self.assertNotIn((self.p, "big"), em._COLD_FOLDS, "and the fold is no longer cold")
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        self.assertEqual(sorted(self.doc(self.p)["folds"]), ["big"], "the state is oversize again: written as a cursor for that reason")
        self.assertEqual(self.doc(self.p)["folds"]["big"], {"count": 401})
        self.assertEqual(em.checkpoint_stats()["coldWrites"], {"big": 1}, "not as a cold write")

    @staticmethod
    def _noop_init():
        return []

    def test_nothing_is_written_when_no_fold_stands_at_the_witness_unless_forced(self):
        _write(self.p, [{"n": 0}])
        em._read_jsonl_incremental(self.p)                        # an entry with no fold on it
        self.assertFalse(em.checkpoint_write(self.p))
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        self.assertEqual(self.doc(self.p)["folds"], {})

    def test_checkpoints_off_fold_as_before_and_write_nothing(self):
        em.set_checkpoint_dir(None)
        _write(self.p, [{"n": 0}])
        self.assertEqual(self.fold(), [0])
        self.assertFalse(em.checkpoint_write(self.p))
        self.assertEqual(self.ckpt_files(), [])

    def test_the_boot_sweep_removes_checkpoints_of_files_that_are_gone(self):
        q = str(self.td / "q.jsonl")
        _write(self.p, [{"n": 0}]); _write(q, [{"n": 1}])
        self.fold(); em.fold_records({}, q, list, self._step, ckpt="q")
        em.checkpoint_write_dirty()
        self.assertEqual(len(self.ckpt_files()), 2)
        os.unlink(q)
        (jd.STATE / "checkpoints" / "junk.json").write_text("{")
        self.assertEqual(em.checkpoint_sweep(), 2)
        self.assertEqual([f.name for f in self.ckpt_files()], [em._ckpt_file(self.p).name])
        self.assertEqual(em.checkpoint_stats()["swept"], 2)

    def test_the_seq_counts_writes_per_file_and_survives_a_restore(self):
        _write(self.p, [{"n": 0}])
        self.fold(); em.checkpoint_write(self.p)
        _append(self.p, {"n": 1}); self.fold(); em.checkpoint_write(self.p)
        self.assertEqual(self.doc(self.p)["seq"], 2)
        self.fresh_process()
        self.fold(); _append(self.p, {"n": 2}); self.fold(); em.checkpoint_write(self.p)
        self.assertEqual(self.doc(self.p)["seq"], 3)


class KernelFolds(Base):
    """Every kernel fold that carries a checkpoint name: the answer after (checkpoint at a prefix, a fresh process,
    the tail appended, the fold) equals the answer of a cold fold over the whole file."""

    def setUp(self):
        super().setUp()
        self.proj = self.td / "proj"; self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        sub = self.proj / SID / "subagents"; sub.mkdir(parents=True)
        self.agent = str(sub / "agent-aaaa.jsonl")
        self.states = str(jd.STATE / "states" / (SID + ".jsonl"))
        self.postal = str(jd.STATE / "timeline" / "messages.jsonl")
        self.queue_leaf = str(self.proj / "queue.jsonl")
        self.nudge = str(jd.STATE / "nudge-events.jsonl")
        self.files = (self.leaf, self.agent, self.states, self.postal, self.queue_leaf, self.nudge)

    def leaf_recs(self, tail):
        head = [_user("wire the fixtures", "u1", None, TS0, permissionMode="acceptEdits"),
                _bg_launch("toolu_bg1", "run the suite", "a1", "u1", TS0 + 10),
                _assistant([{"type": "tool_use", "id": "toolu_ag1", "name": "Agent", "input": {"prompt": "check the width"}}], "a2", "a1", TS0 + 20),
                _assistant([{"type": "tool_use", "id": "toolu_ed1", "name": "Edit", "input": {"file_path": "/w/notes-api/web/app.ts"}}], "a3", "a2", TS0 + 30)]
        rest = [_bg_note("toolu_bg1", "completed", "u2", "a3", TS0 + 40),
                _tool_result("toolu_ag1", "u3", "u2", TS0 + 50, text="done"),
                _bg_launch("toolu_bg2", "lint", "a4", "u3", TS0 + 60),
                dict(_user("and the cap?", "u4", "a4", TS0 + 70), gitBranch="api"),
                dict(_assistant([{"type": "tool_use", "id": "toolu_ed2", "name": "Write", "input": {"file_path": "/w/notes-api/api/cap.py"}}], "a5", "u4", TS0 + 80), gitBranch="api")]
        return head + rest if tail else head

    def agent_recs(self, tail):
        head = [_user("check the width", "s1", None, TS0 + 21),
                _assistant([{"type": "tool_use", "id": "toolu_s1", "name": "Bash", "input": {"command": "ls web", "description": "list"}}], "s2", "s1", TS0 + 22)]
        rest = [_assistant([{"type": "tool_use", "id": "toolu_s2", "name": "Read", "input": {"file_path": "/w/notes-api/web/app.ts"}}], "s3", "s2", TS0 + 23),
                _assistant([{"type": "tool_use", "id": "toolu_s3", "name": "Agent", "input": {"prompt": "deeper"}}], "s4", "s3", TS0 + 24)]
        return head + rest if tail else head

    def states_recs(self, tail):
        head = [{"t": TS0, "state": "working"}, {"t": TS0 + 5, "awaiting": True, "state": "waiting"},
                {"t": TS0 + 6, "machineCut": "restart", "state": "idle"},
                {"t": TS0 + 7, "retriesRecovered": True, "retries": 2}]
        rest = [{"t": TS0 + 8, "state": "working"}, {"t": TS0 + 8.5, "state": "idle", "by": "stop"},
                {"t": TS0 + 9, "state": "retrying"}, {"t": TS0 + 9.5, "state": "retrying"},
                {"t": TS0 + 10, "machineCut": "deploy"}, {"t": TS0 + 11, "retriesGaveUp": True, "retries": 5, "errorKind": "overloaded"}]
        return head + rest if tail else head

    def postal_recs(self, tail):
        head = [{"ev": "sent", "id": "m1", "from_id": SID, "to_id": "22222222-2222-4333-8444-000000000302", "body": "ping", "t": TS0, "kind": "coordinate"},
                {"ev": "exec", "id": "m1", "t": TS0 + 1, "dmid": None}]
        rest = [{"ev": "sent", "id": "m2", "from_id": SID, "to_id": SID, "body": "note", "t": TS0 + 2},
                {"ev": "recall", "id": "m2", "t": TS0 + 3}, {"ev": "unexec", "id": "m1", "t": TS0 + 4}]
        return head + rest if tail else head

    def queue_recs(self, tail):
        head = [{"type": "queue-operation", "operation": "enqueue", "content": "first", "timestamp": _iso(TS0)},
                {"type": "queue-operation", "operation": "enqueue", "content": "second", "timestamp": _iso(TS0 + 1)}]
        rest = [{"type": "queue-operation", "operation": "remove", "content": "first", "timestamp": _iso(TS0 + 2)},
                {"type": "queue-operation", "operation": "enqueue", "content": "third", "timestamp": _iso(TS0 + 3)}]
        return head + rest if tail else head

    def nudge_recs(self, tail):
        head = [{"gid": SID + ":g1", "t": TS0 + 1}, {"gid": SID + ":g1", "t": TS0 + 2}]
        rest = [{"gid": SID + ":g2", "t": TS0 + 3}, {"gid": SID + ":g1", "t": TS0 + 4}]
        return head + rest if tail else head

    def recs_of(self, p, tail):
        return {self.leaf: self.leaf_recs, self.agent: self.agent_recs, self.states: self.states_recs,
                self.postal: self.postal_recs, self.queue_leaf: self.queue_recs, self.nudge: self.nudge_recs}[p](tail)

    def answers(self):
        return {
            "sessionMeta": dict(km._session_meta(self.leaf)),
            "bgRunning": km._bg_scan_cached(self.leaf),
            "bgAll": km._bg_scan_all_cached(self.leaf),
            "agentLaunches": {k: (dict(v) if isinstance(v, dict) else sorted(v)) for k, v in km._agent_launch_state(self.leaf).items()},
            "agentGist": km._agent_steps(self.agent),
            "agentLaunchIds": sorted(km._agent_launch_ids(self.agent)),
            "statesOverlay": km._states_awaiting_overlay(SID),
            "stateIntervals": km._state_intervals(SID, "working", TS0 + 200),
            "statesNotes": km._states_notes(SID),
            "machineCut": km._last_machine_cut(SID),
            "lastState": km._last_state(SID),
            "lastNaturalState": km._last_natural_state(SID),
            "retryingSince": km._fold_records(km._retrying_since_cache, jd.STATE / "states" / (SID + ".jsonl"), lambda: None,
                                              km._retrying_since_step, ckpt="retryingSince"),
            "queueLedger": km._pending_ledger(self.queue_leaf),
            "wakeTail": km._undelivered_wake_tail(self.queue_leaf),
            "nudgeTimes": km._nudge_times(),
            "bgJudge": [dict(t) for t in jd._bg_unresolved(self.leaf, now=TS0 + 100)],
            "postalLog": {k: (dict(v) if isinstance(v, dict) else sorted(v)) for k, v in
                          km._fold_records(km._postal_log_cache, self.postal, km._postal_log_fresh, km._postal_log_step, ckpt="postalLog").items()},
        }

    def write_all(self, tail):
        for p in self.files:
            _write(p, self.recs_of(p, tail))

    def test_every_named_fold_resumes_from_its_checkpoint_and_equals_a_cold_fold(self):
        self.write_all(tail=False)
        self.answers()                                            # every fold at the prefix
        names = set()
        for p in self.files:
            self.assertTrue(em.checkpoint_write(p), p)
            names |= set(self.doc(p)["folds"])
        ALL_NAMES = {"sessionMeta", "bgRunning", "bgAll", "bgJudge", "agentLaunches", "agentGist", "agentLaunchIds", "statesOverlay",
                     "stateIntervals", "statesNotes", "machineCut", "queueLedger", "wakeTail", "postalLog", "lastState",
                     "lastNaturalState", "retryingSince", "nudgeTimes"}
        self.assertEqual(names, ALL_NAMES, "every kernel fold this test drives left its state in the checkpoint")
        ksrc = open(os.path.join(BIN, "romp-kernel")).read(); jsrc = open(os.path.join(BIN, "romp-judge")).read()
        import re as _re
        named = set(_re.findall(r'ckpt="([A-Za-z]+)"', ksrc)) | set(_re.findall(r'name_fold_cache\([^,]+, "([A-Za-z]+)"\)', jsrc))
        self.assertEqual(named, ALL_NAMES, "every checkpoint name the kernel and the judge give a fold is driven here")
        self.fresh_process()
        for p in self.files:
            full, head = self.recs_of(p, True), self.recs_of(p, False)
            _append(p, *full[len(head):])
        warm = self.answers()
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        self.assertEqual(em.checkpoint_stats()["restored"], 6, "one restore per file")
        self.assertEqual(set(em.checkpoint_stats()["restoredFolds"]), ALL_NAMES, "every fold restored from its recorded state")
        for p in self.files:
            with em._JSONL_CACHE_LOCK:
                self.assertGreater(em._JSONL_CACHE[p][5], 0, "a tail entry: %s" % p)
        self.fresh_process(); em.set_checkpoint_dir(None)       # the cold answer: a fresh process with no checkpoints at all
        cold = self.answers()
        self.assertEqual(warm, cold)
        self.assertEqual(cold["sessionMeta"]["lastEditPath"], "/w/notes-api/api/cap.py", "the fixtures drive the folds")
        self.assertEqual(cold["sessionMeta"]["gitBranch"], "api")
        self.assertEqual([t["id"] for t in cold["bgRunning"]], ["toolu_bg2"])
        self.assertEqual(cold["agentLaunches"]["settled"], ["toolu_ag1"])
        self.assertEqual(len(cold["agentGist"]["steps"]), 3)
        self.assertEqual(cold["machineCut"], (float(TS0 + 10), "deploy"))
        self.assertEqual(len(cold["stateIntervals"]), 2, "two working stretches in the states rows")
        self.assertEqual(cold["lastState"], ("retrying", TS0 + 9.5)); self.assertEqual(cold["lastNaturalState"], ("retrying", TS0 + 9.5))
        self.assertEqual(cold["retryingSince"], TS0 + 9, "the current retrying stretch dates from its first row")
        _append(self.states, {"t": TS0 + 12, "state": "waiting", "by": "stop"})       # a Stop press: romp's row, not the session's
        self.assertEqual(km._last_state(SID), ("waiting", TS0 + 12))
        self.assertEqual(km._last_natural_state(SID), ("retrying", TS0 + 9.5), "the session's own newest state skips rows with `by`")
        self.assertEqual(cold["queueLedger"], ["second", "third"])
        self.assertEqual([e["text"] for e in cold["wakeTail"][0]], ["second", "third"])
        self.assertEqual(cold["nudgeTimes"], {SID + ":g1": [TS0 + 1, TS0 + 2, TS0 + 4], SID + ":g2": [TS0 + 3]})
        self.assertEqual([t["id"] for t in cold["bgJudge"]], ["toolu_bg2"], "the judge's settled gate reads the same pairing")
        self.assertEqual(cold["postalLog"]["ended"], ["m2"])
        self.assertNotIn("m1", cold["postalLog"]["execd"])

    def test_the_kernel_writes_at_a_settle_or_a_states_log_move_and_not_otherwise(self):
        self.write_all(tail=False)
        self.answers()
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        try:
            self.assertGreater(len(em.checkpoint_dirty()), 0)
            self.assertEqual(km._persist_checkpoints(TS0 - 1), 0, "the first sight of a session records its evidence and writes nothing: "
                             "the evidence predates this kernel life, and a boot must not prime every session at once (2026-09-11)")
            turn_end[0] = TS0 + 50                                 # a settle after the first sight: new evidence
            km._persist_checkpoints(TS0)
            self.assertEqual(sorted(set(em.checkpoint_dirty()) & {self.leaf, self.agent, self.states, self.postal}), [])
            self.assertIn(self.queue_leaf, em.checkpoint_dirty(), "a file of no session waits for exit")
            _append(self.leaf, _user("more", "u9", "a3", TS0 + 100))
            km._session_meta(self.leaf)
            self.assertIn(self.leaf, em.checkpoint_dirty())
            self.assertEqual(km._persist_checkpoints(TS0 + 1), 0, "no settle, no states move: nothing written")
            turn_end[0] = TS0 + 100                                # the Stop hook stamped the settle
            em._ASM_CKPT_STATS["skipped"] = {}
            self.assertEqual(km._persist_checkpoints(TS0 + 2), 1)
            self.assertGreaterEqual(em.asm_checkpoint_stats()["skipped"].get("noEntry", 0) + em.asm_checkpoint_stats()["skipped"].get("noBoundary", 0), 1,
                                    "the settle write asked for the leaf's assembly document too (a counted skip here: no parse, no boundary)")
            self.assertNotIn(self.leaf, em.checkpoint_dirty())
            _append(self.agent, _assistant([{"type": "tool_use", "id": "toolu_s9", "name": "Bash", "input": {"command": "true"}}], "s9", "s2", TS0 + 101))
            km._agent_steps(self.agent)
            self.assertEqual(km._persist_checkpoints(TS0 + 3), 0)
            _append(self.states, {"t": TS0 + 102, "state": "working"})   # a states row is an event, with no settle
            self.assertEqual(km._persist_checkpoints(TS0 + 4), 1, "the agent file rides the states-log move")
            self.assertEqual(em.checkpoint_write_dirty(), 2, "exit writes what is left (the queue ledger's file and the nudge log)")
            self.assertEqual(em.checkpoint_dirty(), [])
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn

    def test_the_settle_write_leaves_a_cursor_for_every_leaf_fold_whether_or_not_it_ran(self):
        """A fold no caller happened to run before the write (a kernel stopped before a judges' pass reached its
        background-task pairing) would have no cursor for the next process, whose first run of it would read the leaf
        whole: the settle write and the exit drain bring every leaf fold current first (_prime_leaf_folds)."""
        self.write_all(tail=False)
        self.fresh_process()
        km._session_meta(self.leaf)                               # the one fold a build happened to run
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        try:
            self.assertEqual(km._persist_checkpoints(TS0 - 1), 0, "first sight: recorded, nothing written")
            turn_end[0] = TS0
            self.assertGreaterEqual(km._persist_checkpoints(TS0), 1)
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn
        self.assertEqual(sorted(self.doc(self.leaf)["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"],
                         "the leaf's document holds every leaf fold, the judges' pairing included")
        self.fresh_process()
        km._session_meta(self.leaf)                               # a restored TAIL entry: priming would read the file whole
        self.assertFalse(em.entry_whole_resident(self.leaf))
        size = os.path.getsize(self.leaf)
        self.assertTrue(km._prime_leaf_folds(self.leaf), "over a tail entry the folds holding a cursor at it are primed (an append)")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and the leaf was not read whole")
        self.assertFalse(km._prime_leaf_folds(str(self.leaf) + ".absent"), "no entry: nothing read")
        self.fresh_process(); km._session_meta(self.leaf)
        self.assertEqual(sorted(k for k in em._FOLD_REG if em._FOLD_REG[k].get(self.leaf)), ["sessionMeta"])
        self.assertTrue(km._prime_leaf_folds(self.leaf))
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "the four folds with no cursor here were left alone")
        self.assertEqual(sorted(k for k in em._FOLD_REG if em._FOLD_REG[k].get(self.leaf)), ["sessionMeta"])

    def test_a_fold_lagging_behind_a_restored_tail_entry_is_primed_at_the_settle_and_the_next_boot_restores_it(self):
        """Review find (2026-09-11, third round): after the first boot every restored leaf is a tail entry; a fold whose
        cursor merely lagged the leaf (the judges' pairing steps only in their passes) dropped out of the settle write, and
        the next boot's first pass read the leaf whole. Over a tail entry the primer steps every fold holding a cursor at
        that entry, so the document carries all five and the next boot restores each."""
        self.write_all(tail=False)
        self.answers()
        for p in self.files:
            em.checkpoint_write(p)
        self.fresh_process()
        for fn in (km._session_meta, km._bg_scan_cached, km._bg_scan_all_cached, km.jd._bg_scan, km._agent_launch_state):
            fn(self.leaf)                                             # five cursors restored at the tail entry
        self.assertFalse(em.entry_whole_resident(self.leaf))
        _append(self.leaf, _user("a new prompt", "u_lag", "a3", TS0 + 500))
        km._session_meta(self.leaf)                                   # the one fold a build stepped; the other four lag
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [TS0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        size = os.path.getsize(self.leaf)
        try:
            self.assertEqual(km._persist_checkpoints(TS0 + 499), 0, "first sight: recorded, nothing written")
            turn_end[0] = TS0 + 500                                   # the settle after the new prompt
            self.assertGreaterEqual(km._persist_checkpoints(TS0 + 501), 1)
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"], "all five at the new count")
        self.assertEqual({f["count"] for f in d["folds"].values()}, {d["count"]})
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "no whole read at the settle")
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0)
        km.jd._bg_scan(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0), before + 1, "the next boot restores the judges' pairing")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and reads no leaf whole")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_prime_leaf_folds(_s[\"path\"])", src, "the exit drain primes every session's leaf before its write")

    def test_perf_carries_the_checkpoint_counters_and_the_kernel_wires_the_three_events(self):
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(sorted(snap["checkpoints"]), ["coldFolds", "coldWrites", "dirty", "documentBytes", "droppedRestores", "fallbacks", "oversizeFolds", "readByPath",
                                                        "readBytes", "restored", "restoredFolds", "skippedFolds", "swept", "writes"])
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("em.checkpoint_write_dirty(budget_s=EXIT_CKPT_WRITE_BUDGET_S)", src,
                      "exit writes the dirty checkpoints in _drain_and_exit, bounded (2026-09-11: unbounded, it met the manager's SIGKILL)")
        self.assertIn("_persist_checkpoints(now)", src)
        self.assertIn("em.checkpoint_sweep()", src)


if __name__ == "__main__":
    unittest.main()
