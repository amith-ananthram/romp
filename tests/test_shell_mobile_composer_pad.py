#!/usr/bin/env python3
"""The composer buttons no longer couple to the mobile shell's padding (2026-07-30).

From 2026-07-29 to 2026-07-30 the Send and Attach buttons were absolutely positioned inside #composer,
with `right` offsets measured from its padding edge — and the kernel's mobile CSS narrows that padding
from 24px to 10px, so styles.css carried a second offset set under a matching media query. Two files had
to move in lockstep for the buttons to land inside the box at all.

The Signal-style compose row dissolved that: the buttons are in-flow flex items now, so the kernel's
padding override is just padding again. This pins the DISSOLUTION — the kernel side still narrows the
padding for phones, and no absolutely-positioned button offsets may sneak back into styles.css, because
nothing would keep them aligned with the kernel's padding this time either.
"""
import os
import re
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_mcomp", os.path.join(BIN, "romp-kernel"))
STYLES = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "styles.css")).read()


class MobileComposerPadding(unittest.TestCase):
    def test_the_mobile_shell_still_narrows_the_composer_padding_to_10px(self):
        self.assertIn("#composer{padding:8px 10px 6px}", km._CHAT_MOBILE_CSS)

    def test_it_is_scoped_to_coarse_pointers_up_to_1024px(self):
        self.assertIn("@media (pointer:coarse) and (max-width:1024px)", km._CHAT_MOBILE_CSS)

    def test_no_absolute_button_offsets_may_return(self):
        """In-flow flex items need no offsets; an offset would re-create the two-file coupling."""
        self.assertIsNone(re.search(r"#composer-(send|attach)[^{]*\{[^}]*\bright:", STYLES))
        self.assertIsNone(re.search(r"#composer-(send|attach)[^{]*\{[^}]*position: absolute", STYLES))

    def test_the_buttons_are_in_flow_flex_rounded_squares(self):
        self.assertIn(
            "#composer-attach, #composer-send, .cmt-attach, .cmt-send {\n"
            "  flex: 0 0 auto; width: calc(1.4 * var(--fs) + 18px); "
            "height: calc(1.4 * var(--fs) + 18px); border-radius: 10px;", STYLES)   # the comment popover shares the rule (2026-08-17)


if __name__ == "__main__":
    unittest.main()
