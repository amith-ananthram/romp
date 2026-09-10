"""`romp update` answers a help request or an unknown option by refusing, never by updating. bin/romp hands
romp-update every word after `update`, and the command defines no option; before this the dash-prefixed
words were dropped unread, so `romp update --help` (or `-n`, or a misspelled option) took the no-host path
and pushed the local HEAD to every out-of-date attached remote, restarting it and cutting its sessions'
turns, with exit 0 and an "ok" line. Now -h/--help prints the usage and returns 0, any other option names
itself on stderr with the usage and returns 2, and neither posts anything; the no-host path itself is
unchanged. SYNTHETIC host; the kernel seams (_kernel/_get/_post) are stubbed so nothing connects."""
import contextlib
import io
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load (tests/test_state_isolation_order.py's ratchet): romp modules resolve
# their state root at import time, and only pytest runs conftest's floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ru = load_source("romp_update_flags", os.path.join(BIN, "romp-update"))

USAGE = "romp update [host...]"


class UpdateFlagsRefused(unittest.TestCase):
    def setUp(self):
        self._k, self._g, self._p = ru._kernel, ru._get, ru._post
        ru._kernel = lambda: "http://127.0.0.1:29855"
        # one out-of-date remote: the no-host path WOULD update it, so any flag that falls through shows up here
        ru._get = lambda u, path: {"tunnels": [{"host": "TESTHOST", "outOfDate": True}]}
        self.posted = []
        ru._post = lambda u, path, body: (self.posted.append((path, body)) or {"ok": True, "detail": "updated"})

    def tearDown(self):
        ru._kernel, ru._get, ru._post = self._k, self._g, self._p

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ru.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _assert_help(self, flag):
        rc, out, err = self._run([flag])
        self.assertEqual(self.posted, [], "%s is a help request; it must push to no remote" % flag)
        self.assertEqual(rc, 0)
        self.assertIn(USAGE, out, "the help text names the usage")
        self.assertEqual(out, ru.__doc__, "the module docstring is the help text")
        self.assertEqual(err, "")

    def test_long_help_prints_the_usage_and_updates_nothing(self):
        self._assert_help("--help")

    def test_short_help_prints_the_usage_and_updates_nothing(self):
        self._assert_help("-h")

    def test_help_needs_no_running_kernel(self):
        ru._kernel = lambda: None
        rc, out, err = self._run(["--help"])
        self.assertEqual((rc, self.posted), (0, []))
        self.assertIn(USAGE, out)

    def _assert_refused(self, argv, option):
        rc, out, err = self._run(argv)
        self.assertEqual(self.posted, [], "%s is not an option this command has; it must push to no remote" % option)
        self.assertEqual(rc, 2)
        self.assertEqual(out, "", "no 'updating … ok' line for a refused command")
        self.assertIn(option, err, "the refusal names the option")
        self.assertIn(USAGE, err, "the refusal names the usage")
        self.assertEqual(err.count("\n"), 1, "one plain line on stderr")

    def test_an_unknown_short_option_is_refused(self):
        self._assert_refused(["-n"], "-n")

    def test_an_unknown_option_is_refused_even_with_a_host_named(self):
        self._assert_refused(["--dry-run", "TESTHOST"], "--dry-run")

    def test_no_arg_still_updates_the_out_of_date_remote(self):
        # the control: the no-host path is what the flags used to fall into, and it still does its job
        rc, out, err = self._run([])
        self.assertEqual(rc, 0)
        self.assertEqual(self.posted, [("/tunnels/update", {"host": "TESTHOST"})])
        self.assertIn("updating TESTHOST", out)


if __name__ == "__main__":
    unittest.main()
