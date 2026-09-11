#!/usr/bin/env python3
"""Where romp's tmux server keeps its socket (T325, 2026-09-11): one rule, the same answer from the kernel's
resolver (kernel/tmux_socket.py), the shell twin (bin/romp-tmux-env) and the manager's node twin (tmuxTmpdir in
bin/romp-manager). An operator's TMUX_TMPDIR wins as it stands; else a writable XDG_RUNTIME_DIR gives
<XDG_RUNTIME_DIR>/romp, created 0700; else tmux's own default (None / an empty line / null). The kernel resolves it
into its environment at import, before its first tmux call. Synthetic directories under a temp root only."""
import json
import os
import stat
import subprocess
import tempfile
import unittest

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ts = load_source("romp_tmux_socket_t325", os.path.join(BIN, "romp_tmux_socket.py"))


class Resolver(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.run = os.path.join(self.td.name, "run"); os.mkdir(self.run, 0o700)

    def tearDown(self):
        for root, dirs, _files in os.walk(self.td.name):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)   # an unwritable case must not defeat the cleanup
        self.td.cleanup()

    def test_the_operator_s_tmux_tmpdir_wins_as_it_stands(self):
        env = {"TMUX_TMPDIR": "/somewhere/else", "XDG_RUNTIME_DIR": self.run}
        self.assertEqual(ts.tmux_tmpdir(env), "/somewhere/else")
        self.assertFalse(os.path.exists(os.path.join(self.run, "romp")), "nothing is made when the operator chose")

    def test_a_writable_runtime_dir_gives_its_romp_subdirectory_created_0700(self):
        env = {"XDG_RUNTIME_DIR": self.run}
        d = ts.tmux_tmpdir(env)
        self.assertEqual(d, os.path.join(self.run, "romp"))
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertEqual(ts.tmux_tmpdir(env), d, "idempotent once it exists")

    def test_without_a_runtime_dir_tmux_s_default_stands(self):
        self.assertIsNone(ts.tmux_tmpdir({}))
        self.assertIsNone(ts.tmux_tmpdir({"XDG_RUNTIME_DIR": ""}))
        self.assertIsNone(ts.tmux_tmpdir({"XDG_RUNTIME_DIR": os.path.join(self.td.name, "missing")}), "a missing directory")
        f = os.path.join(self.td.name, "afile"); open(f, "w").write("x")
        self.assertIsNone(ts.tmux_tmpdir({"XDG_RUNTIME_DIR": f}), "a file is not a runtime directory")

    def test_an_unwritable_runtime_dir_falls_back_to_the_default(self):
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere; the unwritable case has no meaning as root")
        ro = os.path.join(self.td.name, "ro"); os.mkdir(ro, 0o500)
        self.assertIsNone(ts.tmux_tmpdir({"XDG_RUNTIME_DIR": ro}))
        self.assertFalse(os.path.exists(os.path.join(ro, "romp")))

    def test_reporting_without_making_is_possible(self):
        env = {"XDG_RUNTIME_DIR": self.run}
        self.assertIsNone(ts.tmux_tmpdir(env, mkdir=False), "not made, so not usable yet")
        os.mkdir(os.path.join(self.run, "romp"), 0o700)
        self.assertEqual(ts.tmux_tmpdir(env, mkdir=False), os.path.join(self.run, "romp"))

    def test_export_writes_the_value_into_the_environment_it_is_handed(self):
        env = {"XDG_RUNTIME_DIR": self.run}
        self.assertEqual(ts.export_tmux_tmpdir(env), os.path.join(self.run, "romp"))
        self.assertEqual(env["TMUX_TMPDIR"], os.path.join(self.run, "romp"))
        env2 = {"TMUX_TMPDIR": "/op", "XDG_RUNTIME_DIR": self.run}
        self.assertEqual(ts.export_tmux_tmpdir(env2), "/op")
        self.assertEqual(env2["TMUX_TMPDIR"], "/op", "the operator's value is left as it stands")
        env3 = {}
        self.assertIsNone(ts.export_tmux_tmpdir(env3))
        self.assertNotIn("TMUX_TMPDIR", env3, "nothing written when the default stands")

    def test_the_socket_path_names_the_default_socket_under_the_resolved_dir(self):
        os.mkdir(os.path.join(self.run, "romp"), 0o700)
        self.assertEqual(ts.socket_path({"XDG_RUNTIME_DIR": self.run}),
                         os.path.join(self.run, "romp", "tmux-%d" % os.getuid(), "default"))
        self.assertEqual(ts.socket_path({}), "/tmp/tmux-%d/default" % os.getuid())


