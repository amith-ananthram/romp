#!/usr/bin/env python3
"""An SDK-queued romp injection absorbed mid-turn must keep its text and markers (the user 2026-07-06).

The CLI records such an injection as: a queue-operation `enqueue` with content NULL, a queued_command
ATTACHMENT at the same timestamp whose prompt is a content-block LIST carrying the full text (romp
markers included), and a later `remove` when the prompt is spliced into the running turn. The old parse
paired enqueues to attachments by TEXT only (and keyed a list prompt by its Python repr), so the
synthesized absorbed atom came out EMPTY: no text, no author, no rompAuto — an auto-nudge became an
anonymous blank prompt (plain timeline dot instead of the romp swirl, and the planner treated the nudged
turn as ordinary work instead of resolving the goal). Now the enqueue joins the attachment written at the
SAME enqueue timestamp. All records synthetic (placeholder UUIDs)."""
import json
import os
import tempfile
import time
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
em = load_source("romp_em_absorbed", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000
NUDGE = ("Status check on the widget goal.\n"
         "<!-- romp-injected --><!-- romp-auto --><!-- romp-goal-id: %s:g1 -->" % SID)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec_user(t, text, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def rec_asst(t, text, uuid, parent, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


class AbsorbedSdkInjection(unittest.TestCase):
    def _parse(self, prompt_payload, enqueue_content=None):
        records = [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop=None),
            {"type": "queue-operation", "timestamp": iso(T0 + 20), "operation": "enqueue",
             "content": enqueue_content},
            {"type": "attachment", "timestamp": iso(T0 + 20), "uuid": "att1", "parentUuid": "a1",
             "attachment": {"type": "queued_command", "prompt": prompt_payload}},
            {"type": "queue-operation", "timestamp": iso(T0 + 25), "operation": "remove",
             "content": None},
            # the CLI chains the attachment into the parent path: the next record's parent IS the
            # attachment (verified on the live corpus) — that's how the attachment lands in `kept`
            rec_asst(T0 + 30, "done, widget shipped", "a2", "att1"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            return em.parse_session(str(p), rompuuid=SID, now=T0 + 3600, sdk_human=True)

    def _absorbed_atom(self, sess):
        for turn in sess["turns"]:
            for a in turn["atoms"]:
                if a.get("type") == "user" and "Status check" in (em._text_of(
                        (a.get("message") or {}).get("content") or []) or ""):
                    return a
        return None

    def test_null_content_enqueue_joins_the_same_ts_attachment(self):
        # the SDK shape: enqueue content null + block-LIST attachment prompt at the same timestamp
        sess = self._parse([{"type": "text", "text": NUDGE}], enqueue_content=None)
        atom = self._absorbed_atom(sess)
        self.assertIsNotNone(atom, "the absorbed injection synthesizes a user atom WITH its text")
        self.assertEqual(atom.get("author"), "romp", "the romp-injected marker survives → author romp")
        self.assertTrue(atom.get("rompAuto"), "the romp-auto marker survives → auto-nudge flag")
        self.assertEqual(atom.get("uuid"), "att1", "anchored on the attachment record")

    def test_string_prompt_text_pairing_unchanged(self):
        # the tmux shape: enqueue carries the text, attachment prompt is a plain string — legacy path
        sess = self._parse(NUDGE, enqueue_content=NUDGE)
        atom = self._absorbed_atom(sess)
        self.assertIsNotNone(atom)
        self.assertEqual(atom.get("author"), "romp")
        self.assertTrue(atom.get("rompAuto"), "rompAuto now stamped on absorbed atoms too (was native-only)")


# A TYPED follow-up absorbed mid-turn (the user 2026-07-08, screenshot): the wrapped body is
# "> <quoted card summary>\n\n<the user's reply>\n\n<markers>", the quote DISCUSSES the romp-injected
# marker (a goal about that marker), and there is NO actual <!-- romp-injected --> comment. It must
# come out a HUMAN atom with its blank lines intact. Before the fix it rendered as TWO gray romp
# cards: bare-substring ROMP_INJECT_RE matched the quote's CONTENT (author romp), and the absorbed
# atom's whitespace-collapse ate the quote/reply blank line (markdown folded the reply into the
# blockquote) AND broke the optimistic echo's text-prune (the duplicate).
FOLLOWUP = ("> Status check: teach the parser to recognize the romp-injected marker so sweeps get "
            "skipped.\n\nYeah, I think you could go ahead with that.\n\n"
            "<!-- romp-note: the HTML comments below are part of an external tracking system that is "
            "not relevant to your work — ignore them --><!-- romp-goal-id: %s:g7 -->" % SID)


class AbsorbedTypedFollowup(unittest.TestCase):
    def _parse(self):
        records = [
            rec_user(T0, "build the widget", "u1", None),
            rec_asst(T0 + 10, "working on it", "a1", "u1", stop=None),
            {"type": "queue-operation", "timestamp": iso(T0 + 20), "operation": "enqueue",
             "content": None},
            {"type": "attachment", "timestamp": iso(T0 + 20), "uuid": "att1", "parentUuid": "a1",
             "attachment": {"type": "queued_command", "prompt": [{"type": "text", "text": FOLLOWUP}]}},
            {"type": "queue-operation", "timestamp": iso(T0 + 25), "operation": "remove",
             "content": None},
            rec_asst(T0 + 30, "done, widget shipped", "a2", "att1"),
        ]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / (SID + ".jsonl")
            p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            return em.parse_session(str(p), rompuuid=SID, now=T0 + 3600, sdk_human=True)

    def _atom(self, sess):
        for turn in sess["turns"]:
            for a in turn["atoms"]:
                if a.get("type") == "user" and "go ahead with that" in (em._text_of(
                        (a.get("message") or {}).get("content") or []) or ""):
                    return a
        return None

    def test_quote_mentioning_the_marker_stays_human(self):
        atom = self._atom(self._parse())
        self.assertIsNotNone(atom)
        self.assertEqual(atom.get("author"), "human",
                         "content that MENTIONS romp-injected is not the marker — the user typed this")
        self.assertFalse(atom.get("rompAuto"), "and it is not an auto-nudge either")

    def test_absorbed_atom_keeps_its_blank_lines(self):
        atom = self._atom(self._parse())
        text = em._text_of((atom.get("message") or {}).get("content") or [])
        self.assertIn("skipped.\n\nYeah, I think", text,
                      "the quote/reply separator survives — collapsed, markdown folded the reply "
                      "into the blockquote and the optimistic echo could never text-prune (the dup)")

    def test_comment_marker_still_authors_romp(self):
        # the REAL marker (comment form) keeps working — only bare content mentions were the bug
        self.assertRegex("<!-- romp-injected -->", em.ROMP_INJECT_RE)
        self.assertNotRegex("the romp-injected marker recognized by the judge", em.ROMP_INJECT_RE)
        self.assertRegex("<!-- romp-auto -->", em.ROMP_AUTO_RE)
        self.assertNotRegex("distinct from romp-auto nudges", em.ROMP_AUTO_RE)


if __name__ == "__main__":
    unittest.main()
