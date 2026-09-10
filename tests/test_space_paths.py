#!/usr/bin/env python3
"""Backticked filenames WITH SPACES become whole-span links (the user 2026-08-04): the client's token
linkifier can never span a space, so a note named like `My spaced note.md` linkified only its last
word. The kernel decides which space-containing spans deserve the whole-span link by asking the
FILESYSTEM — _space_paths resolves each candidate exactly like a click (_resolve_open_path: ~ expanded,
relative against the session's cwd) and keeps only spans that are real files, so a backticked command
ending in a filename is never mislinked. Verified spans ride the chat event as spacePaths (pinned in
ui/webview/chat-space-paths.test.ts on the client side). Synthetic fixtures only."""
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_spaces", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class SpacePaths(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cwd = Path(self.td.name)
        (self.cwd / "My spaced note.md").write_text("a note whose NAME has spaces\n")
        (self.cwd / "tests").mkdir()
        (self.cwd / "tests" / "test_x.py").write_text("def test_ok(): pass\n")
        self._saved_cwd_of = km._cwd_of
        km._cwd_of = lambda sid: str(self.cwd) if sid == SID else None
        km._SPACE_PATH_CACHE.clear()
        self._n = 0

    def tearDown(self):
        km._cwd_of = self._saved_cwd_of
        km._SPACE_PATH_CACHE.clear()
        self.td.cleanup()

    def _uuid(self):
        self._n += 1
        return "msg-%d" % self._n

    def test_a_spaced_filename_that_exists_is_verified(self):
        got = km._space_paths("see `My spaced note.md` for the plan", SID, self._uuid())
        self.assertEqual(got, ["My spaced note.md"])

    def test_a_backticked_command_ending_in_a_real_file_is_not(self):
        # the adversarial case the whole design guards: tests/test_x.py exists, but the SPAN is a
        # command — resolving the whole span finds no file, so it never whole-links (the token
        # linkifier still links tests/test_x.py inside it, client-side)
        got = km._space_paths("run `uv run pytest tests/test_x.py` first", SID, self._uuid())
        self.assertIsNone(got)

    def test_an_absolute_spaced_path_verifies_without_a_cwd(self):
        km._cwd_of = lambda sid: None
        p = str(self.cwd / "My spaced note.md")
        got = km._space_paths("wrote `%s` to disk" % p, SID, self._uuid())
        self.assertEqual(got, [p])

    def test_a_space_free_span_is_left_to_the_client_token_linkifier(self):
        got = km._space_paths("see `tests/test_x.py`", SID, self._uuid())
        self.assertIsNone(got)

    def test_a_spaced_span_naming_no_file_is_dropped(self):
        got = km._space_paths("see `No such note here.md`", SID, self._uuid())
        self.assertIsNone(got)

    def test_the_prefilter_skips_extensionless_unanchored_prose(self):
        # documented trade-off: a spaced span with no extension and no anchored start is never stat'd,
        # even if a file of that exact name exists — backtick emphasis on plain words stays prose
        (self.cwd / "just some words").write_text("x")
        got = km._space_paths("as `just some words` shows", SID, self._uuid())
        self.assertIsNone(got)

    def test_existence_is_checked_once_per_message_uuid(self):
        u = self._uuid()
        self.assertEqual(km._space_paths("see `My spaced note.md`", SID, u), ["My spaced note.md"])
        (self.cwd / "My spaced note.md").unlink()
        self.assertEqual(km._space_paths("see `My spaced note.md`", SID, u), ["My spaced note.md"],
                         "cached per (sid, uuid): build_session runs per push, the stat ran once")
        self.assertIsNone(km._space_paths("see `My spaced note.md`", SID, self._uuid()),
                          "a NEW message re-checks and sees the deletion")

    def test_no_uuid_or_no_backtick_short_circuits(self):
        self.assertIsNone(km._space_paths("see `My spaced note.md`", SID, None))
        self.assertIsNone(km._space_paths("no code spans here at all", SID, self._uuid()))

    def test_build_session_attaches_spacepaths_to_both_event_kinds(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('sp = _space_paths(prompt, sid, a.get("uuid"))', src, "user events verify their spans")
        self.assertIn('sp = _space_paths(txt, sid, a.get("uuid"))', src, "assistant events verify their spans")
        self.assertIn('ev["spacePaths"] = sp', src, "verified spans ride the event")


if __name__ == "__main__":
    unittest.main()
