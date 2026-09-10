#!/usr/bin/env python3
"""The two records a session move leaves in its transcript (the user 2026-09-01; Claude Code 2.1.257's
`set_cwd`): a uuid-less `{type: relocated, sessionId, relocatedCwd}` marker and an isMeta user record
carrying the CLI's own "your working directory has changed" notice to the model. Neither is a message:
the event model must yield no atom for either, and the conversation's parent chain — which the next
turn hangs off the isMeta record — must still stitch across them. SYNTHETIC transcript only."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_reloc", os.path.join(BIN, "romp-event-model"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
OLD, NEW = "/srv/notes-api", "/srv/notes-api/web"

MOVE_NOTICE = ("<system-reminder>\nThe session's working directory has changed to %s (by the user). The "
               "environment block at the start of this conversation still names the previous directory — "
               "that information is stale. All tool calls and relative paths now resolve from %s.\n"
               "</system-reminder>\n\nCodebase and user instructions are shown below.\n# web\nA tiny synthetic "
               "CLAUDE.md for the promoted subproject." % (NEW, NEW))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, cwd=OLD):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": cwd,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, cwd=OLD):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "cwd": cwd,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def records():
    return [
        uline(T0, "rename the web module's config loader", "u1"),
        aline(T0 + 5, "Done — the loader is renamed.", "a1", parent="u1"),
        # the move: the CLI's marker (no uuid, no timestamp) and its notice to the model
        {"type": "relocated", "sessionId": SID, "relocatedCwd": NEW},
        {"type": "user", "timestamp": iso(T0 + 10), "uuid": "m1", "parentUuid": "a1", "isMeta": True,
         "cwd": NEW, "message": {"role": "user", "content": MOVE_NOTICE}},
        uline(T0 + 20, "now run the web tests here", "u2", parent="m1", cwd=NEW),
        aline(T0 + 25, "All 12 tests pass in the new folder.", "a2", parent="u2", cwd=NEW),
    ]


def parse(recs):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return em.parse_session(str(p), rompuuid=SID, name="web", dir="/TESTDIR",
                                candidate_files=[str(p)], states=None, postal_log=[], now=NOW)


class RelocationRecords(unittest.TestCase):
    def test_neither_record_becomes_an_atom_and_the_chain_still_stitches(self):
        out = parse(records())
        atoms = [a for t in out["turns"] for a in t["atoms"]]
        self.assertEqual([a["uuid"] for a in atoms], ["u1", "a1", "u2", "a2"],
                         "the marker and the isMeta notice are bookkeeping, not conversation")
        texts = json.dumps(atoms)
        self.assertNotIn("working directory has changed", texts)
        self.assertNotIn("relocatedCwd", texts)
        self.assertEqual(len(out["turns"]), 2)
        self.assertTrue(all(t["ended"] for t in out["turns"]), "both turns close normally across the move")

    def test_the_move_notice_never_opens_a_turn(self):
        # a session that moved and then sat idle: the isMeta record is the newest thing on disk and
        # must not read as a pending human ask
        out = parse(records()[:4])
        self.assertEqual(len(out["turns"]), 1)
        self.assertTrue(out["turns"][0]["ended"])
        self.assertEqual([a["uuid"] for a in out["turns"][0]["atoms"]], ["u1", "a1"])


if __name__ == "__main__":
    unittest.main()
