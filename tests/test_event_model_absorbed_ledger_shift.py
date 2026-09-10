#!/usr/bin/env python3
"""Absorbed prompts derive from their attachment witness, not blind FIFO pairing (the user 2026-07-10).

A CLI killed with items queued never writes their dequeue/remove, so the queue-operation ledger ends up
with enqueues that have no resolution. The old parse FIFO-paired enqueues to anonymous resolutions, so
ONE missing resolution shifted every later pairing: a message the user typed at 16:56 was stamped with a
much later resolution time and rendered as the NEWEST message in the chat (hours out of place), and a
message whose pairing shifted off the end was classified 'pending' and never rendered at all. The
queued_command ATTACHMENT record is the CLI's own splice witness — written once per absorb, carrying the
full text and the enqueue timestamp — so absorbed atoms now come from it directly. All records synthetic
(placeholder UUIDs, invented text)."""
import json
import os
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
em = load_source("romp_em_ledger_shift", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781200000
DEAD_NOTE = "<task-notification>\n<task-id>t111</task-id>\n<output-file>/tmp/x</output-file>\n</task-notification>"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec_user(t, text, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def rec_asst(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def qop(t, op, content=None):
    return {"type": "queue-operation", "timestamp": iso(t), "operation": op, "content": content}


def att(t, text, uuid, parent):
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "attachment": {"type": "queued_command", "commandMode": "prompt",
                           "prompt": [{"type": "text", "text": text}]}}


def parse(records):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return em.parse_session(str(p), rompuuid=SID, now=T0 + 7200, sdk_human=True)


def user_atoms(sess):
    out = []
    for turn in sess["turns"]:
        for a in turn["atoms"]:
            if a.get("type") == "user":
                txt = em._text_of((a.get("message") or {}).get("content") or [])
                out.append((a, txt or ""))
    return out


class LedgerWithMissingResolutions(unittest.TestCase):
    """The nimbus shape: a dead enqueue (its CLI was killed before writing the resolution),
    then a real absorbed message, then a NEWER native message, then another witnessed splice
    whose remove used to mis-pair with the real message."""

    def _records(self):
        return [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop=None),
            # a task-notification queued, then the CLI died: no resolution ever lands
            qop(T0 + 30, "enqueue", DEAD_NOTE),
            # the user's message, queued mid-turn and absorbed: null-content enqueue +
            # attachment witness (stamped with the ENQUEUE ts) + an anonymous remove
            qop(T0 + 100, "enqueue"),
            att(T0 + 100, "am I safe to unplug it now?", "att1", "a1"),
            qop(T0 + 110, "remove"),
            rec_asst(T0 + 120, "yes, safe to unplug", "a2", "att1"),
            # the NEWER native message — must stay the newest user message
            rec_user(T0 + 200, "okay, it is unplugged now", "u2", "a2"),
            rec_asst(T0 + 210, "confirmed, running in offline mode", "a3", "u2", stop=None),
            # a later witnessed splice: its remove used to mis-pair with the T0+100 enqueue,
            # dragging the user's absorbed message to the bottom of the chat
            qop(T0 + 300, "enqueue", DEAD_NOTE.replace("t111", "t222")),
            att(T0 + 300, DEAD_NOTE.replace("t111", "t222"), "att2", "a3"),
            qop(T0 + 301, "remove"),
            rec_asst(T0 + 310, "done", "a4", "att2"),
        ]

    def test_absorbed_message_keeps_its_own_time(self):
        atoms = user_atoms(parse(self._records()))
        absorbed = [a for a, txt in atoms if "safe to unplug it" in txt]
        self.assertEqual(len(absorbed), 1, "the absorbed message renders exactly once")
        self.assertEqual(absorbed[0]["t"], T0 + 100,
                         "stamped at its OWN splice (the attachment's enqueue ts), not at a "
                         "later remove the shifted FIFO happened to pair it with")

    def test_newer_native_message_stays_newest(self):
        atoms = user_atoms(parse(self._records()))
        typed = [(a, txt) for a, txt in atoms if "unplug" in txt]
        self.assertTrue(typed, "both unplug messages parse")
        newest = max(typed, key=lambda p: p[0]["t"])
        self.assertIn("okay, it is unplugged", newest[1],
                      "the message the user typed LAST renders last — the absorbed one must "
                      "not leapfrog it on a mis-paired resolution time")

    def test_dead_enqueue_stays_unrendered_and_nothing_blank(self):
        atoms = user_atoms(parse(self._records()))
        self.assertFalse([1 for _, txt in atoms if "t111" in txt],
                         "the dead enqueue (no witness, CLI killed) is not synthesized")
        self.assertFalse([1 for a, txt in atoms if not txt.strip() and a.get("uuid") is None],
                         "no blank anonymous atoms from null-content enqueues")


class ReplayedAttachmentDedup(unittest.TestCase):
    def test_replayed_attachment_emits_once(self):
        # compaction/resume replays the attachment record verbatim (same text, same embedded
        # ts, NEW uuid) — one splice, one atom
        records = [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop=None),
            qop(T0 + 20, "enqueue"),
            att(T0 + 20, "also update the readme", "att1", "a1"),
            qop(T0 + 25, "remove"),
            att(T0 + 20, "also update the readme", "att1b", "att1"),   # the replayed copy
            rec_asst(T0 + 30, "done", "a2", "att1b"),
        ]
        atoms = user_atoms(parse(records))
        hits = [a for a, txt in atoms if "update the readme" in txt]
        self.assertEqual(len(hits), 1, "identical (ts, text) attachment copies are ONE splice")


