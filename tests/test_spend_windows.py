#!/usr/bin/env python3
"""T235 (the user's spend hover, 2026-09-03): the "1 month" row read LOWER than "1 week" — because
hour/day/week were ROLLING sums while month was CALENDAR month-to-date, three days into September.
A label beside "1 hour / 1 day / 1 week" promises a rolling window, and a superset window can never
read lower. Now `month` is a rolling 30 local days over the days ledger, and the bill-tracking figure
lives under its own honest key, `monthToDate`, which carries the month budget. A ledger younger than
30 days marks the rolling window with its `since` date rather than reading as a silently short window.
Frozen clock, synthetic ledger, local-time bucket keys built the recorder's own way."""
import json
import os
import tempfile
import time
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_spendwin", os.path.join(BIN, "romp-kernel"))

# the 3rd of a month, midday local — the exact shape of the user's screenshot
FROZEN = time.mktime((2026, 9, 3, 12, 0, 0, 0, 0, -1))


def _day(n):
    return time.strftime("%Y-%m-%d", time.localtime(FROZEN - n * 86400))


def _hour(n):
    return time.strftime("%Y-%m-%dT%H", time.localtime(FROZEN - n * 3600))


class SpendWindows(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.jd.STATE, km.time)
        km.jd.STATE = Path(self.td.name)
        # freeze the module's clock: time() answers FROZEN; every formatter stays the real one, fed
        # explicit struct_times by the implementation (a bare strftime() would read the wall clock)
        frozen = types.SimpleNamespace(time=lambda: FROZEN, strftime=time.strftime, localtime=time.localtime,
                                       gmtime=time.gmtime, mktime=time.mktime, sleep=time.sleep,
                                       monotonic=time.monotonic, time_ns=time.time_ns)
        km.time = frozen

    def tearDown(self):
        km.jd.STATE, km.time = self._saved
        self.td.cleanup()

    def _ledger(self, days=None, hours=None, budgets=None):
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": days or {}, "hours": hours or {}}))
        if budgets is not None:
            (km.jd.STATE / "spend-budgets.json").write_text(json.dumps(budgets))

    def _bucket(self, usd, turns=1, tok=10):
        return {"usd": usd, "turns": turns, "tokIn": tok}

    def test_windows_are_monotone_hour_day_week_month(self):
        # 40 days of $24/day (one $1 turn per hour for the last 7 days mirrors the days ledger)
        days = {_day(n): self._bucket(24.0, turns=24, tok=240) for n in range(40)}
        hours = {_hour(n): self._bucket(1.0) for n in range(7 * 24 + 1)}
        self._ledger(days, hours)
        w = km._spend_windows()
        for metric in ("usd", "tok", "turns"):
            chain = [w[k][metric] for k in ("hour", "day", "week", "month")]
            self.assertEqual(chain, sorted(chain), "%s: hour ≤ day ≤ week ≤ month — the invariant the screenshot broke: %r" % (metric, chain))
        self.assertGreater(w["month"]["usd"], w["week"]["usd"], "a rolling month is a strict superset of a week here")

    def test_month_to_date_is_the_calendar_sum_and_carries_the_budget(self):
        days = {_day(n): self._bucket(10.0) for n in range(40)}   # Sep 1,2,3 = 3 buckets in the calendar month
        self._ledger(days, {}, budgets={"month": 150})
        w = km._spend_windows()
        self.assertEqual(w["monthToDate"]["usd"], 30.0, "Sep 1–3 only — what the bill accrues")
        self.assertEqual(w["monthToDate"]["budget"], 150.0, "the bill-cycle budget rides the bill figure")
        self.assertNotIn("budget", w["month"], "…never the rolling window — a cycle cap is not a 30-day cap")

    def test_rolling_month_spans_exactly_the_last_30_local_days_across_a_boundary(self):
        days = {_day(30): self._bucket(100.0), _day(29): self._bucket(1.0), _day(0): self._bucket(1.0),
                _day(31): self._bucket(1000.0)}
        self._ledger(days, {})
        w = km._spend_windows()
        self.assertEqual(w["month"]["usd"], 102.0, "today back through 30 days ago inclusive (31 local dates) — 31 days ago is out")

    def test_a_ledger_younger_than_30_days_marks_its_since_date(self):
        days = {_day(n): self._bucket(1.0) for n in range(5)}
        self._ledger(days, {})
        w = km._spend_windows()
        self.assertEqual(w["month"]["since"], _day(4), "a short ledger says how far back it truly reaches")
        self._ledger({_day(n): self._bucket(1.0) for n in range(35)}, {})
        self.assertNotIn("since", km._spend_windows()["month"], "a full window carries no caveat")

    def test_empty_ledger_stays_honest_zero_everywhere(self):
        self._ledger({}, {})
        w = km._spend_windows()
        zero = {"usd": 0.0, "tok": 0, "turns": 0, "tokIn": 0, "tokOut": 0, "tokCacheR": 0, "tokCacheW": 0}
        self.assertEqual(w["month"], zero)
        self.assertEqual(w["monthToDate"], zero)

    def test_windows_carry_the_by_kind_split_of_their_tokens(self):
        # the user 2026-09-06 read a day's token count and could not see how it was possible: the count
        # is every kind together, and cache reads (each API call of a turn re-reading the whole context)
        # are most of it at a tenth of the price — so each window says how its count divides
        b = {"usd": 1.0, "turns": 1, "tokIn": 3, "tokOut": 4, "tokCacheR": 500, "tokCacheW": 60}
        self._ledger({_day(0): b}, {_hour(0): b, _hour(1): b})
        w = km._spend_windows()
        self.assertEqual(w["day"]["tok"], 2 * 567)
        self.assertEqual((w["day"]["tokIn"], w["day"]["tokOut"], w["day"]["tokCacheR"], w["day"]["tokCacheW"]), (6, 8, 1000, 120))
        self.assertEqual(sum(w["day"][k] for k in ("tokIn", "tokOut", "tokCacheR", "tokCacheW")), w["day"]["tok"],
                         "the split accounts for the whole count")
        self.assertEqual(w["month"]["tokCacheR"], 500, "the days ledger splits the same way")

    def test_a_keyed_window_splits_only_when_every_bucket_carries_the_split(self):
        # keyed sub-counts written before 2026-09-06 hold `tok` alone; a window folding one of those in
        # shows NO split rather than a partial one that would read as whole. The bucket totals always
        # carried the kinds, so the total-mode windows on the same ledger split regardless.
        split = {"usd": 1.0, "turns": 1, "tok": 567, "tokIn": 3, "tokOut": 4, "tokCacheR": 500, "tokCacheW": 60}
        new = {"usd": 1.0, "turns": 1, "tokIn": 3, "tokOut": 4, "tokCacheR": 500, "tokCacheW": 60, "key": dict(split)}
        legacy = {"usd": 1.0, "turns": 1, "tokIn": 3, "tokOut": 4, "tokCacheR": 500, "tokCacheW": 60,
                  "key": {"usd": 1.0, "turns": 1, "tok": 567}}
        self._ledger({}, {_hour(0): new, _hour(30): legacy})
        k = km._spend_windows(keyed_only=True)
        self.assertEqual((k["day"]["tok"], k["day"]["tokCacheR"]), (567, 500), "a window of split buckets splits")
        self.assertEqual(k["week"]["tok"], 1134)
        self.assertNotIn("tokCacheR", k["week"], "…one legacy bucket in the window and it stays unsplit")
        t = km._spend_windows()
        self.assertEqual((t["week"]["tok"], t["week"]["tokCacheR"]), (1134, 1000), "the totals split either way")



