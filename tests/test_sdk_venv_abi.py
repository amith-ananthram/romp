#!/usr/bin/env python3
"""The kernel adds the SDK venv's site-packages only when they were built for the interpreter it runs.

bin/romp-sdk-setup builds the venv under STATE/sdkvenv with one python; the kernel's
_ensure_sdk_on_path used to add EVERY sdkvenv/lib/python3.*/site-packages it found to sys.path. When
the kernel came up on a different python (2026-09-06: a newer interpreter appeared on the machine
between two respawns), the 3.X venv joined a 3.Y process, the SDK's compiled dependency failed to
import, and the error every session showed blamed a missing install that was in fact present and intact.

Pinned here: only the site-packages whose python3.X matches sys.version_info is added; a venv present
for another version adds nothing and logs one line naming both versions and the two remedies; no venv
at all stays the plain "not importable" path with no version talk. Hermetic state; find_spec is
stubbed so the outcome does not depend on whether this machine has the SDK installed."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_venv_abi", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# The tag venv names this interpreter's lib directory with, computed here from sys itself rather than
# read back from the code under test: `3.12`, or `3.14t` on a free-threaded build.
TAG = "%d.%d%s" % (sys.version_info[0], sys.version_info[1], "t" if "t" in getattr(sys, "abiflags", "") else "")
RUNNING = "python" + TAG


class EnsureSdkOnPath(unittest.TestCase):
    def setUp(self):
        self.venv = jd.STATE / "sdkvenv"
        shutil.rmtree(self.venv, ignore_errors=True)
        self.path_before = list(sys.path)
        self.err = io.StringIO()
        km._SDK_VENV_BUILT_FOR = []            # the once-per-verdict dedupe must not leak across tests
        self._be = mock.patch.object(km, "_sdk_backend", None)   # no backend built in this module
        self._be.start()

    def tearDown(self):
        self._be.stop()
        shutil.rmtree(self.venv, ignore_errors=True)
        sys.path[:] = self.path_before

    def _site(self, pyname):
        sp = self.venv / "lib" / pyname / "site-packages"
        sp.mkdir(parents=True)
        return str(sp)

    def _run(self, importable_from=None, fn=None):
        """Call _ensure_sdk_on_path (or `fn`) with find_spec stubbed: the SDK is importable only once a
        directory under `importable_from` is on sys.path (the real finder would answer from this machine)."""
        import importlib.util
        real_find_spec = importlib.util.find_spec

        def fake_find_spec(name, *a, **k):
            if name != "claude_agent_sdk":
                return real_find_spec(name, *a, **k)
            if importable_from and any(p.startswith(importable_from) for p in sys.path):
                return SimpleNamespace(name=name)
            return None
        with mock.patch.object(importlib.util, "find_spec", fake_find_spec), \
             mock.patch.object(sys, "stderr", self.err):
            return (fn or km._ensure_sdk_on_path)()

    def _free_threaded(self, minor=99):
        """Pretend this process is a free-threaded 3.<minor>: venv names its lib dir python3.<minor>t."""
        return mock.patch.multiple(sys, abiflags="t", version_info=(3, minor, 0, "final", 0), create=True)

    def test_a_venv_for_another_python_is_not_added_and_is_named(self):
        sp = self._site("python3.99")
        self.assertFalse(self._run(importable_from=sp))
        self.assertNotIn(sp, sys.path, "a 3.99 venv must never join a %s process" % RUNNING)
        line = self.err.getvalue()
        self.assertIn("3.99", line, "names what the venv was built for")
        self.assertIn("%d.%d" % sys.version_info[:2], line, "and what the kernel runs")
        self.assertIn("romp-sdk-setup", line)
        self.assertIn("ROMP_PYTHON", line, "both remedies: rebuild, or run the venv's interpreter")
        self.assertNotIn("not installed", line)
        self.assertEqual(km._SDK_VENV_BUILT_FOR, ["3.99"])
        self.assertFalse(self._run(importable_from=sp))
        self.assertEqual(self.err.getvalue(), line, "one line per verdict, not one per caller")

    def test_a_matching_venv_is_added_and_the_sdk_imports(self):
        self.assertEqual(km._running_python_tag(), TAG)
        sp = self._site(RUNNING)
        self.assertTrue(self._run(importable_from=sp))
        self.assertIn(sp, sys.path)
        self.assertEqual(self.err.getvalue(), "", "nothing to say when the venv matches")

    def test_only_the_matching_directory_joins_when_both_exist(self):
        # a rebuilt venv can leave the old lib/python3.X beside the new one; add the match only
        old = self._site("python3.99")
        new = self._site(RUNNING)
        self.assertTrue(self._run(importable_from=new))
        self.assertIn(new, sys.path)
        self.assertNotIn(old, sys.path)
        self.assertEqual(self.err.getvalue(), "")

    def test_a_free_threaded_interpreter_recognizes_its_own_venv(self):
        # venv names a 3.99t build's lib dir python3.99t; the match is on the full tag, so the venv that
        # interpreter built is added, not refused as "built for 3.99t but the kernel runs 3.99" (a state
        # neither remedy could exit: romp-sdk-setup saw nothing to rebuild, ROMP_PYTHON named the same binary)
        sp = self._site("python3.99t")
        with self._free_threaded():
            self.assertEqual(km._running_python_tag(), "3.99t")
            self.assertTrue(self._run(importable_from=sp))
        self.assertIn(sp, sys.path)
        self.assertEqual(self.err.getvalue(), "", "its own venv: nothing to say")
        self.assertEqual(km._SDK_VENV_BUILT_FOR, [])

    def test_a_free_threaded_interpreter_and_a_default_build_venv_are_a_mismatch(self):
        # the same major.minor is NOT the same ABI: a python3.99 venv in a 3.99t process must not be added
        sp = self._site("python3.99")
        with self._free_threaded():
            self.assertFalse(self._run(importable_from=sp))
        self.assertNotIn(sp, sys.path)
        line = self.err.getvalue()
        self.assertIn("built for python 3.99 but the kernel runs 3.99t", line)

    def test_the_creation_refusal_comes_from_the_backends_verdict(self):
        # `romp new` / the browser's create refuse with _sdk_setup_hint(), which reads the SAME verdict
        # the session card reads (SdkBackend.creation_refusal over unavailable_verdict), evaluated at
        # request time, so the two surfaces cannot tell different stories; the kernel hands over its
        # plain install hint as the default for the verdicts where the install remedy fits
        calls = []

        class Backend:
            def creation_refusal(self, default):
                calls.append(default)
                return "the backend's verdict, whatever it is"
        with mock.patch.object(km, "_sdk_backend", Backend()):
            self.assertEqual(km._sdk_setup_hint(), "the backend's verdict, whatever it is")
        self.assertEqual(calls, [km.SDK_SETUP_HINT])

    def test_without_a_backend_the_refusal_names_only_the_rebuild(self):
        # the backend module failed to load, so nothing has probed the venv's interpreter: the fallback
        # names the mismatch and the one remedy this process can vouch for; never a ROMP_PYTHON pin to
        # an interpreter nothing has seen run, never "isn't installed" over an installed venv
        self._run(importable_from=self._site("python3.99"))
        hint = km._sdk_setup_hint()
        self.assertIn("3.99", hint)
        self.assertIn(TAG, hint)
        self.assertIn("romp-sdk-setup", hint)
        self.assertIn("restart romp", hint)
        self.assertNotIn("ROMP_PYTHON", hint)
        self.assertNotIn("isn't installed", hint)
        shutil.rmtree(self.venv)
        self._run()
        self.assertEqual(km._sdk_setup_hint(), km.SDK_SETUP_HINT)

    def test_no_venv_is_the_plain_not_importable_path(self):
        self.assertFalse(self._run())
        self.assertEqual(sys.path, self.path_before)
        self.assertEqual(self.err.getvalue(), "", "no venv: nothing to say here (the backend reports it)")

    def test_the_boot_notice_is_not_found_only_when_no_mismatch_explains_it(self):
        # _sdk_locked's gate (_sdk_import_notice): with no venv the log says the SDK was not found and
        # names the install; with a venv for another python the mismatch line stands ALONE, because
        # "not found, run bin/romp-sdk-setup" over an installed venv was the misleading 2026-09-06 message
        self.assertFalse(self._run(fn=km._sdk_import_notice))
        self.assertIn("claude_agent_sdk not found", self.err.getvalue())
        self.assertIn("romp-sdk-setup", self.err.getvalue())
        self.err.truncate(0); self.err.seek(0)
        self._site("python3.99")
        self.assertFalse(self._run(fn=km._sdk_import_notice))
        line = self.err.getvalue()
        self.assertIn("built for python 3.99", line)
        self.assertNotIn("not found", line, "one verdict per boot: the mismatch, not a contradicting install line")
        self.err.truncate(0); self.err.seek(0)
        self.assertTrue(self._run(importable_from=self._site(RUNNING), fn=km._sdk_import_notice))
        self.assertEqual(self.err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
