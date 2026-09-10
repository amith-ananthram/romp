"""The timeline's idle FADE must match the chat tab's (the user 2026-07-22: idle threads dim in the chat
but never on the timeline). The timeline derived `active` from the RAW tmux state and counted "waiting" as
active — but "waiting" IS the post-turn idle state, so every LIVE lane stayed unfaded forever and only DEAD
lanes ever dimmed. Both surfaces now key the fade on the DERIVED chip: ready + idle > 1h.
"""
import inspect
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
km = load_source("romp_kernel_tlfade", os.path.join(BIN, "romp-kernel"))


class TimelineIdleFade(unittest.TestCase):
    def test_timeline_fade_keys_on_the_derived_chip_not_the_raw_tmux_state(self):
        src = inspect.getsource(km.build_timeline)
        # the fade rides the derived chip `state` (from _session_chip), mirroring the chat tab's rule,
        # and `not live` keeps dead lanes dimmed as before
        self.assertIn(
            'faded = (not live) or (state == "ready" and _idle_faded(state, tm and tm["since"], now))',
            src)
        # the old raw-tmux set — which listed "waiting", the IDLE state, as ACTIVE — is gone
        self.assertNotIn('tm["state"] in ("working", "permission", "picker", "compacting", "waiting")', src)

    def test_both_surfaces_share_one_idle_rule(self):
        # the chat tab's rule is the reference: ready chip + idle longer than an hour
        self.assertIn('faded = chip == "ready" and _idle_faded(chip, tm["since"], now)',
                      inspect.getsource(km))
        # ONE shared rule since T155: both payload sites AND the conserve sweep read _idle_faded,
        # so the faded look and the parked process can never drift apart
        self.assertIn("def _idle_faded(state, since, now):", inspect.getsource(km))

    def test_the_derived_state_is_computed_before_the_fade_uses_it(self):
        # build_timeline already derives the chip into `state`; the fade must read THAT, not re-derive
        src = inspect.getsource(km.build_timeline)
        self.assertIn('state = _session_chip(sid, s["path"], comp_sess, tm, now)', src)
        self.assertLess(src.index('state = _session_chip('), src.index("faded = (not live)"),
                        "the fade must be computed after the derived chip it reads")


if __name__ == "__main__":
    unittest.main()
