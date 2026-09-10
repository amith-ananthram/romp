#!/usr/bin/env python3
"""A revival preserves the session's recorded identity (2026-08-17).

The incident: a machine kernel-panicked and the relaunch brought a session back under a NAME nobody
chose, carrying its whole history — the spawn-frozen env made the new name permanent. The mechanism:
SdkBackend.resume() trusted the CALLER's name outright, and the callers read it from the names/
registry or a discovery row — both rewritten by other machinery (the tmux launcher frees a dead
session's name by renaming its names/ entry; fork lanes emit rows whose "sid" is a transcript stem).

The rule now: when the reg already carries a name, that name WINS; the caller's is adopted only on a
create-from-nothing revive (no reg at all — usually a leaked transcript fsid, so it is also logged).
SYNTHETIC fixtures only."""
import os
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_reviveid", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-dddddddddddd"


class ReviveIdentity(unittest.TestCase):
    def _backend(self, td):
        be = sb.SdkBackend.__new__(sb.SdkBackend)   # no threads/venv — resume touches only the reg store
        be.state_dir = td
        be.sessions = {}
        be._reg_lock = threading.Lock()             # resume's alive flip holds the RMW lock (2026-08-31)
        be._poke = lambda: None
        be._log = lambda *a, **k: (be.__dict__.setdefault("_logged", []).append(a))
        return be

    def test_the_regs_own_name_outranks_the_callers(self):
        with tempfile.TemporaryDirectory() as td:
            be = self._backend(td)
            sb.write_reg(td, SID, {"sid": SID, "name": "local_butler", "cwd": td,
                                   "lastSid": "22222222-3333-4444-5555-eeeeeeeeeeee", "alive": False})
            self.assertTrue(be.resume("local_misc", SID))
            reg = sb.read_reg(td, SID)
            self.assertEqual(reg["name"], "local_butler",
                             "a revival keeps the recorded identity — the caller's copy may be rewritten state")
            self.assertTrue(reg["alive"])
            self.assertEqual(reg["lastSid"], "22222222-3333-4444-5555-eeeeeeeeeeee", "history pointer untouched")

    def test_a_create_from_nothing_revive_adopts_the_callers_name_and_says_so(self):
        with tempfile.TemporaryDirectory() as td:
            be = self._backend(td)
            self.assertTrue(be.resume("fresh_name", SID))
            reg = sb.read_reg(td, SID)
            self.assertEqual(reg["name"], "fresh_name", "no reg → nothing recorded to preserve")
            self.assertTrue(getattr(be, "_logged", []), "minting a reg for an unknown sid is logged, never silent")


if __name__ == "__main__":
    unittest.main()
