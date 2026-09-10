#!/usr/bin/env python3
"""A slash command's stdout is captured from the TUI VERBATIM, so it can carry ANSI SGR color codes.
The ESC byte is invisible but the "[38;5;114m…[39m" rendered as LITERAL text in the chat (the user
2026-07-16: /rate-limit-options showed "[38;5;114mRemoved monthly spend limit[39m"). The event model
now strips ANSI at the atom source — the one place both the chat and the timeline read — so the codes
never reach a renderer. Synthetic transcript only: placeholder UUIDs, invented command output."""
import datetime
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_ansi", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _atoms(recs):
    td = tempfile.mkdtemp()
    p = os.path.join(td, SID + ".jsonl")
    open(p, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
    sess = em.parse_session(p, rompuuid=SID, name="web", dir="/TESTDIR",
                            candidate_files=[p], states=None, postal_log=[], now=int(time.time()))
    return [a for turn in sess["turns"] for a in turn["atoms"]]


class StripAnsiHelper(unittest.TestCase):
    def test_removes_sgr_color_codes_keeps_text(self):
        self.assertEqual(em.strip_ansi("\x1b[38;5;114mRemoved monthly spend limit\x1b[39m"),
                         "Removed monthly spend limit")

    def test_removes_bare_reset_and_bold(self):
        self.assertEqual(em.strip_ansi("\x1b[1mbold\x1b[0m normal"), "bold normal")

    def test_passthrough_when_no_escape(self):
        self.assertEqual(em.strip_ansi("plain text"), "plain text")

    def test_empty_and_none_safe(self):
        self.assertEqual(em.strip_ansi(""), "")
        self.assertIsNone(em.strip_ansi(None))


class CommandStdoutAtom(unittest.TestCase):
    def _recs(self, stdout):
        now = int(time.time())
        return [
            {"type": "user", "timestamp": _iso(now - 60), "uuid": "u1", "parentUuid": None,
             "message": {"role": "user", "content": "hello"}},
            {"type": "assistant", "timestamp": _iso(now - 55), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                         "stop_reason": "end_turn"}},
            # the slash-command invocation (a command-flagged user atom) ...
            {"type": "user", "timestamp": _iso(now - 30), "uuid": "c1", "parentUuid": "a1",
             "message": {"role": "user", "content":
                         "<command-name>/rate-limit-options</command-name>"
                         "<command-message>rate-limit-options</command-message>"
                         "<command-args></command-args>"}},
            # ... and its stdout (a synthetic assistant atom) — the ANSI carrier
            {"type": "user", "timestamp": _iso(now - 30), "uuid": "c2", "parentUuid": "c1",
             "message": {"role": "user", "content":
                         "<local-command-stdout>%s</local-command-stdout>" % stdout}},
        ]

    def test_ansi_codes_are_stripped_from_the_stdout_atom(self):
        atoms = self._atoms = _atoms(self._recs(
            "\x1b[38;5;114mRemoved monthly spend limit\x1b[39m"))
        stdout = next(a for a in atoms if a.get("command") is True)
        text = stdout["message"]["content"][0]["text"]
        self.assertEqual(text, "Removed monthly spend limit")
        self.assertNotIn("\x1b", text)
        self.assertNotIn("[38;5", text)   # the literal that leaked into the chat before the fix

    def test_plain_stdout_is_unchanged(self):
        atoms = _atoms(self._recs("Session color set to: cyan"))
        stdout = next(a for a in atoms if a.get("command") is True)
        self.assertEqual(stdout["message"]["content"][0]["text"], "Session color set to: cyan")


if __name__ == "__main__":
    unittest.main()
