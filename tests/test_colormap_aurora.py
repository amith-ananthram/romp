"""The 'aurora' colormap (the user 2026-06-27): romp's brand hues — purple → blue → teal → green — swept at
CONSTANT (perceptual) lightness. Added to the canonical romp_colormap.COLORMAPS and to the gear picker's
options. This test verifies it exists, ramps, and actually holds its lightness (OKLab L spread is tiny)."""
import math
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
cm = load_source("romp_colormap", os.path.join(BIN, "romp_colormap.py"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


def _oklab_L(r, g, b):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s


class Aurora(unittest.TestCase):
    def test_registered_default_and_first(self):
        self.assertIn("aurora", cm.COLORMAPS)
        self.assertEqual(cm.DEFAULT, "aurora", "aurora is the default colormap (the user 2026-06-27)")
        self.assertEqual(list(cm.COLORMAPS)[0], "aurora", "and listed first so it leads the picker")
        stops = cm.stops_for("aurora")
        self.assertEqual(len(stops), 9)
        self.assertEqual(cm.ramp(0.0, stops), stops[0])     # v=0 → purple end
        self.assertEqual(cm.ramp(1.0, stops), stops[-1])    # v=1 → green end

    def test_ends_are_the_true_romp_green_and_purple(self):
        stops = cm.stops_for("aurora")
        # reversed (the user 2026-06-27): green end first, purple end last. Per-anchor chroma is preserved,
        # so the ends faithfully ARE the romp shades.
        self.assertEqual(stops[0], (84, 178, 4), "green end (first) is the romp green #54B204")
        rN, gN, bN = stops[-1]
        self.assertGreater(bN, gN, "purple end (last): blue channel above green")
        self.assertGreater(bN, rN, "purple end: blue-leaning (romp #9088F0)")

    def test_lightness_is_constant(self):
        Ls = [_oklab_L(*s) for s in cm.stops_for("aurora")]
        self.assertLess(max(Ls) - min(Ls), 0.02, "OKLab lightness stays ~constant across the ramp (%r)" % Ls)

    def test_selectable_first_in_the_gear_picker(self):
        # the gear picker lives in the feed BUNDLE now (ui/webview/gear.js, 2026-07-13)
        import pathlib
        gear = (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()
        self.assertIn("CMAPS = { aurora: [[84, 178, 4]", gear, "the gear picker lists aurora FIRST as an option")

    def test_set_colormap_accepts_aurora(self):
        try:
            km._set_colormap("aurora")
            self.assertEqual(km._colormap(), "aurora")
        finally:
            km._set_colormap("hawaii")


if __name__ == "__main__":
    unittest.main()
