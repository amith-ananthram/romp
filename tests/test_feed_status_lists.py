#!/usr/bin/env python3
"""build_feed's stateUnknown list — the sessions whose live state could not be READ.

The feed's pips mark what is happening or what is wrong: a gold dot for working, straw for awaiting
dispatched background work, and nothing at all for a healthy idle session. That last one is only
trustworthy if every OTHER case renders, and one did not: a session the kernel listed but whose live
state it could not read drew the same nothing as an idle one, so a rendering hole was
indistinguishable from health. stateUnknown is that case made explicit (the client draws it a gray
ring), which is what puts the meaning back into a blank.

Synthetic sessions only — the demo notes-api world (web / api / tests), never real names.
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pips", os.path.join(BIN, "romp-kernel"))


def sess(sid, name):
    return {"sid": sid, "name": name}


ALIVE = [sess("11111111-2222-3333-4444-000000000001", "web"),
         sess("11111111-2222-3333-4444-000000000002", "api"),
         sess("11111111-2222-3333-4444-000000000003", "tests")]


class StateUnknownIsTheUnREADABLEOnes(unittest.TestCase):
    def setUp(self):
        self._flag = km._session_flag
        km._session_flag = lambda sid, flag: False       # nothing hidden unless a test says so
        self.addCleanup(lambda: setattr(km, "_session_flag", self._flag))

    def test_a_session_with_no_live_row_is_unknown(self):
        tmux = {ALIVE[0]["sid"]: {}, ALIVE[1]["sid"]: {}}     # 'tests' has no row at all
        self.assertEqual(km._state_unknown_names(ALIVE, tmux, [], []), ["tests"])

    def test_a_readable_idle_session_is_NOT_unknown(self):
        # the whole point: a session we CAN read and that is quiet gets no pip, so it must not
        # appear here — otherwise every idle session would wear the gray ring
        tmux = {s["sid"]: {} for s in ALIVE}
        self.assertEqual(km._state_unknown_names(ALIVE, tmux, [], []), [])

    def test_working_and_awaiting_are_never_unknown(self):
        # their state was read by definition; they already have their own dots
        tmux = {}                                             # no live rows at all
        out = km._state_unknown_names(ALIVE, tmux, ["web"], ["api"])
        self.assertEqual(out, ["tests"], "only the session with neither a dot nor a readable state")

    def test_hidden_sessions_are_in_no_list(self):
        # mirrors build_feed's own filter: a hideFromFeed session has no card, so it has no pip
        km._session_flag = lambda sid, flag: sid == ALIVE[2]["sid"] and flag == "hideFromFeed"
        self.assertEqual(km._state_unknown_names(ALIVE, {}, [], []), ["web", "api"])

    def test_build_feed_ships_the_list(self):
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertIn('"stateUnknown": _state_unknown_names(alive, tmux, working, awaiting)', src)

    def test_no_ready_list_is_published(self):
        # a healthy idle session is deliberately NOT enumerated: blank means quiet, so there is
        # nothing for the client to draw and nothing for the payload to carry
        import inspect
        src = inspect.getsource(km.build_feed)
        self.assertNotIn('"ready":', src)


if __name__ == "__main__":
    unittest.main()
