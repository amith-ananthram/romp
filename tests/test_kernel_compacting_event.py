"""LIVE compaction indicator in the chat (the user 2026-07-06): while a session compacts, build_session emits a
{kind:"compacting"} event so the client can render an animated inline element in the transcript flow — appended
BEFORE the {kind:"queued"} bubble so a message sent mid-compaction stacks BELOW it instead of clobbering it. It
rides the corroborated `_compacting` signal (same one the chip/timeline use), and vanishes when compaction ends,
where the transcript's {kind:"compact"} boundary divider takes over. Source pins on build_session."""
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


class CompactingEvent(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(km.build_session)

    def test_compacting_signal_is_hoisted_from_the_busy_check(self):
        # the corroborated compacting signal is computed once and reused (not the raw tmux state);
        # the path_override arm is the read-only episode render, where nothing is live by definition
        self.assertIn(
            '_compacting(sid, (tm0 or {}).get("state", ""), parsed, now, (tm0 or {}).get("since"))',
            self.src)
        self.assertIn('busy = not path_override and (_session_working(parsed["turns"]) or compacting_now)',
                      self.src)

    def test_a_compacting_event_is_emitted_while_compacting(self):
        self.assertIn('if compacting_now:', self.src)
        self.assertIn('events.append({"kind": "compacting"})', self.src)

    def test_the_compacting_event_precedes_the_queued_bubble(self):
        # ordering is the whole point: the animated element sits ABOVE any provisional/queued message so a
        # message sent mid-compaction never clobbers it.
        i_compacting = self.src.index('events.append({"kind": "compacting"})')
        i_queued = self.src.index('events.append({"kind": "queued"')
        self.assertGreater(i_compacting, 0)
        self.assertGreater(i_queued, i_compacting,
                           "the queued bubble must be appended AFTER the compacting element")

    def test_the_running_compact_is_folded_from_the_queue_while_compacting(self):
        # the live "Compacting context…" element already represents the running /compact, so it must NOT
        # ALSO show as a queued bubble (the user 2026-07-07) — drop ONE "/compact" from the queue when
        # compacting_now, and never emit an empty queued event if that fold emptied the list.
        self.assertIn('(m.get("md") or "").strip() == "/compact":', self.src)
        self.assertIn('del qmsgs[i]', self.src)
        self.assertIn('if qmsgs:', self.src)   # guard: don't emit an empty "queued"
        i_fold = self.src.index('del qmsgs[i]')
        i_queued = self.src.index('events.append({"kind": "queued"')
        self.assertGreater(i_queued, i_fold, "the /compact fold must run before the queued event is built")

    def test_the_compact_event_carries_the_model_summary(self):
        # the boundary event ships the summary the parser captured, so the chat can show it in the
        # collapsible "Context compacted" box (the user 2026-07-07).
        self.assertIn('"summary": a.get("summary")', self.src)


if __name__ == "__main__":
    unittest.main()
