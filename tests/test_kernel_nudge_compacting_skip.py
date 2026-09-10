"""Auto-nudge must SKIP a compacting session (the user 2026-07-06, who reported a nudge getting called after compact). The old
guard keyed on the tmux `st == "compacting"`, but SDK sessions have NO tmux state — so a /compact on an SDK
session left the raw-state check blind and a status-check nudge fired mid-compaction. The guard now also
consults the corroborated _compacting_now (the same signal the chip/timeline/chat compacting element use).
A compaction is not a stall, so nudging there is a false interrupt."""
import inspect
import os
import unittest
from romp_load import load_source
from pathlib import Path
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_ncs", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


class NudgeCompactingSkip(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        # snapshot every module global we monkeypatch so tearDown restores the real kernel
        self._orig = {n: getattr(km, n) for n in (
            "_auto_nudge_on", "_alive_sessions", "_wait_for_graph", "_session_flag",
            "_api_error", "_compacting_now")}
        self._orig_parsed = jd.parsed_session

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(km, n, v)
        jd.parsed_session = self._orig_parsed
        jd.STATE = self.saved_state
        self.td.cleanup()

    def test_guard_uses_the_corroborated_compacting_signal(self):
        # the per-session body lives in _auto_nudge_session since the 2026-07-16 isolation split
        src = inspect.getsource(km._auto_nudge_session)
        self.assertIn("or _compacting_now(sid)", src,
                      "the compacting skip must corroborate, not trust the tmux-only state")

    def _drive(self, compacting):
        # minimal alive SDK session (tmux {} → no raw state); a spy on the parse tells us whether the tick
        # got PAST the compacting guard. The spy returns an empty session so the tick continues harmlessly
        # (a raise would be swallowed by the tick's own `except Exception: continue`, hiding a guard failure).
        km._auto_nudge_on = lambda: True
        km._alive_sessions = lambda now, tmux: [{"sid": SID, "path": "/nonexistent.jsonl"}]
        km._wait_for_graph = lambda now, sids: {}
        km._session_flag = lambda sid, flag: False
        km._api_error = lambda path: None
        km._compacting_now = lambda sid: compacting
        reached = {"parse": False}

        def spy(*a, **k):
            reached["parse"] = True
            return {"turns": []}
        jd.parsed_session = spy
        km._auto_nudge_tick(2000, {})
        return reached["parse"]

    def test_a_compacting_sdk_session_is_skipped_before_the_parse(self):
        # SDK session: tmux is {} (no state), so only _compacting_now can catch the compaction.
        self.assertFalse(self._drive(compacting=True),
                         "a compacting session must be skipped BEFORE the parse (no nudge)")

    def test_a_non_compacting_session_is_NOT_skipped_by_this_guard(self):
        # control: _compacting_now False → the compacting guard lets the session through to the parse.
        self.assertTrue(self._drive(compacting=False),
                        "a non-compacting session must pass the compacting guard")


if __name__ == "__main__":
    unittest.main()
