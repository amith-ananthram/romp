#!/usr/bin/env python3
"""T304: GET /session-events, the kernel's read of the session-event ledger for the dashboard's
"sessions gone wrong" cue and `romp restart-metrics`: rows newest first since a stamp (default this
kernel's boot), each naming its host, and `count`, this kernel's problems since its boot (the boot
sweep's own summary row excluded; never a cross-kernel sum). Token-gated like /api-health. Also pins
that the manager's `quiet-window` audit note is passed over by the restart-reason walk. Drives the
REAL Handler over HTTP (the test_color_route.py pattern). Synthetic only."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_sevents", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000304"
BOOT = 1_700_000_100


class SessionEventsRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (km.jd.STATE, km._STARTED, km._self_host)
        km.jd.STATE = self.tmp
        km._STARTED = float(BOOT)
        km._self_host = lambda: "TESTHOST"
        rows = [
            {"t": BOOT - 30, "pid": 1, "kind": "drain.unjoined", "sid": SID, "name": "web", "inflight": 1, "reaped": False},
            {"t": BOOT + 1, "pid": 2, "kind": "reconcile.orphan-reaped", "sid": SID, "name": "web", "cliPid": 77},
            {"t": BOOT + 1, "pid": 2, "kind": "reconcile.boot", "sessions": 3, "resumed": 1, "reaped": 1},
            {"t": BOOT + 40, "pid": 2, "kind": "crash.heal", "sid": SID, "name": "web", "attempt": 1},
        ]
        (self.tmp / "session-events.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\nnot json\n" + json.dumps({"t": "x", "kind": "bad"}) + "\n")

    def tearDown(self):
        km.jd.STATE, km._STARTED, km._self_host = self._saved

    def _get(self, qs="", token=True):
        req = urllib.request.Request("http://127.0.0.1:%d/session-events%s" % (self.port, qs),
                                     headers={"X-Romp-Token": km.TOKEN} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_rows_newest_first_since_boot_with_host_and_count(self):
        code, d = self._get()
        self.assertEqual(code, 200)
        self.assertEqual(d["host"], "TESTHOST")
        self.assertEqual(d["bootAt"], BOOT)
        self.assertEqual(d["count"], 2, "the orphan reap and the crash heal; the boot summary is not a problem")
        self.assertEqual([r["kind"] for r in d["rows"]], ["crash.heal", "reconcile.boot", "reconcile.orphan-reaped"],
                         "since the boot, newest first; the previous kernel's drain row is before the boot")
        self.assertTrue(all(r["host"] == "TESTHOST" for r in d["rows"]))

    def test_since_and_limit(self):
        code, d = self._get("?since=%d&limit=2" % (BOOT - 60))
        self.assertEqual(code, 200)
        self.assertEqual(len(d["rows"]), 2, "the limit caps the newest")
        self.assertEqual(d["count"], 2, "count is since the boot whatever `since` asks")
        code, d = self._get("?since=%d" % (BOOT - 60))
        self.assertEqual([r["kind"] for r in d["rows"]][-1], "drain.unjoined")
        code, d = self._get("?since=garbage&limit=zero")
        self.assertEqual(code, 200, "bad parameters fall back to the defaults")
        self.assertEqual(len(d["rows"]), 3)

    def test_a_row_in_the_boot_second_is_counted_and_listed(self):
        """The default `since` is the boot's whole second, the resolution every row's `t` has, the same stamp
        `bootAt` reports and `count` filters by. A real _STARTED is fractional, so a row stamped in the boot
        second (the previous kernel's drain row, written as it exited and this kernel was spawned) sits at
        t == int(_STARTED) < _STARTED: with a float default it was counted and not listed. Both defaults, the
        one a missing ?since= gets and the one an unparseable ?since= falls back to, are that whole second."""
        km._STARTED = BOOT + 0.6
        with open(self.tmp / "session-events.jsonl", "a") as f:
            f.write(json.dumps({"t": BOOT, "pid": 1, "kind": "drain.unjoined", "sid": SID, "name": "web",
                                "inflight": 1, "reaped": True}) + "\n")
        for qs in ("", "?since=garbage"):
            with self.subTest(query=qs or "none"):
                code, d = self._get(qs)
                self.assertEqual(code, 200)
                kinds = [r["kind"] for r in d["rows"]]
                self.assertIn("drain.unjoined", kinds, "the boot-second row is listed with this boot's rows")
                self.assertEqual(d["count"], len([k for k in kinds if k != "reconcile.boot"]),
                                 "count and rows are one predicate but for the boot summary: %s" % kinds)
                self.assertEqual(d["since"], BOOT, "the default since is the boot's whole second")
                self.assertEqual(d["since"], d["bootAt"])

    def test_missing_ledger_is_empty_not_an_error(self):
        (self.tmp / "session-events.jsonl").unlink()
        code, d = self._get()
        self.assertEqual((code, d["rows"], d["count"]), (200, [], 0))

    def test_token_required(self):
        code, _ = self._get(token=False)
        self.assertIn(code, (401, 403))


class QuietWindowNoteIsNotARequest(unittest.TestCase):
    def test_walk_passes_over_the_quiet_window_row(self):
        tmp = Path(tempfile.mkdtemp())
        saved = km.jd.STATE
        km.jd.STATE = tmp
        try:
            now = 1_700_000_500
            rows = [{"t": now - 20, "action": "remote-restart", "reason": "requested from TESTHOST"},
                    {"t": now - 3, "action": "quiet-window", "since": now - 600, "waitedS": 597, "reason": "quiet",
                     "backstop": False, "coalesced": 1, "mode": "all"}]
            (tmp / "restart-audit.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            (tmp / "restart-cuts.jsonl").write_text("")
            self.assertIn("quiet-window", km._NO_RESTART_ACTIONS)
            rec = km._recent_restart_audit(window=90, now=now, started=now - 1000)
            self.assertEqual(rec["action"], "remote-restart", "the note is passed over; the request beneath names the cut")
        finally:
            km.jd.STATE = saved


class KernelSampleOnRestartRows(unittest.TestCase):
    """T304: the kernel process's own resident size and CPU ride the cut row and the boot row."""

    def test_cut_row_carries_the_sample(self):
        row = km._restart_cut_row({"cutTurns": []}, now=1)
        self.assertIsInstance(row["rssKb"], int)
        self.assertIsInstance(row["cpuS"], float)
        self.assertGreater(row["cpuS"], 0.0)
        self.assertEqual(row["cutTurns"], [], "the row's existing fields are untouched")

    def test_boot_row_carries_the_sample(self):
        tmp = Path(tempfile.mkdtemp())
        saved = km.RESTART_CUTS_FILE
        km.RESTART_CUTS_FILE = tmp / "restart-cuts.jsonl"
        try:
            km._append_restart_cut(km._restart_cut_row({"cutTurns": []}, now=100))
            km._append_boot_settled(103.0, 103.4)
            rows = [json.loads(x) for x in km.RESTART_CUTS_FILE.read_text().splitlines()]
        finally:
            km.RESTART_CUTS_FILE = saved
        boot = rows[-1]
        self.assertTrue(boot["bootSettled"])
        self.assertEqual((boot["prevCutT"], boot["outageS"]), (100, 3.0))
        self.assertIsInstance(boot["rssKb"], int)
        self.assertIsInstance(boot["cpuS"], float)

    def test_sample_never_raises(self):
        saved = km._process_stats
        km._process_stats = lambda: (_ for _ in ()).throw(RuntimeError("no proc"))
        try:
            self.assertEqual(km._kernel_process_sample(), {})
            self.assertNotIn("rssKb", km._restart_cut_row({"cutTurns": []}, now=1))
        finally:
            km._process_stats = saved


if __name__ == "__main__":
    unittest.main()
