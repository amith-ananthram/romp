"""Model-name + effort text tint (the user 2026-07-02): the model name and effort in the chat statusline and
on the timeline lanes are colored on the SAME global colormap as the context bars, by capability / effort
RANK (brighter = more capable / higher effort). The kernel computes the RGB (modelColor / effortColor,
mirroring ctxColor); the clients just apply it. Ranks live only in bin/romp-kernel.
"""
import inspect
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
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))


class ModelColors(unittest.TestCase):
    def setUp(self):
        # a DARK→LIGHT map so higher rank == brighter is testable (the default 'aurora' is iso-luminant)
        self.stops = km.cm.stops_for("hawaii")

    def _lum(self, rgb):
        return sum(rgb)  # good enough to order brightness on a dark→light ramp

    def test_model_rank_order_fable_opus_sonnet_haiku(self):
        # capability high→low maps to bright→dark on a dark→light map
        fable = km._model_color("claude-fable-5", self.stops)
        opus = km._model_color("claude-opus-4-8", self.stops)
        sonnet = km._model_color("sonnet", self.stops)
        haiku = km._model_color("Haiku 4.5", self.stops)
        for c in (fable, opus, sonnet, haiku):
            self.assertEqual(len(c), 3)
        self.assertGreater(self._lum(fable), self._lum(opus))
        self.assertGreater(self._lum(opus), self._lum(sonnet))
        self.assertGreater(self._lum(sonnet), self._lum(haiku))

    def test_model_matched_by_family_word_any_form(self):
        # "opus" / "claude-opus-4-8" / "Opus 4.8" all resolve to the same rank color
        a = km._model_color("opus", self.stops)
        b = km._model_color("claude-opus-4-8", self.stops)
        c = km._model_color("Opus 4.8", self.stops)
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_unknown_or_empty_model_is_none(self):
        self.assertIsNone(km._model_color("gpt-4o", self.stops))
        self.assertIsNone(km._model_color("", self.stops))
        self.assertIsNone(km._model_color(None, self.stops))

    def test_effort_rank_low_to_max(self):
        lo = km._effort_color("low", self.stops)
        mx = km._effort_color("max", self.stops)
        self.assertGreater(self._lum(mx), self._lum(lo))
        # ordered low < medium < high < xhigh < max
        vals = [self._lum(km._effort_color(e, self.stops)) for e in ("low", "medium", "high", "xhigh", "max", "ultracode")]
        self.assertEqual(vals, sorted(vals))

    def test_effort_case_insensitive_unknown_is_none(self):
        self.assertEqual(km._effort_color("HIGH", self.stops), km._effort_color("high", self.stops))
        self.assertIsNone(km._effort_color("turbo", self.stops))
        self.assertIsNone(km._effort_color("", self.stops))

    def test_classic_colors_use_the_full_colormap_ends_and_tones_ride_beside(self):
        # DUAL palette (PR #763 review item 1): the CLASSIC colors are the recency-colormap sample,
        # byte-identical to what main always shipped — the default theme's colors are the owner's
        # call. The single-hue TONES (orange model / violet effort) ship in parallel fields for the
        # yatharth themes; the client picks.
        self.assertEqual(km._model_color("fable", self.stops), list(km.cm.ramp(1.0, self.stops)))
        self.assertEqual(km._model_color("haiku", self.stops), list(km.cm.ramp(0.0, self.stops)))
        self.assertEqual(km._effort_color("ultracode", self.stops), list(km.cm.ramp(1.0, self.stops)))
        self.assertEqual(km._effort_color("low", self.stops), list(km.cm.ramp(0.0, self.stops)))
        self.assertEqual(km._model_tone("fable"), list(km.cm.tone_rgb("model", 1.0)))
        self.assertEqual(km._model_tone("haiku"), list(km.cm.tone_rgb("model", 0.0)))
        self.assertEqual(km._effort_tone("ultracode"), list(km.cm.tone_rgb("effort", 1.0)))
        self.assertEqual(km._effort_tone("low"), list(km.cm.tone_rgb("effort", 0.0)))
        self.assertIsNone(km._model_tone("mystery"))
        self.assertIsNone(km._effort_tone("turbo"))

    def test_tone_families_are_distinct_and_legible_on_dark(self):
        # the three quantities may never collide again, and every sampled value must clear ~5:1
        # on the #1e1e1e page (the WCAG floor is 4.5; the ramps are tuned above it)
        def lum(rgb):
            def ch(c):
                c = c / 255.0
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])
        dark_l = lum((30, 30, 30))
        for fam in ("model", "effort", "context"):
            for v in (0.0, 0.5, 1.0):
                c = km.cm.tone_rgb(fam, v)
                ratio = (lum(c) + 0.05) / (dark_l + 0.05)
                self.assertGreaterEqual(ratio, 4.5, "%s@%s %s" % (fam, v, c))
        self.assertNotEqual(km.cm.tone_rgb("model", 1.0), km.cm.tone_rgb("effort", 1.0))
        self.assertNotEqual(km.cm.tone_rgb("model", 0.0), km.cm.tone_rgb("effort", 0.0))

    def test_build_session_status_carries_model_and_effort_colors(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"modelColor": _model_color(tm["model"], cm.stops_for(_colormap()))', src)
        self.assertIn('"effortColor": _effort_color(tm["effort"], cm.stops_for(_colormap()))', src)

    def test_build_timeline_lane_carries_model_and_effort_colors(self):
        src = inspect.getsource(km.build_timeline)
        self.assertIn('"modelColor": _model_color(tm["model"] if tm else "", ctx_stops)', src)
        self.assertIn('"effortColor": _effort_color(tm["effort"] if tm else "", ctx_stops)', src)


if __name__ == "__main__":
    unittest.main()
