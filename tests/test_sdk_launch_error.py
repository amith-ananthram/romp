#!/usr/bin/env python3
"""A session that CANNOT START says why — instead of swallowing every message sent to it.

The failure this closes (the user 2026-07-28, on a fresh install): romp-sdk-setup had bailed on that
machine (a python3 with no ensurepip, so the venv was never built), the kernel logged ONE stderr line and
built the SDK backend anyway, and every SDK session then accepted messages it could never run. From the
user's side: a message sent to a session did nothing at all — no flip to working, no error — a brand-new
session behaved the same, and the model/effort readouts and the usage bars stayed blank (all three publish
only AFTER a connect that could never happen). tmux sessions worked throughout, so it read as an outage at
Anthropic rather than a missing local dependency.

What is pinned here:
  1. the backend detects its own missing dependency ONCE, up front, and reports EVERY session as unable to
     start — no session has to die first for the user to be told;
  2. the text names the REMEDY (bin/romp-sdk-setup), not the symptom, and never a bare ModuleNotFoundError;
  3. a launch failure recorded on a session survives on the registry (the thread that saw it is dying) and
     is cleared by the connect that DISPROVES it, never by a timer;
  4. queued messages do NOT vanish when the session's thread dies — the persisted queue answers
     pending_queued, so what the user typed stays on screen;
  5. the account-out-of-usage flavor is classified apart, because that queue is parked, not broken.

SYNTHETIC fixtures only (placeholder ids, hostname TESTHOST).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_launcherr", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# The tag venv names this interpreter's lib directory with (`3.12`, or `3.14t` on a free-threaded build),
# computed from sys itself so the venv cases below expect a value the code under test did not produce.
TAG = "%d.%d%s" % (sys.version_info[0], sys.version_info[1], "t" if "t" in getattr(sys, "abiflags", "") else "")


class _FakeSess:
    """What _record_launch_error reads off a session: its identity, plus the stderr the CLI wrote
    before it died (a real SdkSession buffers that in _stderr_tail — see _on_cli_stderr)."""

    def __init__(self, sid=SID, name="api", stderr=()):
        self.sid = sid
        self.name = name
        self._stderr_tail = list(stderr)

    def stderr_tail(self):
        return "\n".join(self._stderr_tail)


def _sess_for_options(state=None):
    """A REAL SdkSession — the stderr buffer and its callback are the things under test, so a stub
    would pin nothing. Construction is plain attribute setup; no event loop is needed."""
    td = state or tempfile.mkdtemp()
    be = _backend(td)
    return sb.SdkSession(be, {"sid": SID, "name": "api", "cwd": td, "mode": "acceptEdits"})


def _backend(state, missing=False):
    saved = sb.sdk_importable
    sb.sdk_importable = lambda: not missing
    try:
        return sb.SdkBackend(state, "/bin/true", lambda *a, **k: None)
    finally:
        sb.sdk_importable = saved


class MissingDependencyIsReportedForEverySession(unittest.TestCase):
    """The dep is checked at construction, so the report needs no session to have crashed first."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = self.td.name

    def tearDown(self):
        self.td.cleanup()

    def test_every_session_reports_unable_to_start(self):
        be = _backend(self.state, missing=True)
        for sid in (SID, OTHER):
            err = be.launch_error(sid)
            self.assertIsNotNone(err, "a session that cannot possibly run must not read as fine")
            self.assertFalse(err["limit"], "a missing dependency is not a usage limit")

    def test_the_text_names_the_remedy_not_the_symptom(self):
        err = _backend(self.state, missing=True).launch_error(SID)
        self.assertIn("romp-sdk-setup", err["text"],
                      "the user needs the command to run, not the name of a python module")
        self.assertNotIn("ModuleNotFoundError", err["text"])
        self.assertIn("tmux", err["text"], "say what still works — tmux sessions are unaffected")

    def test_a_healthy_install_reports_nothing(self):
        self.assertIsNone(_backend(self.state, missing=False).launch_error(SID))


