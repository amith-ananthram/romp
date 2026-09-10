#!/usr/bin/env python3
"""T247 (the user 2026-09-07): clicking the dashboard's usage readout opens a modal with a per-session
spend breakdown and a stacked histogram of spend over time colored by session. The data is GET
/spend/detail: the ledger's bySid maps (T100's per-session attribution) with names and identity colors
resolved kernel-side, two ranges at the ledger's own granularity (192 hours, 90 days), the top-N sessions
as stacks (every session its own, since T247e) plus ONE "unattributed" stack — spend recorded before attribution existed, or
the part of a bucket no sid accounts for, is shown as such, never dropped (fail loudly). Hermetic state
root, synthetic ledger, the notes-api demo sessions (web/api/tests), placeholder uuids, TESTHOST."""
import inspect
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_spenddetail", os.path.join(BIN, "romp-kernel"))

WEB, API, TESTS = ("11111111-2222-3333-4444-000000000001", "11111111-2222-3333-4444-000000000002",
                   "11111111-2222-3333-4444-000000000003")
NOW = time.mktime((2026, 9, 7, 15, 30, 0, 0, 0, -1))   # a fixed afternoon; keys built the recorder's way


def _hour(n):
    return time.strftime("%Y-%m-%dT%H", time.localtime(NOW - n * 3600))


def _day(n):
    return time.strftime("%Y-%m-%d", time.localtime(NOW - n * 86400))


