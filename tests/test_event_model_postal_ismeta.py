#!/usr/bin/env python3
"""A hook-delivered postal message must survive the isMeta skip (the user 2026-07-23).

Claude Code hands romp mail to a session as Stop-hook feedback, and that transcript record carries
isMeta. The event model skipped every isMeta record as harness noise, so a hook-delivered message never
became an atom at all: no user event reached _hydrate_postal, no incoming card was built, and nothing
downstream carried the message id. A timeline arc into one of those messages therefore landed nowhere
while the transcript plainly contained it, and the failure was silent.

Deliveries arriving by other paths were unaffected, which is why this broke for some messages and not
others, and why the chat looked fine most of the time.

The skip still has to eat the things it was written for — `<command-…>` echoes and caveats — so the
exemption is keyed on the romp-msg-id marker rather than on isMeta alone.

Synthetic only — invented bodies, placeholder uuids, hostname TESTHOST.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_ismeta", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"
MID = "1700000000.11111_22222.TESTHOST"
# The real shape: Stop-hook feedback, message.content a bare STRING, isMeta true, marker at the end.
DELIVERY = ("Stop hook feedback:\nNew message(s) from your romp peers:\n\n"
            "— from web (2026-07-23T09:10:12-0700):\n"
            "QUESTION: which auth story are we shipping?\n"
            "<!-- romp-msg-id: %s -->\n<!-- romp-msg-kind: question -->" % MID)


def rec(uuid, text, meta, parent=None, content_str=True):
    msg = {"role": "user", "content": text if content_str else [{"type": "text", "text": text}]}
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": SID,
            "timestamp": "2026-07-23T16:10:12.000Z", "isMeta": meta, "message": msg}


NOW = 1784823100


def atoms_for(records):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, SID + ".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        sess = em.parse_session(p, rompuuid=SID, now=NOW, sdk_human=True)
        return [a for turn in sess["turns"] for a in turn["atoms"]]


class PostalSurvivesIsMeta(unittest.TestCase):
    def _texts(self, records):
        atoms = atoms_for(records)
        got = []
        for a in atoms:
            if a.get("type") != "user":
                continue
            c = (a.get("message") or {}).get("content") or []
            got.append(" ".join(b.get("text", "") for b in c if isinstance(b, dict)))
        return got

    def test_a_hook_delivered_message_becomes_a_user_atom(self):
        texts = self._texts([rec("u1", DELIVERY, True)])
        self.assertTrue(any(MID in t for t in texts),
                        "the delivery must survive the isMeta skip, or nothing downstream can see it")

    def test_the_marker_is_what_admits_it_and_ordinary_isMeta_noise_still_goes(self):
        texts = self._texts([rec("u1", "<command-name>/clear</command-name>", True),
                             rec("u2", "Caveat: the messages below were generated…", True)])
        self.assertEqual(texts, [], "command echoes and caveats stay skipped — the skip still does its job")

    def test_a_non_meta_message_is_unaffected(self):
        texts = self._texts([rec("u1", "just a typed prompt", False)])
        self.assertEqual(len(texts), 1)

    def test_the_block_list_shape_works_too_not_only_a_bare_string(self):
        # _content normalizes a bare string into one text block; both shapes reach the same path, and the
        # delivery has been seen as a bare string, so pin that the exemption does not depend on the shape.
        texts = self._texts([rec("u1", DELIVERY, True, content_str=False)])
        self.assertTrue(any(MID in t for t in texts))

    def test_the_admitted_atom_carries_the_id_the_deep_link_needs(self):
        # The whole point: _hydrate_postal finds the id on this atom's text and builds the incoming card
        # that carries mid, which is what a timeline arc click matches against.
        texts = self._texts([rec("u1", DELIVERY, True)])
        joined = " ".join(texts)
        self.assertEqual(em.POSTAL_RE.findall(joined), [MID])


if __name__ == "__main__":
    unittest.main()
