#!/usr/bin/env python3
"""The token login page is the screen a signed-out user actually lands on, so it has to be
self-service. After a reinstall (the state dir goes, the next kernel mints a new token) an
existing tab or bookmark silently drops to this page — and read as "the dashboard never
loaded" rather than "you are signed out" (the user 2026-07-27, on a real reinstall).

It must therefore say what happened, and give a command that works from the state the reader
is actually in: a terminal opened BEFORE the install has a stale PATH, so `romp url` alone is
not enough advice — the raw token file has to be there too.

Synthetic only: asserts on the static HTML constant, starts no server.
"""
import os
import re
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_loginpage", os.path.join(BIN, "romp-kernel"))

HTML = km._TOKEN_LOGIN_HTML


class TokenLoginPage(unittest.TestCase):
    def test_explains_that_a_reinstall_signs_you_out(self):
        """The misreading to prevent is "it's broken". Name the cause and say it isn't."""
        low = HTML.lower()
        self.assertIn("reinstall", low)
        self.assertIn("signed out", low)
        self.assertIn("not broken", low)

    def test_names_the_exact_command_not_just_the_binary(self):
        """`romp` opens the dashboard; `romp url` PRINTS the link. A signed-out reader needs
        the link, and on a headless/remote box `romp` alone has nothing to open."""
        self.assertIn("romp url", HTML)

    def test_covers_the_stale_PATH_case_the_command_alone_does_not(self):
        """A terminal opened before the install cannot run `romp` at all. Without the file
        fallback the advice dead-ends exactly when it is most needed."""
        self.assertIn("serve-token", HTML)
        self.assertIn("PATH", HTML)

    def test_is_self_contained_and_leaks_nothing(self):
        """Every other route is token-gated, so this page is served to an UNAUTHENTICATED
        caller. It must pull in no external asset and must never embed the token itself."""
        self.assertNotIn("/dist/", HTML)
        self.assertNotIn("http://", HTML)
        self.assertNotIn("https://", HTML)
        # No interpolation holes that a token could ever be rendered into.
        self.assertNotIn("%s", HTML)
        self.assertNotIn("{}", HTML)

    def test_posts_nothing_and_keeps_the_token_out_of_history(self):
        """The form redirects client-side to /?token=…; it must not POST anywhere."""
        self.assertNotIn("method=", HTML.lower())
        self.assertIn("location.replace", HTML)   # replace(), so the token is not left in
        #                                           the back-stack of the signed-out page

    def test_encodes_the_pasted_token(self):
        """A pasted token with URL-significant characters must survive the redirect."""
        self.assertIn("encodeURIComponent", HTML)

    def test_renders_as_one_html_document(self):
        self.assertTrue(HTML.lstrip().lower().startswith("<!doctype html>"))
        # Balanced enough to render: the form the reader types into is actually closed.
        self.assertEqual(HTML.count("<form"), 1)
        self.assertEqual(HTML.count("</form>"), 1)
        # No stray backslash-continuations left dangling inside an attribute value.
        self.assertNotIn("\\\n", HTML)

    def test_uses_the_accent_colour_the_repo_standardised_on(self):
        """Accent chrome is --accent #9cd2ff on --accent-fg #0c1a2e."""
        self.assertIn("#9cd2ff", HTML)
        self.assertIn("#0c1a2e", HTML)


if __name__ == "__main__":
    unittest.main()
