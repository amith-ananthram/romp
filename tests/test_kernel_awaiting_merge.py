#!/usr/bin/env python3
"""The nimbus false-block chain (the user 2026-07-11): the SDK snapshot carried the live bg-task set
but Sessions.live()'s merge never copied `bgTasks` into the merged map — so every consumer
(_session_awaiting source 0.5, the #bg-tasks live gate, the auto-nudge gate) read None, the session
never read awaiting, the auto-nudge fired on a genuinely-waiting session, and the failed nudge
hard-blocked its card with "it needs your direction". Two guards here:

  * the MERGE itself carries bgTasks through — tested through the REAL Sessions.live() with a fake
    backend, exactly the seam the earlier _live_map-stubbing tests bypassed;
  * _mark_nudge_failed never converts a nudge into a block while the session is AWAITING — its reply
    ("waiting on the experiment") DID explain itself.

SYNTHETIC fixtures only (placeholder UUIDs, invented text).
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_awm", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_awm", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
TIMER = {"desc": "20-minute timer for campaign-start check", "type": "local_bash",
         "since": 1, "toolUseId": "tu1", "lastTool": ""}


class _FakeSdkBackend:
    def __init__(self, snap):
        self._snap = snap

    def live_sessions(self):
        return {SID: dict(self._snap)}


class MergeCarriesBgTasks(unittest.TestCase):
    def test_the_real_merge_carries_bgTasks_and_subagents_through(self):
        # through the REAL Sessions.live(), not a _live_map stub — the seam the bug lived in
        snap = {"state": "waiting", "since": "1", "model": "Fable 5", "effort": "xhigh",
                "modelPending": False, "effortPending": False, "retryCount": 0, "retryInfo": None,
                "ctx": 10, "mode": "auto", "subagents": [], "bgTasks": [dict(TIMER)]}
        saved_sdk, saved_codex = km._sdk, km._codex
        fake = _FakeSdkBackend(snap)
        km._sdk = lambda: fake
        km._codex = lambda: None          # no Codex backend on this box: the merge is the SDK rows alone
        try:
            out = km.Sessions.live()
        finally:
            km._sdk, km._codex = saved_sdk, saved_codex
        self.assertIn(SID, out)
        self.assertEqual([t["desc"] for t in out[SID]["bgTasks"]],
                         ["20-minute timer for campaign-start check"],
                         "the merged map carries the live bg-task set — the awaiting/nudge gates read it here")
        self.assertEqual(out[SID]["subagents"], [])
        # ...and _session_awaiting source 0.5 fires off exactly that merged map
        saved_sessions = km._live_map
        km._live_map = lambda: out
        try:
            why = km._session_awaiting(SID, "/nonexistent", True)
        finally:
            km._live_map = saved_sessions
        self.assertEqual(why, {"kind": "task", "since": 1,   # the dispatch stamp → the chips' elapsed readout (the user 2026-08-23)
                               "why": "waiting on a background command: 20-minute timer for campaign-start check",   # "command" since slice 2 (2026-09-05)
                               "count": 1,
                               "items": [{"kind": "commands", "id": "tu1", "label": "20-minute timer for campaign-start check", "since": 1,
                                          "stoppable": True}],   # the one awaited row; stoppable = a lifecycle-set task (2026-09-10)
                               "tasks": ["20-minute timer for campaign-start check"]})


class NudgeFailedRespectsAwaiting(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        # patch the KERNEL's own jd instance (km imports its own copy; a separately-loaded jd is a
        # different module object and the kernel would keep reading the live state dirs)
        self._saved = (km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of)
        km.jd.STATE = td
        km.jd.GOALDIR = td / "goals"
        km.jd.GOALDIR.mkdir(parents=True)
        km._autonudge_cache.clear()
        self.gid = SID + ":g1"
        (km.jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "lastNode": self.gid, "placements": {}, "status": {},
            "nodes": {self.gid: {"id": self.gid, "text": "run the long experiment", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "trail": [], "t": 100, "mt": 100, "log": []}}}))
        (td / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {self.gid: {"count": 1, "lastTurnId": "t1"}}}))
        km._path_of = lambda sid, now=None: "/nonexistent"

    def tearDown(self):
        km.jd.STATE, km.jd.GOALDIR, km._session_awaiting, km._path_of = self._saved
        km._autonudge_cache.clear()
        self.td.cleanup()

    def test_awaiting_session_never_gets_the_failure_block(self):
        km._session_awaiting = lambda sid, path, idle, stamp=False: {"kind": "task", "why": "waiting on a background task: the experiment watcher"}
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertFalse(store["nodes"][self.gid]["blocked"],
                         "an awaiting session's nudge is never converted into a needs-you block")
        rec = km._auto_nudge_data()["nudged"][self.gid]
        self.assertFalse(rec.get("failed"), "the episode isn't failed either — it re-arms cleanly")

    def test_a_genuinely_stalled_session_still_gets_the_block(self):
        km._session_awaiting = lambda sid, path, idle, stamp=False: None
        km._mark_nudge_failed(self.gid)
        store = km.jd.load_goals(SID)
        self.assertTrue(store["nodes"][self.gid]["blocked"], "the existing stall→block behavior stands")
        self.assertTrue(km._auto_nudge_data()["nudged"][self.gid].get("failed"))


class _FakeCodexBackend:
    def __init__(self, snap):
        self._snap = snap

    def live_sessions(self):
        return {SID: dict(self._snap)}


class MergeReadsACodexSince(unittest.TestCase):
    """The Codex backend stamps since = time.time() (a float) and live_sessions ships it raw, where the
    SDK backend ships str(int(...)). Sessions.live() parsed since with a digits-only test, so every
    Codex row's since merged as None and _idle_faded never fired: a Codex session idle past FADED_S
    stayed a solid "ready" in the chat tab and the timeline lane while every idle SDK tab and lane
    dimmed (2026-09-11). Through the REAL merge with a fake Codex backend, like the class above. The SDK
    arm has the same latent gap, which is why the parse was widened in the merge and not in the Codex
    backend: its dormant read serves the state log's LAST record, and the machineCut and resume-fork
    lines carry a float t by design (a bound that must not move earlier), so a dormant SDK row whose last
    line is one of those ships a float string too — the third case drives that arm."""

    def test_a_codex_rows_float_since_merges_as_an_epoch_and_fades_past_the_hour(self):
        snap = {"state": "waiting", "since": 1781100000.5, "model": "gpt-5-test", "effort": "",
                "mode": "sandboxed", "context": None, "compactPct": None, "backend": "codex",
                "name": "web", "cwd": "/TESTDIR", "color": None}
        saved_sdk, saved_codex = km._sdk, km._codex
        fake = _FakeCodexBackend(snap)
        km._sdk = lambda: None            # no SDK backend on this box: the merge is the Codex rows alone
        km._codex = lambda: fake
        try:
            out = km.Sessions.live()
        finally:
            km._sdk, km._codex = saved_sdk, saved_codex
            km._LIVE_LAST_ROWS.pop("codex", None)
        self.assertIn(SID, out)
        self.assertEqual(out[SID]["backend"], "codex")
        self.assertEqual(out[SID]["since"], 1781100000,
                         "the merged map carries the Codex row's since as an epoch — the faded rule reads it here")
        # ...and the one faded rule fires off exactly that merged value, as it does for an SDK row
        self.assertFalse(km._idle_faded("ready", out[SID]["since"], 1781100000 + km.FADED_S))
        self.assertTrue(km._idle_faded("ready", out[SID]["since"], 1781100000 + km.FADED_S + 1),
                        "a Codex session idle past the hour wears the faded look like an SDK one")

    def test_a_dormant_sdk_rows_float_since_merges_as_an_epoch_too(self):
        # the dormant SDK read ships str(last_state(...)["t"]); after a machineCut or resume-fork line that
        # is "1781100000.5", where a state line's is "1781100000" — the SDK arm of the merge parses it too
        snap = {"state": "waiting", "since": "1781100000.5", "model": "Fable 5", "effort": "",
                "modelPending": False, "effortPending": False, "retryCount": 0, "retryInfo": None,
                "ctx": None, "mode": "auto", "subagents": [], "bgTasks": []}
        saved_sdk, saved_codex = km._sdk, km._codex
        fake = _FakeSdkBackend(snap)
        km._sdk = lambda: fake
        km._codex = lambda: None          # no Codex backend on this box: the merge is the SDK rows alone
        # a fresh process: no previous SDK rows to go on. This module's state root has no sdk/ directory (no
        # boot pass ran), and with rows left from an earlier read the merge would judge the registry blind
        # and serve THOSE rows instead of this fake's (_sdk_records_blind)
        km._LIVE_LAST_ROWS.pop("sdk", None)
        try:
            out = km.Sessions.live()
        finally:
            km._sdk, km._codex = saved_sdk, saved_codex
            km._LIVE_LAST_ROWS.pop("sdk", None)
        self.assertIn(SID, out)
        self.assertEqual(out[SID]["backend"], "sdk")
        self.assertEqual(out[SID]["since"], 1781100000,
                         "a dormant SDK row whose last state-log line is a machineCut or resume-fork ships a float since")

    def test_the_since_parser_reads_a_float_string_and_stays_none_for_non_numbers(self):
        self.assertEqual(km._num("1781100000.5"), 1781100000)
        self.assertEqual(km._num("1781100000"), 1781100000)
        self.assertEqual(km._num("-3"), -3)
        for bad in ("", "  ", "soon", "nan", "inf", None):
            self.assertIsNone(km._num(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
