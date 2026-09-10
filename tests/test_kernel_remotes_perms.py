#!/usr/bin/env python3
"""remotes.json is a CREDENTIAL STORE — every attached-host row carries that host's serve token (fetched
over ssh at attach), which authorizes control of that machine's kernel through the tunnel. It must be
0600: at the default 0644 any other local user could lift a remote's token and drive that machine, which
would defeat the loopback token gate for the federation case (found in the pre-release sweep, 2026-07-22).

Also pins the general rule: _atomic_write applies `mode` to the TEMP before the replace, so a credential
file is never briefly world-readable between publish and chmod.

Synthetic only — hermetic temp STATE, placeholder host/token.
"""
import os
import stat
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class RemotesFilePermissions(unittest.TestCase):
    def setUp(self):
        # the kernel module is ONE object per process for every test file that loads it under this
        # name (a peer file's km IS this km), so the row planted here leaves with the test: under
        # xdist a whole-map reader elsewhere (test_kernel_trust's PairsSnapshot) found it (2026-09-02)
        saved = dict(km._remotes)
        self.addCleanup(lambda: (km._remotes.clear(), km._remotes.update(saved)))
        km._remotes.clear()
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 8801,
                                   "bus_port": 8802, "token": "REMOTE-SECRET-TOKEN", "proc": None,
                                   "status": "up", "detail": "", "sids": [], "trust": "directed"}

    def test_save_writes_0600(self):
        km._remotes_save()
        self.assertTrue(km.REMOTES_FILE.exists())
        self.assertEqual(_mode(km.REMOTES_FILE), 0o600,
                         "remotes.json holds remote serve tokens — it must not be group/world readable")

    def test_save_tightens_an_existing_world_readable_file(self):
        km._remotes_save()
        os.chmod(km.REMOTES_FILE, 0o644)          # simulate a file written before the fix
        km._remotes_save()
        self.assertEqual(_mode(km.REMOTES_FILE), 0o600, "a re-save must re-tighten the mode")

    def test_load_heals_a_stale_world_readable_file(self):
        km._remotes_save()
        os.chmod(km.REMOTES_FILE, 0o644)          # a file left over from before the fix
        km._remotes_load()
        self.assertEqual(_mode(km.REMOTES_FILE), 0o600,
                         "loading an old 0644 remotes.json must heal it, not keep leaking tokens")

    def test_the_token_really_is_in_there(self):
        # guard the premise: if rows ever stop carrying tokens this test's reason to exist changes
        km._remotes_save()
        self.assertIn("REMOTE-SECRET-TOKEN", km.REMOTES_FILE.read_text())


class AtomicWriteMode(unittest.TestCase):
    def test_mode_applied_and_default_unchanged(self):
        d = km.jd.STATE / "permtest"
        d.mkdir(parents=True, exist_ok=True)
        secret, plain = d / "secret.json", d / "plain.json"
        km._atomic_write(secret, "{}", mode=0o600)
        km._atomic_write(plain, "{}")
        self.assertEqual(_mode(secret), 0o600)
        self.assertNotEqual(_mode(plain), 0o600, "no mode → umask default, unchanged behavior")

    def test_no_temp_files_left_behind(self):
        d = km.jd.STATE / "permtest2"
        d.mkdir(parents=True, exist_ok=True)
        km._atomic_write(d / "x.json", "{}", mode=0o600)
        self.assertEqual([p.name for p in d.glob("*.tmp.*")], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
