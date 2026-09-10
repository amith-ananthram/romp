#!/usr/bin/env python3
"""Every working card narrates its open turn (the user 2026-08-13): tool count + running time — the
previously-mute "ordinary working card" case. A frozen count under a climbing timer is how a silent
regression becomes visible at a glance. Synthetic fixtures only."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_narr", os.path.join(BIN, "romp-kernel"))

T0 = 1781100000


def _tool(n="Bash"):
    return {"type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "name": n, "input": {}}]}}


def _text(t="working on it"):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": t}]}}


class OpenTurnProgress(unittest.TestCase):
    def test_counts_tool_uses_in_the_open_turn(self):
        turns = [{"id": "t1", "t": T0, "ended": False,
                  "atoms": [{"type": "user", "message": {}}, _tool(), _text(), _tool(), _tool()]}]
        self.assertEqual(km._open_turn_progress(turns), {"since": T0, "toolUses": 3})

    def test_an_ended_turn_narrates_nothing(self):
        # the other spin states (Analyzing…, the stall chip, the floors) own every idle beat
        turns = [{"id": "t1", "t": T0, "ended": True, "atoms": [_tool()]}]
        self.assertIsNone(km._open_turn_progress(turns))
        self.assertIsNone(km._open_turn_progress([]))

    def test_only_assistant_tool_use_blocks_count(self):
        turns = [{"id": "t1", "t": T0, "ended": False,
                  "atoms": [{"type": "user", "message": {"content": [{"type": "tool_use"}]}},
                            _text(), {"type": "assistant", "message": None}]}]
        self.assertEqual(km._open_turn_progress(turns)["toolUses"], 0)


class PayloadPins(unittest.TestCase):
    def test_working_cards_carry_the_narration(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn('sess_progress = _open_turn_progress(ps["turns"]) if (ps and who_working) else None', src)
        self.assertIn('"working": (sess_progress if column == "working" else None)', src)

    def test_working_cards_carry_the_mute_proof_floor(self):
        # the user 2026-08-14: a working card ALWAYS says its state. The kernel grades each session's
        # disposition — "open" (turn running), "quiet" (parsed, between turns), "unknown" (no parse to
        # read: a cold cache or a machine reporting nothing) — and every working card carries it, so
        # the feed's spin floor can speak even when the narration payload cannot ride.
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn('sess_state = "open" if who_working else ("quiet" if ps else "unknown")', src)
        self.assertIn('"sessState": (sess_state if column == "working" else None)', src)


if __name__ == "__main__":
    unittest.main()
