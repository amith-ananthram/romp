#!/usr/bin/env python3
"""The bus refuses the machine's fixed port under a test (2026-09-10): `romp-postal-service serve` and `ensure` will not
bind it when PYTEST_CURRENT_TEST is set, or when the state root sits under a temporary directory (a test runner's
romp-tests- root, or the system's temporary directory at all, where a bare unittest run puts its state), unless
ROMP_POSTAL_PORT names the port this process bound at import; they say why on stderr, and the kernel's ensure runner
copies a refusal into its own log.

Three leaks in one afternoon put a hermetic bus on the shared port while the real bus was down for a restart: two lab
kernels started as processes without kernel_env's trio, and one kernel module loaded inside a test process whose
revive path ran `ensure`. The fixture guard (tests/test_hermetic_kernel_postal.py) sees the spawns; the refusal in the
bus holds for every shape.

Every case here runs the real script in a subprocess with a bare environment: the refusal happens before any bind, so
nothing listens anywhere, and the allowed cases are read from the helper, never by starting a bus.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BUS = os.path.join(ROOT, "bin", "romp-postal-service")
PROBE = ("import importlib.util as u, importlib.machinery as m, sys; s = u.spec_from_loader('romp_postal_probe', m.SourceFileLoader('romp_postal_probe', sys.argv[1])); "
         "p = u.module_from_spec(s); s.loader.exec_module(p); print(repr(p._fixed_port_refusal()))")


# state roots that are NEVER created: the refusal precedes every mkdir and the probe only imports the module, so these are
# strings the bus reads, not directories; one outside any temporary directory (the machine's own case), one under a test
# runner's root shape, one under the system's temporary directory. No temp API, nothing written outside the run.
OUTSIDE_ROOT = "/nonexistent/belt-home"
TESTS_ROOT = "/nonexistent/romp-tests-belt/lab"
TEMP_ROOT = "/tmp/tmpbelt-never-made"


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

    def test_a_state_root_under_the_systems_temporary_directory_is_refused_too(self):
        why = _refusal(_env(XDG_STATE_HOME=TEMP_ROOT + "/xdg"))   # a bare unittest run's state, no runner root, no PYTEST_CURRENT_TEST
        self.assertIsNotNone(why)
        self.assertIn("under the temporary directory", why)

    def test_an_explicit_port_is_allowed_under_a_test(self):
        self.assertIsNone(_refusal(_env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)", ROMP_POSTAL_PORT="45678")),
                          "a hermetic bus that names its own port is what the labs run")

    def test_the_named_port_must_be_the_one_this_process_bound(self):
        """a port named AFTER the module read its own (PORT is frozen at import) does not license the fixed one: the
        message says both numbers"""
        probe = PROBE.replace("print(repr(p._fixed_port_refusal()))", "import os; os.environ['ROMP_POSTAL_PORT'] = '45678'; print(repr(p._fixed_port_refusal()))")
        p = subprocess.run([sys.executable, "-c", probe, BUS], env=_env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)"), capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        why = eval(p.stdout.strip())
        self.assertIsNotNone(why); self.assertIn("it names 45678, this process bound 25302", why)

    def test_the_message_names_the_whole_trio_not_client_only_alone(self):
        why = _refusal(_env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)"))
        self.assertIn("ROMP_POSTAL_CLIENT_ONLY=1 and ROMP_POSTAL_PEERS=0", why, "client-only alone is inert while peers are on")

    def test_the_kernels_ensure_runner_copies_a_refusal_into_its_own_log(self):
        src = open(os.path.join(ROOT, "kernel", "kernel.py"), encoding="utf-8").read()
        fn = src[src.index("def _ensure_postal_bus():"):src.index("def _revive_postal_bus():")]
        self.assertIn('stderr=subprocess.PIPE, text=True', fn, "the bus's stderr is read, not discarded")
        self.assertIn('sys.stderr.write("postal bus ensure refused (exit %d): %s\\n"', fn, "a non-zero exit is said in the kernel's log")

    def test_outside_a_test_nothing_is_refused(self):
        self.assertIsNone(_refusal(_env()), "the machine's own bus binds the fixed port as ever")

    def test_serve_exits_loudly_before_binding(self):
        env = _env(PYTEST_CURRENT_TEST="tests/test_x.py::T::t (call)")
        p = subprocess.run([sys.executable, BUS, "serve"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stderr[-800:])
        self.assertIn("romp-postal-service: under a test (PYTEST_CURRENT_TEST is set)", p.stderr)
        self.assertIn("refusing to bind the machine's fixed bus port", p.stderr)
        self.assertFalse(os.path.exists(env["XDG_STATE_HOME"]), "no bus came up and nothing was written: the refusal precedes every mkdir")

    def test_ensure_refuses_before_it_spawns(self):
        src = open(BUS, encoding="utf-8").read()
        ens = src[src.index("def ensure():"):src.index("def restart():")]
        self.assertLess(ens.index("_fixed_port_refusal()"), ens.index("subprocess.Popen("), "the refusal sits before the spawn")
        self.assertIn("_refuse_loudly(why)", ens)
        srv = src[src.index("def serve():"):src.index("def serve():") + 400]
        self.assertLess(srv.index("_fixed_port_refusal()"), srv.index("STATE.mkdir"), "serve refuses before it touches anything")


if __name__ == "__main__":
    unittest.main()
