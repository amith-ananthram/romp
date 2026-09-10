#!/usr/bin/env python3
"""Concurrent same-session publishes must not share a temp file (2026-08-27).

write_archive, save_goals and save_goal_archive each publish via temp-write + rename, with the
temp named by PID alone — but the writers here are concurrent THREADS of one process: judge
passes run per-session workers while the kernel stamps blocks and archives clears on threads of
its own. Two threads publishing the SAME session's file therefore minted the SAME temp path:
the loser renamed a temp the winner had already moved (FileNotFoundError out of a plain save),
or renamed the other writer's half-written bytes into place. Observed live on a self-hosted
deployment when two writers saved one session's goals in the same tick.

These tests pin: (1) a two-thread save_goals hammer on one sid never raises and leaves a whole,
parseable publish behind (unfixed, the first raise lands well inside a second); (2) the temp
name is keyed by (pid, thread, call), not pid alone; (3) all three publishers mint through the
one shared namer, so no call site keeps the old colliding name. All fixtures SYNTHETIC; the sid
is this module's own (goal-store tests never share the placeholder sid — override journals are
sid-keyed and node ids collide across modules).
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the load — the module resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_tmp_race", os.path.join(BIN, "romp-judge"))

SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeee0001"   # this module's own synthetic sid


class SameSidPublishRace(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        self._switch = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)   # the race window is a few bytecodes wide — preempt often

    def tearDown(self):
        sys.setswitchinterval(self._switch)
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def test_two_threads_saving_one_sid_never_lose_a_temp(self):
        # The live shape: two threads of one process publish the SAME session's goal store in
        # the same tick. With a pid-only temp name both mint one path — the loser renames a
        # temp the winner already moved, or publishes the other writer's half-written bytes.
        stop = time.monotonic() + 2.0
        errors = []

        def hammer(k):
            i = 0
            try:
                while time.monotonic() < stop and not errors:
                    jd.save_goals(SID, {"rompUuid": SID, "nodes": {}, "status": {},
                                        "writer": k, "i": i})
                    i += 1
            except Exception as e:   # noqa: BLE001 — the raise IS the defect under test
                errors.append(e)

        threads = [threading.Thread(target=hammer, args=(k,)) for k in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"a same-sid save raised cross-thread: {errors[:3]!r}")
        published = json.loads((jd.GOALDIR / (SID + ".json")).read_text())
        self.assertEqual(published.get("rompUuid"), SID, "the survivor is a whole publish")
        self.assertEqual(list(jd.GOALDIR.glob(SID + ".json.tmp.*")), [],
                         "every publish consumed its own temp — no orphans left behind")

    def test_the_temp_name_is_keyed_by_thread_not_just_pid(self):
        def parts(p):
            self.assertTrue(p.name.startswith(SID + ".json.tmp."),
                            f"temp lives beside the target it publishes: {p.name}")
            return [int(x) for x in p.name[len(SID + ".json.tmp."):].split(".")]

        mine = [parts(jd._publish_tmp(jd.GOALDIR, SID)) for _ in range(2)]
        other = []
        t = threading.Thread(target=lambda: other.append(parts(jd._publish_tmp(jd.GOALDIR, SID))))
        t.start()
        t.join()
        for pid, ident, _n in mine:
            self.assertEqual(pid, os.getpid(), "the pid key stays (cross-process writers)")
            self.assertEqual(ident, threading.get_ident(), "the minting thread's ident is in the name")
        self.assertEqual(other[0][1], t.ident, "another thread's temp carries ITS ident")
        self.assertNotEqual(other[0][1], mine[0][1], "so two threads never share a temp path")
        self.assertNotEqual(mine[0][2], mine[1][2],
                            "consecutive mints differ even within one thread (the call counter)")

    def test_all_three_publishers_mint_through_the_shared_namer(self):
        minted = []
        real = jd._publish_tmp

        def spy(dirpath, fsid):
            minted.append(dirpath)
            return real(dirpath, fsid)

        jd._publish_tmp = spy
        try:
            jd.save_goals(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
            jd.write_archive(SID, {"headline": "x", "abstract": "y"})
            jd.save_goal_archive(SID, {"rompUuid": SID, "nodes": {}, "status": {}})
        finally:
            jd._publish_tmp = real
        self.assertEqual(minted, [jd.GOALDIR, jd.ARCHDIR, jd.GOALARCHDIR],
                         "every <sid>.json publisher mints its temp via the thread-keyed namer")


if __name__ == "__main__":
    unittest.main()
