#!/usr/bin/env python3
"""T338 and T340 (the user 2026-09-11): the API health histograms' x-axis carries the timeline pane's clock times, never
ages, by the timeline view's OWN formatter and tick rule lifted verbatim into the landing page (_timeline_axis_js); the
popup's legend names each class in its ink with no swatch, a waiting row shows its status code in its class ink with no
coloured square beside the name, and the no-connection/other band wears a hue of its own per theme. The behaviour of the
axis over real spans rides ui/webview/api-health-axis.test.ts; this module holds the kernel's side: the lift's text
equals the view's lines, its null on a missing view, the landing's order, and the served CSS. Synthetic fixtures only."""
import os
import pathlib
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from romp_load import load_source  # noqa: E402

ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs conftest's
# floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_apih_axis", os.path.join(BIN, "romp-kernel"))

VIEW = pathlib.Path(ROOT, "ui", "romp-timeline-view.js").read_text()
JS = km._LANDING_APIH_JS


class TimelineAxisLift(unittest.TestCase):
    def test_the_lift_is_the_views_own_three_lines_verbatim(self):
        js = km._timeline_axis_js()
        self.assertTrue(js.startswith("window.__rompTimelineAxis=(function(){const NICE = ["), js[:80])
        self.assertTrue(js.endswith("\nreturn {NICE:NICE,clock:clock,niceStep:niceStep};})();"))
        for pat in km._TIMELINE_AXIS_PARTS:
            line = re.search(pat, VIEW, re.M).group(0)
            self.assertIn(line, js, "the view's line, character for character: one formatter, no second copy")
        self.assertEqual(len(km._TIMELINE_AXIS_PARTS), 3)
        self.assertIn("function clock(t) { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, '0')", js, "the local HH:MM")
        self.assertIn("function niceStep(W) { for (const s of NICE) if (W / s <= 8) return s;", js, "the nice step: at most eight ticks")

    def test_a_missing_view_publishes_null_and_says_so(self):
        real = km.UI
        try:
            km.UI = pathlib.Path(tempfile.mkdtemp())
            self.assertEqual(km._timeline_axis_js(), "window.__rompTimelineAxis=null;")
        finally:
            km.UI = real
        # the popup then draws no clocks rather than a second formatter's guesses
        self.assertIn("function axisTicks(t0,span,W){if(!TL||!(span>0))return [];", JS)

    def test_the_landing_publishes_the_lift_before_the_script_that_reads_it(self):
        html = km._landing()
        lift = km._timeline_axis_js()
        i, j = html.find(lift), html.find("var TL=window.__rompTimelineAxis||null;")
        self.assertTrue(0 < i < j, "the lift's script precedes the popup's")
        self.assertIn("var step=TL.niceStep(span)", JS, "the timeline's tick rule")
        self.assertIn("TL.clock(tk)", JS, "the timeline's formatter")
        self.assertNotIn("tickWords", JS, "the age words are gone")
        self.assertNotIn('">now</span>', JS)
        # the date on a day change, in the State changes rows' own form; the relative forms stay where they belong
        self.assertIn("var label=(crosses&&(dk!==prevDay||step>=86400))?dateWords(tk):TL.clock(tk);", JS)
        self.assertIn("return dateWords(ep)+' '+hm(ep);}", JS)
        self.assertIn("function ageWords(){return LANDED&&MERGE?'read '+MERGE.agoWords((Date.now()-LANDED)/1000):'';}", JS)
        self.assertIn("(r.since?' · since '+hm(r.since):'')", JS)


class LegendRowsAndBand(unittest.TestCase):
    def test_the_legend_names_each_class_in_its_ink_with_no_swatch(self):
        self.assertIn("var LEGEND_ROWS=[['r429','429','rate limit: the API told us to slow down'],['r5xx','5xx','server error: the API itself failed'],"
                      "['none','other','no connection, or another error']];", JS)
        self.assertIn("h+='<div class=ah-lrow><span class=\"ah-lt ah-c-'+r[0]+'\">'+r[1]+'</span> <span>'+r[2]+'</span></div>';", JS)
        self.assertNotIn("ah-lsw", JS)
        self.assertNotIn("ah-sw", JS, "no coloured square beside a waiting session's name either")

    def test_a_waiting_row_paints_its_status_code_in_its_class_ink(self):
        self.assertIn("if(r.cls==='429')return '<span class=ah-c-r429>429</span> rate limited';if(r.cls==='529')return '<span class=ah-c-r5xx>529</span> overloaded';", JS)
        self.assertIn("if(/^5[0-9][0-9]$/.test(st))return 'error <span class=ah-c-r5xx>'+st+'</span>';return 'error'+(st?' '+st:'');}", JS)
        self.assertIn("var st=r.status?esc(r.status):'';", JS, "the status is escaped before it is painted")
        self.assertIn("+(bg?'<span class=ah-nm style=\"color:'+bg+'\">':'<span class=ah-nm>')+esc(r.name)+'</span>'", JS)

    def test_the_other_bands_hue_per_theme_and_the_inks_that_follow_it(self):
        html = km._landing()
        for rule in (".ah-c-none{color:#d9f99d}", ".ah-seg-noStatus,.ah-seg-other{fill:#d9f99d}", ".ah-lt{font-weight:600}",
                     "body.theme-light .ah-c-none{color:#4f46e5}", "body.theme-light .ah-seg-noStatus,body.theme-light .ah-seg-other{fill:#4f46e5}"):
            self.assertIn(rule, html, rule)
        for gone in (".ah-sw{", ".ah-lsw{", ".ah-sw-r429{", ".ah-sw-r5xx{", ".ah-sw-none{", "body.theme-light .ah-sw-"):
            self.assertNotIn(gone, html, gone)
        # the reserved statuses keep their meanings: the retrying amber and the working yellow are not the band's hue
        self.assertNotIn("#e67e22", html.split(".ah-seg-noStatus")[1][:60])
        self.assertNotIn("#e0b020", html.split(".ah-seg-noStatus")[1][:60])


if __name__ == "__main__":
    unittest.main()
