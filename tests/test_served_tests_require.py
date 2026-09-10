#!/usr/bin/env python3
"""ROMP_SERVED_TESTS_REQUIRE=1 turns a skip inside a browser-backed served-page test file into a failure (T308,
2026-09-10). The files tests/test_*_browser.py and tests/test_*_served.py boot a hermetic kernel and drive the real
dashboard pages in playwright's Chromium; without the extension's node deps or a browser they skip, and say why. CI's
Python jobs are such machines, so a served-page regression never turned them red (the deep-link landing pin, T307,
red on main for a day while CI stayed green). The extension job installs that browser and runs these files with the
switch set, the stance the pane bench takes with ROMP_UI_BENCH_REQUIRE: a runner that cannot run them fails, loudly,
with the skip's own reason, rather than passing with the coverage gone.

Pinned here by running pytest in a child on two synthetic files outside tests/ (tests/conftest.py loaded as a plugin
with -p, the way it would load for files under tests/): a served-named file whose class skips in setUpClass and a
plain-named file that skips the same way. Off, all three skip. On, the served file's two skips become red, each with
its reason quoted (a skip raised in setUpClass is a setup-phase report, which pytest counts as an error; one raised
in the test body counts as a failure; both turn the run red), a skip whose reason begins with "optional:" stays a
skip (a leg the runner declared it does not carry), and the plain file's still skips: the switch reaches only the
files it names. Synthetic fixtures only; no browser, no
kernel, no network.
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

SERVED = '''
import unittest
class ServedClass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise unittest.SkipTest("no browser on this synthetic box")
    def test_lands(self):
        pass
class ServedMethod(unittest.TestCase):
    def test_drives(self):
        self.skipTest("no playwright browser here, synthetic")
class ServedOptional(unittest.TestCase):
    def test_other_engine(self):
        self.skipTest("optional: this synthetic runner declares no webkit")
'''
PLAIN = '''
import unittest
class Plain(unittest.TestCase):
    def test_something(self):
        self.skipTest("a plain skip stays a skip")
'''


class ServedTestsRequire(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="served-require-")
        for name, body in (("test_fake_served.py", SERVED), ("test_fake_plain.py", PLAIN)):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(body)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _run(self, require):
        env = {k: v for k, v in os.environ.items() if k != "ROMP_SERVED_TESTS_REQUIRE"}
        if require:
            env["ROMP_SERVED_TESTS_REQUIRE"] = "1"
        p = subprocess.run([sys.executable, "-m", "pytest", "-p", "tests.conftest", "-p", "no:cacheprovider", "-q", "-rs",
                            os.path.join(self.d, "test_fake_served.py"), os.path.join(self.d, "test_fake_plain.py")],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=240)
        return p.returncode, p.stdout + p.stderr

    def test_off_all_skip_as_before(self):
        rc, out = self._run(require=False)
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("4 skipped", out, out[-2000:])
        self.assertNotIn("failed", out, out[-2000:])
        self.assertNotIn("error", out, out[-2000:])

    def test_on_the_served_file_fails_with_the_skip_reason_and_the_plain_file_still_skips(self):
        rc, out = self._run(require=True)
        self.assertNotEqual(rc, 0, "the served skip must fail the run: " + out[-2000:])
        self.assertIn("1 failed, 2 skipped, 1 error", out, out[-2000:])
        self.assertIn("optional: this synthetic runner declares no webkit", out, "the optional leg is reported as the skip it is: " + out[-2000:])
        self.assertEqual(out.count("ROMP_SERVED_TESTS_REQUIRE=1"), 2, "each red names the switch: " + out[-2000:])
        self.assertIn("no browser on this synthetic box", out, "…and carries the skip's own reason (the class-level one): " + out[-2000:])
        self.assertIn("no playwright browser here, synthetic", out, "…and the method-level one: " + out[-2000:])
        self.assertIn("a plain skip stays a skip", out, "the plain file's skip is reported as a skip: " + out[-2000:])


if __name__ == "__main__":
    unittest.main()