class VenvBuiltForAnotherInterpreter(unittest.TestCase):
    """The SDK is not importable AND a venv exists for a different python: the text says THAT, with
    the remedy that fits what is on disk, instead of claiming nothing was installed (2026-09-06: two
    hours of "isn't installed" over a venv that was present, intact and built for the old python)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = self.td.name
        self.venv = Path(self.state) / "sdkvenv"
        (self.venv / "lib" / "python3.99" / "site-packages").mkdir(parents=True)

    def tearDown(self):
        self.td.cleanup()

    def _cfg(self, executable):
        (self.venv / "pyvenv.cfg").write_text(
            "home = %s\nversion = 3.99.0\nexecutable = %s\n" % (os.path.dirname(executable), executable))

    def test_recorded_interpreter_present_names_the_pin(self):
        # the stub stands for the venv's own python: the kernel's probe runs it and reads the tag it
        # prints (a `#!/bin/sh` script gets `-c ...` as positional parameters, so it answers with the
        # fixed tag), and it reports the venv's, so a pin to it brings the kernel up matching the venv
        interp = os.path.join(self.state, "py399", "python3.99")
        os.makedirs(os.path.dirname(interp))
        Path(interp).write_text("#!/bin/sh\necho 3.99\n")
        os.chmod(interp, 0o755)
        self._cfg(interp)
        be = _backend(self.state, missing=True)
        v = be.unavailable_verdict()
        self.assertEqual(v["kind"], "mismatch")
        self.assertEqual(v["interp_tag"], "3.99", "the probe reads the tag the interpreter reports")
        self.assertNotIn("interp_runs", v, "exit 0 alone decides nothing")
        err = be.launch_error(SID)
        text = err["text"]
        self.assertTrue(err["dep"])
        self.assertNotIn("isn't installed", text, "it IS installed; the interpreter changed")
        self.assertIn("3.99", text, "what the venv was built for")
        self.assertIn(TAG, text, "what romp is running on")
        self.assertIn("ROMP_PYTHON=" + interp, text, "the interpreter is still there: point romp at it")
        self.assertIn("restart", text)
        self.assertNotIn("romp-sdk-setup", text, "one remedy, the one that fits")
        self.assertIn("tmux", text)

    def test_recorded_interpreter_gone_names_the_rebuild(self):
        self._cfg(os.path.join(self.state, "gone", "python3.99"))
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertNotIn("isn't installed", text)
        self.assertIn("3.99", text)
        self.assertIn("romp-sdk-setup", text, "the old interpreter is gone: rebuild for the new one")
        self.assertNotIn("ROMP_PYTHON", text, "a pin to a missing interpreter would not help")

    def test_recorded_interpreter_present_but_not_running_names_the_rebuild(self):
        # executable, and exits non-zero when run: the state pick_python's fallback creates (the kernel
        # came up on a newer python BECAUSE this one is broken). A pin to it would have romp-serve exec a
        # binary that cannot start, and the manager respawn it in a loop
        interp = os.path.join(self.state, "py399", "python3.99")
        os.makedirs(os.path.dirname(interp))
        Path(interp).write_text("#!/bin/sh\nexit 1\n")
        os.chmod(interp, 0o755)
        self._cfg(interp)
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertIn("3.99", text)
        self.assertIn("romp-sdk-setup", text, "it will not run: rebuild for the python romp has")
        self.assertNotIn("ROMP_PYTHON", text, "never prescribe a pin to an interpreter that does not run")
        self.assertEqual(sb.interpreter_tag(interp), "", "a non-zero exit reports no tag")
        self.assertEqual(sb.interpreter_tag(os.path.join(self.state, "nope")), "", "nor does a missing path")
        self.assertEqual(sb.interpreter_tag(sys.executable), TAG, "a real python reports its own tag")
        # and one that prints the venv's tag and then never exits: the probe is bounded, so it reads as not
        # running (asserted on the verdict, not on elapsed time; unbounded, this call would return the tag
        # once the stub's sleep ends, 30 s on)
        hang = os.path.join(self.state, "py399", "python3.99-hangs")
        Path(hang).write_text("#!/bin/sh\necho 3.99\nexec sleep 30\n")
        os.chmod(hang, 0o755)
        self.assertEqual(sb.interpreter_tag(hang, timeout=0.5), "", "a tag from a python that never exits is no answer")

    def test_the_probe_reads_a_tag_from_the_last_line_and_nothing_else(self):
        # the probe parses the interpreter's stdout, so what it accepts is narrow: the last line, stripped,
        # must be a whole tag (`3.12`, `3.14t`). A sitecustomize that prints ahead of the tag does not
        # defeat it, and a script that prints anything else, or prints a tag and then fails, reports none
        def stub(name, body):
            path = os.path.join(self.state, "py399", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            Path(path).write_text("#!/bin/sh\n" + body)
            os.chmod(path, 0o755)
            return path
        self.assertEqual(sb.interpreter_tag(stub("noisy", "echo a sitecustomize said this\necho 3.99\n")), "3.99")
        self.assertEqual(sb.interpreter_tag(stub("padded", "echo '  3.99t  '\n")), "3.99t")
        self.assertEqual(sb.interpreter_tag(stub("indented", "echo noise\necho '  3.99  '\n")), "3.99",
                         "the last line is stripped on its own, not only the output as a whole")
        self.assertEqual(sb.interpreter_tag(stub("trailing", "echo 3.99\necho\n")), "3.99",
                         "a blank line after the tag (an atexit hook that prints nothing) is not the answer")
        self.assertEqual(sb.interpreter_tag(stub("words", "echo hello\n")), "")
        self.assertEqual(sb.interpreter_tag(stub("silent", "")), "", "exit 0 with no tag is not a python")
        self.assertEqual(sb.interpreter_tag(stub("partial", "echo 3.99 and more\n")), "")
        self.assertEqual(sb.interpreter_tag(stub("failing", "echo 3.99\nexit 1\n")), "")
        self.assertEqual(sb.interpreter_tag(""), "")

    def test_the_probes_program_is_the_tag_this_process_computes(self):
        # the program the probe runs in the recorded interpreter is the expression running_python_tag()
        # evaluates in this process (and bin/romp-sdk-setup's pytag runs in the setup script), so the two
        # sides of the remedy's comparison are computed the same way. Run here it prints this process's tag;
        # under a free-threaded 3.99 it prints 3.99t, the build half of the tag, which the probe never
        # exercises on a default-build machine's own interpreter
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(sb._TAG_PROGRAM, {})
        self.assertEqual(out.getvalue(), TAG + "\n")
        self.assertEqual(out.getvalue().strip(), sb.running_python_tag())
        out = io.StringIO()
        with mock.patch.multiple(sys, abiflags="t", version_info=(3, 99, 0, "final", 0), create=True), \
             contextlib.redirect_stdout(out):
            exec(sb._TAG_PROGRAM, {})
            self.assertEqual(sb.running_python_tag(), "3.99t")
        self.assertEqual(out.getvalue(), "3.99t\n")

    def test_the_remedy_follows_the_tag_the_probe_reports(self):
        # the verdict's seam: with the probe's answer given, the remedy is a function of that tag against
        # the tags the venv was built for and nothing else. The venv's own tag names the pin; this process's
        # tag (a repointed `python3`), the same minor in the other build (3.99t over a venv built for 3.99,
        # whose extensions are not that python's either) and no tag at all name the rebuild
        interp = os.path.join(self.state, "py399", "python3.99")
        self._cfg(interp)
        for answer, pin in (("3.99", True), (TAG, False), ("3.99t", False), ("", False)):
            with self.subTest(answer=answer):
                seen = []
                v = sb.sdk_venv_verdict(self.state, probe=lambda p: seen.append(p) or answer)
                self.assertEqual(seen, [interp], "the probe is asked about the recorded interpreter")
                self.assertEqual((v["kind"], v["interp"], v["interp_tag"]), ("mismatch", interp, answer))
                text = sb._mismatch_remedy(v)
                self.assertEqual("ROMP_PYTHON=" + interp in text, pin)
                self.assertEqual("romp-sdk-setup" in text, not pin)

    def test_a_recorded_interpreter_that_reports_another_tag_names_the_rebuild(self):
        # the recorded path runs and exits 0, but it is no longer the venv's python: an upgrade repointed
        # `python3` (or replaced the recorded binary in place) at the python romp now runs. bin/romp-serve
        # honors a ROMP_PYTHON pin as given, so a pin to this path brings the kernel up on that same other
        # python and this card returns after the restart with the same remedy; the rebuild is the one
        # that works. The stub answers the probe with THIS process's tag, as that repointed binary would
        interp = os.path.join(self.state, "py399", "python3")
        os.makedirs(os.path.dirname(interp))
        Path(interp).write_text("#!/bin/sh\necho %s\n" % TAG)
        os.chmod(interp, 0o755)
        self._cfg(interp)
        be = _backend(self.state, missing=True)
        text = be.launch_error(SID)["text"]
        self.assertIn("3.99", text)
        self.assertIn(TAG, text)
        self.assertNotIn("ROMP_PYTHON", text,
                         "a pin to a path that no longer runs the venv's python repeats the same card after the restart")
        self.assertIn("romp-sdk-setup", text, "it runs, but as another python: rebuild for the one romp has")
        v = be.unavailable_verdict()
        self.assertEqual(v["kind"], "mismatch")
        self.assertEqual(v["built"], ["3.99"])
        self.assertEqual(v["interp"], interp)
        self.assertEqual(v["interp_tag"], TAG, "it runs, and reports the python romp is already on")

    def test_a_cfg_with_home_and_version_but_no_executable_names_the_pin(self):
        # python < 3.11 wrote no `executable =` line (and uv writes version_info): home plus the version's
        # X.Y reach the interpreter, and when it runs as the venv's python the pin to it is the remedy, as
        # with `executable`
        home = os.path.join(self.state, "py399")
        interp = os.path.join(home, "python3.99")
        os.makedirs(home)
        Path(interp).write_text("#!/bin/sh\necho 3.99\n")
        os.chmod(interp, 0o755)
        (self.venv / "pyvenv.cfg").write_text("home = %s\nversion = 3.99.0\n" % home)
        self.assertEqual(sb.sdk_venv_interpreter(self.state), interp)
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertIn("ROMP_PYTHON=" + interp, text)
        self.assertNotIn("romp-sdk-setup", text)
        (self.venv / "pyvenv.cfg").write_text("home = %s\nversion_info = 3.99.0\n" % home)
        self.assertEqual(sb.sdk_venv_interpreter(self.state), interp, "uv's spelling of the version line")
        (self.venv / "pyvenv.cfg").write_text("home = %s\n" % home)
        self.assertEqual(sb.sdk_venv_interpreter(self.state), "", "a home with no version names no binary")

    def test_the_boot_log_line_carries_the_verdict_not_the_fixed_install_remedy(self):
        # the backend's construction-time line for an unimportable SDK is the same verdict the card
        # shows: over a venv for another python whose interpreter runs, it names the pin, not an install
        interp = os.path.join(self.state, "py399", "python3.99")
        os.makedirs(os.path.dirname(interp))
        Path(interp).write_text("#!/bin/sh\necho 3.99\n")
        os.chmod(interp, 0o755)
        self._cfg(interp)
        lines = []
        saved = sb.sdk_importable
        sb.sdk_importable = lambda: False
        try:
            sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lines.append)
        finally:
            sb.sdk_importable = saved
        boot = [ln for ln in lines if "NOT importable" in ln]
        self.assertEqual(len(boot), 1, lines)
        self.assertIn("3.99", boot[0])
        self.assertIn("ROMP_PYTHON=" + interp, boot[0])
        self.assertNotIn("run bin/romp-sdk-setup", boot[0])

    def test_no_pyvenv_cfg_still_says_mismatch(self):
        # the lib/python3.99 directory alone proves the mismatch; without a cfg the rebuild is the remedy
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertIn("3.99", text)
        self.assertIn("romp-sdk-setup", text)

    def test_a_matching_venv_that_still_fails_is_the_plain_missing_text(self):
        # a venv for THIS python with no importable SDK is a broken/half-built venv: the install remedy
        shutil.rmtree(self.venv)
        (self.venv / "lib" / ("python" + TAG) / "site-packages").mkdir(parents=True)
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertEqual(text, sb.SDK_MISSING_TEXT)

    def test_no_venv_is_the_plain_missing_text(self):
        shutil.rmtree(self.venv)
        text = _backend(self.state, missing=True).launch_error(SID)["text"]
        self.assertEqual(text, sb.SDK_MISSING_TEXT)

    def test_a_late_import_error_records_the_same_mismatch_text(self):
        # the dependency check passed at construction but a session's own import failed: the record
        # written onto the session reads the disk at that moment, not a stale construction-time verdict
        be = _backend(self.state, missing=False)
        be._record_launch_error(_FakeSess(), ImportError("No module named 'pydantic_core._pydantic_core'"))
        rec = (sb.read_reg(Path(self.state), SID) or {})["launchError"]
        self.assertTrue(rec["dep"])
        self.assertIn("3.99", rec["text"])
        self.assertNotIn("isn't installed", rec["text"])

    def test_a_free_threaded_tag_is_the_whole_tag(self):
        # a 3.99t process against a python3.99 venv is a mismatch (and against python3.99t is not)
        with mock.patch.multiple(sys, abiflags="t", version_info=(3, 99, 0, "final", 0), create=True):
            self.assertEqual(sb.running_python_tag(), "3.99t")
            self.assertEqual(sb.sdk_venv_verdict(self.state)["kind"], "mismatch")
            shutil.rmtree(self.venv)
            (self.venv / "lib" / "python3.99t" / "site-packages" / "claude_agent_sdk").mkdir(parents=True)
            (self.venv / "lib" / "python3.99t" / "site-packages" / "claude_agent_sdk" / "__init__.py").write_text("")
            self.assertEqual(sb.sdk_venv_verdict(self.state)["kind"], "present")


class OneVerdictForEverySurface(unittest.TestCase):
    """The session card (launch_error) and the session-creation refusal (creation_refusal, which the
    kernel's `romp new` and browser create read) take their text from ONE verdict, read from the disk at
    request time. After the user runs bin/romp-sdk-setup while the kernel is up, both say to restart
    romp; before this the refusal flipped back to "isn't installed" (the remedy the user had just
    applied) while the card still said mismatch, and the refusal named a ROMP_PYTHON pin without
    checking the interpreter existed."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = self.td.name
        self.venv = Path(self.state) / "sdkvenv"

    def tearDown(self):
        self.td.cleanup()

    def _venv(self, tag, with_sdk=False):
        sp = self.venv / "lib" / ("python" + tag) / "site-packages"
        sp.mkdir(parents=True, exist_ok=True)
        if with_sdk:
            (sp / "claude_agent_sdk").mkdir(exist_ok=True)
            (sp / "claude_agent_sdk" / "__init__.py").write_text("")

    def _interp(self, body="#!/bin/sh\necho 3.99\n"):
        """A stub for the venv's recorded interpreter; by default the venv's own python, which answers the
        kernel's probe with the venv's tag (a `#!/bin/sh` script gets `-c ...` as positional parameters)."""
        interp = os.path.join(self.state, "py399", "python3.99")
        os.makedirs(os.path.dirname(interp), exist_ok=True)
        Path(interp).write_text(body)
        os.chmod(interp, 0o755)
        (self.venv / "pyvenv.cfg").write_text("home = %s\nversion = 3.99.0\nexecutable = %s\n"
                                              % (os.path.dirname(interp), interp))
        return interp

    def test_no_venv_is_the_plain_pair(self):
        be = _backend(self.state, missing=True)
        self.assertEqual(be.launch_error(SID)["text"], sb.SDK_MISSING_TEXT)
        self.assertEqual(be.creation_refusal(default="THE KERNEL'S HINT"), "THE KERNEL'S HINT")
        self.assertEqual(be.creation_refusal(), sb.SDK_SETUP_REFUSAL)

    def test_a_venv_for_this_python_with_no_sdk_in_it_is_the_plain_pair(self):
        self._venv(TAG)
        be = _backend(self.state, missing=True)
        self.assertEqual(be.unavailable_verdict()["kind"], "broken")
        self.assertEqual(be.launch_error(SID)["text"], sb.SDK_MISSING_TEXT)
        self.assertEqual(be.creation_refusal(), sb.SDK_SETUP_REFUSAL)

    def test_mismatch_with_a_running_interpreter_offers_the_pin_on_both(self):
        self._venv("3.99")
        interp = self._interp()
        be = _backend(self.state, missing=True)
        card, refusal = be.launch_error(SID)["text"], be.creation_refusal()
        for text in (card, refusal):
            self.assertIn("3.99", text)
            self.assertIn(TAG, text)
            self.assertIn("ROMP_PYTHON=" + interp, text)
            self.assertNotIn("romp-sdk-setup", text, "one remedy, the one that fits")
        self.assertTrue(refusal.startswith("Session not created:"))
        self.assertIn("restart romp and try again", refusal)
        self.assertNotIn("messages are being kept", refusal, "no session exists to keep messages for")

    def test_mismatch_with_a_broken_interpreter_offers_the_rebuild_on_both(self):
        self._venv("3.99")
        self._interp("#!/bin/sh\nexit 1\n")
        be = _backend(self.state, missing=True)
        for text in (be.launch_error(SID)["text"], be.creation_refusal()):
            self.assertIn("romp-sdk-setup", text)
            self.assertNotIn("ROMP_PYTHON", text)

    def test_mismatch_with_an_interpreter_of_another_tag_offers_the_rebuild_on_both(self):
        # the recorded interpreter runs, and reports this process's tag rather than the venv's (a
        # repointed `python3`): both surfaces name the rebuild, never a pin that would bring the kernel
        # up on the same python again
        self._venv("3.99")
        self._interp("#!/bin/sh\necho %s\n" % TAG)
        be = _backend(self.state, missing=True)
        for text in (be.launch_error(SID)["text"], be.creation_refusal()):
            self.assertIn("3.99", text)
            self.assertNotIn("ROMP_PYTHON", text)
            self.assertIn("romp-sdk-setup", text)
        self.assertEqual(be.unavailable_verdict()["interp_tag"], TAG)

    def test_mismatch_with_an_interpreter_of_the_other_build_offers_the_rebuild_on_both(self):
        # the recorded interpreter reports the venv's minor in the other build (3.99t over a venv built for
        # 3.99: a free-threaded python swapped in at the recorded path). The venv's extensions are not that
        # python's, bin/romp-serve's _runs_as would not follow it, and a pin to it would bring the kernel up
        # refusing the same venv: both surfaces name the rebuild
        self._venv("3.99")
        self._interp("#!/bin/sh\necho 3.99t\n")
        be = _backend(self.state, missing=True)
        for text in (be.launch_error(SID)["text"], be.creation_refusal()):
            self.assertIn("3.99", text)
            self.assertNotIn("ROMP_PYTHON", text)
            self.assertIn("romp-sdk-setup", text)
        self.assertEqual(be.unavailable_verdict()["interp_tag"], "3.99t")

    def test_a_repointed_recorded_interpreter_moves_the_cached_verdict_to_the_rebuild(self):
        # the trace end to end on one backend: the cfg records `python3`, a link to the venv's own python,
        # and both surfaces name the pin. An upgrade repoints the link at another build, which runs and
        # exits 0 as the python this process is on. The verdict is cached on the venv's fingerprint, which
        # stats the recorded path through the link (the target's mtime and size), so the next ask re-runs
        # the probe and both surfaces move to the rebuild. The two targets differ in size, so the change of
        # fingerprint does not rest on the clock
        self._venv("3.99")
        home = os.path.join(self.state, "py399")
        os.makedirs(home)
        own = os.path.join(home, "venv-build")
        Path(own).write_text("#!/bin/sh\necho 3.99\n")
        other = os.path.join(home, "other-build")
        Path(other).write_text("#!/bin/sh\n# the build an upgrade put at the same path\necho %s\n" % TAG)
        for p in (own, other):
            os.chmod(p, 0o755)
        link = os.path.join(home, "python3")
        os.symlink(own, link)
        (self.venv / "pyvenv.cfg").write_text("home = %s\nversion = 3.99.0\nexecutable = %s\n" % (home, link))
        be = _backend(self.state, missing=True)
        for text in (be.launch_error(SID)["text"], be.creation_refusal()):
            self.assertIn("ROMP_PYTHON=" + link, text)
            self.assertNotIn("romp-sdk-setup", text)
        os.remove(link)
        os.symlink(other, link)
        for text in (be.launch_error(SID)["text"], be.creation_refusal()):
            self.assertNotIn("ROMP_PYTHON", text, "the same pin again would show this text again after the restart")
            self.assertIn("romp-sdk-setup", text)
        v = be.unavailable_verdict()
        self.assertEqual((v["kind"], v["interp"], v["interp_tag"]), ("mismatch", link, TAG))

    def test_a_rebuild_while_the_kernel_runs_moves_both_surfaces_to_the_restart(self):
        # kernel on this python, venv for 3.99: both say mismatch. The user rebuilds for this python
        # WITHOUT restarting: the same backend, asked again, says the backend was set up after romp
        # started and to restart; neither surface falls back to "isn't installed"
        self._venv("3.99")
        be = _backend(self.state, missing=True)
        self.assertIn("3.99", be.launch_error(SID)["text"])
        self.assertIn("3.99", be.creation_refusal())
        shutil.rmtree(self.venv)
        self._venv(TAG, with_sdk=True)
        card, refusal = be.launch_error(SID)["text"], be.creation_refusal()
        self.assertEqual(be.unavailable_verdict()["kind"], "present")
        for text in (card, refusal):
            self.assertIn("after romp started", text)
            self.assertIn("Restart romp", text)
            self.assertNotIn("isn't installed", text)
            self.assertNotIn("romp-sdk-setup", text, "the user just ran it")
            self.assertNotIn("3.99", text)
        self.assertTrue(refusal.startswith("Session not created:"))
        self.assertIn("tmux", card)

    def test_the_verdict_is_cached_on_the_disk_state_and_the_probe_runs_once_per_state(self):
        self._venv("3.99")
        self._interp()
        be = _backend(self.state, missing=True)
        with mock.patch.object(sb, "interpreter_tag", wraps=sb.interpreter_tag) as probe:
            for _ in range(5):
                be.launch_error(SID)
                be.creation_refusal()
            self.assertEqual(probe.call_count, 1, "ten reads of the same disk state: one probe")
            (self.venv / "pyvenv.cfg").write_text("home = /nowhere\nversion = 3.99.0\nexecutable = /nowhere/python3.99\n")
            self.assertIn("romp-sdk-setup", be.launch_error(SID)["text"], "the cfg changed: re-read")

    def test_a_late_import_error_in_a_process_that_had_the_sdk_is_not_a_late_build(self):
        # the SDK imported at construction and a session's import then failed with the venv present for
        # this python: the import broke, so the install text's rebuild is the remedy, not "restart romp"
        self._venv(TAG, with_sdk=True)
        be = _backend(self.state, missing=False)
        be._record_launch_error(_FakeSess(), ImportError("No module named 'pydantic_core._pydantic_core'"))
        rec = (sb.read_reg(Path(self.state), SID) or {})["launchError"]
        self.assertEqual(rec["text"], sb.SDK_MISSING_TEXT)


class RecordedLaunchFailures(unittest.TestCase):
    """A failure the thread saw on its way out has to outlive the thread — so it lands on the registry."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name, missing=False)

    def tearDown(self):
        self.td.cleanup()

    def _reg(self, sid=SID):
        return sb.read_reg(Path(self.td.name), sid) or {}

    def test_an_import_failure_records_the_remedy(self):
        self.be._record_launch_error(_FakeSess(), ImportError("No module named 'claude_agent_sdk'"))
        rec = self._reg()["launchError"]
        self.assertIn("romp-sdk-setup", rec["text"])
        self.assertTrue(rec["dep"])
        self.assertEqual(self.be.launch_error(SID)["text"], rec["text"])

    def test_an_ordinary_failure_keeps_its_own_text(self):
        self.be._record_launch_error(_FakeSess(), RuntimeError("claude exited with code 1"))
        rec = self._reg()["launchError"]
        self.assertIn("claude exited with code 1", rec["text"])
        self.assertFalse(rec["limit"], "a plain crash is not a usage limit")

    def test_a_fallback_launch_that_then_fails_records_the_clis_reason(self):
        # the scope wrapper's fallback notice is the first stderr line of such a launch, and it was
        # logged when it arrived (tests/test_cli_scope.py FallbackNotice); the card keeps the CLI's line
        sess = _FakeSess(stderr=[LaunchFailureText.NOTICE,
                                 "Error: No conversation found with session ID: " + SID])
        self.be._record_launch_error(sess, RuntimeError("Command failed with exit code 1"))
        text = self._reg()["launchError"]["text"]
        self.assertTrue(text.startswith("Error: No conversation found"), text)
        self.assertNotIn("romp-cli-scope", text)

    def test_the_connect_that_disproves_it_clears_it(self):
        self.be._record_launch_error(_FakeSess(), RuntimeError("transport closed"))
        self.assertIsNotNone(self.be.launch_error(SID))
        self.be._clear_launch_error(SID)
        self.assertIsNone(self.be.launch_error(SID),
                          "the record clears on the connect that disproves it, never on a timer")

    def test_clearing_a_session_that_never_failed_is_a_no_op(self):
        self.be._clear_launch_error(SID)
        self.assertIsNone(self.be.launch_error(SID))


class LaunchFailureText(unittest.TestCase):
    """Pick the line that actually names the cause, and know a usage limit when the CLI states one."""

    def test_the_clis_own_stderr_wins_over_the_exception_repr(self):
        exc = RuntimeError("Command failed")
        exc.stderr = "You've hit your session limit · resets 4:00pm (America/Los_Angeles)"
        self.assertIn("session limit", sb.launch_failure_text(exc))

    def test_a_long_stderr_dump_is_bounded(self):
        exc = RuntimeError("boom")
        exc.stderr = "x" * 5000
        self.assertLessEqual(len(sb.launch_failure_text(exc)), 601, "this text lands in a chat card")

    def test_a_bare_exception_still_yields_text(self):
        self.assertIn("ValueError", sb.launch_failure_text(ValueError("no executable found")))

    def test_the_sdks_placeholder_is_never_shown_as_the_reason(self):
        """"Check stderr output for details" is what the SDK substitutes when nobody piped the
        child's stderr. Showing it sends the user to read an output romp never captured — and it
        used to OUTRANK the exception text, so the card said strictly less than nothing."""
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc)
        self.assertNotIn("Check stderr output", text)
        self.assertIn("exit code 1", text, "fall through to the text that at least names the failure")

    def test_the_captured_tail_answers_when_the_exception_only_has_the_placeholder(self):
        """The whole point of piping stderr: the CLI's own line becomes the reason shown."""
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        tail = "No conversation found with session ID: 11111111-2222-3333-4444-555555555555"
        self.assertIn("No conversation found", sb.launch_failure_text(exc, tail))

    def test_a_real_stderr_still_outranks_the_captured_tail(self):
        exc = RuntimeError("Command failed")
        exc.stderr = "claude: command not found"
        self.assertIn("command not found", sb.launch_failure_text(exc, "some older noise"))

    # bin/romp-cli-scope's fallback notice (2026-09-05): on a launch whose pre-flight scope failed it is
    # the FIRST line of the CLI's stderr, about 230 characters, and the kernel logs it the moment it
    # arrives (_note_cli_scope_fallback). Left in the tail, it led the card text of a CLI that then
    # failed at start and pushed the CLI's own reason past the 600-character cut.
    NOTICE = (sb.CLI_SCOPE_FALLBACK_PREFIX + " systemd-run cannot start a transient scope (Failed to connect to "
              "bus: No such file or directory) — running the CLI directly, outside a scope; a service restart "
              "will take its background work down")

    def test_the_scope_wrappers_fallback_notice_is_dropped_from_the_tail(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        tail = self.NOTICE + "\n" + "y" * 500 + "\nNo conversation found with session ID: " + SID
        text = sb.launch_failure_text(exc, tail)
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("y" * 500), "the CLI's own stderr leads the card: %r" % text[:60])
        self.assertIn("No conversation found", text, "and its reason fits inside the cut")

    def test_the_notice_is_dropped_from_the_exceptions_own_stderr_too(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = self.NOTICE + "\nclaude: the CLI's own reason"
        text = sb.launch_failure_text(exc)
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("claude: the CLI's own reason"), text)

    def test_a_tail_that_was_only_the_notice_falls_through_to_the_exception(self):
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc, self.NOTICE)
        self.assertNotIn("romp-cli-scope", text)
        self.assertIn("exit code 1", text)

    def test_the_wrappers_ignored_line_is_dropped_from_the_tail_too(self):
        # the third form (2026-09-06): a per-session limit not applied; the CLI starts in its scope and
        # the kernel logged the line at arrival (_note_cli_scope_ignored), so on the card it is noise
        ignored = (sb.CLI_SCOPE_IGNORED_PREFIX + " ROMP_CLI_SCOPE_MEMORY_MAX is not a size (digits with an optional "
                   "K, M, G or T suffix, or infinity) — the CLI runs in its scope without it")
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        text = sb.launch_failure_text(exc, ignored + "\n" + self.NOTICE + "\nclaude: the CLI's own reason")
        self.assertNotIn("romp-cli-scope", text)
        self.assertTrue(text.startswith("claude: the CLI's own reason"), text)

    def test_the_wrappers_refusal_stays_on_the_card(self):
        # ROMP_CLI_REAL unset: the wrapper exits 127 before any CLI runs, so its line IS the reason and
        # nothing else reports it (the kernel logs no fallback for it: tests/test_cli_scope.py)
        exc = RuntimeError("Command failed with exit code 127")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        refusal = sb.CLI_SCOPE_REFUSAL_PREFIX + " ROMP_CLI_REAL is unset or empty; it must name the real claude CLI"
        text = sb.launch_failure_text(exc, refusal)
        self.assertIn("ROMP_CLI_REAL", text)
        self.assertTrue(text.startswith(sb.CLI_SCOPE_REFUSAL_PREFIX), text)

    def test_usage_limits_are_classified_apart_from_breakage(self):
        self.assertTrue(sb.is_launch_limit("You've hit your session limit · resets 4:00pm"))
        self.assertTrue(sb.is_launch_limit("usage limit reached"))
        self.assertFalse(sb.is_launch_limit("claude: command not found"))
        self.assertFalse(sb.is_launch_limit(""))


