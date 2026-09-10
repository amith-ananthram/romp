#!/usr/bin/env python3
"""busy_count — the quiet-window gate for deferred deploy refreshes (the user 2026-07-20). Peers'
`romp --refresh` deploys bounced the kernel 11x in one day, each cutting in-flight SDK turns; the
manager now defers a deploy until the kernel's /busy reports zero. busy_count must mirror exactly what
a restart would DISRUPT: sessions with inflight > 0, plus (T240) sessions with live background work —
a Workflow run or a background agent, which has no turn in flight between its own turns — and never
ended sessions or queued turns (the persisted queue survives a bounce losslessly). Synthetic; no
SDK import."""
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_busy", os.path.join(BIN, "romp_sdk_backend.py"))


def _sess(inflight, ended=False, bg=False):
    s = mock.Mock()
    s.inflight = inflight
    s.ended = ended
    s._bg_tasks = {"w1": {"type": "local_workflow"}} if bg else {}   # a Mock attribute would read truthy
    s._subagents = {}
    return s


class BusyCount(unittest.TestCase):
    def _backend(self):
        return sb.SdkBackend(tempfile.mkdtemp(), "/bin/true", lambda *a, **k: None)

    def test_counts_inflight_and_background_unended_sessions(self):
        be = self._backend()
        be.sessions = {"a": _sess(1), "b": _sess(0), "c": _sess(2), "d": _sess(1, ended=True)}
        self.assertEqual(be.busy_count(), 2,
                         "idle and ended sessions are not turns a restart would cut")
        be.sessions["e"] = _sess(0, bg=True)            # a Workflow between its own turns (T240)
        self.assertEqual(be.busy_count(), 3, "background work is disrupted by a restart too")
        self.assertEqual(be.busy_breakdown(), (2, 1), "counted once each, in flight first")
        be.sessions["f"] = _sess(0, ended=True, bg=True)
        self.assertEqual(be.busy_count(), 3, "an ended session's leftovers are nobody's work")

    def test_empty_backend_is_quiet(self):
        self.assertEqual(self._backend().busy_count(), 0)

    def test_kernel_serves_busy_route(self):
        # Source pin: the kernel must expose busy_count as GET /busy (auth-exempt, like /healthz) —
        # the manager polls it tokenless while a deploy refresh is pending.
        ksrc = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        get_src = ksrc.split("def do_GET", 1)[1]     # the GET router only (do_HEAD authorizes too)
        self.assertIn('if p == "/busy":', get_src)
        self.assertIn("busy_count", get_src)
        # served BEFORE the auth gate, beside the other exempt routes
        self.assertLess(get_src.index('if p == "/busy":'),
                        get_src.index("ok, self._set_cookie, why = self._authorize(q)"))


if __name__ == "__main__":
    unittest.main()
