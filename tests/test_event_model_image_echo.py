#!/usr/bin/env python3
"""The synthetic image-read placeholder never becomes a chat atom (the user 2026-07-23).

When a session Reads an image (a PNG, a screenshot), Claude Code emits a synthetic user record carrying
JUST a human-readable placeholder alongside the image block:

    [Image: original 2496x572, displayed at 2000x458. Multiply coordinates by 1.25 to map to original image.]

On disk that record is isMeta, so the old isMeta skip already ate it. But the SDK LIVE stream has no
isMeta flag, so the twin skip in sdk_backend keys on the content pattern instead — and the file adapter
carries the same content skip so the two stay in lockstep and any Claude build that omits isMeta on the
record is still covered. Either way the placeholder must not render as a bare "you typed this" bubble;
the tool that fed the image already shows in the rail.

A composer paste chip is a DIFFERENT thing — `[Image #N]` (no colon), always riding with the human's
typed text — and must survive.

Synthetic only — invented text, placeholder uuids, hostname TESTHOST.
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
em = load_source("romp_event_model_image_echo", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"
ECHO = "[Image: original 2496x572, displayed at 2000x458. Multiply coordinates by 1.25 to map to original image.]"
NOW = 1784823100


def _urec(uuid, text, meta, parent):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "sessionId": SID,
            "promptSource": "typed", "timestamp": "2026-07-23T16:10:12.000Z", "isMeta": meta,
            "message": {"role": "user", "content": text}}


def _arec(uuid, parent):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "sessionId": SID,
            "timestamp": "2026-07-23T16:10:13.000Z",
            "message": {"role": "assistant", "model": "claude-x",
                        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}}


def _turns(*user_msgs):
    """Each (text, isMeta) → a user record + a closing assistant reply, parent-chained. A user atom only
    surfaces once an assistant reply closes its turn, so every prompt gets one."""
    recs, parent = [], None
    for i, (text, meta) in enumerate(user_msgs):
        u, a = "u%d" % i, "a%d" % i
        recs.append(_urec(u, text, meta, parent))
        recs.append(_arec(a, u))
        parent = a
    return recs


def texts(records):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, SID + ".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        sess = em.parse_session(p, rompuuid=SID, now=NOW, sdk_human=True)
        out = []
        for turn in sess["turns"]:
            for a in turn["atoms"]:
                if a.get("type") != "user":
                    continue
                for b in (a.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text":
                        out.append(b.get("text", ""))
        return out


class ImageEchoNeverAnAtom(unittest.TestCase):
    def test_ismeta_echo_is_skipped(self):
        got = texts(_turns(("real question here", False), (ECHO, True)))
        self.assertIn("real question here", got)
        self.assertNotIn(ECHO, got, "the isMeta image echo must not become a chat atom")

    def test_echo_skipped_even_without_ismeta(self):
        # a Claude build that omits isMeta on the record must not leak the echo as a bubble
        got = texts(_turns(("real question here", False), (ECHO, False)))
        self.assertIn("real question here", got)
        self.assertNotIn(ECHO, got, "the content skip covers a non-isMeta image echo too")

    def test_paste_chip_message_survives(self):
        # `[Image #N]` (no colon) rides with typed text and is a genuine human turn — never swept up
        chip = "look at [Image #1] and tell me what's wrong"
        got = texts(_turns((chip, False)))
        self.assertIn(chip, got, "a human message that references a paste chip is kept")


if __name__ == "__main__":
    unittest.main()