class ThreeTwinsAgree(unittest.TestCase):
    """The shell helper and the manager's node function answer exactly as the Python resolver does, case by case."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.run = os.path.join(self.td.name, "run"); os.mkdir(self.run, 0o700)
        self.base = {"PATH": os.environ.get("PATH", ""), "HOME": self.td.name, "ROMP_STATE_DIR": os.path.join(self.td.name, "state")}

    def tearDown(self):
        self.td.cleanup()

    def _shell(self, extra):
        r = subprocess.run(["bash", os.path.join(BIN, "romp-tmux-env")], env={**self.base, **extra}, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip() or None

    def _node(self, extra):
        js = ("process.env.ROMP_STATE_DIR = process.argv[1]; const m = require(process.argv[2]);"
              "const env = JSON.parse(process.argv[3]); console.log(JSON.stringify(m.tmuxTmpdir({ env })));")
        r = subprocess.run(["node", "-e", js, "--", self.base["ROMP_STATE_DIR"], os.path.join(BIN, "romp-manager"), json.dumps(extra)],
                           env=self.base, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_operator_set_writable_runtime_dir_and_no_runtime_dir(self):
        cases = [({"TMUX_TMPDIR": "/op/dir", "XDG_RUNTIME_DIR": self.run}, "/op/dir"),
                 ({"XDG_RUNTIME_DIR": self.run}, os.path.join(self.run, "romp")),
                 ({}, None),
                 ({"XDG_RUNTIME_DIR": os.path.join(self.td.name, "missing")}, None)]
        for extra, want in cases:
            with self.subTest(extra=extra):
                self.assertEqual(ts.tmux_tmpdir(dict(extra)), want, "python")
                self.assertEqual(self._shell(extra), want, "bash twin")
                self.assertEqual(self._node(extra), want, "node twin")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.run, "romp")).st_mode), 0o700)


class KernelResolvesAtImport(unittest.TestCase):
    """The kernel writes the resolved directory into its own environment as it loads, before any tmux call."""

    def test_the_kernel_exports_tmux_tmpdir_from_the_runtime_dir(self):
        td = tempfile.mkdtemp(); run = os.path.join(td, "run"); os.mkdir(run, 0o700)
        saved = (os.environ.get("TMUX_TMPDIR"), os.environ.get("XDG_RUNTIME_DIR"))
        os.environ.pop("TMUX_TMPDIR", None)
        os.environ["XDG_RUNTIME_DIR"] = run
        try:
            km = load_source("romp_kernel_tmux_socket_t325", os.path.join(BIN, "romp-kernel"))
            self.assertEqual(km._TMUX_TMPDIR, os.path.join(run, "romp"))
            self.assertEqual(os.environ.get("TMUX_TMPDIR"), os.path.join(run, "romp"), "every tmux subprocess inherits it")
            self.assertTrue(os.path.isdir(os.path.join(run, "romp")))
            self.assertIs(km.tsock.tmux_tmpdir, ts.tmux_tmpdir.__class__ and km.tsock.tmux_tmpdir, "the kernel loads the same module")
        finally:
            for k, v in zip(("TMUX_TMPDIR", "XDG_RUNTIME_DIR"), saved):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
