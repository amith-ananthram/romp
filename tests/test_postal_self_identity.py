#!/usr/bin/env python3
"""Forked-session self-identity on the postal bus (the user 2026-07-27).

CLAUDE_CODE_SESSION_ID carries the CURRENT transcript fsid; a /clear or resume fork moves it off the
stable romp sid that every store is keyed by. A session that trusted the env var mailed as "unknown"
(my_name read names/<fsid>, which never exists), published its working note under an invisible id, and
read an empty mailbox. _self_row resolves through the kernel sessions seam — exact id first, then the
row whose lastSid matches — so my_id returns the STABLE sid and my_name the live name.

Synthetic only — hermetic temp state dir, placeholder UUIDs, invented notes-domain sessions."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

STABLE = "11111111-2222-3333-4444-555555555555"
FORK = "99999999-8888-7777-6666-555555555555"

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
_SESS = os.path.join(os.environ["XDG_STATE_HOME"], "sessions.json")
Path(_SESS).write_text(json.dumps([
    {"id": STABLE, "name": "web", "dir": "/tmp/notes-api", "state": "waiting",
     "working": "", "lastSid": FORK}]))
os.environ["ROMP_SESSIONS_FILE"] = _SESS
ps = load_source("romp_postal_selfid", os.path.join(BIN, "romp-postal-service"))


class ForkedSelfIdentity(unittest.TestCase):
    def setUp(self):
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = _SESS
        self._env = os.environ.get("CLAUDE_CODE_SESSION_ID")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self._env
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def test_exact_id_match_resolves_directly(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = STABLE
        self.assertEqual(ps.my_id(), STABLE)
        self.assertEqual(ps.my_name(), "web")

    def test_forked_fsid_resolves_to_the_stable_row(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = FORK
        self.assertEqual(ps.my_id(), STABLE,
                         "the stable sid is the identity every store is keyed by — never the fork fsid")
        self.assertEqual(ps.my_name(), "web", "the live row name, never the 'unknown' fallback")

    def test_unknown_env_id_keeps_the_raw_value_and_no_name(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "abcdef00-0000-0000-0000-000000000000"
        self.assertEqual(ps.my_id(), "abcdef00-0000-0000-0000-000000000000",
                         "no row and no lastSid match → the env value passes through (mail still routes by id)")
        self.assertIsNone(ps.my_name())

    def test_no_env_means_not_a_romp_session(self):
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertIsNone(ps.my_id())
        self.assertIsNone(ps.my_name())


if __name__ == "__main__":
    unittest.main(verbosity=2)
