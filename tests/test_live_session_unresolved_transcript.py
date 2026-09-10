"""A LIVE session stays in the tab list while its transcript file is briefly unreadable (T258, the user
2026-09-08). _alive_sessions resolves each live sid through discover(), which scans project dirs for
<sid>.jsonl; a file moved aside leaves discover with no entry, so the live session dropped off the chat tab
list and the pane tore its tab down (T236 omission). A transient read failure is not a state change: a
session whose tmux lane is live stays listed with a names/-derived stub, said once per episode. Behavioural
pins driving the real _alive_sessions / _chat_tab_sessions with a scripted names registry and a vanishing
transcript; synthetic ids only.
"""
import io
import os
import sys
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_liveunresolved", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-4333-8444-000000000258"
NOW = 1781300000


class _World(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.cwd = root / "work"; self.cwd.mkdir()
        # The kernel's judge is one module object shared by every test module in the process, so a names entry
        # and the session order this world writes at its import-bound root outlived the module and were read by
        # every later module's feed (T281). The world lives under this test's root for the duration.
        self._state = jd.STATE
        jd._rebind_state(root / "state")
        # names registry entry: name \t cwd \t #bg  (written at launch for both backends). The kernel binds
        # NAMES at import while the ONE romp_judge module is re-executed by every later kernel load in the
        # suite, so km.NAMES and jd.NAMES can name different roots under the full run — align them.
        self._names_patch = mock.patch.object(km, "NAMES", jd.NAMES); self._names_patch.start()
        # …and the transcript root: jd.PROJECTS defaults to the REAL ~/.claude/projects when CLAUDE_CONFIG_DIR is
        # unset, so every project dir this test makes lives under its own temp root (never live state)
        self._proj_patch = mock.patch.object(jd, "PROJECTS", root / "projects"); self._proj_patch.start()
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("web\t%s\t#3fa7c9\n" % self.cwd)
        # the real transcript, where discover expects it
        self.proj = jd._proj_dir(str(self.cwd)); self.proj.mkdir(parents=True, exist_ok=True)
        self.tx = self.proj / (SID + ".jsonl")
        self.tx.write_text('{"type":"user","uuid":"11111111-2222-4333-8444-0000000258a1","parentUuid":null,'
                           '"timestamp":"2026-09-01T00:00:00.000Z","sessionId":"%s",'
                           '"message":{"role":"user","content":"hi"}}\n' % SID)
        self.tmux = {SID: {"state": "waiting", "since": NOW - 60, "model": "", "effort": "",
                           "context": None, "backend": "tmux", "color": "#3fa7c9"}}
        if hasattr(km, "_UNRESOLVED_LIVE_NOTED"):
            km._UNRESOLVED_LIVE_NOTED.discard(SID)   # tolerate a pre-fix kernel so the premise test is a real red-first
        # discover caches on a namespace fingerprint; clear it so the vanish is seen
        jd._discover_cache.clear(); jd._namefp_memo.clear()

    def tearDown(self):
        self._proj_patch.stop()
        self._names_patch.stop()
        if hasattr(km, "_UNRESOLVED_LIVE_NOTED"):
            km._UNRESOLVED_LIVE_NOTED.discard(SID)
        for f in (jd.NAMES / SID,):
            try: f.unlink()
            except OSError: pass
        self.td.cleanup()
        jd._rebind_state(self._state)   # last: everything above cleans under THIS test's root

    def _alive_sids(self):
        jd._discover_cache.clear()
        return {s["sid"] for s in km._alive_sessions(NOW, self.tmux)}

    def test_present_while_the_transcript_is_readable(self):
        self.assertIn(SID, self._alive_sids(), "premise: a live session with its transcript is listed")

    def test_stays_listed_when_the_transcript_file_is_moved_aside(self):
        self.assertIn(SID, self._alive_sids())
        moved = self.tx.with_suffix(".jsonl.away")
        self.tx.rename(moved)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            alive = km._alive_sessions(NOW, self.tmux)
        sids = {s["sid"] for s in alive}
        self.assertIn(SID, sids, "a LIVE session whose transcript vanished for a cycle stays in the tab list")
        row = next(s for s in alive if s["sid"] == SID)
        self.assertEqual(row["name"], "web", "with its last-known name from names/")
        self.assertEqual(row["path"], str(self.tx), "and the EXPECTED path, re-resolved when the file returns")
        self.assertIn("LIVE but its transcript could not be resolved", err.getvalue(), "said on stderr")
        # and the chat tab list carries it, so the tabOrder push never omits it
        moved.rename(self.tx)   # (restore for the next assertion's discover)
        jd._discover_cache.clear()

    def test_the_note_is_said_once_per_episode_and_re_arms_after_the_file_returns(self):
        moved = self.tx.with_suffix(".jsonl.away"); self.tx.rename(moved)
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            km._alive_sessions(NOW, self.tmux)
            jd._discover_cache.clear()
            km._alive_sessions(NOW, self.tmux)
        self.assertEqual(err.getvalue().count("could not be resolved"), 1, "one line per episode, not per cycle")
        moved.rename(self.tx); jd._discover_cache.clear()
        self.assertIn(SID, self._alive_sids(), "resolved again once the file is back")
        self.assertFalse(getattr(km, "_UNRESOLVED_LIVE_NOTED", set()).__contains__(SID), "the episode note re-arms after content returns")

    def test_the_chat_tab_list_keeps_the_live_session(self):
        moved = self.tx.with_suffix(".jsonl.away"); self.tx.rename(moved)
        jd._discover_cache.clear()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            tabs = km._chat_tab_sessions(NOW, self.tmux)
        self.assertIn(SID, {s["sid"] for s in tabs}, "the tabOrder push (built from this) never omits a live session")
        moved.rename(self.tx)

    def test_a_dead_session_whose_file_is_gone_is_NOT_stubbed(self):
        moved = self.tx.with_suffix(".jsonl.away"); self.tx.rename(moved)
        jd._discover_cache.clear()
        with mock.patch.object(sys, "stderr", io.StringIO()):
            alive = km._alive_sessions(NOW, {})   # tmux says nothing is live
        self.assertNotIn(SID, {s["sid"] for s in alive}, "a read failure only KEEPS a live session; a dead one still drops")
        moved.rename(self.tx)

    def test_an_unknown_live_sid_with_no_names_entry_is_left_out(self):
        (jd.NAMES / SID).unlink()
        moved = self.tx.with_suffix(".jsonl.away"); self.tx.rename(moved)
        jd._discover_cache.clear()
        with mock.patch.object(km, "_sdk", lambda: None), mock.patch.object(sys, "stderr", io.StringIO()):
            alive = km._alive_sessions(NOW, self.tmux)
        self.assertNotIn(SID, {s["sid"] for s in alive}, "nothing to stub from → never invented")
        moved.rename(self.tx)


class FrameCarriesLive(unittest.TestCase):
    def test_the_tab_order_frame_names_the_live_sids(self):
        with mock.patch.object(km, "_views_payload", lambda: {"views": {}}):   # the frame's one views carrier (2026-09-08)
            f = km._tab_order_frame(["a", "b"], [{"id": "a"}], {"a", "b", "c"})
        self.assertEqual(f["type"], "tabOrder")
        self.assertEqual(f["order"], ["a", "b"])
        self.assertEqual(f["live"], ["a", "b", "c"], "sorted, deduped, independent of the order")


if __name__ == "__main__":
    unittest.main()
