#!/usr/bin/env python3
"""Unit tests for the stuck-working healer's pure decision (romp-idle-dots).

Born from the 2026-06-10 test_slector incident: a terminal Esc-interrupt fires
NO Claude hook, so @claude-state sat at "working" for 34+ minutes, stranding
the chat-tab chip, the timeline work-bar, and the ghostty tab dot at once.
The trap these tests encode: a stale `since` ALONE cannot distinguish an
interrupted session from one legitimately inside a long tool call — only the
pane content can ("esc to interrupt" = genuinely busy; idle composer ❯ = heal).

Run:  python3 tests/test_romp_idle_dots.py
"""
import os
import sys
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
dots = load_source("romp_idle_dots_t", os.path.join(SCRIPTS, "romp-idle-dots"))

NOW = 1_781_153_000
STALE = NOW - 2_000          # well past STUCK_AFTER_SECS
FRESH = NOW - 30             # inside it

# pane snapshots (trimmed to the discriminating tails)
PANE_IDLE = "  Finished the run.\n\n✻ Sautéed for 8s\n\n❯ \n  ctx:7%  Opus 4.8\n"
PANE_BUSY = "✶ Reticulating… (esc to interrupt)\n"
# a long tool call: composer hidden, spinner present — the false-positive trap
PANE_LONG_TOOL = "  Bash(npm test) … running\n✶ Testing… (esc to interrupt · ctrl+t)\n❯ \n"
PANE_WEIRD = "some full-screen app output, no composer, no spinner\n"


class TestDiagnose(unittest.TestCase):
    def test_interrupted_session_heals(self):
        # the incident: working + stale + idle composer
        self.assertEqual(dots.diagnose("working", STALE, NOW, False, PANE_IDLE), "heal")

    def test_stuck_compacting_heals_too(self):
        self.assertEqual(dots.diagnose("compacting", STALE, NOW, False, PANE_IDLE), "heal")

    def test_long_tool_call_is_left_alone(self):
        # THE TRAP: stale since + frozen transcript, but genuinely working —
        # the spinner marker must veto the heal even with a ❯ visible
        self.assertEqual(dots.diagnose("working", STALE, NOW, False, PANE_LONG_TOOL), "leave")
        self.assertEqual(dots.diagnose("working", STALE, NOW, False, PANE_BUSY), "leave")

    def test_fresh_since_never_captures_a_heal(self):
        self.assertEqual(dots.diagnose("working", FRESH, NOW, False, PANE_IDLE), "leave")

    def test_non_stuckable_states_left_alone(self):
        for st in ("waiting", "idle"):
            self.assertEqual(dots.diagnose(st, STALE, NOW, False, PANE_IDLE), "leave")

    def test_stranded_permission_heals_on_idle_pane(self):
        # 2026-06-11 timeline_window incident: a phantom/answered permission strands
        # the state forever (no hook clears it) — an idle composer pane heals it
        self.assertEqual(dots.diagnose("permission", STALE, NOW, False, PANE_IDLE), "heal")
        self.assertEqual(dots.diagnose("picker", STALE, NOW, False, PANE_IDLE), "heal")

    def test_real_dialog_on_screen_never_heals(self):
        # a REAL pending prompt shows its cursor on a numbered row — the state is true
        pane_dialog = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n\n  ctx:7%\n"
        self.assertEqual(dots.diagnose("permission", STALE, NOW, False, pane_dialog), "leave")

    def test_copy_mode_pane_is_unjudgeable(self):
        # scrolled-back pane shows history, not live state — never judge it
        self.assertEqual(dots.diagnose("working", STALE, NOW, True, PANE_IDLE), "leave")

    def test_unrecognized_pane_is_conservative(self):
        self.assertEqual(dots.diagnose("working", STALE, NOW, False, PANE_WEIRD), "leave")

    def test_garbage_since_left_alone(self):
        self.assertEqual(dots.diagnose("working", "", NOW, False, PANE_IDLE), "leave")
        self.assertEqual(dots.diagnose("working", "nope", NOW, False, PANE_IDLE), "leave")


class TestCompactPct(unittest.TestCase):
    # the TUI's compaction bar: 'Compacting…' line, then the bar + NN% on the NEXT line
    PANE = "✶ Compacting conversation… (2m 1s)\n  ▰▰▰▱▱ 74%\n  ctx:80%  Opus 4.8\n"

    def test_reads_the_bar_percent(self):
        self.assertEqual(dots.compact_pct(self.PANE), 74)

    def test_none_when_not_compacting(self):
        self.assertIsNone(dots.compact_pct("  Finished.\n❯ \n  ctx:7%\n"))

    def test_none_when_bar_not_drawn_yet(self):
        # just started — 'Compacting…' shown but no % bar yet
        self.assertIsNone(dots.compact_pct("✶ Compacting conversation… (0s)\n❯ \n"))

    def test_grabs_the_compaction_percent_not_an_unrelated_one(self):
        # an unrelated 7% (ctx) BEFORE the bar must not be picked up
        pane = "  ctx:7%\n✶ Compacting conversation…\n  ▰▰ 41%\n"
        self.assertEqual(dots.compact_pct(pane), 41)

    def test_sweep_cadence_catches_a_compaction_start(self):
        """The daemon must detect a compaction's START fast — a ~20s compaction needs the sweep to come
        around within a few seconds (then COMPACT_INTERVAL polls the live %). A 60s cadence missed the
        first ~50% (the user). Guards against regressing INTERVAL back up."""
        self.assertLessEqual(dots.INTERVAL, 10, "sweep cadence must catch a compaction's start")
        self.assertLessEqual(dots.COMPACT_INTERVAL, dots.INTERVAL, "fast-poll while compacting")


if __name__ == "__main__":
    unittest.main(verbosity=1)
