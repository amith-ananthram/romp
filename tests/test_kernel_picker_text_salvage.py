#!/usr/bin/env python3
"""A textless disk twin must not land a texty live atom (the user 2026-07-28).

On some model+tool combinations (observed: fable-5 replying before an AskUserQuestion) the CLI
persists the reply text that streamed before the tool call as an EMPTY thinking record under the
SAME uuid. _merge_live_atoms pruned the live text by that uuid alone, so the explanation the user
watched stream vanished mid-turn, the settle salvage (retire_live_work) never saw the atom, and no
orphan marker was ever written. The merge now withholds a uuid from the prune/fresh sets when the
live atom carries prose the disk twin lacks — the text stays visible until settle makes it durable.
SYNTHETIC only."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pts", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1781100000


def _session(atoms):
    return {"turns": [{"id": "t", "trigger": None, "t": T0, "end": T0, "ended": True, "atoms": atoms}]}


def _disk(uuid, blocks):
    return {"type": "assistant", "uuid": uuid, "t": T0 + 1,
            "message": {"role": "assistant", "content": blocks}}


def _live_text(uuid, text):
    return {"type": "assistant", "uuid": uuid, "session_id": SID, "t": T0 + 1,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class _FakeBackend:
    def __init__(self, live):
        self._atoms = {a["uuid"]: a for a in live}
        self.pruned_with = None                     # the tx_uuids set the kernel handed prune_live

    def live_atoms(self, sid):
        return sorted(self._atoms.values(), key=lambda a: a.get("t", 0))

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        self.pruned_with = set(tx_uuids)
        for k in list(self._atoms):
            if k in tx_uuids:
                del self._atoms[k]


class PickerTextSalvage(unittest.TestCase):
    def setUp(self):
        self._saved = km.Sessions.backend_for

    def tearDown(self):
        km.Sessions.backend_for = self._saved

    def _merge(self, disk_atoms, live):
        fake = _FakeBackend(live)
        km.Sessions.backend_for = staticmethod(lambda sid: fake)
        merged = km._merge_live_atoms(_session(disk_atoms), SID)
        return merged, fake

    def test_textless_twin_does_not_prune_the_live_text(self):
        # the buggy shape: thinking, then the text's EMPTY-thinking twin, then the picker tool_use
        disk = [_disk("th1", [{"type": "thinking", "thinking": ""}]),
                _disk("tx1", [{"type": "thinking", "thinking": ""}]),
                _disk("tu1", [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}])]
        merged, fake = self._merge(disk, [_live_text("tx1", "the explanation before the picker")])
        self.assertNotIn("tx1", fake.pruned_with, "a textless twin must not land the live text")
        self.assertIn("th1", fake.pruned_with, "atoms without live prose still prune by uuid")
        self.assertIn("tu1", fake.pruned_with)
        texts = [t for a in merged["turns"][-1]["atoms"]
                 for t in ([b.get("text") for b in (a.get("message") or {}).get("content", [])
                            if isinstance(b, dict) and b.get("type") == "text"])]
        self.assertIn("the explanation before the picker", texts,
                      "the streamed text stays visible instead of vanishing mid-turn")

    def test_twin_that_kept_the_text_lands_the_atom_as_before(self):
        # self-neutralizing: a CLI that persists the text correctly gets the exact old behavior
        disk = [_disk("tx1", [{"type": "text", "text": "the explanation before the picker"}])]
        merged, fake = self._merge(disk, [_live_text("tx1", "the explanation before the picker")])
        self.assertIn("tx1", fake.pruned_with, "a twin carrying the text retires the live copy")
        texts = [t for a in merged["turns"][-1]["atoms"]
                 for t in ([b.get("text") for b in (a.get("message") or {}).get("content", [])
                            if isinstance(b, dict) and b.get("type") == "text"])]
        self.assertEqual(texts.count("the explanation before the picker"), 1, "never doubled")


if __name__ == "__main__":
    unittest.main()
