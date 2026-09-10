"""The GLOBAL colormap (the user 2026-06-26) now colors the CONTEXT-window % bars too, not just the feed
recency tint + the usage "used" bar. The kernel computes the color SERVER-SIDE (ctxColor=[r,g,b]) where it
builds each payload — the timeline lanes (build_timeline) and the chat status (build_session) — so the three
client surfaces (timeline battery, chat statusline battery, chat tab-tooltip battery) just apply it, exactly
like the usage bar. The gear's colormap label is global now ("Colormap", not "Feed colormap").
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


class ContextColormap(unittest.TestCase):
    def test_build_timeline_colors_the_lane_context_bar_server_side(self):
        src = inspect.getsource(km.build_timeline)
        self.assertIn("ctx_stops = cm.stops_for(_colormap())", src, "the global colormap still drives the compaction sweep")
        self.assertIn('"ctxColor"', src, "each lane carries a server-computed context color")
        self.assertIn('cm.ramp((tm["context"] or 0) / 100.0, ctx_stops)', src,
                      "classic ctxColor stays the recency-colormap sample, byte-identical to main (PR #763 item 1)")
        self.assertIn("cm.context_rgb(tm[\"context\"] or 0)", src,
                      "the yatharth tone (ctxTone) rides beside the classic color")

    def test_build_session_status_carries_a_context_color(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"ctxColor"', src, "the chat status carries a server-computed context color")
        self.assertIn('cm.ramp(tm["context"] / 100.0, cm.stops_for(_colormap()))', src,
                      "classic ctxColor stays the recency-colormap sample, byte-identical to main (PR #763 item 1)")
        self.assertIn("cm.context_rgb(tm[\"context\"])", src,
                      "the yatharth tone (ctxTone) rides beside the classic color")

    def test_context_tone_thresholds_are_the_one_pair(self):
        # ONE warn/danger pair for every gauge (was 60/85 on ctx gauges, 70/90 on usage bars)
        self.assertEqual((km.cm.CTX_WARN, km.cm.CTX_DANGER), (70, 88))
        self.assertEqual(tuple(km.cm.context_rgb(km.cm.CTX_DANGER)), km.cm.DANGER_RGB)
        self.assertEqual(tuple(km.cm.context_rgb(km.cm.CTX_WARN)), km.cm.WARN_RGB)
        self.assertEqual(tuple(km.cm.context_rgb(km.cm.CTX_WARN - 1)), km.cm.tone_rgb("context", (km.cm.CTX_WARN - 1) / 100.0))

    def test_build_timeline_ships_a_compaction_sweep_gradient(self):
        # the timeline scan-bar has no client-side colormap, so build_timeline samples the SAME map at the
        # sweep's scaleX stops (widest→narrowest) and ships cmapGrad; the client sets --cmpN vars from it so
        # the compaction bar mirrors the context battery fill as it compresses (the user 2026-07-02).
        src = inspect.getsource(km.build_timeline)
        self.assertIn('"cmapGrad": cmap_grad', src, "the timeline payload carries the compaction gradient")
        self.assertIn("cm.ramp(v, ctx_stops)", src, "sampled on the global colormap")
        self.assertIn("for v in (0.12, 0.34, 0.56, 0.78, 1.0)", src,
                      "5 stops matching the client's applyCompactSweep positions")

    def test_context_color_is_none_when_there_is_no_context_yet(self):
        # both payloads guard on context is not None so a dormant/never-reported lane sends no color
        self.assertIn('if tm and tm["context"] is not None else None', inspect.getsource(km.build_timeline))
        self.assertIn('if tm["context"] is not None else None', inspect.getsource(km.build_session))

    def test_the_gear_colormap_label_is_global_not_feed_only(self):
        # _GEAR_HTML became the _gear_html() builder when the settings modal went dynamic
        self.assertIn(">Colormap<", _gear_src(), "the label is global now")
        self.assertNotIn(">Feed colormap<", _gear_src(), "no longer scoped to the feed")

    def test_ramp_maps_higher_to_the_bright_end_on_a_darklight_map(self):
        # ramp(v) walks v=0→stops[0] to v=1→stops[-1]; on a DARK→LIGHT map (hawaii) a higher fill lands
        # brighter. (The default 'aurora' is intentionally ISO-LUMINANT — it conveys value by hue, not
        # brightness — so this brightness check is pinned on hawaii, not cm.DEFAULT.)
        stops = km.cm.stops_for("hawaii")
        lo, hi = km.cm.ramp(0.1, stops), km.cm.ramp(0.95, stops)
        self.assertGreater(sum(hi), sum(lo), "fuller context → brighter color on a dark→light map")


if __name__ == "__main__":
    unittest.main()


# The gear moved from kernel-inline strings into the shared feed bundle
# (2026-07-13): ui/webview/gear.js is the single source both hosts render, so
# the gear pins read THAT file (and feed.css for its styling).
def _gear_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.js").read_text()


def _gear_css_src():
    import pathlib
    return (pathlib.Path(__file__).resolve().parent.parent / "ui" / "webview" / "gear.css").read_text()
