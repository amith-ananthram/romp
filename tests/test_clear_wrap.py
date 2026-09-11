#!/usr/bin/env python3
"""Clear is a SILENT discard (the user 2026-08-23, reversing the 2026-07-24 one-round wrap-up).

The wrap-up told the owning session its open card was being dismissed; the response turn then
routinely re-minted the very card the user had just cleared — a discard that answered back. The
gesture now only discards: the session hears NOTHING, and if anything real remains it is on the
user to ask a follow-up or say so themselves. Judge-side recognition of the historical
<!-- romp-clear-wrap --> marker STAYS: recorded transcripts still contain old wraps, and re-parsing
them must keep classifying those turns correctly. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_clearwrap", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
G_OPEN = SID + ":g1"
NOW = 1781100000


class SentNothing:
    def __init__(self):
        self.sent = []

    def send(self, sid, text):
        self.sent.append((sid, text))


class ClearIsSilent(unittest.TestCase):
    def setUp(self):
        # the kernel's judge is one module object shared by every test module in the process, so a store saved at its import-bound GOALDIR outlives the module and reaches every later module's feed for the placeholder sid (T281/T282); a private root for the duration.
        self._td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd._rebind_state(Path(self._td.name))

    def tearDown(self):
        jd._rebind_state(self._state)
        self._td.cleanup()

    def test_the_store_this_module_saves_does_not_outlive_it(self):
        # The residue pin (T281/T282): a store saved through the shared judge lands under this test's root and
        # the run-wide root (what every later module's feed reads) is exactly as it was.
        shared = Path(self._state) / "goals" / (SID + ".json")
        before = (shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None)
        st = jd.load_goals(SID)
        st["nodes"][SID + ":t282"] = jd.GuardedNode({"id": SID + ":t282", "text": "a note", "parentId": None, "nodeComplete": False,
                                                        "blocked": False, "cleared": False, "trail": [], "t": 1, "mt": 1, "log": []})
        jd.save_goals(SID, st)
        self.assertTrue((Path(self._td.name) / "goals" / (SID + ".json")).exists(), "the store lives under this module's root")
        self.assertEqual((shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None), before,
                         "the run-wide goals directory is untouched by this module")

    def test_clearing_an_open_card_sends_the_session_nothing(self):
        store = jd.load_goals(SID)
        store["nodes"][G_OPEN] = jd.GuardedNode({"id": G_OPEN, "text": "build the exporter",
                                                 "parentId": None, "nodeComplete": False,
                                                 "blocked": False, "cleared": False,
                                                 "t": NOW - 600, "mt": NOW - 60, "log": []})
        store["status"] = {G_OPEN: "working"}
        jd.save_goals(SID, store)
        be = SentNothing()
        saved = (km.Sessions.backend_for, km._live_map)
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        km._live_map = lambda: {SID: {"state": "idle"}}
        try:
            km._clear_all([G_OPEN])
        finally:
            km.Sessions.backend_for, km._live_map = saved
        self.assertEqual(be.sent, [], "a discard that answers back is the loop this retirement ends")
        rows = [json.loads(l) for l in (jd.STATE / "cleared.jsonl").read_text().splitlines()]
        self.assertTrue(any(r.get("id") == G_OPEN and r.get("op") == "clear" for r in rows),
                        "the discard itself still lands")

    def test_the_sender_machinery_is_gone_and_the_tombstone_stands(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertNotIn("def _clear_wrap_notify", src)
        self.assertNotIn("def _clear_wrap_body", src)
        self.assertIn("CLEAR IS SILENT", src)

    def test_the_judge_still_recognizes_historical_wrap_markers(self):
        src = open(os.path.join(BIN, "romp-judge")).read()
        self.assertIn("romp-clear-wrap", src,
                      "recorded transcripts contain old wraps; re-parsing must keep classifying them")


if __name__ == "__main__":
    unittest.main()
