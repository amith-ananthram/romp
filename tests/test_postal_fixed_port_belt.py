#!/usr/bin/env python3
"""The bus's belt against hermetic leaks (2026-09-10): `romp-postal-service serve` and `ensure` refuse to bind the
machine's fixed bus port under a test (PYTEST_CURRENT_TEST set, or the state root under a test runner's temporary
directory, the /tmp/romp-tests-* shape) unless ROMP_POSTAL_PORT names a port explicitly, and say why on stderr.

Three leaks in one afternoon put a hermetic bus on the shared port while the real bus was down for a restart: two lab
kernels started as processes without kernel_env's trio, and one kernel module loaded inside a test process whose
revive path ran `ensure`. The fixture guard (tests/test_hermetic_kernel_postal.py) sees the spawns; this belt holds
for every shape, because it is the bus that refuses.

Every case here runs the real script in a subprocess with a hermetic environment: the refusal happens before any bind,
so nothing listens anywhere, and the allowed case is read from the helper, never by starting a bus.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BUS = os.path.join(ROOT, "bin", "romp-postal-service")
PROBE = ("import importlib.machinery as m, sys; p = m.SourceFileLoader('romp_postal_probe', sys.argv[1]).load_module(); "
         "print(repr(p._fixed_port_refusal()))")


# state roots that are NEVER created: the refusal precedes every mkdir and the probe only imports the module, so these are
# strings the belt reads, not directories; one outside any test runner's root (no romp-tests- segment, the machine's own
# case) and one inside the runner's root shape (the case the belt refuses). No temp API, nothing written outside the run.
OUTSIDE_ROOT = "/nonexistent/belt-home"
TESTS_ROOT = "/nonexistent/romp-tests-belt/lab"


def _env(**over):
    """a bare environment: the path, a home and state root that never come to exist, no postal port unless the case sets
    one, and no PYTEST_CURRENT_TEST unless the case sets one (the runner exports it into ours)"""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": OUTSIDE_ROOT, "XDG_STATE_HOME": OUTSIDE_ROOT + "/xdg", "ROMP_KERNEL_NO_OPEN": "1",
           "ROMP_SERVE_TOKEN": "testtok-belt"}   # given, so the module mints none under a root that never exists
    env.update(over)
    return env


def _refusal(env):
    p = subprocess.run([sys.executable, "-c", PROBE, BUS], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-800:]
    return eval(p.stdout.strip())   # repr of a str or None


class FixedPortBelt(unittest.TestCase):
    def test_under_a_test_the_fixed_port_is_refused_with_the_reason(self):
        why = _refusal(_env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)"))
        self.assertIsNotNone(why)
        self.assertIn("refusing to bind the machine's fixed bus port 25302", why)
        self.assertIn("PYTEST_CURRENT_TEST", why)

    def test_a_state_root_under_the_test_runners_temporary_directory_is_refused_too(self):
        why = _refusal(_env(XDG_STATE_HOME=TESTS_ROOT + "/xdg"))   # the runner's root shape, no PYTEST_CURRENT_TEST at all
        self.assertIsNotNone(why)
        self.assertIn("test runner's temporary directory", why)
        self.assertIn("25302", why)

    def test_an_explicit_port_is_allowed_under_a_test(self):
        self.assertIsNone(_refusal(_env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)", ROMP_POSTAL_PORT="45678")),
                          "a hermetic bus that names its own port is what the labs run")

    def test_outside_a_test_nothing_is_refused(self):
        self.assertIsNone(_refusal(_env()), "the machine's own bus binds the fixed port as ever")

    def test_serve_exits_loudly_before_binding(self):
        env = _env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)")
        p = subprocess.run([sys.executable, BUS, "serve"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stderr[-800:])
        self.assertIn("romp-postal-service: under a test", p.stderr)
        self.assertIn("refusing to bind the machine's fixed bus port", p.stderr)
        self.assertFalse(os.path.exists(env["XDG_STATE_HOME"]), "no bus came up and nothing was written: the refusal precedes every mkdir")

    def test_ensure_refuses_before_it_spawns(self):
        src = open(BUS, encoding="utf-8").read()
        ens = src[src.index("def ensure():"):src.index("def restart():")]
        self.assertLess(ens.index("_fixed_port_refusal()"), ens.index("subprocess.Popen("), "the belt sits before the spawn")
        self.assertIn("_refuse_loudly(why)", ens)
        srv = src[src.index("def serve():"):src.index("def serve():") + 400]
        self.assertLess(srv.index("_fixed_port_refusal()"), srv.index("STATE.mkdir"), "serve refuses before it touches anything")


if __name__ == "__main__":
    unittest.main()
