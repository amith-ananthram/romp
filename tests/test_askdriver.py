#!/usr/bin/env python3
"""AskDriver — the keystroke-injection side of the live AskUserQuestion picker (bin/romp-kernel). The
full pane→keystroke loop needs a LIVE tmux picker to verify end-to-end (a static-pane test can't model
the cursor moving in response to the keys), so this covers the testable slice: the pure nav-key decision,
the kind guards, the non-navigating actions, and a STATEFUL-pane stepNav (a fake pane whose cursor
advances as arrows are 'sent', the way the real TUI does). The parser is covered by test_askparse.py.
"""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class AskDriver(unittest.TestCase):
    def setUp(self):
        # snapshot the tmux I/O + parse seams so each test can fake them, restored in tearDown
        self.saved = (km._send_keys, km._send_literal, km._ask_parse)
        self.keys, self.lit = [], []
        km._send_keys = lambda n, k: self.keys.append(list(k))
        km._send_literal = lambda n, t: self.lit.append(t)

    def tearDown(self):
        km._send_keys, km._send_literal, km._ask_parse = self.saved

    def test_nav_key(self):
        self.assertEqual(km._nav_key(1, 3), "Down")
        self.assertEqual(km._nav_key(3, 1), "Up")
        self.assertIsNone(km._nav_key(2, 2))

    def test_answer_noops_on_multi(self):
        km._ask_parse = lambda n: {"kind": "multi", "cursor": 1, "cursorFound": True, "options": []}
        km._ask_answer("s", 2)
        self.assertEqual(self.keys, [], "single-select answer must not act on a multi picker")

    def test_toggle_noops_on_single(self):
        km._ask_parse = lambda n: {"kind": "single", "cursor": 1, "cursorFound": True}
        km._ask_toggle("s", 2)
        self.assertEqual(self.keys, [], "multi toggle must not act on a single-select picker")

    def test_cancel_sends_escape(self):
        km._ask_cancel("s")
        self.assertEqual(self.keys, [["Escape"]])

    def test_send_text_types_then_enter(self):
        km._ask_send_text("s", "hello world")
        self.assertEqual(self.lit, ["hello world"])
        self.assertEqual(self.keys, [["Enter"]])

    def test_step_nav_walks_to_target_then_acts(self):
        # stateful fake pane: each Down/Up moves the cursor, so stepNav converges like the real TUI
        state = {"cur": 1}
        km._send_keys = lambda n, k: state.update(cur=state["cur"] + (1 if k == ["Down"] else -1))
        km._ask_parse = lambda n: {"kind": "single", "cursor": state["cur"], "cursorFound": True}
        acted = []
        km._ask_step_nav_to("s", 3, lambda q: bool(q) and q["kind"] == "single", lambda: acted.append(True))
        self.assertEqual(state["cur"], 3, "navigated to the target row")
        self.assertEqual(acted, [True], "acted once landed")

    def test_answer_navigates_then_enters(self):
        state = {"cur": 1}

        def sk(n, k):
            self.keys.append(list(k))
            if k == ["Down"]:
                state["cur"] += 1
            elif k == ["Up"]:
                state["cur"] -= 1

        km._send_keys = sk
        km._ask_parse = lambda n: {"kind": "single", "cursor": state["cur"], "cursorFound": True}
        km._ask_answer("s", 2)
        self.assertEqual(self.keys, [["Down"], ["Enter"]], "one Down to row 2, then Enter")

    def test_focus_navigates_WITHOUT_selecting(self):
        # ↑/↓ preview-step: move the picker cursor to the target row but send NO Enter — so the chat can
        # step the focused option's scraped preview without committing the answer (the user 2026-06-22).
        state = {"cur": 1}

        def sk(n, k):
            self.keys.append(list(k))
            if k == ["Down"]:
                state["cur"] += 1
            elif k == ["Up"]:
                state["cur"] -= 1

        km._send_keys = sk
        km._ask_parse = lambda n: {"kind": "single", "cursor": state["cur"], "cursorFound": True}
        km._ask_focus("s", 3)
        self.assertEqual(state["cur"], 3, "cursor moved to row 3")
        self.assertEqual(self.keys, [["Down"], ["Down"]], "two Downs, and NO Enter — nothing is selected")

    def test_focus_noops_on_multi(self):
        km._ask_parse = lambda n: {"kind": "multi", "cursor": 1, "cursorFound": True}
        km._ask_focus("s", 2)
        self.assertEqual(self.keys, [], "focus is single-select only (multi has no preview)")


if __name__ == "__main__":
    unittest.main()
