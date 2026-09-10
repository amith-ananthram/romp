#!/usr/bin/env python3
"""Change gates on the pusher's idle path (2026-09-08): waiting must not cost CPU.

A profile of an idle devbox kernel (py-spy plus the kernel's own /perf) put most of a core into work whose
inputs had not changed: every comment thread's chat rebuilt per cycle (tests/test_comment_threads.py pins
that gate), the fold prefixes of those threads evicted just before they were rebuilt, the comments stores
re-decoded per cycle, the tasks root re-scanned per build for a session whose store never joins (a negative
memo declared and read but never written), the dormant regs' state tails re-read on every liveness read,
and the working notes re-read on every GET /sessions (polled about once a second by every session's postal
service). Each gate is stat-keyed: an unchanged input costs a stat; a moved input misses exactly.
Synthetic sids and paths; hermetic state."""
import contextlib
import json
import os
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
# The tasks root TaskJoinMiss writes under is $CLAUDE_CONFIG_DIR/tasks (default ~/.claude/tasks): floored here
# like the state root, before the loads, so a bare `python` or `pytest --noconftest` run never writes its
# synthetic stores into a developer's live ~/.claude (it did, review 2026-09-08). Under pytest, conftest's
# per-test fixture re-asserts its own floor; either way the root is a temp dir.
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
jd = load_source("romp_judge_gates", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_gates", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_gates", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


class CommentsStoreMemo(unittest.TestCase):
    def setUp(self):
        km._comments_memo.clear()
        (jd.STATE / "comments").mkdir(parents=True, exist_ok=True)

    def test_decoded_once_per_file_version_and_never_shared(self):
        km._save_comments(SID, {"threads": [{"tid": "t1", "sid": "s1", "status": "open"}]})
        real = km._load_comments
        calls = []
        km._load_comments = lambda sid: (calls.append(sid), real(sid))[1]
        try:
            a = km._load_comments_cached(SID)
            b = km._load_comments_cached(SID)
            self.assertEqual(calls, [SID], "one decode for two reads of the same file version")
            self.assertEqual(a, b)
            a["threads"].append({"tid": "junk"})
            self.assertEqual(len(km._load_comments_cached(SID)["threads"]), 1, "a reader's copy leaks nowhere")
            st0 = os.stat(km._comments_path(SID))
            km._save_comments(SID, {"threads": []})          # a rewrite (a rename) moves the key
            # pinned back to the PREVIOUS mtime_ns: the size and the inode still differ, and they must carry the
            # miss on their own (a same-tick rename-publish is exactly the case the three-part key exists for)
            os.utime(km._comments_path(SID), ns=(st0.st_atime_ns, st0.st_mtime_ns))
            self.assertEqual(km._load_comments_cached(SID)["threads"], [], "the new content is read on size and inode alone")
            self.assertEqual(len(calls), 2)
        finally:
            km._load_comments = real

    def test_a_missing_store_reads_empty_and_drops_the_memo(self):
        km._save_comments(SID, {"threads": [{"tid": "t1"}]})
        km._load_comments_cached(SID)
        os.unlink(km._comments_path(SID))
        self.assertEqual(km._load_comments_cached(SID), {"threads": []})
        self.assertNotIn(SID, km._comments_memo)

    def test_the_read_only_callers_use_the_memo_and_the_writers_do_not(self):
        import inspect
        self.assertIn("_load_comments_cached(sid)", inspect.getsource(km._comments_frame))
        self.assertIn("_load_comments_cached(sid)", inspect.getsource(km._comment_markers))
        self.assertNotIn("_load_comments_cached", inspect.getsource(km._comment_thread), "writers read fresh")


class TaskJoinMiss(unittest.TestCase):
    """The negative memo _task_store_resolve reads was never written: same fold pairs, the whole tasks root
    re-read on every call. The tasks root is $CLAUDE_CONFIG_DIR/tasks, floored to a temp dir by this module's
    preamble (and by conftest under pytest); tearDown removes the stores setUp and the tests wrote."""

    def setUp(self):
        km._task_join_miss.clear(); km._task_dir_hint.clear()
        self.root = km._task_store_dir("x").parent
        self._root_was_there = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        self.made = []
        self._store("session-aaaaaaaa", [(1, "alpha")])
        self._store("session-bbbbbbbb", [(2, "beta")])

    def tearDown(self):
        for d in self.made:
            shutil.rmtree(d, ignore_errors=True)
        if not self._root_was_there:
            shutil.rmtree(self.root, ignore_errors=True)
        km._task_join_miss.clear(); km._task_dir_hint.clear()

    def _store(self, name, tasks):
        d = self.root / name; d.mkdir(exist_ok=True); self.made.append(d)
        for i, subj in tasks:
            (d / ("%d.json" % i)).write_text(json.dumps({"id": str(i), "subject": subj}))
        return d

    def test_a_failed_join_is_remembered_until_the_fold_or_the_root_changes(self):
        # the root scan (a scandir plus a stat per store dir) runs on every call by design: it is the memo's
        # second key; what the memo saves is listing and decoding every task file under every store
        fold = [{"id": "7", "subject": "gamma"}]            # in no store: the join fails
        listed = []
        real = os.listdir
        with mock.patch.object(km.os, "listdir", side_effect=lambda p: (listed.append(str(p)), real(p))[1]):
            self.assertIsNone(km._task_store_resolve(SID, fold))
            self.assertEqual(len(listed), 2, "the first miss reads every store once")
            self.assertEqual(km._task_join_miss[SID][0], {("7", "gamma")}, "the memo carries the pairs...")
            self.assertEqual(sorted(n for n, _m in km._task_join_miss[SID][1]), ["session-aaaaaaaa", "session-bbbbbbbb"],
                             "...and the root's listing")
            self.assertIsNone(km._task_store_resolve(SID, fold))
            self.assertEqual(len(listed), 2, "the same fold under the same root does not re-read the stores")
            fold2 = [{"id": "7", "subject": "gamma"}, {"id": "8", "subject": "delta"}]
            self.assertIsNone(km._task_store_resolve(SID, fold2))
            self.assertEqual(len(listed), 4, "a changed fold re-reads")

    def test_a_miss_a_read_fault_produced_is_not_remembered(self):
        # a task file mid-rewrite decodes as nothing, so its pair is missing and the join fails; remembered, that
        # transient miss held until the fold next changed (status updates never change the pairs), and the todo
        # card showed the store as unreadable for the rest of the session (review 2026-09-08)
        fold = [{"id": "1", "subject": "alpha"}]
        (self.root / "session-aaaaaaaa" / "1.json").write_text('{"id": "1", "sub')      # a partial write
        self.assertIsNone(km._task_store_resolve(SID, fold))
        self.assertNotIn(SID, km._task_join_miss, "a fault is not a verdict")
        (self.root / "session-aaaaaaaa" / "1.json").write_text(json.dumps({"id": "1", "subject": "alpha"}))
        d = km._task_store_resolve(SID, fold)
        self.assertEqual(d.name, "session-aaaaaaaa", "the next call joins")
        self.assertNotIn(SID, km._task_join_miss)

    def test_a_store_that_appears_after_a_genuine_miss_joins_on_the_next_call(self):
        # Claude Code writes the store a moment after the TaskCreate the fold saw: the pairs do not change, the
        # root's listing does, and that is the memo's second key (review 2026-09-08)
        fold = [{"id": "3", "subject": "gamma"}]
        self.assertIsNone(km._task_store_resolve(SID, fold))
        self.assertIn(SID, km._task_join_miss, "premise: a genuine miss is remembered")
        self._store("session-cccccccc", [(3, "gamma")])
        d = km._task_store_resolve(SID, fold)
        self.assertIsNotNone(d, "the appeared store is found without the fold changing")
        self.assertEqual(d.name, "session-cccccccc")
        self.assertNotIn(SID, km._task_join_miss)

    def test_a_successful_join_clears_the_miss_memo(self):
        fold = [{"id": "1", "subject": "alpha"}]
        km._task_join_miss[SID] = {("9", "nothing")}
        d = km._task_store_resolve(SID, fold)
        self.assertEqual(d.name, "session-aaaaaaaa")
        self.assertNotIn(SID, km._task_join_miss)


class LastStateMemo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        (Path(self.td) / "states").mkdir()
        self.p = Path(self.td) / "states" / (SID + ".jsonl")
        sb._LAST_STATE_MEMO.clear()

    def test_the_tail_is_read_once_per_file_version(self):
        self.p.write_text('{"state": "working", "t": 1}\n{"state": "waiting", "t": 2}\n')
        reads = []
        real = sb._lines_from_end
        sb._lines_from_end = lambda p, block=65536: (reads.append(str(p)), real(p, block))[1]
        try:
            a = sb.last_state(Path(self.td), SID)
            b = sb.last_state(Path(self.td), SID)
            self.assertEqual(a, {"state": "waiting", "t": 2}); self.assertEqual(a, b)
            self.assertEqual(len(reads), 1, "the second read is a stat")
            a["state"] = "mutated"; b["state"] = "mutated too"       # the build-path result AND the hit-path one
            self.assertEqual(sb.last_state(Path(self.td), SID)["state"], "waiting", "callers get copies on both paths")
            with open(self.p, "a") as fh:
                fh.write('{"state": "working", "t": 3}\n')
            self.assertEqual(sb.last_state(Path(self.td), SID)["t"], 3, "an appended record is read")
            self.assertEqual(len(reads), 2)
        finally:
            sb._lines_from_end = real

    def test_a_missing_file_reads_empty_and_forgets(self):
        self.p.write_text('{"state": "waiting", "t": 2}\n')
        sb.last_state(Path(self.td), SID)
        self.p.unlink()
        self.assertEqual(sb.last_state(Path(self.td), SID), {})
        self.assertNotIn(str(self.p), sb._LAST_STATE_MEMO)


class WorkingNotesMemo(unittest.TestCase):
    def setUp(self):
        km._working_notes_memo[0] = None
        km.WORKING_DIR.mkdir(parents=True, exist_ok=True)
        for f in km.WORKING_DIR.iterdir():
            f.unlink()

    def test_read_once_per_directory_version(self):
        (km.WORKING_DIR / SID).write_text("editing the notes API\n")
        real = Path.read_text
        reads = []
        with mock.patch.object(Path, "read_text", lambda self, *a, **k: (reads.append(str(self)), real(self, *a, **k))[1]):
            a = km._working_notes(); b = km._working_notes()
            self.assertEqual(a, {SID: "editing the notes API"}); self.assertEqual(a, b)
            self.assertEqual(len(reads), 1)
            a[SID] = "mutated"; b.pop(SID)                           # the build-path result AND the hit-path one
            self.assertEqual(km._working_notes(), {SID: "editing the notes API"}, "callers get copies on both paths")
            (km.WORKING_DIR / SID).write_text("done, idle\n")
            st = os.stat(km.WORKING_DIR / SID); os.utime(km.WORKING_DIR / SID, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
            self.assertEqual(km._working_notes()[SID], "done, idle")
            (km.WORKING_DIR / SID).unlink()
            self.assertEqual(km._working_notes(), {}, "a removed note is gone at once")

    def test_a_note_that_vanishes_between_readdir_and_stat_costs_only_itself(self):
        # one try around the whole listing returned {} for the call, and every live session read as owning
        # nothing (which the postal contract takes as free ownership); the vanished entry alone is skipped
        gone = "11111111-2222-3333-4444-666666666666"
        (km.WORKING_DIR / SID).write_text("editing the notes API\n")
        (km.WORKING_DIR / gone).write_text("unlinked by a clear before the stat\n")
        real = os.scandir

        class Vanished:
            def __init__(self, e):
                self.name, self.path = e.name, e.path

            def stat(self, *a, **k):
                raise FileNotFoundError(self.path)

        @contextlib.contextmanager
        def scan(p):
            with real(p) as it:
                yield (Vanished(e) if e.name == gone else e for e in it)

        with mock.patch.object(km.os, "scandir", scan):
            self.assertEqual(km._working_notes(), {SID: "editing the notes API"}, "the other note is still returned")


class FoldEvictionKeepsThreads(unittest.TestCase):
    """_push evicts every fold entry whose sid is not a shown tab, EXCEPT the comment threads the previous
    cycle's comments loop built: evicting those made every thread build a cold reshape. Pinned at the source
    and driven for real (the test_chat_empty_build_guard.py harness: one chat client, one tab, the builders
    and the sends stubbed)."""

    PSID = "11111111-2222-3333-4444-777777777777"           # the one shown tab
    TSID = "66666666-7777-8888-9999-aaaaaaaaaaaa"           # a thread: never a tab

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, self.PSID + ".jsonl")
        with open(self.path, "w") as f:
            f.write("{}\n")                                 # stat-able: the build cache keys on it
        self.sess = {"sid": self.PSID, "name": "web", "anchor": None, "path": self.path, "mtime": 0}
        self.tmux = {self.PSID: {"state": "waiting", "color": "#888888", "since": 0, "model": "", "effort": "",
                                 "context": None, "backend": "tmux"}}
        self.client = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None}
        self._saved_keep = [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])]
        km._thread_fold_keep[0], km._thread_fold_keep[1] = set(), set()
        self._forget()

    def tearDown(self):
        km._thread_fold_keep[0], km._thread_fold_keep[1] = self._saved_keep
        self._forget()
        self.td.cleanup()

    def _forget(self):
        with km._chat_fold_lock:
            km._chat_fold.pop(self.TSID, None)
        for d in (km._built_chat, km._prev_chat_events, km._prev_chat_ledger):
            d.pop(self.PSID, None)

    def _cycle(self):
        frame = {"type": "session", "id": self.PSID, "name": "web", "color": None, "status": {"state": "ready"},
                 "ledger": None, "events": [{"kind": "assistant", "uuid": "11111111-2222-3333-4444-0000000000a1", "md": "hi"}]}
        with mock.patch.object(km, "_alive_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_chat_tab_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_warm_fleet_bg", lambda now: None), \
                mock.patch.object(km, "_tmux_sessions", lambda: dict(self.tmux)), \
                mock.patch.object(km, "build_session", lambda sid, now, tmux=None, **kw: dict(frame)), \
                mock.patch.object(km, "_cached_feed", lambda *a, **k: {"working": [], "awaiting": [], "asks": []}), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, sig=None, kind="full": None):
            km._push([self.client], tmux=self.tmux)

    def _held(self):
        with km._chat_fold_lock:
            return self.TSID in km._chat_fold

    def test_a_thread_built_last_cycle_keeps_its_fold_prefix_and_ages_out_after_one_idle_cycle(self):
        km._chat_fold_put(self.TSID, {"probe": 1})
        km._thread_fold_keep[1].add(self.TSID)              # what a thread build does inside a cycle's comments loop
        self._cycle()
        self.assertTrue(self._held(), "the thread the previous cycle built keeps its prefix")
        km._thread_fold_keep[1].add(self.TSID)              # built again this cycle
        self._cycle()
        self.assertTrue(self._held(), "a thread built every cycle is never evicted")
        self._cycle()                                       # nothing rebuilt it
        self.assertFalse(self._held(), "a thread that stopped being built is evicted after one cycle")

    def test_push_keeps_the_fold_prefixes_of_last_cycles_threads(self):
        import inspect
        src = inspect.getsource(km._push)
        swap = "_thread_fold_keep[0], _thread_fold_keep[1] = _thread_fold_keep[1], set()"
        keep = "keep = shown_sids | _thread_fold_keep[0]"
        self.assertIn(keep, src)
        self.assertIn(swap, src)
        self.assertIn("if sid not in keep:", src, "the eviction tests the keep set, not the shown tabs")
        self.assertLess(src.index(swap), src.index(keep),
                        "the swap runs BEFORE the eviction, so the keep set is last cycle's, not the one before")
        self.assertIn("_thread_fold_keep[1].add(tsid)", inspect.getsource(km._thread_events))


if __name__ == "__main__":
    unittest.main()
