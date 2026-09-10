#!/usr/bin/env python3
"""A slash command that fires lifecycle hooks (e.g. /compact) echoes each one back in its OUTPUT as
"PreCompact [~/.claude/hooks/tmux-status.sh] completed successfully" — internal plumbing the user never wants
to see (the user 2026-06-30, who asked what the pre-compact thing was). build_session strips those notices from a
command's output text via _strip_hook_notices; when nothing else remains, the atom is dropped entirely (the
✦ Compacted boundary already marks the compaction). This tests the stripper directly.
"""
import inspect
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
km = load_source("romp_kernel_hn", os.path.join(BIN, "romp-kernel"))


class StripHookNotices(unittest.TestCase):
    def test_compact_output_reduces_to_its_lead(self):
        txt = ("Compacted PreCompact [~/.claude/hooks/tmux-status.sh] completed successfully "
               "PostCompact [~/.claude/hooks/tmux-status.sh] completed successfully")
        self.assertEqual(km._strip_hook_notices(txt), "Compacted")

    def test_output_that_is_only_notices_becomes_empty_so_the_atom_is_dropped(self):
        txt = "PreCompact [~/.claude/hooks/tmux-status.sh] completed successfully"
        self.assertEqual(km._strip_hook_notices(txt), "", "nothing but notices → empty → build_session drops it")

    def test_real_prose_is_untouched(self):
        # no bracketed-path notice → left exactly as-is (whitespace-normalized)
        s = "The build completed successfully after two retries."
        self.assertEqual(km._strip_hook_notices(s), s)

    def test_prose_mentioning_a_bracketed_path_without_the_notice_shape_is_kept(self):
        s = "Wrote the config [prod] and moved on."
        self.assertEqual(km._strip_hook_notices(s), s)


class CompactionRendering(unittest.TestCase):
    """build_session turns the compact_boundary into a dedicated {kind:"compact"} divider carrying the
    trigger + token before/after (the client draws the clean teal marker), and drops the /compact stdout
    when it reduces to just the bare "Compacted" confirmation (the divider covers it) — the user 2026-07-01."""

    def test_boundary_emits_a_compact_divider_with_metadata(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"kind": "compact"', src)
        # local metadata is `cmeta`, NOT `cm` — `cm` is the colormap module used later in build_session
        self.assertIn('"trigger": cmeta.get("trigger")', src)
        self.assertIn('"preTokens": cmeta.get("pre_tokens")', src)
        self.assertIn('"postTokens": cmeta.get("post_tokens")', src)
        self.assertNotIn("cm = a.get(\"compact_metadata\")", src, "must not shadow the colormap module `cm`")
        self.assertNotIn('"md": "✦ Compacted"', src, "no longer a plain assistant paragraph")

    def test_compacted_only_command_output_is_dropped(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('txt.strip().lower() == "compacted"', src)


if __name__ == "__main__":
    unittest.main()