class LegacyShapesStillCovered(unittest.TestCase):
    def test_content_enqueue_with_offset_attachment_ts(self):
        # the legacy tmux shape as pinned by the golden fixture: the enqueue carries the text
        # and the attachment is stamped at the REMOVE ts — text-witnessing must not double-emit
        records = [
            rec_user(T0, "refactor the ledger", "u1", None),
            rec_asst(T0 + 20, "reading it", "a1", "u1", stop=None),
            qop(T0 + 40, "enqueue", "also rename the digest file"),
            qop(T0 + 60, "remove"),
            att(T0 + 60, "also rename the digest file", "att1", "a1"),
            rec_asst(T0 + 90, "folded the rename in", "a2", "att1"),
        ]
        atoms = user_atoms(parse(records))
        hits = [a for a, txt in atoms if "rename the digest" in txt]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["uuid"], "att1", "anchored on the attachment record")

    def test_witnessless_remove_pairing_emits_nothing(self):
        # no attachment = no splice happened (0 of the corpus's 104 remove-bearing transcripts
        # lack attachments) — a contentful enqueue whose FIFO pairing lands on an anonymous
        # remove is a dead/mis-paired entry, not a message to render
        records = [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop=None),
            qop(T0 + 20, "enqueue", "and bump the version"),
            qop(T0 + 25, "remove"),
            rec_asst(T0 + 30, "done", "a2", "a1"),
        ]
        atoms = user_atoms(parse(records))
        self.assertFalse([1 for _, txt in atoms if "bump the version" in txt],
                         "an unwitnessed enqueue never synthesizes an absorbed atom")

    def test_popall_recall_renders_only_the_resent_native_line(self):
        # the live-corpus popAll shape: the user RECALLS the queued message (queue cleared, no
        # attachment), edits, resends — only the native resent line renders, no absorbed twin
        records = [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop="end_turn"),
            qop(T0 + 20, "enqueue", "first draft of the ask"),
            qop(T0 + 30, "popAll"),
            qop(T0 + 31, "enqueue", "edited version of the ask"),
            qop(T0 + 40, "dequeue"),
            rec_user(T0 + 40, "edited version of the ask", "u2", "a1"),
            rec_asst(T0 + 50, "on it", "a2", "u2"),
        ]
        atoms = user_atoms(parse(records))
        self.assertFalse([1 for _, txt in atoms if "first draft" in txt],
                         "the recalled draft was never delivered — it must not render")
        self.assertEqual(len([1 for _, txt in atoms if "edited version" in txt]), 1,
                         "the resent message renders once, from its native line")


if __name__ == "__main__":
    unittest.main()
