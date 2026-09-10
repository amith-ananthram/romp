#!/usr/bin/env python3
"""The silent wrong-case launch (the user 2026-07-17): a new-session dir typed with the wrong case
passes the isdir check on macOS's case-insensitive filesystem and the session genuinely launches —
but the cwd STRING is load-bearing: the Claude CLI encodes its transcript/projects dir from its own
getcwd (true on-disk casing) while romp's discovery encodes the registry string, so the transcript
is never found and the launch looks silently dead. Two seams fix it:

1. create time — _resolve_create_dir canonicalizes casing via _true_case (exercised here);
2. first turn — the SDK init handler adopts the CLI-reported cwd when it differs (source-pinned
   here, in the repo's established wiring-pin style).
"""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cdc", os.path.join(ROOT, "bin", "romp-kernel"))


class TrueCase(unittest.TestCase):
    def test_wrong_case_component_is_corrected(self):
        # Pure listdir logic, so this corrects on case-sensitive filesystems too — no macOS gate.
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "MixedCase", "Inner")
            os.makedirs(real)
            typed = os.path.join(td, "mixedcase", "inner")
            self.assertEqual(km._true_case(typed), real)

    def test_correct_case_passes_through(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(td, "MixedCase")
            os.makedirs(real)
            self.assertEqual(km._true_case(real), real)

    def test_ambiguous_match_keeps_the_typed_component(self):
        # Two case-variant siblings (possible only on a case-sensitive filesystem): no unique
        # match, so the component stays as typed — never guess between real directories.
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "Case"), os.path.join(td, "CASE")
            os.makedirs(a)
            try:
                os.makedirs(b)
            except OSError:
                self.skipTest("case-insensitive filesystem cannot host the ambiguity")
            typed = os.path.join(td, "case")
            self.assertEqual(km._true_case(typed), typed)

    def test_missing_component_kept_as_given(self):
        with tempfile.TemporaryDirectory() as td:
            typed = os.path.join(td, "NoSuchDir")
            self.assertEqual(km._true_case(typed), typed)


class ResolveCreateDir(unittest.TestCase):
    def test_wrong_case_resolves_to_on_disk_casing(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(os.path.realpath(td), "ProjHome")
            os.makedirs(real)
            typed = os.path.join(td, "projhome")
            if not os.path.isdir(typed):   # case-sensitive filesystem: the typed path errors loudly
                _, err = km._resolve_create_dir(typed)
                self.assertIn("directory not found", err)
                return
            path, err = km._resolve_create_dir(typed)   # case-insensitive: silently launching a
            self.assertIsNone(err)                      # mismatched-string session is the bug
            self.assertEqual(path, real)

    def test_trailing_slash_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            real = os.path.join(os.path.realpath(td), "ProjHome")
            os.makedirs(real)
            path, err = km._resolve_create_dir(real + "/")
            self.assertIsNone(err)
            self.assertEqual(path, real)

    def test_missing_dir_still_errors_loudly(self):
        path, err = km._resolve_create_dir("/no/such/dir/anywhere")
        self.assertIsNone(path)
        self.assertIn("directory not found", err)


class InitCwdAdoptPins(unittest.TestCase):
    """The runtime heal for any residual variant (symlink text, a mount alias): the CLI's init
    message reports its own cwd — the authoritative string its transcript encoding is keyed on —
    and the SDK session adopts it into self.cwd + the registry the moment it differs."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "kernel", "sdk_backend.py")) as f:
            cls.src = f.read()

    def test_init_branch_adopts_the_cli_cwd(self):
        self.assertIn('cli_cwd = d.get("cwd")', self.src)
        self.assertIn("self.cwd = cli_cwd", self.src)
        self.assertIn("_update_reg(self.sid, cwd=cli_cwd)", self.src)

    def test_adoption_is_guarded_and_logged(self):
        self.assertIn("cli_cwd != self.cwd", self.src, "same string adopts nothing")
        self.assertIn("adopting CLI cwd", self.src, "the heal is loud in the backend log")


if __name__ == "__main__":
    unittest.main()
