#!/usr/bin/env python3
"""Every lab kernel a test starts as a PROCESS is hermetic about the postal bus (2026-09-10): its environment carries
ROMP_POSTAL_CLIENT_ONLY=1 (the kernel's boot-time ensure then starts no bus) and its own ROMP_POSTAL_PORT (an ephemeral
port, never the machine's fixed one) and ROMP_POSTAL_PEERS=0, the trio tests/test_ship_reship.py kernel_env gives.

Why a guard: one served test built its kernel environment by hand without the trio. Its kernel ran the postal
service's `ensure`, which starts the bus detached (its own session, so the kernel's death never reaches it) on the
FIXED port whenever nothing listens there. During a kernel restart the machine's real bus was down for a moment, the
lab bus took the port, the test's teardown killed the kernel and not the bus, and every session's mail then failed
against the lab's token until someone found the process.

The third leak of that day came by a shape no spawn scan can see: a test module that loads the kernel module
IN-PROCESS (load_source of bin/romp-kernel, no subprocess at all) drove an attach whose bus call was refused, and the
kernel's revive path ran `ensure` from inside the test process, with the test's environment and no trio. So the rule
here scans every process spawn whose argv names the kernel (Popen, run, check_output, check_call, call; the argument
span read across lines, whatever spells the path), and the BELT for the in-process shape lives in the bus itself:
`romp-postal-service serve` and `ensure` refuse the fixed port under a test (PYTEST_CURRENT_TEST set, or the state root
under a test runner's temporary directory) unless ROMP_POSTAL_PORT names a port, pinned by tests/test_postal_fixed_port_belt.py.
A module that loads the kernel in-process and exercises the bus should still carry the trio before its load (the tunnel
tests do), so its kernel never even asks.

The fixture rule below is static, so it holds for tests that skip here (no browser, no extension deps) and fails at
the spawn site, naming the file.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_ship_reship as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

CALL = re.compile(r"(?:subprocess\.(?:Popen|run|check_output|check_call|call)|(?<![\w.])Popen)\s*\(")
KERNEL_ARGV = re.compile(r"""romp-kernel|bin/romp\b|\bBIN\b[^\]\n]*?["']romp["']""")   # a joined path: BIN, "romp"
TRIO = ("ROMP_POSTAL_PORT", "ROMP_POSTAL_PEERS", "ROMP_POSTAL_CLIENT_ONLY")


def _call_spans(src):
    """The argument span of every subprocess call in `src`, read across lines to the matching parenthesis."""
    for m in CALL.finditer(src):
        i = m.end(); depth = 1; j = i
        while j < len(src) and depth:   # loop-ok: a bounded scan of one call's argument span
            c = src[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        yield src[i:j]


def _spawns_kernel(src):
    return any(KERNEL_ARGV.search(span) for span in _call_spans(src))


def _hermetic(src):
    return "kernel_env(" in src or all(k in src for k in TRIO)


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
            if _spawns_kernel(src) and not _hermetic(src):
                offenders.append(name)
        self.assertEqual(offenders, [], "these tests start a kernel process without the postal trio (use kernel_env, or set "
                                        "ROMP_POSTAL_PORT to a free port, ROMP_POSTAL_PEERS=0 and ROMP_POSTAL_CLIENT_ONLY=1): %r" % offenders)

    def test_the_guard_itself_sees_the_spawn_sites(self):
        """the scan must match the spawn idioms the labs use, else the rule above would pass vacuously"""
        hits = [n for n in os.listdir(HERE) if n.endswith(".py") and _spawns_kernel(open(os.path.join(HERE, n), encoding="utf-8", errors="replace").read())]
        self.assertIn("test_federation_missing_served.py", hits)
        self.assertIn("test_notification_tap_resume_browser.py", hits)
        # the shapes the scan must read: a list literal, a path joined or divided, a call split across lines, run as well as Popen
        for src in ('subprocess.Popen([os.path.join(BIN, "romp-kernel")], env=env)',
                    'subprocess.run(\n    [sys.executable, str(BIN / "romp-kernel")],\n    capture_output=True)',
                    'Popen(["python3", "bin/romp-kernel"])',
                    'subprocess.check_output([os.path.join(BIN, "romp"), "kernel", "--serve"])'):
            self.assertTrue(_spawns_kernel(src), src)
        self.assertFalse(_spawns_kernel('subprocess.run(["node", "esbuild.js"], cwd=EXT)'), "a build is not a kernel")
        self.assertFalse(_spawns_kernel('load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))'), "an in-process load is not a spawn: the bus's belt covers it")

    def test_the_module_that_loads_the_kernel_in_process_and_attaches_carries_the_trio_before_its_load(self):
        src = open(os.path.join(HERE, "test_kernel_tunnels.py"), encoding="utf-8", errors="replace").read()
        load = src.index('load_source("romp_kernel"')
        for k in TRIO:
            self.assertIn(k, src[:load], "%s is set before the kernel module loads (it binds the port at import)" % k)


if __name__ == "__main__":
    unittest.main()
