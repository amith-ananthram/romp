"""distillState (the user 2026-07-21): the card's distilled line — the decision brief for a blocked goal, the
takeaway for a completed one — must ride the GENUINE resolution state, not the transient `column`. A blocked
top drops to the Working column every time its session takes a turn (recheck/rejudging), and the line, keyed on
`column`, flickered OFF each time — a busy session's blocked card read as "unblocked, no distilled summary"
(the docs thread). build_feed now emits `distillState` ("completed"/"blocked"/null) computed from `col` +
the hard-block floors, INDEPENDENT of recheck/rejudging, so the client keeps the brief up through the flip.
Source pins on build_feed (its inputs — a live parse, states, sessions — make an end-to-end feed build heavy;
the same convention as test_kernel_apierror_working)."""
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
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class DistillState(unittest.TestCase):
    def test_distill_state_is_computed_from_the_genuine_block_not_the_column(self):
        src = inspect.getsource(km.build_feed)
        # "completed" mirrors col; "blocked" fires for the SAME hard blocks that make the card genuinely
        # needs-you (api_block / the judge-auth floor / a live perm_top / a soft col=="blocked") — but
        # WITHOUT the recheck/rejudging suppression that `column` carries, so the brief survives the
        # Working flip.
        self.assertIn('distill_state = ("completed" if col == "completed"', src)
        self.assertIn('else "blocked" if (api_block or nid == jauth_top or nid == perm_top', src)
        self.assertIn('or col == "blocked")', src)
        self.assertIn("else None)", src)

    def test_distill_state_drops_the_recheck_rejudging_suppression_that_column_keeps(self):
        src = inspect.getsource(km.build_feed)
        # column suppresses needs_input during recheck/rejudging; distill_state must NOT — that suppression is
        # exactly what blanked the brief. Guard: the column line carries the suppression, the distill_state
        # line does not mention recheck/rejudging at all.
        self.assertIn('or (col == "blocked" and not recheck and not rejudging))', src)   # column keeps it
        start = src.index("distill_state = (")
        ds = src[start: src.index("else None)", start) + len("else None)")]   # just the assignment expression
        self.assertNotIn("recheck", ds, "distill_state must not re-inherit the column's recheck suppression")
        self.assertNotIn("rejudging", ds)

    def test_the_ask_payload_carries_distillState(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('"distillState": distill_state,', src)


if __name__ == "__main__":
    unittest.main()
