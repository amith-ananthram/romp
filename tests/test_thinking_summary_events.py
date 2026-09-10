#!/usr/bin/env python3
"""A thinking block that carries TEXT renders its text; only a signature-only block is opaque (2026-09-01).

build_session marked every signed thinking block `encrypted` (kernel.py, the ChatEvent builder), and
render.ts shows "Thinking…" for an encrypted block. That was right while every block the CLI wrote was
signature-only. With thinking summaries requested (sdk_backend._options passes the SDK's typed
`thinking={"type": "adaptive", "display": "summarized"}` when the per-install gear toggle is on), a
block carries BOTH a signature and the summary text — and the old rule would have hidden every summary
behind the placeholder. The rule is now: opaque only when the block has a signature AND no text.

The judges stay blind to thinking text on purpose: their readers key on type == "text" (event_model
_text_of, kernel _atom_md), and this file pins that they still do. Synthetic data only.
"""
import inspect
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
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
km = load_source("romp_kernel_thinksum", os.path.join(BIN, "romp-kernel"))
jd = km.jd
em = load_source("romp_event_model_thinksum",
                      os.path.join(os.path.dirname(HERE), "kernel", "event_model.py"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1750000000
SIG = "c2lnbmF0dXJlLWJ5dGVzLXN5bnRoZXRpYw=="   # a synthetic opaque signature, never a real one


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(t, uuid, text, parent=None):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t),
            "sessionId": SID, "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main",
            "message": {"role": "user", "content": text}}


def _arec(t, uuid, blocks, parent):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t),
            "sessionId": SID, "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main",
            "message": {"role": "assistant", "model": "claude-fable-5-1", "content": blocks}}


class ThinkingBlockOpacity(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self._saved_state = jd.STATE
        jd._rebind_state(Path(self._td))
        self.path = Path(self._td) / "transcript.jsonl"
        self.path.write_text("".join(json.dumps(r) + "\n" for r in [
            _urec(NOW - 100, "u1", "wire the notes-api health route"),
            # 1) summarized: a signature AND text — the shape the SDK returns with display "summarized"
            _arec(NOW - 90, "a1", [{"type": "thinking", "thinking": "Checking the router for an existing "
                                                                     "health handler before adding one.",
                                    "signature": SIG},
                                   {"type": "text", "text": "Adding the route now."}], "u1"),
            # 2) omitted: a signature and EMPTY text — every block the CLI wrote before this change
            _arec(NOW - 80, "a2", [{"type": "thinking", "thinking": "", "signature": SIG},
                                   {"type": "text", "text": "Route added."}], "a1"),
            # 3) unsigned text (older transcripts) — was never opaque and still is not
            _arec(NOW - 70, "a3", [{"type": "thinking", "thinking": "Now the tests."},
                                   {"type": "text", "text": "Tests pass."}], "a2"),
        ]))

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _thinking_events(self):
        # path_override is the read-only render of exactly this transcript; the tmux stub makes the
        # sid resolvable without a registry (the same synthesized entry a brand-new pane gets)
        m = km.build_session(SID, NOW, tmux={SID: {}}, path_override=str(self.path))
        self.assertIsNotNone(m)
        return [e for e in m["events"] if e.get("kind") == "thinking"]

    def test_a_signed_block_with_text_is_not_opaque_and_keeps_its_text(self):
        evs = self._thinking_events()
        self.assertEqual(len(evs), 3, "one thinking event per block")
        summarized, omitted, unsigned = evs
        self.assertFalse(summarized["encrypted"], "signature + text = a summary to SHOW, not a placeholder")
        self.assertIn("health handler", summarized["text"])
        self.assertTrue(omitted["encrypted"], "signature + no text = opaque, exactly as before")
        self.assertEqual(omitted["text"], "")
        self.assertFalse(unsigned["encrypted"])
        self.assertEqual(unsigned["text"], "Now the tests.")

    def test_whitespace_only_text_still_reads_as_opaque(self):
        self.path.write_text(json.dumps(_urec(NOW - 10, "u1", "hi")) + "\n"
                             + json.dumps(_arec(NOW - 5, "a1", [{"type": "thinking", "thinking": "  \n",
                                                                 "signature": SIG},
                                                                {"type": "text", "text": "hello"}], "u1")) + "\n")
        evs = self._thinking_events()
        self.assertEqual(len(evs), 1)
        self.assertTrue(evs[0]["encrypted"], "blank text is no summary — the placeholder is honest here")

    def test_the_seam_states_the_rule(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"encrypted": bool(b.get("signature")) and not (b.get("thinking") or "").strip()', src,
                      "opaque = signature AND no text, in one expression at the builder")


class JudgesStayBlindToThinking(unittest.TestCase):
    """Summaries are for the human reading the chat. The planner/closer/distiller read text blocks
    only — a summary must not start leaking into verdicts or briefs by accident."""

    ATOM = {"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": "a reasoning summary the judges must not read", "signature": SIG},
        {"type": "text", "text": "the reply "},
        {"type": "text", "text": "the judges do read"}]}}

    def test_kernel_atom_md_joins_text_blocks_only(self):
        self.assertEqual(km._atom_md(self.ATOM), "the reply the judges do read")

    def test_event_model_text_reader_skips_thinking(self):
        got = em._text_of(self.ATOM["message"]["content"])
        self.assertNotIn("reasoning summary", got)
        self.assertIn("the judges do read", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