class TheClisStderrIsCaptured(unittest.TestCase):
    """The CLI's stderr has to be PIPED to exist at all, and the reason has to reach the log.

    The failure this closes (the user 2026-07-29): every SDK session in the fleet died at launch and
    each card said only "Check stderr output for details". There was no stderr to check — romp had
    never registered options.stderr, so the SDK handed the child romp's own stderr and dropped the
    line the CLI printed on its way out. The cause (a moved repo, so every --resume looked for a
    conversation under a path that no longer held it) was knowable the whole time and shown nowhere.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_options_register_the_stderr_callback(self):
        """Without this the transport never pipes the child's stderr — the whole failure above."""
        captured = {}

        class _FakeOptions:
            def __init__(self, **kw):
                captured.update(kw)

        sess = _sess_for_options()
        fake_sdk = types.ModuleType("claude_agent_sdk")
        # an attribute-bearing stand-in, like the SDK's dataclass: with hosts on (the default since T348) the
        # options loop sets each matcher's `timeout` to the host's hook bound, which a plain dict refused
        fake_sdk.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
        saved = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake_sdk
        try:
            self.be._options(sess, _FakeOptions)
        finally:
            if saved is None:
                del sys.modules["claude_agent_sdk"]
            else:
                sys.modules["claude_agent_sdk"] = saved
        cb = captured.get("stderr")
        self.assertIsNotNone(cb, "options.stderr must be registered or the CLI's stderr is discarded")
        self.assertIs(getattr(cb, "__func__", None), sb.SdkSession._on_cli_stderr)
        self.assertIs(getattr(cb, "__self__", None), sess, "and bound to THIS session's buffer")

    def test_the_tail_keeps_the_last_lines_and_is_bounded(self):
        sess = _sess_for_options()
        for i in range(sb.STDERR_TAIL_LINES * 3):
            sess._on_cli_stderr("line %d\n" % i)
        tail = sess.stderr_tail().splitlines()
        self.assertEqual(len(tail), sb.STDERR_TAIL_LINES, "a chatty CLI must not grow this forever")
        self.assertEqual(tail[-1], "line %d" % (sb.STDERR_TAIL_LINES * 3 - 1),
                         "the LAST lines are the ones that name the exit")

    def test_blank_stderr_lines_are_not_kept(self):
        sess = _sess_for_options()
        for line in ("\n", "   ", "real trouble\n", ""):
            sess._on_cli_stderr(line)
        self.assertEqual(sess.stderr_tail(), "real trouble")

    def test_the_recorded_failure_shows_what_the_cli_said(self):
        sess = _FakeSess(stderr=["No conversation found with session ID: %s" % SID])
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        self.be._record_launch_error(sess, exc)
        text = (sb.read_reg(Path(self.td.name), SID) or {})["launchError"]["text"]
        self.assertIn("No conversation found", text)
        self.assertNotIn("Check stderr output", text)

    def test_the_full_stderr_reaches_the_kernel_log(self):
        """The card gets one glanceable line; the log is where the user goes looking, so it gets
        everything the CLI said."""
        lines = []
        be = sb.SdkBackend(self.td.name, "/bin/true", lambda *a, **k: None,
                           log=lambda m, *a, **k: lines.append(m))
        sess = _FakeSess(stderr=["first complaint", "No conversation found with session ID: %s" % SID])
        exc = RuntimeError("Command failed with exit code 1")
        exc.stderr = sb.SDK_STDERR_PLACEHOLDER
        be._record_launch_error(sess, exc)
        blob = "\n".join(lines)
        self.assertIn("first complaint", blob, "the whole tail belongs in the log, not just the last line")
        self.assertIn("No conversation found", blob)

    def test_a_dependency_failure_still_reports_the_remedy(self):
        """The tail must not displace the one text that names what to run."""
        sess = _FakeSess(stderr=["irrelevant chatter"])
        self.be._record_launch_error(sess, ImportError("No module named 'claude_agent_sdk'"))
        rec = (sb.read_reg(Path(self.td.name), SID) or {})["launchError"]
        self.assertIn("romp-sdk-setup", rec["text"])
        self.assertNotIn("irrelevant chatter", rec["text"])


