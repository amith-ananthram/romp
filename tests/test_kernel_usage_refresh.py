#!/usr/bin/env python3
"""Click-to-refresh on the rail usage bars (the user 2026-06-30).

The rail's /usage rate-limit bars already re-read each poll, but the user wanted a CLICK to force the same
check on demand. The mechanism is unchanged — _usage() re-reads usage.json (the snapshot Claude Code's
statusline writes) — exposed as a GET /usage endpoint the rail widget fetches on click. Synthetic fixtures.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_usage", os.path.join(BIN, "romp-kernel"))
jd = km.jd


class UsageReadMechanism(unittest.TestCase):
    """The refresh reuses _usage() — the exact check the push already uses (re-reading usage.json)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        self.td.cleanup()

    def test_reads_the_statusline_snapshot_into_five_hour_and_weekly_segments(self):
        (jd.STATE / "usage.json").write_text(json.dumps({
            "t": 1782868545,
            "five_hour": {"pct": 10, "resets_at": 1782787200},
            "seven_day": {"pct": 11, "resets_at": 1783364400},
        }))
        u = km._usage()
        self.assertIsNotNone(u, "a present usage.json yields bars")
        self.assertEqual(u["fiveHour"]["pct"], 10)
        self.assertEqual(u["fiveHour"]["resetsAt"], 1782787200)
        self.assertEqual(u["sevenDay"]["pct"], 11)
        self.assertIn("color", u["fiveHour"], "the used bar carries the server-computed colormap")

    def test_missing_or_empty_usage_is_none_not_an_error(self):
        self.assertIsNone(km._usage(), "no usage.json → None (the widget hides)")
        (jd.STATE / "usage.json").write_text(json.dumps({"t": 1, "five_hour": None, "seven_day": None}))
        self.assertIsNone(km._usage(), "both segments absent → None")


class UsageRefreshWiring(unittest.TestCase):
    """Source-level pins: the GET /usage route + the clickable rail widget that fetches it."""

    def test_get_usage_route_returns_the_usage_read(self):
        src = Path(os.path.join(BIN, "romp-kernel")).read_text()
        self.assertIn('if p == "/usage":', src, "a GET /usage endpoint exists")
        self.assertIn("json.dumps(_usage() or {})", src, "it serves the SAME _usage() read the push uses")

    def test_rail_usage_widget_is_click_to_refresh(self):
        html = km._landing()
        # the widget is a clickable affordance, but it no longer ADVERTISES the click: refresh is
        # automatic (the 60s pull + the timeline's live forward), and a click-me line misread on a
        # hover surface (the user 2026-08-14) — no hint anywhere, and never a native title
        # (the user 2026-08-08: the browser's flat box fought the tip)
        self.assertIn("id=rail-usage", html)
        self.assertIn("el.style.cursor='pointer'", html)
        self.assertNotIn("click to refresh", html)
        self.assertNotIn("click the bars", html)   # tightened 2026-08-08 — the reader is already on the bars
        self.assertNotIn("Click to refresh usage", html)
        # an OPEN hover tip follows every data landing instead of asking for the click
        self.assertIn("if(tip.style.display==='block'&&!tip.classList.contains('ru-modal'))", html)
        # a click fetches the on-demand endpoint and re-renders through the normal render() path.
        # (2026-07-30: /usage/fleet — /usage plus one row per OTHER Claude account signed in across the
        # fleet. It collapses to the single local row whenever every machine is on the same login.)
        self.assertIn("fetch('/usage/fleet'", html)
        self.assertIn("el.addEventListener('click'", html)
        # instant acknowledgement BEFORE the round-trip (dim), then restored — per the button rules
        self.assertIn("el.style.opacity='0.45'", html)
        # re-entrancy guard so a double-click doesn't stack fetches
        self.assertIn("_ruBusy", html)

    def test_rail_usage_has_a_backup_auto_refresh_timer(self):
        # a 60s timer re-reads /usage silently so the bars stay fresh even when the timeline iframe isn't
        # forwarding usage (idle / Timeline pane off) — the gap that made them look stale until a click.
        html = km._landing()
        self.assertIn("setInterval(function(){pull(false);},60000)", html)
        self.assertIn("function pull(ack)", html, "click + timer share one refresh path")
        self.assertIn("pull(false);", html)   # also pulled once on load, independent of the timeline forward


if __name__ == "__main__":
    unittest.main()
