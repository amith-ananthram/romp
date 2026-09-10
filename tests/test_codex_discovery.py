#!/usr/bin/env python3
"""Codex transcript-root discovery (kernel/judge.py _codex_rows): sessions materialized under
STATE/codex/projects are discovered alongside the Claude roots — same (fsid, path, anchor_sid, name)
tuple contract — and the discover cache's fingerprint sees a registry change exactly when the list
would change. Synthetic data throughout per CLAUDE.md.

Run:    python3 tests/test_codex_discovery.py
"""
import json
import os
import re
import tempfile
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
jd = load_source("romp_judge_codexdisc", os.path.join(ROOT, "bin", "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
TID = "01911111-2222-7333-8444-555555555555"


class CodexDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def _mint(self, cwd="/TESTDIR", name="cx", sid=SID, tid=TID, dead=False, mtime=None):
        enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
        proj = jd.CODEXDIR / "projects" / enc
        proj.mkdir(parents=True, exist_ok=True)
        p = proj / (tid + ".jsonl")
        p.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                 "timestamp": "2026-06-10T14:00:00.000Z",
                                 "message": {"role": "user", "content": "synthetic prompt"}}) + "\n")
        os.utime(p, (mtime or NOW, mtime or NOW))
        reg = {}
        try:
            reg = json.loads((jd.CODEXDIR / "registry.json").read_text())
        except Exception:
            pass
        reg[sid] = {"tid": tid, "name": name, "cwd": cwd, "dead": dead}
        (jd.CODEXDIR / "registry.json").write_text(json.dumps(reg))
        return p

    def test_codex_session_discovered_with_tuple_contract(self):
        # the STABLE SID is the identity slot (the v1.3.13 audit's P1: the app-server TID there
        # meant live rows never joined liveness — keyed on SID — the picker offered a not-running
        # TID row, and reviving it shelled `romp resume <TID>` through the tmux path); the TID
        # rides only in the transcript PATH
        p = self._mint()
        rows = jd._discover_impl(NOW)
        cx = [r for r in rows if r[0] == SID]
        self.assertEqual(len(cx), 1)
        fsid, path, anchor, name = cx[0]
        self.assertEqual((fsid, str(path), anchor, name), (SID, str(p), SID, "cx"))
        self.assertFalse(any(r[0] == TID for r in rows),
                         "the thread id is transcript metadata, never a session identity")

    def test_window_ages_out(self):
        self._mint(mtime=NOW - 10 * 24 * 3600)
        rows = jd._discover_impl(NOW)          # default window is far shorter than 10 days
        self.assertEqual([r for r in rows if r[2] == SID], [],
                         "aged out by the window — the old filter-by-TID was unsatisfiable by "
                         "construction after the identity fix and passed with age-out broken "
                         "(the r37 mutant hunt)")

    def test_registry_change_invalidates_discover_cache(self):
        self._mint()
        first = jd.discover(NOW)
        self.assertEqual(len([r for r in first if r[0] == SID]), 1)
        # a second session lands: only the registry + a new file change — the fingerprint must move
        time.sleep(0.02)
        tid2 = TID.replace("01911111", "01922222")
        sid2 = SID.replace("1111", "2222", 1)
        self._mint(name="cx2", sid=sid2, tid=tid2)
        # bump the registry mtime past same-second granularity for the stat-based signature
        os.utime(jd.CODEXDIR / "registry.json", (NOW + 5, NOW + 5))
        second = jd.discover(NOW)
        self.assertEqual(len([r for r in second if r[0] == sid2]), 1,
                         "new codex session invisible — fingerprint missed the registry change")

    def test_missing_or_broken_registry_is_harmless(self):
        self.assertEqual(jd._codex_rows(0, set()), [])
        jd.CODEXDIR.mkdir(parents=True, exist_ok=True)
        (jd.CODEXDIR / "registry.json").write_text("{not json")
        self.assertEqual(jd._codex_rows(0, set()), [])


class StagingStraysNeverDiscovered(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jd._rebind_state(self.tmp)
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def test_a_leaked_names_tmp_is_not_a_session(self):
        # the r33 verification: discover() is THE primary NAMES consumer and read a leaked
        # staging stray as a phantom session — it even stole a live session's fork lane. The
        # stray is RESOLVABLE here (its transcript exists on disk): with the filter removed —
        # or filtering the wrong variable, f.stem, which strips the suffix (the r34 mutant
        # hunt) — discover returns the phantom and this test fails
        stray = "99999999-8888-7777-6666-555555555555.tmp"
        (jd.NAMES / "11111111-2222-3333-4444-555555555555").write_text("web\t/TESTDIR\t\t\n")
        (jd.NAMES / stray).write_text("phantom\t/TESTDIR\t\t\n")
        import re as _re
        with tempfile.TemporaryDirectory() as pd:
            proj = Path(pd) / _re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath("/TESTDIR"))
            proj.mkdir(parents=True)
            tr = proj / (stray + ".jsonl")
            tr.write_text(json.dumps({"type": "user", "uuid": "u1", "parentUuid": None,
                                      "timestamp": "2026-06-10T14:00:00.000Z",
                                      "message": {"role": "user",
                                                  "content": "synthetic prompt"}}) + "\n")
            os.utime(tr, (NOW, NOW))
            from unittest import mock
            with mock.patch.object(jd, "PROJECTS", Path(pd)):
                found = jd.discover(NOW)
        sids = [t[2] for t in found]
        self.assertNotIn(stray, sids)
        self.assertFalse(any(str(s).endswith(".tmp") for s in sids), sids)
        # and the fingerprint ignores it too: a stray appearing must not invalidate caches
        fp1 = jd._discover_fingerprint()
        (jd.NAMES / "99999999-8888-7777-6666-555555555555.tmp").write_text("changed\t/x\t\t\n")
        self.assertEqual(fp1, jd._discover_fingerprint(),
                         "staging churn is invisible to the discovery fingerprint")


class ModelsRouteConsultsCodexOnlyWhenOptedIn(unittest.TestCase):
    """GET /models spawns `codex app-server` through model_catalog(); on the default (Claude) path it
    must not be consulted at all (PR #885 review: every dashboard load spawned or failed to spawn it)."""

    def test_the_gate_reads_opt_in_state_not_readiness(self):
        src = open(os.path.join(ROOT, "kernel", "kernel.py")).read()
        self.assertIn('if cx and (_default_backend() == "codex" or _judge_engine_name() == "codex"', src)
        self.assertIn("or bool(cx.live_sessions())):", src)
        self.assertNotIn("if cx:\n                    try:\n                        cx_models = cx.model_catalog()", src)
        self.assertIn("def _judge_engine_name():", src)
