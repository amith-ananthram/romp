#!/usr/bin/env python3
"""The Analyzing… swirl covers a judge call actually in flight (the user 2026-08-12).

The incident: when the judge-auth outage healed, the pipeline swept an 18-hour backlog — 201
successful calls in minutes — while every card sat inertly in Working. Two blind spots, both
pinned here:
  * the swirl's only trigger was the SETTLE GAP (latest turn settled, closer verdict pending),
    which planner/grouper work on OLDER turns never trips;
  * that trigger is cache-warm-only by design, and a fresh kernel's parse caches are cold at
    exactly the moment a drain runs (the restart that healed the outage is the restart that
    emptied the caches).
So build_feed now lights the swirl from jd.active_runs() too — the in-process registry of judge
calls in flight, the same authority the nudge gate trusts (_revivers_pending, whose comment
always claimed the swirl covered this) — needing neither a live pane nor a warm parse.

The registry round-trip runs for real; the build_feed wiring is pinned by source, the build_feed
test pattern (see test_kernel_apierror_working.py).
"""
import inspect
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
jd = load_source("romp_judge_jactive", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_jactive", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class ActiveRegistryRoundTrip(unittest.TestCase):
    def test_a_call_is_visible_with_its_session_while_in_flight_and_gone_after(self):
        rid = jd._active_begin("planner", SID, 123.0)
        try:
            self.assertIn(SID, {r.get("fsid") for r in jd.active_runs()},
                          "an in-flight call names its session — the Analyzing… prong reads exactly this")
        finally:
            jd._active_end(rid)
        self.assertNotIn(SID, {r.get("fsid") for r in jd.active_runs()},
                         "deregistered in a finally — the swirl can never outlive the call")


class BuildFeedWiring(unittest.TestCase):
    def test_the_feed_reads_the_registry_once_per_build(self):
        src = inspect.getsource(km.build_feed)
        self.assertIn('_jactive = {r.get("fsid") for r in jd.active_runs()}', src)

    def test_either_prong_lights_the_swirl_and_the_active_prong_needs_no_warm_parse(self):
        src = inspect.getsource(km.build_feed)
        # the active prong stands alone — no `live`, no `ps`: a fresh kernel's caches are cold at
        # exactly the moment a backlog drain runs, and the registry is an in-process fact
        self.assertIn("sess_judging = bool(not who_working", src)
        self.assertIn("and (fsid in _jactive", src)
        # the settle-gap prong keeps its cache-warm gates — it needs the parse
        self.assertIn('or (live and ps and _closer_pending(fsid, s["path"], now, store))', src)

    def test_an_open_turn_still_reads_working_not_analyzing(self):
        # who_working guards BOTH prongs: a session actively mid-turn is Working — a captioner call
        # running beside an open turn must not re-dress the card as Analyzing…
        src = inspect.getsource(km.build_feed)
        self.assertIn("bool(not who_working\n", src)

    def test_the_card_key_is_unchanged(self):
        # feed.ts + spin-caption.ts key on `judging` as before — the kernel broadened WHEN it is
        # true (active calls 2026-08-12; in-flight-class stall holds 2026-08-13), not the contract
        self.assertIn('"judging": bool((sess_judging or _stall_inflight) and column == "working")',
                      inspect.getsource(km.build_feed))


if __name__ == "__main__":
    unittest.main()
