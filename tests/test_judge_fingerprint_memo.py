#!/usr/bin/env python3
"""_discover_fingerprint() MEMOIZES each names/ entry, so a cache hit stats instead of re-reading.

The fingerprint is discover()'s cache-validity check, so it runs on EVERY discover() call — and every
call re-opened and re-read all ~226 names/ entries, then threw the contents away. Profiling a hot kernel
(the user 2026-07-22, whose laptop was running its fans) put `open (pathlib)` at ~110% of one core, the
whole of the pegged core; the fingerprint was the caller. Measured on that fleet: 7.85ms per call, and
0.76ms once each entry's parsed launch dir is memoized against its own mtime.

The memo keys on the entry's mtime — the same exact-change idiom _sdk_last_sid already uses two functions
up, NOT a time heuristic. What must stay LIVE is the project dir's mtime: that is the fork signal, so the
memo may cache the resolved PATH but must re-stat it every call. Synthetic only: placeholder UUIDs,
TESTHOST names, hermetic temp STATE.
"""
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
OTHER = "99999999-0000-1111-2222-333333333333"
NAME = "TESTHOST-session"


class NamesReadCounter:
    """Counts read_text() calls against names/ entries only — the syscall the memo is there to avoid."""

    def __init__(self, names_dir):
        self.names = str(names_dir)
        self.n = 0
        self._orig = pathlib.Path.read_text

    def __enter__(self):
        orig, names, box = self._orig, self.names, self

        def counting(self, *a, **k):
            if str(self).startswith(names):
                box.n += 1
            return orig(self, *a, **k)

        pathlib.Path.read_text = counting
        return self

    def __exit__(self, *exc):
        pathlib.Path.read_text = self._orig
        return False


class FingerprintMemoTest(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None                      # module-globals → reset between tests
        jd._discover_cache["result"] = None
        jd._namefp_memo.clear()
        jd._lastsid_memo.clear()
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))
        self._write_transcript(SID)

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        jd._namefp_memo.clear()
        shutil.rmtree(self._td, ignore_errors=True)

    def _write_transcript(self, sid, title=None):
        p = self.proj / (sid + ".jsonl")
        head = json.dumps({"type": "custom-title", "customTitle": title or NAME}) + "\n"
        p.write_text(head + json.dumps({"type": "user", "uuid": "u1"}) + "\n")

    def _touch(self, p, delta=10):
        """Move an mtime EXPLICITLY: a rewrite inside one filesystem timestamp tick would otherwise be
        indistinguishable, and this test is about the mtime signal, not about clock resolution."""
        st = os.stat(p)
        os.utime(p, (st.st_atime + delta, st.st_mtime + delta))

    # ── the fix ─────────────────────────────────────────────────────────────
    def test_an_unchanged_entry_is_not_reread(self):
        jd._discover_fingerprint()                            # first call populates the memo
        with NamesReadCounter(jd.NAMES) as c:
            jd._discover_fingerprint()
            jd._discover_fingerprint()
            jd._discover_fingerprint()
        self.assertEqual(c.n, 0, "an unchanged names/ entry is never re-read — that IS the fix")

    def test_the_first_call_does_read_every_entry(self):
        with NamesReadCounter(jd.NAMES) as c:
            jd._discover_fingerprint()
        self.assertEqual(c.n, 1, "a cold memo still reads each entry exactly once")

    def test_a_deleted_entry_is_dropped_from_the_memo(self):
        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        jd._discover_fingerprint()
        self.assertIn(OTHER, jd._namefp_memo)
        (jd.NAMES / OTHER).unlink()
        jd._discover_fingerprint()
        self.assertNotIn(OTHER, jd._namefp_memo,
                         "a retired session's entry is evicted — the memo can't grow without bound")

    # ── what must NOT change ────────────────────────────────────────────────
    def test_the_memo_does_not_change_the_fingerprint_value(self):
        def reference():
            """The pre-memo computation, inline: read every entry, every call."""
            out = []
            for f in sorted(jd.NAMES.iterdir()):
                try:
                    mt = f.stat().st_mtime
                except OSError:
                    continue
                try:
                    parts = f.read_text().rstrip("\n").split("\t")
                    cdir = parts[1] if len(parts) > 1 else ""
                except Exception:
                    cdir = ""
                pm = 0
                if cdir:
                    try:
                        pm = os.stat(jd._proj_dir(cdir)).st_mtime
                    except OSError:
                        pm = 0
                out.append((f.name, mt, pm, jd._sdk_last_sid(f.name) or ""))
            return tuple(out)

        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        self.assertEqual(jd._discover_fingerprint(), reference(), "cold memo matches the old computation")
        self.assertEqual(jd._discover_fingerprint(), reference(), "warm memo matches it too")

    def test_a_rewritten_entry_is_reread_and_moves_the_fingerprint(self):
        a = jd._discover_fingerprint()
        cdir2 = str(Path(self._td) / "work2")
        jd._proj_dir(cdir2).mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, cdir2))     # session relaunched from a new dir
        self._touch(jd.NAMES / SID)
        with NamesReadCounter(jd.NAMES) as c:
            b = jd._discover_fingerprint()
        self.assertEqual(c.n, 1, "a moved mtime forces exactly one re-read")
        self.assertNotEqual(a, b, "...and the new launch dir reaches the fingerprint")

    def test_a_new_fork_still_moves_the_fingerprint_on_a_warm_memo(self):
        """The memo may cache the RESOLVED project dir, but it must re-stat it every call: a fork landing
        in that dir bumps the DIR mtime while the names/ entry never moves."""
        a = jd._discover_fingerprint()
        self._write_transcript(FORK, title=NAME)
        self._touch(self.proj)
        b = jd._discover_fingerprint()
        self.assertNotEqual(a, b, "a fork's dir-mtime bump is read LIVE, never served from the memo")

    def test_a_new_entry_appears_through_a_warm_memo(self):
        a = jd._discover_fingerprint()
        (jd.NAMES / OTHER).write_text("%s\t%s" % ("TESTHOST-two", self.cdir))
        b = jd._discover_fingerprint()
        self.assertNotEqual(a, b)
        self.assertIn(OTHER, [row[0] for row in b])

    def test_discover_still_serves_its_cached_list(self):
        """End to end: the memo sits UNDER discover()'s cache, so an unchanged namespace still short-circuits."""
        now = int(time.time())
        a = jd.discover(now)
        self.assertIs(jd.discover(now), a)
        with NamesReadCounter(jd.NAMES) as c:
            jd.discover(now)
        self.assertEqual(c.n, 0, "a warm discover() reads no names/ entry at all")


if __name__ == "__main__":
    unittest.main()
