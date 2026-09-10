#!/usr/bin/env python3
"""Postal must resolve THIS session's identity from CLAUDE_CODE_SESSION_ID (the harness's reliable
per-session fsid), NOT the tmux @romp-session-id var. The tmux var is wrong for an SDK (non-tmux) session
whose MCP is parented under a leftover tmux pane — the user 2026-06-24 hit this: an SDK session sitting in a
stale 'FRO' pane sent mail AS the isolated FRO session and was wrongly blocked as isolated, while the
timeline icon (keyed on the real fsid) correctly showed it un-isolated. Synthetic only — placeholder ids.
"""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_id", os.path.join(BIN, "romp-postal-service"))

FSID = "11111111-2222-3333-4444-555555555555"


class SelfIdentity(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("CLAUDE_CODE_SESSION_ID")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._env

    def test_my_id_is_the_env_session_id(self):
        # the env IS the identity — the bus has no tmux at all to fall back to (it was the wrong id for an
        # SDK session in a leftover pane; the env is always right).
        os.environ["CLAUDE_CODE_SESSION_ID"] = FSID
        self.assertEqual(pm.my_id(), FSID)
        self.assertFalse(hasattr(pm, "tmux"), "the bus has no tmux() helper to fall back to")

    def test_my_id_is_none_when_env_absent(self):
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertIsNone(pm.my_id())


if __name__ == "__main__":
    unittest.main()
