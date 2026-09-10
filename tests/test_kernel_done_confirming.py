"""The done-confirming window on the feed (the user 2026-07-24): a top whose done verdict has landed but
whose settle event is still pending stays in the Working COLUMN (the settle gate exists precisely so the
column never flickers working↔done) and ships `doneConfirming` on its card, which the feed renders as a
steady "done, confirming" chip. The fact comes from the rollup's `confirming` export on the store — the
authoritative product of the judge's is_complete — never from the raw nodeComplete flag, which lies for
agent-open umbrellas. Source pins on build_feed (its inputs — a live parse, states, sessions — make an
end-to-end feed build heavy; the same convention as test_kernel_distill_state). The rollup export itself
is behavior-tested in test_judge.py (DistillAtDone), and the chip in done-confirming.test.ts."""
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


class DoneConfirming(unittest.TestCase):
    def test_the_flag_reads_the_rollup_export_not_the_raw_node_flag(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('confirming = set(store.get("confirming") or ())', src,
                      "the store's rollup export is the one source")
        self.assertIn('"doneConfirming": (True if (column == "working" and nid in confirming) else None),', src,
                      "shipped only on a Working-column card, keyed on the export")

    def test_the_column_is_untouched_by_the_window(self):
        # "I definitely don't want a working done flicker" (the user 2026-07-24): the window annotates the
        # card; the column expression must not consult it.
        src = inspect.getsource(km.build_feed)
        start = src.index('column = ("needs_input"')
        col = src[start: src.index('else "completed" if col == "completed" else "working")', start)]
        self.assertNotIn("confirming", col, "the column expression never reads the confirming window")

    def test_the_judge_exports_confirming_from_rollup(self):
        src = inspect.getsource(km.jd.rollup_status)
        self.assertIn('store["confirming"] = confirming', src)
        self.assertIn("if is_complete(nid):", src,
                      "the export reuses the rollup's own is_complete — agent-open umbrellas stay out")


if __name__ == "__main__":
    unittest.main()
