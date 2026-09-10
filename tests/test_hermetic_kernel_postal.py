#!/usr/bin/env python3
"""Every lab kernel a test starts as a PROCESS is hermetic about the postal bus (2026-09-10): its environment carries
ROMP_POSTAL_CLIENT_ONLY=1 (the kernel's boot-time ensure then starts no bus) and its own ROMP_POSTAL_PORT (an ephemeral
port, never the machine's fixed one) and ROMP_POSTAL_PEERS=0, the trio tests/test_ship_reship.py kernel_env gives.

Why a guard: one served test built its kernel environment by hand without the trio. Its kernel ran the postal
service's `ensure`, which starts the bus detached (its own session, so the kernel's death never reaches it) on the
FIXED port whenever nothing listens there. During a kernel restart the machine's real bus was down for a moment, the
lab bus took the port, the test's teardown killed the kernel and not the bus, and every session's mail then failed
against the lab's token until someone found the process. The fixture rule below is static, so it holds for tests that
skip here (no browser, no extension deps) and fails at the spawn site, naming the file.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SPAWN = re.compile(r"""Popen\(\s*\[[^\]]*["']romp-kernel["']""")
TRIO = ("ROMP_POSTAL_PORT", "ROMP_POSTAL_PEERS", "ROMP_POSTAL_CLIENT_ONLY")


class HermeticKernelPostal(unittest.TestCase):
    def test_kernel_env_gives_every_lab_kernel_its_own_never_started_bus(self):
        env = _lab.kernel_env("/tmp/lab", "/tmp/lab/claude", "/tmp/lab/dist", 1, "tok")
        self.assertEqual(env.get("ROMP_POSTAL_CLIENT_ONLY"), "1", "the kernel's ensure starts no bus")
        self.assertEqual(env.get("ROMP_POSTAL_PEERS"), "0")
        port = int(env.get("ROMP_POSTAL_PORT") or 0)
        self.assertTrue(port and port != 25302, "an ephemeral port, never the machine's fixed bus port: %r" % env.get("ROMP_POSTAL_PORT"))

    def test_every_test_that_starts_a_kernel_process_carries_the_trio(self):
        offenders = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".py") or name == os.path.basename(__file__):
                continue
            src = open(os.path.join(HERE, name), encoding="utf-8", errors="replace").read()
            if not SPAWN.search(src):
                continue
            hermetic = "kernel_env(" in src or all(k in src for k in TRIO)
            if not hermetic:
                offenders.append(name)
        self.assertEqual(offenders, [], "these tests start a kernel process without the postal trio (use kernel_env, or set "
                                        "ROMP_POSTAL_PORT to a free port, ROMP_POSTAL_PEERS=0 and ROMP_POSTAL_CLIENT_ONLY=1): %r" % offenders)

    def test_the_guard_itself_sees_the_spawn_sites(self):
        """the regex must match the spawn idiom the labs use, else the rule above would pass vacuously"""
        hits = [n for n in os.listdir(HERE) if n.endswith(".py") and SPAWN.search(open(os.path.join(HERE, n), encoding="utf-8", errors="replace").read())]
        self.assertIn("test_federation_missing_served.py", hits)
        self.assertIn("test_notification_tap_resume_browser.py", hits)


if __name__ == "__main__":
    unittest.main()