class QueuedMessagesSurviveTheSessionsDeath(unittest.TestCase):
    """What the user typed must stay on screen when the CLI dies — it is still owed to them."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = _backend(self.td.name, missing=False)

    def tearDown(self):
        self.td.cleanup()

    def test_the_persisted_queue_answers_when_no_session_is_running(self):
        typed = "set up the deploy script for the notes-api"
        self.be._update_reg(SID, queue=[typed])
        self.assertEqual(self.be.pending_queued(SID), [typed],
                         "a dead thread must not make the user's message vanish from the chat")

    def test_a_session_with_no_queue_reports_nothing(self):
        self.be._update_reg(SID, queue=[])
        self.assertEqual(self.be.pending_queued(SID), [])
        self.assertEqual(self.be.pending_queued(OTHER), [])

    def test_a_corrupt_queue_mirror_does_not_crash_the_chat(self):
        self.be._update_reg(SID, queue="not a list")
        self.assertEqual(self.be.pending_queued(SID), [])
        self.be._update_reg(OTHER, queue=[None, "", "keep me", 7])
        self.assertEqual(self.be.pending_queued(OTHER), ["keep me"])


class ContractConformance(unittest.TestCase):
    """launch_error is part of the backend contract, with a None default for tmux."""

    def test_the_abc_defaults_to_no_known_failure(self):
        mod = load_source(
            "romp_session_backend_launcherr",
            os.path.join(BIN, "romp_session_backend.py"))
        self.assertIsNone(mod.SessionBackend.launch_error(object(), SID),
                          "a backend whose CLI launches into a visible pane reports nothing here")


class KernelSurfaces(unittest.TestCase):
    """The kernel side: the error reaches the chat, and a usage-limit launch parks the queue instead."""

    @classmethod
    def setUpClass(cls):
        cls.kernel_src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()

    def test_the_chat_raises_a_card_for_a_launch_failure(self):
        self.assertIn("_lerr = _launch_error(sid)", self.kernel_src,
                      "the chat build must ask the backend why the session could not start")
        self.assertIn('"This session\'s claude process could not start — %s" % _lerr["text"]',
                      self.kernel_src)
        self.assertIn('_lerr["text"] if _lerr.get("dep")', self.kernel_src,
                      "a missing dependency already reads as a sentence — don't wrap it in a second one")

    def test_a_usage_limit_launch_parks_the_queue_instead_of_erroring(self):
        self.assertIn('if _lerr and not _lerr.get("limit")', self.kernel_src,
                      "a parked queue is a wait, not damage — it must not also raise a red card")
        self.assertIn('_le = _launch_error(sid)', self.kernel_src,
                      "_limit_hold reads the launch that the limit refused — usage.json cannot see it")


if __name__ == "__main__":
    unittest.main()


class SessionCreationRefusesWhenTheSdkCannotRun(unittest.TestCase):
    """The gap the user actually hit: they created a session in the BROWSER and got no error at all.

    Both creation paths (the WS createSession op and POST /new for `romp new`) already carried the
    right refusal — never silently hand back something that can't work — and both asked `_sdk()`.
    But the backend object is built even with the dependency missing, on purpose, so it can keep
    owning the registry and the chat. `_sdk()` therefore answered "yes" and the refusal never fired:
    a session was created that could never run, silently (the user 2026-07-28)."""

    def test_the_backend_reports_its_own_unavailability(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.assertFalse(_backend(td.name, missing=True).available(),
                         "a backend that cannot import its SDK must not answer 'ready'")
        self.assertTrue(_backend(td.name, missing=False).available())

    def test_both_creation_paths_gate_on_ready_not_on_the_object(self):
        src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()
        self.assertIn("def _sdk_ready():", src)
        self.assertIn("if _sdk_ready():", src, "the WS createSession op")
        self.assertIn("if not _sdk_ready():", src, "POST /new, the `romp new` path")
        self.assertNotIn("if _sdk():\n                        _create_sdk_session", src,
                         "the old check took a dependency-less backend as a yes")

    def test_the_refusal_names_the_remedy_and_says_nothing_was_created(self):
        src = Path(BIN).parent.joinpath("kernel", "kernel.py").read_text()
        self.assertIn("SDK_SETUP_HINT = ", src)
        i = src.index("SDK_SETUP_HINT = ")
        hint = src[i:i + 400]
        self.assertIn("Session not created", hint, "say plainly that nothing was made")
        self.assertIn("romp-sdk-setup", hint, "name the one command that fixes it")
