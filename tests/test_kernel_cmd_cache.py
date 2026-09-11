#!/usr/bin/env python3
"""The slash-command cache survives kernel restarts and is warmed by session events (the user
2026-08-13, whose first "/" on a fresh kernel took the length of a whole `claude` boot). The probe
result persists to STATE/commands-cache.json keyed to the CLI BINARY (path + mtime — a CLI update
changes the command set, nothing else romp can see does); load keeps each entry's original ts so the
existing stale-while-rewarming semantics decide freshness. Spawn/create/revive pre-warm the cwd — the
events that predict a composer. SYNTHETIC fixtures only."""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cmdcache", os.path.join(BIN, "romp-kernel"))

CMDS = [{"name": "compact", "description": "Compact the conversation"},
        {"name": "autocompact", "description": "Toggle autocompact", "argumentHint": "[on|off|auto]"}]


class CmdCachePersistence(unittest.TestCase):
    def setUp(self):
        self._fp = km._claude_fingerprint
        km._claude_fingerprint = lambda: "/fake/claude:12345"
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE.clear()
        km._CMD_CACHE_FILE.unlink(missing_ok=True)

    def tearDown(self):
        km._claude_fingerprint = self._fp
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE.clear()
        km._CMD_CACHE_FILE.unlink(missing_ok=True)

    def test_round_trip_survives_a_restart(self):
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE["/tmp/proj"] = {"commands": CMDS, "ts": 1781100000.0, "warming": False, "err": ""}
        km._save_cmd_cache()
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE.clear()                     # the restart
        km._load_cmd_cache()
        ent = km._CMD_CACHE.get("/tmp/proj")
        self.assertIsNotNone(ent, "the persisted list is back without a probe")
        self.assertEqual(ent["commands"], CMDS)
        self.assertEqual(ent["ts"], 1781100000.0, "original ts — stale-while-rewarming decides freshness")
        self.assertFalse(ent["warming"])

    def test_a_stale_persisted_entry_serves_instantly_and_rewarms(self):
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE["/tmp/proj"] = {"commands": CMDS, "ts": 1781100000.0, "warming": False, "err": ""}
        km._save_cmd_cache()
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE.clear()
        km._load_cmd_cache()
        kicked = []
        _thr = km.threading.Thread
        km.threading.Thread = lambda **kw: type("T", (), {"start": lambda s: kicked.append(kw.get("args"))})()
        try:
            cmds, warming = km._commands_for_cwd("/tmp/proj")
        finally:
            km.threading.Thread = _thr
        self.assertEqual(cmds, CMDS, "the persisted list serves on the FIRST request after a restart")
        self.assertTrue(warming, "…while the old ts kicks the background refresh")
        self.assertEqual(kicked, [("/tmp/proj",)])

    def test_a_different_cli_binary_invalidates_the_file(self):
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE["/tmp/proj"] = {"commands": CMDS, "ts": 1781100000.0, "warming": False, "err": ""}
        km._save_cmd_cache()
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE.clear()
        km._claude_fingerprint = lambda: "/fake/claude:99999"   # the CLI updated
        km._load_cmd_cache()
        self.assertEqual(km._CMD_CACHE, {}, "another binary's command set is not this one's")

    def test_failed_and_empty_probes_are_not_persisted(self):
        with km._CMD_CACHE_LOCK:
            km._CMD_CACHE["/tmp/ok"] = {"commands": CMDS, "ts": 1.0, "warming": False, "err": ""}
            km._CMD_CACHE["/tmp/err"] = {"commands": CMDS, "ts": 1.0, "warming": False, "err": "sdk-unavailable"}
            km._CMD_CACHE["/tmp/empty"] = {"commands": [], "ts": 1.0, "warming": False, "err": ""}
        km._save_cmd_cache()
        d = json.loads(km._CMD_CACHE_FILE.read_text())
        self.assertEqual(sorted(d["cwds"].keys()), ["/tmp/ok"])


class SessionEventsWarmTheCache(unittest.TestCase):
    """Source pins: the two session events that predict a composer each kick the warm. Placement
    pins (the functions are heavyweight to execute here); the warm itself is executed above."""

    def test_spawn_create_and_revive_prewarm(self):
        # the public _create_sdk_session and _revive_session are claim wrappers since session names are
        # reserved atomically (2026-09-08); the events that predict a composer, and so the prewarm, live
        # in the bodies they wrap
        for fn in (km._create_sdk_session_inner, km._revive_session_inner):
            self.assertIn("_commands_for_cwd(", inspect.getsource(fn),
                          "%s must pre-warm the slash-command list" % fn.__name__)

    def test_the_probe_persists_its_wins(self):
        self.assertIn("_save_cmd_cache()", inspect.getsource(km._do_warm_commands),
                      "a good probe is worth keeping across restarts")


if __name__ == "__main__":
    unittest.main()