def _bucket(usd, tok, turns, by=None, key=None):
    e = {"usd": usd, "turns": turns, "tokIn": tok // 4, "tokOut": tok // 4, "tokCacheR": tok // 4,
         "tokCacheW": tok - 3 * (tok // 4)}
    if key is not None:
        e["key"] = key
    if by is not None:
        e["bySid"] = by
    return e


def write_ledger(state, extra_sids=0):
    hours = {}
    for n in range(0, 60):
        # web spends every hour, api every other, tests rarely
        by = {WEB: {"usd": 1.0, "turns": 2, "tok": 20000}}
        if n % 2 == 0:
            by[API] = {"usd": 0.5, "turns": 1, "tok": 8000}
        if n % 10 == 0:
            by[TESTS] = {"usd": 0.25, "turns": 1, "tok": 3000}
        tot_usd = sum(v["usd"] for v in by.values())
        tot_tok = sum(v["tok"] for v in by.values())
        tot_turns = sum(v["turns"] for v in by.values())
        hours[_hour(n)] = _bucket(tot_usd, tot_tok, tot_turns, by)
    # an hour that predates per-session attribution: no bySid at all → unattributed, never dropped
    hours[_hour(70)] = _bucket(3.0, 30000, 4)
    days = {}
    for n in range(0, 40):
        by = {WEB: {"usd": 24.0, "turns": 48, "tok": 480000}, API: {"usd": 6.0, "turns": 12, "tok": 96000}}
        if n % 5 == 0:
            by[TESTS] = {"usd": 0.6, "turns": 2, "tok": 7200}
        for i in range(extra_sids):   # many small sessions, to overflow the top-N into "other"
            by["11111111-2222-3333-4444-0000000001%02d" % i] = {"usd": 0.1, "turns": 1, "tok": 1000}
        days[_day(n)] = _bucket(sum(v["usd"] for v in by.values()), sum(v["tok"] for v in by.values()),
                                sum(v["turns"] for v in by.values()), by)
    # a PARTIAL day: the bucket total exceeds what its sids account for (a turn recorded sid-less)
    days[_day(2)]["usd"] += 5.0
    days[_day(2)]["tokIn"] += 50000
    days[_day(2)]["turns"] += 1
    # two days before attribution existed: whole-bucket unattributed
    days[_day(50)] = _bucket(40.0, 400000, 30)
    days[_day(51)] = _bucket(41.0, 410000, 31)
    (state / "spend.json").write_text(json.dumps({"days": days, "hours": hours}))


class SpendDetail(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        self._saved = (km.jd.STATE, km.NAMES, km._live_names, km._tmux_sessions, km._self_host,
                       km._claude_account, km._auth_key_present, dict(km._remotes))
        km.jd.STATE = state
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        (km.NAMES / WEB).write_text("web\t/tmp/notes-api\t#1EA1EB\t#ffffff\n")
        (km.NAMES / API).write_text("api\t/tmp/notes-api\t#54B204\t#ffffff\n")
        (km.NAMES / TESTS).write_text("tests\t/tmp/notes-api\n")          # no identity color
        km._live_names = lambda tm: {"web": WEB, "api": API}                # tests is no longer running
        km._tmux_sessions = lambda: []
        km._self_host = lambda: "TESTHOST"
        km._claude_account = lambda: ""                                     # a key-only machine: total scope
        km._auth_key_present = lambda: True
        km._remotes.clear()
        (state / "usage.json").write_text(json.dumps({"apiKey": True}))
        write_ledger(state)

    def tearDown(self):
        (km.jd.STATE, km.NAMES, km._live_names, km._tmux_sessions, km._self_host,
         km._claude_account, km._auth_key_present, saved_remotes) = self._saved
        km._remotes.clear()
        km._remotes.update(saved_remotes)
        self.td.cleanup()

    def test_sessions_are_named_colored_sorted_and_flagged_live(self):
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["host"], "TESTHOST")
        self.assertEqual([(h["host"], h["status"]) for h in d["hosts"]], [("TESTHOST", "ok")])
        self.assertEqual(d["scope"], "total")
        names = [s["name"] for s in d["sessions"]]
        self.assertEqual(names, ["web", "api", "tests"], "sorted by dollars, largest first")
        web, api, tests = d["sessions"]
        self.assertEqual((web["bg"], web["fg"]), ("#1EA1EB", "#ffffff"), "identity color from the registry")
        self.assertTrue(tests["bg"].startswith("#"), "a session without an identity color still gets one (see below)")
        self.assertTrue(web["live"] and api["live"] and not tests["live"], "a dead session keeps its name, flagged")
        self.assertAlmostEqual(web["usd"], 24.0 * 40, places=3)
        self.assertEqual(web["turns"], 48 * 40)
        self.assertEqual(api["tok"], 96000 * 40)
        self.assertNotIn("topN", d, "T247e: no top-N — every session is its own stack and row")
        self.assertTrue(tests.get("bgDerived") and tests["bg"] in km.pal.colors(km.pal.active_name(km.jd.STATE)),
                        "a session the registry never colored gets a deterministic swatch of the active palette")
        self.assertEqual(tests["bg"], km._spend_detail(now=NOW)["sessions"][2]["bg"], "…the same one every open")
        self.assertNotIn("windows", d, "the modal's window rows are the hover's own (no second parse, no dead payload)")
        self.assertEqual(d["tzOffsetMin"], int((time.localtime(NOW).tm_gmtoff or 0) // 60))

    def test_unattributed_spend_is_shown_never_dropped(self):
        d = km._spend_detail(now=NOW)
        un = d["unattributed"]
        # two pre-attribution days + the partial day's remainder
        self.assertAlmostEqual(un["usd"], 40.0 + 41.0 + 5.0, places=3)
        self.assertEqual(un["turns"], 30 + 31 + 1)
        self.assertEqual(un["tok"], 400000 + 410000 + 50000)
        kinds = [s["kind"] for s in d["days"]["stacks"]]
        self.assertEqual(kinds, ["sid", "sid", "sid", "unattributed"], "one stack per session, then the unattributed stack")
        una = d["days"]["stacks"][-1]
        keys = d["days"]["keys"]
        self.assertEqual(len(keys), 90)
        self.assertEqual(keys[-1], _day(0), "dense, oldest first, today last")
        self.assertAlmostEqual(una["usd"][keys.index(_day(50))], 40.0, places=3)
        self.assertAlmostEqual(una["usd"][keys.index(_day(2))], 5.0, places=3, msg="the partial day's remainder")
        self.assertEqual(una["tok"][keys.index(_day(2))], 50000)
        hk = d["hours"]["keys"]
        self.assertEqual(len(hk), km._SERIES_HOURS)
        self.assertEqual(hk[-1], _hour(0))
        hun = [s for s in d["hours"]["stacks"] if s["kind"] == "unattributed"][0]
        self.assertAlmostEqual(hun["usd"][hk.index(_hour(70))], 3.0, places=3, msg="a pre-attribution hour, whole")
        web = [s for s in d["hours"]["stacks"] if s.get("name") == "web"][0]
        self.assertAlmostEqual(web["usd"][hk.index(_hour(5))], 1.0, places=3)
        self.assertEqual(web["tok"][hk.index(_hour(5))], 20000)
        self.assertFalse(web["usd"][hk.index(_hour(100))], "an hour with nothing recorded is a true zero")

    def test_every_session_is_its_own_stack_no_other_fold(self):
        # T247e (the user 2026-09-08): no top-N, no "other (N sessions)" — the list is the legend
        write_ledger(km.jd.STATE, extra_sids=12)
        d = km._spend_detail(now=NOW)
        self.assertEqual(len(d["sessions"]), 15, "the table lists EVERY session")
        kinds = [s["kind"] for s in d["days"]["stacks"]]
        self.assertEqual(kinds.count("sid"), 15, "one stack per session")
        self.assertNotIn("other", kinds)
        self.assertEqual(kinds[-1], "unattributed")
        keys = d["days"]["keys"]
        small = [s for s in d["days"]["stacks"] if s.get("sid", "").endswith("0111")][0]
        self.assertAlmostEqual(small["usd"][keys.index(_day(1))], 0.1, places=3, msg="a small session keeps its own series")
        self.assertTrue(all(s["bg"].startswith("#") for s in d["days"]["stacks"] if s["kind"] == "sid"), "every stack has a color")
        # derived colors never take a swatch an identity-colored session here holds while a free one remains
        # (review find: a plain hash gave a dead session web's blue)
        ident = {s["bg"] for s in d["sessions"] if not s.get("bgDerived")}
        derived = [s["bg"] for s in d["sessions"] if s.get("bgDerived")]
        free = len(km.pal.colors(km.pal.active_name(km.jd.STATE))) - len(ident)
        self.assertTrue(all(c not in ident for c in derived[:free]), "the free swatches go first")
        self.assertEqual(len(set(derived[:free])), free, "…each once")
        self.assertEqual(d["hours"]["stacks"][0]["name"], "web", "stack order is the table's: top by dollars")

    def test_the_keyed_scope_reads_each_sids_key_split_like_the_rail(self):
        # a login's windows beside the key's spend: the rail sums ONLY key-billed turns, so does the modal
        km._claude_account = lambda: "acct-digest"
        (km.jd.STATE / "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}}))
        state = km.jd.STATE
        days = {_day(0): _bucket(10.0, 100000, 10, {WEB: {"usd": 6.0, "turns": 6, "tok": 60000,
                                                           "key": {"usd": 2.0, "turns": 2, "tok": 20000}},
                                                     API: {"usd": 4.0, "turns": 4, "tok": 40000}},
                                  key={"usd": 2.0, "turns": 2, "tok": 20000})}
        (state / "spend.json").write_text(json.dumps({"days": days, "hours": {}}))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["scope"], "keyed")
        by = {s["name"]: s for s in d["sessions"]}
        self.assertAlmostEqual(by["web"]["usd"], 2.0, places=3, msg="the key-billed dollars only")
        self.assertNotIn("api", by, "a login-only session bills nothing to the key: not a row, not a stack")
        self.assertEqual([s["name"] for s in d["days"]["stacks"]], ["web"])
        self.assertEqual(d["unattributed"]["usd"], 0.0)
        # the TOTAL scope, same ledger: the split rides beside the totals where the hover would show it
        km._claude_account = lambda: ""
        d2 = km._spend_detail(now=NOW)
        self.assertEqual(d2["scope"], "total")
        by2 = {s["name"]: s for s in d2["sessions"]}
        self.assertAlmostEqual(by2["web"]["usd"], 6.0, places=3)
        self.assertEqual(by2["web"]["key"], {"usd": 2.0, "tok": 20000, "turns": 2})
        self.assertNotIn("key", by2["api"])

    def test_the_hosts_asked_are_the_hovers_spend_hosts(self):
        # the hover's predicate exactly: a remote joins when it reports SPEND windows (a login-only
        # remote contributes bars, not spend, and is not asked); a down one is reported, never omitted
        km._remotes["peer"] = {"host": "peer", "status": "up", "local_port": 1, "token": "t",
                               "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
        km._remotes["bars"] = {"host": "bars", "status": "up", "usage": {"fiveHour": {"pct": 5}}}
        km._remotes["down"] = {"host": "down", "status": "down", "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}
        saved = km._PEER_SPEND_TIMEOUT_S
        km._PEER_SPEND_TIMEOUT_S = 0.5
        try:
            d = km._spend_detail(now=NOW)
        finally:
            km._PEER_SPEND_TIMEOUT_S = saved
        self.assertEqual([h["host"] for h in d["hosts"]], ["TESTHOST", "peer", "down"], "bars has no spend: not asked, not listed")
        self.assertEqual(d["hosts"][1]["status"], "unreachable", "nothing answers on port 1")
        self.assertEqual(d["hosts"][2]["status"], "unreachable")

    def test_a_login_without_a_key_is_the_computed_scope(self):
        # the rail's THIRD arm: windows present, no key → the rail shows no spend at all; the modal must
        # not dress computed costs up as API spend (review find)
        km._claude_account = lambda: "acct-digest"
        km._auth_key_present = lambda: False
        (km.jd.STATE / "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}}))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["scope"], "computed")
        self.assertEqual(d["sessions"][0]["name"], "web", "the computed costs are still per session")
        html = km._landing()
        self.assertIn("computed cost, not billed", html)
        self.assertIn("Spend (computed)", html)

    def test_totals_cover_the_days_the_chart_draws_not_every_bucket_kept(self):
        # the recorder keeps the 90 most RECENT spend days, which can reach past 90 calendar days; the
        # header says "last 90 days", so a bucket older than the chart's window counts nowhere (review find)
        d0 = km._spend_detail(now=NOW)
        led = json.loads((km.jd.STATE / "spend.json").read_text())
        led["days"][_day(120)] = _bucket(999.0, 9990000, 99, {TESTS: {"usd": 999.0, "turns": 99, "tok": 9990000}})
        (km.jd.STATE / "spend.json").write_text(json.dumps(led))
        d = km._spend_detail(now=NOW)
        self.assertEqual([s["name"] for s in d["sessions"]], [s["name"] for s in d0["sessions"]], "the ranking is unmoved")
        self.assertEqual(d["sessions"][2]["usd"], d0["sessions"][2]["usd"], "tests' total is the chart's window, not the ledger's reach")
        self.assertEqual(d["unattributed"], d0["unattributed"])

    def test_a_fall_back_transition_writes_one_slot_per_local_hour(self):
        # TZ-pinned: 192 epoch hours across a fall-back hold 191 distinct local keys; the recorder merges
        # both 01:00s into one key, so the series holds one slot for it, not a labeled empty twin
        import os as _os
        saved = _os.environ.get("TZ")
        _os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()
        try:
            fall = time.mktime((2026, 11, 1, 12, 0, 0, 0, 0, -1))
            d = km._spend_detail(now=fall)
            hk = d["hours"]["keys"]
            self.assertEqual(len(hk), len(set(hk)), "no duplicate hour key")
            self.assertEqual(len(hk), km._SERIES_HOURS - 1)
        finally:
            if saved is None:
                _os.environ.pop("TZ", None)
            else:
                _os.environ["TZ"] = saved
            time.tzset()

    def test_an_empty_or_missing_ledger_answers_with_zeros_not_an_error(self):
        (km.jd.STATE / "spend.json").unlink()
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["sessions"], [])
        self.assertEqual(d["days"]["stacks"], [])
        self.assertEqual(len(d["days"]["keys"]), 90)

    def test_float_residue_never_conjures_an_unattributed_stack_or_row(self):
        # T247b review find: the recorder rounds the bucket sum and each sid's sum independently (6
        # places), so a fully attributed bucket can carry a +1e-6..+6e-6 dollar residue with zero token
        # residue — 28 of 113 live hour buckets did — and the presence test on the unrounded residues
        # hung a hatched "unattributed" chip with no bars on the hourly view. Below the rounding grain
        # a residue is zero: no stack, no row.
        hours, days = {}, {}
        for n in range(0, 40):
            by = {WEB: {"usd": 0.333333, "turns": 1, "tok": 1000}, API: {"usd": 0.666667, "turns": 1, "tok": 2000}}
            hours[_hour(n)] = _bucket(1.000001, 3000, 2, by)          # sum of sids: 1.000000 → +1e-6 residue
        for n in range(0, 30):
            by = {WEB: {"usd": 2.1, "turns": 3, "tok": 5000}, API: {"usd": 3.9, "turns": 4, "tok": 7000}}
            days[_day(n)] = _bucket(6.000004, 12000, 7, by)           # +4e-6 residue, every day
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": days, "hours": hours}))
        d = km._spend_detail(now=NOW)
        self.assertEqual([s["kind"] for s in d["hours"]["stacks"]], ["sid", "sid"], "no unattributed stack from residue")
        self.assertEqual([s["kind"] for s in d["days"]["stacks"]], ["sid", "sid"])
        self.assertEqual(d["unattributed"], {"usd": 0.0, "tok": 0, "turns": 0}, "30 days of residue sum to nothing, not $0.0001")
        # …and a REAL remainder still shows: a sid-less turn's cost is far above the grain
        days[_day(1)]["usd"] += 0.05
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": days, "hours": hours}))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["days"]["stacks"][-1]["kind"], "unattributed")
        self.assertAlmostEqual(d["unattributed"]["usd"], 0.05, places=4)

    def test_the_keyed_scopes_other_stack_counts_only_sessions_that_billed_the_key(self):
        # T247b review find: in the keyed scope a login-only session contributed (0,0,0) yet was counted
        # into "other (N sessions)", while the table dropped it — the legend said 17, the fold said 2
        km._claude_account = lambda: "acct-digest"
        (km.jd.STATE / "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}}))
        by = {}
        for i in range(12):   # twelve key-billed sessions, two beyond the top ten
            by["11111111-2222-3333-4444-0000000002%02d" % i] = {"usd": 1.0 + i, "turns": 1, "tok": 1000,
                                                                "key": {"usd": 1.0 + i, "turns": 1, "tok": 1000}}
        for i in range(5):    # five login-only sessions: no key sub-map
            by["11111111-2222-3333-4444-0000000003%02d" % i] = {"usd": 9.0, "turns": 9, "tok": 9000}
        days = {_day(0): _bucket(sum(v["usd"] for v in by.values()), 17000, 26, by,
                                 key={"usd": sum(1.0 + i for i in range(12)), "turns": 12, "tok": 12000})}
        (km.jd.STATE / "spend.json").write_text(json.dumps({"days": days, "hours": {}}))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["scope"], "keyed")
        self.assertEqual(len(d["sessions"]), 12, "the table lists the key-billed sessions only")
        self.assertEqual([s["kind"] for s in d["days"]["stacks"]], ["sid"] * 12, "twelve stacks, no fold (T247e)")
        self.assertEqual(d["unattributed"]["usd"], 0.0, "every key dollar is a session's: nothing unattributed")

    # ── T247c: every attached kernel's sessions, merged kernel-side ──────────────────────────────
    def _peer_server(self, payload=None, status=200, token="peer-tok", hang=False, seen=None):
        """A stand-in peer kernel: /spend/detail behind the peer's own serve token (the transport
        mirror_trust uses), answering `payload` — or, for an older build, the real kernel's unknown-route
        answer (a plain-text 404 "not found"), or never (hang). `seen` collects the paths it was asked."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        body = json.dumps(payload or {}).encode()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if seen is not None:
                    seen.append(self.path)
                if self.headers.get("X-Romp-Token") != token:
                    self.send_response(401); self.end_headers(); return
                if hang:
                    time.sleep(5); return
                if status == 404 or self.path.split("?")[0] != "/spend/detail":
                    nf = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(nf)))
                    self.end_headers()
                    self.wfile.write(nf)
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def _attach(self, host, port, token="peer-tok", status="up"):
        km._remotes[host] = {"host": host, "status": status, "local_port": port, "token": token,
                             "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}}}

    def _peer_payload(self, off_min, epochs=True):
        """A peer's own /spend/detail, three hours east of us: the same absolute hours, LOCAL keys that
        read three hours later, and one session of its own (plus a pre-attribution hour)."""
        local = km._spend_detail_local(now=NOW)
        keys, eps = local["hours"]["keys"], local["hours"]["epochs"]
        pk = [time.strftime("%Y-%m-%dT%H", time.gmtime(e * 3600 + off_min * 60)) for e in eps]
        n = len(keys)
        usd = [0.0] * n; tok = [0] * n
        i5 = n - 1 - 5
        usd[i5] = 7.0; tok[i5] = 70000
        una = [0.0] * n; una[n - 1 - 9] = 2.0
        pd = {"host": "PEERHOST", "scope": "total", "tz": "PEER", "tzOffsetMin": off_min,
              "order": ["22222222-3333-4444-5555-000000000001"],
              "sessions": [{"sid": "22222222-3333-4444-5555-000000000001", "name": "worker", "bg": "#C2410C", "fg": "#fff",
                            "live": True, "usd": 300.0, "tok": 3000000, "turns": 30}],
              "unattributed": {"usd": 2.0, "tok": 0, "turns": 1},
              "hours": {"keys": pk, "stacks": [
                  {"kind": "sid", "sid": "22222222-3333-4444-5555-000000000001", "name": "worker", "bg": "#C2410C", "live": True, "usd": usd, "tok": tok},
                  {"kind": "unattributed", "name": "unattributed", "usd": una, "tok": [0] * n}]},
              "days": {"keys": local["days"]["keys"], "stacks": [
                  {"kind": "sid", "sid": "22222222-3333-4444-5555-000000000001", "name": "worker", "bg": "#C2410C", "live": True,
                   "usd": [0.0] * 89 + [300.0], "tok": [0] * 89 + [3000000]}]}}
        if epochs:
            pd["hours"]["epochs"] = eps
        return pd

    def test_a_peers_sessions_merge_in_with_their_host_and_hours_align_by_absolute_time(self):
        off = int((time.localtime(NOW).tm_gmtoff or 0) // 60) + 180
        self._attach("PEERHOST", self._peer_server(self._peer_payload(off)))
        d = km._spend_detail(now=NOW)
        self.assertEqual([h["host"] for h in d["hosts"]], ["TESTHOST", "PEERHOST"])
        self.assertEqual([h["status"] for h in d["hosts"]], ["ok", "ok"])
        names = [(s["host"], s["name"]) for s in d["sessions"]]
        self.assertEqual(names[0], ("TESTHOST", "web"))
        self.assertIn(("PEERHOST", "worker"), names, "the peer's session is a row, tagged with its host")
        self.assertEqual(names[1], ("PEERHOST", "worker"), "sorted by dollars across hosts: 300 sits between web's 960 and api's 240")
        hk = d["hours"]["keys"]
        worker = [s for s in d["hours"]["stacks"] if s.get("name") == "worker"][0]
        self.assertEqual(worker["host"], "PEERHOST")
        self.assertAlmostEqual(worker["usd"][len(hk) - 1 - 5], 7.0, places=3,
                               msg="the peer's hour lands on the same ABSOLUTE hour, though its local key reads three hours later")
        self.assertEqual(sum(1 for v in worker["usd"] if v), 1)
        una = [s for s in d["hours"]["stacks"] if s["kind"] == "unattributed"][0]
        self.assertAlmostEqual(una["usd"][len(hk) - 1 - 9], 2.0, places=3, msg="the peer's pre-attribution hour joins the one unattributed stack")
        self.assertAlmostEqual(una["hosts"]["PEERHOST"]["usd"][len(hk) - 1 - 9], 2.0, places=3, msg="…with its host named for the tooltip")
        self.assertAlmostEqual(d["unattributed"]["byHost"]["PEERHOST"]["usd"], 2.0, places=3)
        self.assertAlmostEqual(d["unattributed"]["usd"], 86.0 + 2.0, places=3)
        dk = d["days"]["keys"]
        wd = [s for s in d["days"]["stacks"] if s.get("name") == "worker"][0]
        self.assertAlmostEqual(wd["usd"][len(dk) - 1], 300.0, places=3, msg="daily buckets align by each machine's own date")

    def test_a_peer_is_asked_for_its_local_half_only_and_the_route_serves_it(self):
        # review find: the route served the MERGED payload to a peer too, so two kernels attached to each
        # other asked each other back until the socket timeouts unwound hundreds of nested requests, and
        # the healthy peer read as timed out. The ask carries ?local=1 and the route never fans out for it.
        seen = []
        self._attach("PEERHOST", self._peer_server(self._peer_payload(0), seen=seen))
        km._spend_detail(now=NOW)
        self.assertEqual(seen, ["/spend/detail?local=1"], "the peer is asked for its own half, nothing more")
        ksrc = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('if (q.get("local") or [""])[0]:\n                    return self._send(200, json.dumps(_spend_detail_local())', ksrc,
                      "a peer's ask is served the local reader — the fan-out never runs on a peer's behalf")

    def test_a_merged_shaped_answer_from_a_peer_counts_only_its_own_rows(self):
        # review find: a peer answering with a MERGED payload (a third host it reaches, or us) had every
        # row re-tagged as the peer's, doubling a host reachable both ways and our own sessions
        pd = self._peer_payload(0)
        pd["hosts"] = [{"host": "PEERHOST", "status": "ok"}, {"host": "TESTHOST", "status": "ok"}, {"host": "THIRDHOST", "status": "ok"}]
        pd["sessions"][0]["host"] = "PEERHOST"
        pd["sessions"] += [{"sid": WEB, "name": "web", "host": "TESTHOST", "bg": "#1EA1EB", "fg": "#fff", "live": True, "usd": 960.0, "tok": 1, "turns": 1},
                           {"sid": "22222222-3333-4444-5555-000000000009", "name": "builder", "host": "THIRDHOST", "bg": "", "fg": "", "live": True, "usd": 50.0, "tok": 1, "turns": 1}]
        for st in pd["hours"]["stacks"] + pd["days"]["stacks"]:
            if st["kind"] == "sid":
                st["host"] = "PEERHOST"
        pd["days"]["stacks"].append({"kind": "sid", "sid": WEB, "name": "web", "host": "TESTHOST", "bg": "#1EA1EB", "live": True,
                                     "usd": [0.0] * 89 + [960.0], "tok": [0] * 90})
        self._attach("PEERHOST", self._peer_server(pd))
        d = km._spend_detail(now=NOW)
        rows = [(s["host"], s["name"]) for s in d["sessions"]]
        self.assertEqual(rows.count(("TESTHOST", "web")), 1, "our own session arrives once, as ours")
        self.assertNotIn(("PEERHOST", "web"), rows)
        self.assertNotIn(("PEERHOST", "builder"), rows, "a third host's row is not the peer's")
        self.assertEqual([s for s in d["days"]["stacks"] if s.get("name") == "web"][0]["host"], "TESTHOST")

    def test_a_peers_bucket_beyond_our_axis_extends_it_instead_of_vanishing(self):
        # review find: a peer east of us has a "today" our 90-day axis lacks, and a peer whose hour ticked
        # over after ours ships an epoch beyond our newest — both were dropped without a trace
        pd = self._peer_payload(0)
        import datetime as _dt
        tomorrow = (_dt.date.fromisoformat(pd["days"]["keys"][-1]) + _dt.timedelta(days=1)).isoformat()
        pd["days"]["keys"] = pd["days"]["keys"][1:] + [tomorrow]
        pd["days"]["stacks"][0]["usd"] = [0.0] * 89 + [300.0]
        last_e = pd["hours"]["epochs"][-1]
        pd["hours"]["epochs"] = pd["hours"]["epochs"][1:] + [last_e + 1]
        pd["hours"]["keys"] = pd["hours"]["keys"][1:] + [time.strftime("%Y-%m-%dT%H", time.gmtime((last_e + 1) * 3600))]
        n = len(pd["hours"]["keys"])
        pd["hours"]["stacks"][0]["usd"] = [0.0] * (n - 1) + [7.0]
        self._attach("PEERHOST", self._peer_server(pd))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["days"]["keys"][-1], tomorrow, "the peer's day joins the axis")
        wd = [s for s in d["days"]["stacks"] if s.get("name") == "worker"][0]
        self.assertAlmostEqual(wd["usd"][-1], 300.0, places=3)
        self.assertEqual(d["hours"]["epochs"][-1], last_e + 1, "the peer's newest hour joins the axis")
        wh = [s for s in d["hours"]["stacks"] if s.get("name") == "worker"][0]
        self.assertAlmostEqual(wh["usd"][-1], 7.0, places=3)
        self.assertEqual(len(d["hours"]["keys"]), len(d["hours"]["epochs"]))

    def test_an_older_peers_other_fold_survives_as_that_hosts_own_stack(self):
        # a peer on the previous build still folds beyond a top-N into "other"; its dollars are kept as a
        # stack named with its host rather than dropped (T247e keeps nothing but sid stacks of its own)
        pd = self._peer_payload(0)
        n = len(pd["hours"]["keys"])
        pd["hours"]["stacks"].append({"kind": "other", "name": "other", "count": 3, "usd": [0.0] * (n - 1) + [1.5], "tok": [0] * n})
        self._attach("PEERHOST", self._peer_server(pd))
        d = km._spend_detail(now=NOW)
        oth = [s for s in d["hours"]["stacks"] if s["kind"] == "other"]
        self.assertEqual(len(oth), 1)
        self.assertEqual((oth[0]["host"], oth[0]["count"]), ("PEERHOST", 3))
        self.assertAlmostEqual(oth[0]["usd"][-1], 1.5, places=3)

    def test_a_small_session_keeps_its_own_hourly_stack_across_hosts(self):
        # T247e: with no top-N there is no fold to count — a small session's hour is its own stack
        write_ledger(km.jd.STATE, extra_sids=12)
        led = json.loads((km.jd.STATE / "spend.json").read_text())
        sid = "11111111-2222-3333-4444-000000000111"   # the last extra: outside the merged top ten
        led["hours"][_hour(3)]["bySid"][sid] = {"usd": 0.2, "turns": 1, "tok": 200}
        led["hours"][_hour(3)]["usd"] += 0.2
        led["hours"][_hour(3)]["tokIn"] += 200
        (km.jd.STATE / "spend.json").write_text(json.dumps(led))
        self._attach("PEERHOST", self._peer_server(self._peer_payload(0)))
        d = km._spend_detail(now=NOW)
        self.assertEqual([s["kind"] for s in d["hours"]["stacks"] if s["kind"] == "other"], [], "no fold anywhere")
        small = [s for s in d["hours"]["stacks"] if s.get("sid", "").endswith("0111")]
        self.assertEqual(len(small), 1, "the small session's hour is its own stack")
        self.assertEqual(small[0]["host"], "TESTHOST")
        self.assertEqual(len([s for s in d["days"]["stacks"] if s["kind"] == "sid"]), 16, "sixteen sessions across two hosts, sixteen stacks")

    def test_the_payload_carries_the_shared_session_order_by_host(self):
        # T247f: "your order" is the tab strip's and the lanes' order — the kernel's shared seed
        # (session-order.json) per host, hosts local-first then the remotes listing's order; an older peer
        # that ships no order contributes nothing (its sessions trail, client-side)
        (km.jd.STATE / "session-order.json").write_text(json.dumps([API, TESTS, WEB]))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["order"], [["TESTHOST", API], ["TESTHOST", WEB]],
                         "one host: its seed, host-tagged — restricted to what the tab strip renders (tests is dead: the "
                         "file keeps its slot while its transcript is in the window, the strip shows no such tab, so it trails)")
        self._attach("PEERHOST", self._peer_server(self._peer_payload(0)))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["order"], [["TESTHOST", API], ["TESTHOST", WEB], ["PEERHOST", "22222222-3333-4444-5555-000000000001"]])
        km._remotes.clear()
        pd = self._peer_payload(0)
        del pd["order"]
        self._attach("OLDPEER", self._peer_server(pd))
        d = km._spend_detail(now=NOW)
        self.assertEqual(d["order"], [["TESTHOST", API], ["TESTHOST", WEB]], "no order from an older peer")
        (km.jd.STATE / "session-order.json").unlink()

    def test_the_payload_carries_the_tag_union_by_name_with_a_color_for_every_tag(self):
        # T247g: "merge by tag" groups by this viewer's tags and every attached host's cached ones, unioned
        # by NAME (the user's ruling: tags are equivalent, no home tag); members as the viewer sees them
        # (bare local sid, host:sid remote); a colorless tag gets a stable swatch
        peer_sid = "22222222-3333-4444-5555-000000000001"
        (km.jd.STATE / "timeline-views.json").write_text(json.dumps({"active": "all", "tags": [
            {"id": "g1", "name": "team", "color": "#C2410C", "members": [WEB, API]},
            {"id": "g2", "name": "ops", "color": "", "members": [API, "PEERHOST:" + peer_sid]}]}))
        km._remotes["PEERHOST"] = {"host": "PEERHOST", "status": "up", "local_port": 1, "token": "t",
                                   "usage": {"apiKey": True, "spend": {"day": {"usd": 1}}},
                                   "views": {"seq": 3, "tags": [{"id": "g7", "name": "team", "color": "#123456", "members": [peer_sid]}]}}
        saved = km._PEER_SPEND_TIMEOUT_S
        km._PEER_SPEND_TIMEOUT_S = 0.4
        try:
            d = km._spend_detail(now=NOW)
        finally:
            km._PEER_SPEND_TIMEOUT_S = saved
        tags = {t["name"]: t for t in d["tags"]}
        self.assertEqual(sorted(tags), ["ops", "team"])
        self.assertEqual(tags["team"]["color"], "#C2410C", "the first color a same-named tag carries: the local one")
        self.assertEqual(tags["team"]["members"], [WEB, API, "PEERHOST:" + peer_sid], "the peer's same-named tag joins by name, its member host-prefixed")
        self.assertEqual(tags["ops"]["members"], [API, "PEERHOST:" + peer_sid])
        self.assertTrue(tags["ops"].get("colorDerived") and tags["ops"]["color"] in km.pal.colors(km.pal.active_name(km.jd.STATE)))
        self.assertNotEqual(tags["ops"]["color"], "#C2410C", "…never the swatch another tag already wears while a free one remains")
        (km.jd.STATE / "timeline-views.json").unlink()

    def test_an_older_peer_without_epochs_still_aligns_through_its_offset(self):
        off = int((time.localtime(NOW).tm_gmtoff or 0) // 60) + 180
        self._attach("PEERHOST", self._peer_server(self._peer_payload(off, epochs=False)))
        d = km._spend_detail(now=NOW)
        hk = d["hours"]["keys"]
        worker = [s for s in d["hours"]["stacks"] if s.get("name") == "worker"][0]
        self.assertAlmostEqual(worker["usd"][len(hk) - 1 - 5], 7.0, places=3, msg="local keys converted with the peer's offset, never string-equal")

    def test_a_down_an_older_and_a_hanging_peer_are_reported_by_name_never_omitted(self):
        self._attach("DOWNHOST", 1, status="down")
        self._attach("OLDHOST", self._peer_server(status=404))
        self._attach("SLOWHOST", self._peer_server(hang=True))
        self._attach("PEERHOST", self._peer_server(self._peer_payload(int((time.localtime(NOW).tm_gmtoff or 0) // 60))))
        saved = km._PEER_SPEND_TIMEOUT_S
        km._PEER_SPEND_TIMEOUT_S = 0.6
        try:
            t0 = time.time()
            d = km._spend_detail(now=NOW)
            took = time.time() - t0
        finally:
            km._PEER_SPEND_TIMEOUT_S = saved
        by = {h["host"]: h for h in d["hosts"]}
        self.assertEqual(by["DOWNHOST"]["status"], "unreachable")
        self.assertEqual(by["OLDHOST"]["status"], "older", "no /spend/detail route: an older build, said so by name")
        self.assertEqual(by["SLOWHOST"]["status"], "unreachable", "past the bounded timeout: reported, and the rest renders")
        self.assertIn("timed out", by["SLOWHOST"]["detail"])
        self.assertEqual(by["PEERHOST"]["status"], "ok", "a slow host holds nobody hostage")
        self.assertIn(("PEERHOST", "worker"), [(s["host"], s["name"]) for s in d["sessions"]])
        self.assertLess(took, 3.0, "the peer calls run in parallel under one bounded timeout")

    def test_a_peer_that_rejects_our_token_is_unreachable_by_name(self):
        self._attach("PEERHOST", self._peer_server(self._peer_payload(0)), token="wrong")
        d = km._spend_detail(now=NOW)
        by = {h["host"]: h for h in d["hosts"]}
        self.assertEqual(by["PEERHOST"]["status"], "unreachable")

    def test_the_route_and_the_shell_are_wired(self):
        ksrc = inspect.getsource(km.Handler.do_GET) if hasattr(km.Handler, "do_GET") else open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('if p == "/spend/detail":', ksrc, "the kernel route exists")
        self.assertIn("json.dumps(_spend_detail())", ksrc)
        html = km._landing()
        self.assertIn("id=rsp-back hidden", html, "the modal's shell-native backdrop, hidden until the click")
        self.assertIn("fetch('/spend/detail'", html, "the shell fetches the route on open, never scrapes the hover")
        self.assertIn("el.addEventListener('click',function(){pull(true);openSpend();});", html,
                      "the click on the readout opens the modal (and still kicks the hover's refresh)")
        self.assertIn("if(sp&&!sp.hidden&&window.__rompCloseSpend){window.__rompCloseSpend();closed=true;}", html,
                      "Escape closes it through the shell's one Escape chain")
        self.assertIn("#rsp-back{position:fixed;inset:0;z-index:205;display:flex;align-items:center;justify-content:center;"
                      "background:rgba(0,0,0,0.55)}", html, "the panel rule: a centered card over rgba(0,0,0,0.55)")
        self.assertNotIn("__ROMP_LOADER__", html.split("_LANDING_JS")[0] if "_LANDING_JS" in html else html,
                         "the loader markup is spliced, not left as a placeholder")
        self.assertIn("rl-word", html)
        self.assertNotIn("this machine only", html, "T247c: every attached kernel's sessions are in — no such note")
        self.assertIn("not reachable", html)
        self.assertIn("older build", html)
        self.assertIn("aligned by clock time across machines", html)
        self.assertIn("recorded before per-session tracking", html, "unattributed spend is named, never folded")
        self.assertIn("var rows=spendRowsHTML(LAST||[]);", html, "the modal's window numbers are the hover's own renderer")
        self.assertIn("function fleetSpendHTML(sets){", html, "…whose signature the other suites pin, untouched")
        self.assertIn("body.theme-light #rsp-panel{", html, "a light-theme step of its own")
        # T247b: the pressed toggle's light rule is written explicitly — a state rule must win the cascade
        # (body.theme-light .rsp-btn at (0,2,1) beat .rsp-btn.on at (0,2,0) and painted dark text on the clay)
        self.assertIn("body.theme-light .rsp-btn.on{background:var(--accent,#C2410C);color:var(--accent-fg,#FFF8F2);border-color:transparent}", html)
        # T247b: the loader has a backstop — an AbortController with a generous timeout lands on the
        # existing error + retry path, so a hung socket can never trap the modal
        self.assertIn("var spAbort=new AbortController()", html)
        self.assertIn("setTimeout(function(){spAbort.abort();}", html)
        self.assertIn("signal:spAbort.signal", html)
        # T247b: dimmed rows dim ONCE — the annotation inside a dimmed row stays at the row's level
        self.assertIn(".rsp-dead td{opacity:.55}", html)
        self.assertIn(".rsp-dead .ru-tip-reset{opacity:1}", html)
        # T247e: the chart precedes the list; the list is a scroll pane under a sticky header holding
        # every session (no fold), each row its TAB TITLE (the strip's classes, the identity color as
        # --chip-bg), no swatch, no legend
        js = html
        self.assertLess(js.index("<span>Spend over time</span>"), js.index("<span>By session"), "the chart comes first")
        self.assertNotIn("more session", js, "no fold")
        self.assertNotIn("rsp-leg", js, "no legend: the list is the legend")
        self.assertIn("#rsp-table{max-height:38vh;overflow-y:auto", js)
        self.assertIn(".rsp-tbl thead th{position:sticky;top:0;background:#252526;z-index:1}", js)
        self.assertIn("body.theme-light .rsp-tbl thead th{background:#FFFFFF}", js)
        self.assertIn('<span class="tab-label colored" style="--chip-bg:\'+spColor(s)+\'">', js, "a row's title wears the tab strip's classes")
        # T247f: the order chips beside the measure chips; the choice persists with the other toggles
        self.assertIn('data-act=order:spend>by spend</button>', js)
        self.assertIn('data-act=order:yours>your order</button>', js)
        self.assertIn("var SP_PREFS_KEY='romp:spendModal';", js)
        # T247g: three ranges and the merge toggle, persisted with the rest
        self.assertIn('data-act=range:day>1 day ', js)
        self.assertIn('data-act=range:hours>7 days ', js)   # T293: 7 days over 168 hourly buckets (the ledger's 192 keep a day of slack)
        self.assertNotIn('8 days', js)
        self.assertIn('data-act=range:days>90 days ', js)
        self.assertIn('data-act=merge:toggle>merge by tag</button>', js)
        self.assertIn("JSON.stringify({range:SP.range,measure:SP.measure,order:SP.order,merge:SP.merge})", js)
        self.assertIn("localStorage.getItem('romp:vieworder')", js, "the viewer's arrangement is the strip's own key")
        # T293 (the user 2026-09-09): the hover crosshair — a pointer-inert hairline inside the svg at the pointer's
        # bucket, a stamp naming the bucket in words placed out of the flow (nothing moves under the pointer), and the
        # tooltip listing that bucket's sessions in spend order; all three leave with the pointer. The pure functions
        # are executed by ui/webview/spend-crosshair.test.ts; the served-page test hovers the real chart.
        self.assertIn("var SP_RANGE_BUCKETS={day:24,hours:168};", js)
        self.assertIn("xh.setAttribute('class','rsp-xh');", js)
        self.assertIn("stamp.className='rsp-xh-stamp'", js)
        self.assertIn("svgEl.onpointerleave=xhHide;", js, "the line, the stamp and the tooltip leave with the pointer")
        self.assertIn(".rsp-xh{stroke:rgba(255,255,255,0.45);stroke-width:1;pointer-events:none}", html)
        self.assertIn(".rsp-xh-stamp{position:absolute;top:4px;transform:translateX(6px);font-size:10px;line-height:1;padding:3px 5px;border-radius:3px;"
                      "background:rgba(30,30,30,0.88);color:#cfd6dd;pointer-events:none;white-space:nowrap}", html,
                      "the stamp: out of the flow, the surface's 10px annotation size, no pointer events")
        self.assertIn("body.theme-light .rsp-xh{", html, "a light step for the hairline")
        self.assertIn("body.theme-light .rsp-xh-stamp{", html, "…and for the stamp")
        self.assertIn(".rsp-tip-row i{", html, "a row's dot wears its stack's colour")
        self.assertNotIn("function spTipShow(", js, "the one-segment tip is gone: the bucket tooltip serves a bar too")
        # the landing page loads no stylesheet, so the strip's two rules are inlined as a TWIN; this pins the
        # twin's declarations against the source so the two cannot drift
        import re as _re
        css = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "styles.css")).read()
        def decls(sel):
            # the rule that STARTS a line: a descendant rule such as `.mention-chip .host-prefix` also ends in
            # the selector and may sit earlier in the sheet than the global rule this twin mirrors
            m = _re.search(r"(?m)^" + _re.escape(sel) + r"\s*\{([^}]*)\}", css)
            self.assertIsNotNone(m, sel + " missing from styles.css")
            return _re.sub(r"\s+", "", m.group(1)).rstrip(";")
        self.assertIn(".rsp-name .tab-label.colored{" + decls(".tab.colored .tab-label") + "}", js,
                      "the modal row's title declarations are the tab strip's, byte for byte")
        hp = decls(".host-prefix").replace("var(--dim)", "var(--dim,#9aa0a6)")
        self.assertIn(".rsp-name .host-prefix{" + hp + "}", js,
                      "the host prefix declarations are the strip's (with the dark fallback the landing needs)")
        # T247b: the phone's door is its own row at the hover's button size, full opacity — not an
        # annotation inside .ru-tip-age (10px, .55)
        self.assertIn("<div class=ru-tip-more><button class=rsp-btn id=ru-bysession>", html)
        self.assertNotIn("<div class=ru-tip-age><button class=rsp-btn id=ru-bysession>", html)
        self.assertIn(".ru-tip-more{", html)


if __name__ == "__main__":
    unittest.main()
