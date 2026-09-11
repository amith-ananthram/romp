#!/usr/bin/env python3
"""T316 (the user 2026-09-10): the API-health LEDGER behind the dashboard's histograms. The event ring holds only the
windows' span, so every attempt is also folded into three tiers of fixed-width bins (one-minute bins for the last
hour, five-minute bins for 24 hours, hourly bins for 7 days), five counters per bin, bounded, persisted in
api-health.json with the state and restored at boot, served additively per bucket as `ledger`. Synthetic events,
hermetic state dirs."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "kernel"))
import sdk_backend as sb  # noqa: E402

AUTH, FAM = "key:helper", "fable"
KEY = AUTH + "|" + FAM


def _ah(d):
    return sb.ApiHealth(Path(d))


class Fold(unittest.TestCase):
    """api_health_ledger_add / api_health_ledger_view: pure over their arguments."""

    def test_an_event_lands_in_its_bin_of_every_tier_under_its_class(self):
        led = {}
        base = 1_700_000_100      # a start both 60 and 300 divide: the minute bins below sit inside one five-minute bin
        sb.api_health_ledger_add(led, KEY, base + 5, "ok", "ok")
        sb.api_health_ledger_add(led, KEY, base + 15, "retry", "429")
        sb.api_health_ledger_add(led, KEY, base + 25, "retry", "529")
        sb.api_health_ledger_add(led, KEY, base + 65, "retry", "5xx")
        sb.api_health_ledger_add(led, KEY, base + 75, "gaveup", "none")
        sb.api_health_ledger_add(led, KEY, base + 85, "retry", "other")
        m = led[KEY]["minute"]
        self.assertEqual(sorted(m), [base, base + 60])
        self.assertEqual(m[base], [1, 1, 1, 0, 0], "ok, 429, 529 in the first minute bin")
        self.assertEqual(m[base + 60], [0, 0, 1, 1, 1], "5xx, none, other in the next")
        f = led[KEY]["fiveMin"]
        self.assertEqual(list(f.values()), [[1, 1, 2, 1, 1]], "one five-minute bin holds them all")
        self.assertEqual(sb.api_health_ledger_class("ok", "ok"), 0)
        self.assertEqual(sb.api_health_ledger_class("retry", "429"), 1)
        self.assertEqual((sb.api_health_ledger_class("retry", "529"), sb.api_health_ledger_class("gaveup", "5xx")), (2, 2), "529 is a 5xx")
        self.assertEqual(sb.api_health_ledger_class("retry", "none"), 3)
        self.assertEqual(sb.api_health_ledger_class("retry", ""), 4)

    def test_each_tier_keeps_its_span_and_drops_older_bins(self):
        led = {}
        base = 1_700_000_100       # a minute-aligned start
        for i in range(70):        # 70 minutes of one event per minute
            sb.api_health_ledger_add(led, KEY, base + i * 60, "ok", "ok")
        self.assertEqual(len(led[KEY]["minute"]), 60, "the minute tier keeps the last hour")
        self.assertEqual(min(led[KEY]["minute"]), base + 10 * 60, "the oldest ten minutes fell out")
        self.assertEqual(len(led[KEY]["fiveMin"]), 14, "70 minutes are 14 five-minute bins, all inside the day")
        for h in range(200):       # 200 hours of one event per hour
            sb.api_health_ledger_add(led, KEY, base + h * 3600, "retry", "429")
        self.assertEqual(len(led[KEY]["hour"]), 168, "the hour tier keeps 7 days")
        self.assertLessEqual(len(led[KEY]["fiveMin"]), 288)
        self.assertLessEqual(sum(len(b) for b in led[KEY].values()), 60 + 288 + 168, "bounded: 516 bins at most per bucket")

    def test_the_view_is_dense_oldest_first_ending_at_now_with_zeros_where_nothing_landed(self):
        led = {}
        now = 1_700_003_750.0      # 50 s into its minute bin, so an event 30 s ago sits in the same bin
        sb.api_health_ledger_add(led, KEY, now - 30 - 600, "retry", "429")  # ten minutes back (events land in time order)
        sb.api_health_ledger_add(led, KEY, now - 30, "ok", "ok")            # this minute
        sb.api_health_ledger_add(led, KEY, now + 90, "ok", "ok")            # a clock that went back: a bin in the future
        v = sb.api_health_ledger_view(led[KEY], now)
        m = v["minute"]
        self.assertEqual((m["binS"], len(m["ok"])), (60, 60))
        self.assertEqual(m["from"], int(now // 60) * 60 - 59 * 60)
        self.assertEqual(m["ok"][-1], 1, "the newest bin holds the event 30 s ago")
        self.assertEqual(m["rateLimited"][-11], 1, "ten minutes back")
        self.assertEqual(sum(m["ok"]), 1, "the future bin is not drawn")
        self.assertEqual((len(v["fiveMin"]["ok"]), v["fiveMin"]["binS"]), (288, 300))
        self.assertEqual((len(v["hour"]["ok"]), v["hour"]["binS"]), (168, 3600))
        self.assertEqual(sum(v["hour"]["rateLimited"]), 1)
        for name in ("minute", "fiveMin", "hour"):
            self.assertEqual(set(v[name]) - {"binS", "from"}, set(sb.API_HEALTH_LEDGER_CLASSES))
        empty = sb.api_health_ledger_view(None, now)
        self.assertEqual(sum(empty["minute"]["ok"]), 0)
        # review find: a bin PAST the event being folded (stamped before a clock step back) is dropped with the stale ones, so
        # it never resurfaces as a phantom bar once the clock reaches it (and never reaches the state file from then on)
        sb.api_health_ledger_add(led, KEY, now - 10, "ok", "ok")            # the first event after the step
        self.assertNotIn(int((now + 90) // 60) * 60, led[KEY]["minute"], "the next add after the step dropped the future bin")
        self.assertIn(int((now - 30) // 60) * 60, led[KEY]["minute"], "the neighbouring bins stay")
        led2 = {}
        sb.api_health_ledger_add(led2, KEY, now + 7200, "retry", "429")     # stamped while the clock was two hours fast
        sb.api_health_ledger_add(led2, KEY, now, "ok", "ok")                # the first event after the step
        self.assertEqual(sum(sb.api_health_ledger_view(led2[KEY], now + 7200)["minute"]["rateLimited"]), 0, "two hours later: no phantom 429")
        self.assertEqual(sum(sb.api_health_ledger_view(led2[KEY], now + 7200)["hour"]["rateLimited"]), 0)

    def test_parse_skips_malformed_pieces_and_keeps_the_rest(self):
        raw = {KEY: {"minute": {"1700000000": [1, 0, 0, 0, 0], "x": [1], "1700000060": "nope", "1700000120": [1, 2]},
                     "bogus": {}, "hour": "no"},
               "": {"minute": {}}, "k2": []}
        led, bad = sb.api_health_ledger_parse(raw)
        self.assertEqual(sorted(led), [KEY])
        self.assertEqual(led[KEY]["minute"], {1700000000: [1, 0, 0, 0, 0], 1700000120: [1, 2, 0, 0, 0]}, "a short row is padded")
        self.assertEqual(bad, 6, "a bad start, a bad row, an unknown tier, a bad tier, an empty key, a non-dict bucket")
        self.assertEqual(sb.api_health_ledger_parse(None), ({}, 0))
        self.assertEqual(sb.api_health_ledger_parse([1]), ({}, 1))


class Aggregator(unittest.TestCase):
    """The ledger inside ApiHealth: fed by every event, in the payload, on disk, back at boot."""

    def test_events_fold_into_the_ledger_and_the_snapshot_serves_it_per_bucket(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        ah = _ah(td.name)
        now = 1_700_090_000.0
        # events land in time order, as they do in a running kernel
        ah.note_gaveup(now - 3 * 86400, auth=AUTH, family=FAM, status=None, category="connection", sid="s", turn=1)  # 3 days back: hour tier only
        ah.note_retry(now - 5 * 3600, auth=AUTH, family=FAM, status=529, sid="s", turn=1)  # 5 h back: inside the day, outside the hour
        ah.note_retry(now - 400, auth=AUTH, family=FAM, status=429, sid="s", turn=1)      # 6 min 40 s back: inside the hour
        ah.note_ok(now - 5, auth=AUTH, family=FAM, sid="s", message_id="m1")
        b = ah.snapshot(now)["buckets"][KEY]
        led = b["ledger"]
        self.assertEqual(sorted(led), ["fiveMin", "hour", "minute"])
        self.assertEqual((sum(led["minute"]["ok"]), sum(led["minute"]["rateLimited"]), sum(led["minute"]["serverErrors"])), (1, 1, 0),
                         "the hour: the response and the 429 six minutes back, not the 529 five hours back")
        self.assertEqual((sum(led["fiveMin"]["ok"]), sum(led["fiveMin"]["rateLimited"]), sum(led["fiveMin"]["serverErrors"])), (1, 1, 1), "the day: the 529 five hours back is in")
        self.assertEqual(sum(led["hour"]["noStatus"]), 1, "the week holds the connection failure three days back")
        self.assertEqual(sum(led["fiveMin"]["noStatus"]), 0)
        self.assertIn("series", b, "the older field stays: additive")

    def test_the_ledger_is_written_with_the_state_and_comes_back_at_boot(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        ah = _ah(td.name)
        now = 1_700_090_000.0
        ah.note_retry(now - 4000, auth=AUTH, family=FAM, status=429, sid="s", turn=1)
        ah.note_ok(now - 5, auth=AUTH, family=FAM, sid="s", message_id="m1")
        doc = json.loads((Path(td.name) / sb.API_HEALTH_STATE_FILE).read_text())
        self.assertIn("ledger", doc, "the minute's first event writes the file")
        self.assertEqual(doc["ledger"][KEY]["minute"][str(int((now - 5) // 60) * 60)], [1, 0, 0, 0, 0])
        again = _ah(td.name)
        v = again.snapshot(now)["buckets"][KEY]["ledger"]
        self.assertEqual((sum(v["fiveMin"]["ok"]), sum(v["fiveMin"]["rateLimited"])), (1, 1), "the day's picture survives a restart")
        self.assertEqual(again.window_errors(now), 0, "the ring itself is empty after a boot: the ledger is the memory, the ring the live signal")

    def test_a_malformed_ledger_on_disk_is_skipped_at_boot_and_the_rest_of_the_file_still_loads(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        p = Path(td.name) / sb.API_HEALTH_STATE_FILE
        p.write_text(json.dumps({"schema": sb.API_HEALTH_SCHEMA, "transitions": [], "buckets": {},
                                 "ledger": {KEY: {"minute": {"abc": [1]}, "fiveMin": {"1700000000": [2, 0, 0, 0, 0]}}}}))
        logs = []
        ah = sb.ApiHealth(Path(td.name), log=logs.append)
        self.assertTrue(any("malformed" in m for m in logs), logs)
        self.assertEqual(ah._ledger[KEY]["fiveMin"], {1700000000: [2, 0, 0, 0, 0]}, "the good bin is kept")
        self.assertEqual(ah._ledger[KEY]["minute"], {})

    def test_the_rollover_write_happens_at_most_once_a_minute(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        ah = _ah(td.name)
        writes = []
        real = ah._write_state_locked
        ah._write_state_locked = lambda: writes.append(1) or real()
        now = 1_700_090_000.0
        for i in range(30):
            ah.note_ok(now + i, auth=AUTH, family=FAM, sid="s", message_id="m%d" % i)   # thirty events inside one minute
        self.assertEqual(len(writes), 1)
        ah.note_ok(now + 61, auth=AUTH, family=FAM, sid="s", message_id="next")
        self.assertEqual(len(writes), 2, "the next minute's first event writes again")
        # review find: two threads whose stamps straddle the boundary can land out of order; the rollover is monotone, so
        # the earlier-stamped event landing after the later one does not write a third time (nor a fourth on the next)
        ah.note_ok(now + 59.9, auth=AUTH, family=FAM, sid="s", message_id="late")
        ah.note_ok(now + 62, auth=AUTH, family=FAM, sid="s", message_id="after")
        self.assertEqual(len(writes), 2, "one write per new minute, whatever the order events land in")

    def test_a_full_ledger_is_about_seventeen_kilobytes_in_the_file(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        ah = _ah(td.name)
        now = 1_700_090_000.0
        for h in reversed(range(168)):                          # one event in every hourly bin of the week (oldest first) ...
            ah.note_ok(now - h * 3600 - 1, auth=AUTH, family=FAM, sid="s", message_id="h%d" % h)
        for f in reversed(range(288)):                          # ... every five-minute bin of the day ...
            ah.note_retry(now - f * 300 - 2, auth=AUTH, family=FAM, status=429, sid="s", turn=1)
        for m in reversed(range(60)):                           # ... and every minute bin of the hour
            ah.note_retry(now - m * 60 - 3, auth=AUTH, family=FAM, status=529, sid="s", turn=1)
        ah._write_state_locked()
        with ah._lock:
            n = sum(len(b) for b in ah._ledger[KEY].values())
        self.assertLessEqual(n, 516)
        self.assertGreater(n, 500, "every bin populated")
        size = os.path.getsize(Path(td.name) / sb.API_HEALTH_STATE_FILE)
        self.assertLess(size, 20 * 1024, "about 17 KB per bucket at the bound: %d bytes" % size)
        self.assertGreater(size, 12 * 1024)


if __name__ == "__main__":
    unittest.main()
