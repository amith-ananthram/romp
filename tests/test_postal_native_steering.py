#!/usr/bin/env python3
"""Sessions are steered to postal over Claude Code's native cross-session messaging (the
user 2026-08-08). A native SendMessage between peer sessions bypasses everything postal is
for — declared kinds, owed-reply tracking, per-host trust boundaries, and the user's view
of the traffic — so the two agent-facing norm surfaces (the MCP server instructions and the
romp-postal skill) must both name the native tools and say to prefer postal for peers,
while leaving native SendMessage endorsed for subagents/teammates inside one session (the
user explicitly does not want stock Claude Code features throttled). This pins that
steering so it can't quietly drop out of either copy."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
SKILL = os.path.join(ROOT, "claude", "skills", "romp-postal", "SKILL.md")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_steering", os.path.join(BIN, "romp-postal-service"))


class NativeSteering(unittest.TestCase):
    """Both norm copies steer peers to postal and keep subagents on native SendMessage."""

    def _check(self, text, where):
        low = text.lower()
        # names the native tools, so the steering matches what the agent actually sees
        self.assertIn("sendmessage", low, f"{where}: must name the native SendMessage tool")
        self.assertIn("listagents", low, f"{where}: must name the native ListAgents tool")
        # steers peer traffic to postal ("postal" appears in the steering sentence)
        self.assertIn("postal", low, f"{where}: must steer peer messaging to the postal tools")
        # the user's view is the WHY — mail the user can't see is the failure being prevented
        self.assertIn("invisible", low, f"{where}: must say native sends are invisible to the user")
        # and native SendMessage stays endorsed for in-session subagents/teammates
        self.assertIn("subagents", low, f"{where}: must keep native SendMessage for subagents")

    def test_mcp_instructions_steer(self):
        self._check(pm.MCP_INSTRUCTIONS, "MCP_INSTRUCTIONS")

    def test_skill_prose_steers(self):
        with open(SKILL, encoding="utf-8") as f:
            self._check(f.read(), "SKILL.md")


if __name__ == "__main__":
    unittest.main()