class RollingMonthAcrossDst(SpendWindows):
    """T235b (romp_ui's adversarial pass): `now - i*86400` skips a LOCAL date across a DST shift —
    every 00:00-01:00 reading in the weeks after spring-forward omitted the transition date, so the
    30-day set held 29 dates and month could read below week again in that hour. Date arithmetic
    keeps the recorder's local-date keying without the skip."""

    def _with_tz(self, tz, fn):
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        time.tzset()
        try:
            return fn()
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_the_window_holds_31_consecutive_local_dates_inside_a_dst_hour(self):
        def run():
            frozen = time.mktime((2026, 3, 9, 0, 30, 0, 0, 0, -1))   # 00:30 the day after spring-forward
            km.time.time = lambda: frozen
            # $1 on each of the 31 dates the window should hold (today back through 30 days ago),
            # $1000 on the dates just beyond — a skipped transition date shifts the set one day OLDER,
            # so the seconds-arithmetic bug reads 30 + 1000, never 31 (built by DATE, not seconds)
            import datetime as _dt
            today = _dt.date.fromtimestamp(frozen)
            days = {(today - _dt.timedelta(days=n)).isoformat(): self._bucket(1.0 if n <= 30 else 1000.0)
                    for n in range(40)}
            self._ledger(days, {})
            w = km._spend_windows()
            return w["month"]["usd"], "2026-03-08" in days
        usd, has_dst_date = self._with_tz("America/Los_Angeles", run)
        self.assertTrue(has_dst_date)
        self.assertEqual(usd, 31.0, "exactly today plus the 30 dates before it — the transition date 2026-03-08 must not be skipped for an older one")


class DisplayCaveatsFollowTheData(unittest.TestCase):
    """T235b, source pins on the kernel-served rail JS: the summed rolling month is complete only
    from the YOUNGEST ledger (fold `since` with MAX, and say so), and the collapsed API cell follows
    the hover's version-skew rule instead of silently dropping or mixing a legacy host's month."""
    JS = Path(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read_text()

    def test_since_folds_to_the_youngest_ledger_and_says_complete_since(self):
        self.assertIn("if(v.since&&(!t.since||v.since>t.since))t.since=v.since;", self.JS,
                      "MAX: the sum is complete only from the youngest host's reach")
        self.assertIn("' \\u00b7 complete since '+esc(v.since)", self.JS)
        self.assertNotIn("v.since<t.since", self.JS, "the MIN fold overstated coverage")

    def test_the_collapsed_cell_carries_the_legacy_caveat(self):
        cell = self.JS[self.JS.index("function apiCellHTML"):self.JS.index("// The collapsed rail is the AGGREGATE story")]
        self.assertIn("_spendLegacyMonth", cell, "the cell counts the hosts whose month it cannot fold in")
        self.assertIn("var monthCav=legacyN>0;", cell, "…and the month segment wears the caveat glyph (no native title — the rich tip explains)")


if __name__ == "__main__":
    unittest.main()
