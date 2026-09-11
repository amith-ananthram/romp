#!/usr/bin/env python3
"""The spend modal's per-session list follows the chart's range (T353, the user 2026-09-11): the kernel's half.

Every stack of /spend/detail's series carries, beside its dollars and tokens per bucket, its TURNS and its key-billed
dollars per bucket (`turns`, `keyUsd`), so the modal can sum the list's every column from exactly the buckets the
chart draws. Over the daily range the sums equal the payload's own 90-day session totals (the same ledger read twice
must agree); the merged, federated series carries the same two arrays, and a peer of an older build, whose stacks
have neither, keeps them absent (the modal shows a dash and a note names the host), never a zero. The client half is executed in ui/webview/spend-range-list.test.ts.

Synthetic ledger only (tests/test_spend_detail.py's fixture): the notes-api world, fixed clock.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_spend_range", os.path.join(BIN, "romp-kernel"))
fx = load_source("romp_test_spend_detail_fixture", os.path.join(HERE, "test_spend_detail.py"))

WEB, API, TESTS = fx.WEB, fx.API, fx.TESTS
NOW = fx.NOW


class _Ledger(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        state = Path(self.td.name)
        self._saved = (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
                       km._claude_account, km._auth_key_present, dict(km._remotes))
        km.jd.STATE = state
        km.NAMES = state / "names"
        km.NAMES.mkdir()
        (km.NAMES / WEB).write_text("web\t/tmp/notes-api\t#1EA1EB\t#ffffff\n")
        (km.NAMES / API).write_text("api\t/tmp/notes-api\t#54B204\t#ffffff\n")
        (km.NAMES / TESTS).write_text("tests\t/tmp/notes-api\n")
        km._live_names = lambda tm: {"web": WEB, "api": API}
        km._live_map = lambda: []
        km._self_host = lambda: "TESTHOST"
        km._claude_account = lambda: ""
        km._auth_key_present = lambda: True
        km._remotes.clear()
        (state / "usage.json").write_text(json.dumps({"apiKey": True}))
        fx.write_ledger(state)

    def tearDown(self):
        (km.jd.STATE, km.NAMES, km._live_names, km._live_map, km._self_host,
         km._claude_account, km._auth_key_present, saved_remotes) = self._saved
        km._remotes.clear()
        km._remotes.update(saved_remotes)
        self.td.cleanup()


class StacksCarryEveryColumn(_Ledger):
    def test_turns_and_key_dollars_ride_each_stack_and_sum_to_the_session_totals(self):
        d = km._spend_detail_local(now=NOW)
        for rng in ("days", "hours"):
            for st in d[rng]["stacks"]:
                self.assertEqual(len(st["turns"]), len(d[rng]["keys"]), (rng, st["kind"]))
                if st["kind"] == "sid":
                    self.assertEqual(len(st["keyUsd"]), len(d[rng]["keys"]))
        # over the daily range the stacks' sums ARE the 90-day session totals the payload carries
        by = {s["sid"]: s for s in d["sessions"]}
        for st in d["days"]["stacks"]:
            if st["kind"] != "sid":
                continue
            s = by[st["sid"]]
            self.assertAlmostEqual(sum(st["usd"]), s["usd"], places=3, msg=s["name"])
            self.assertEqual(sum(st["tok"]), s["tok"], s["name"])
            self.assertEqual(sum(st["turns"]), s["turns"], s["name"])
        una = [st for st in d["days"]["stacks"] if st["kind"] == "unattributed"][0]
        self.assertAlmostEqual(sum(una["usd"]), d["unattributed"]["usd"], places=3)
        self.assertEqual(sum(una["turns"]), d["unattributed"]["turns"])
        # the hourly range: web spends 2 turns every hour of the 60 recorded, api 1 every other hour
        hk = d["hours"]["keys"]
        web = [st for st in d["hours"]["stacks"] if st.get("name") == "web"][0]
        self.assertEqual(web["turns"][hk.index(fx._hour(5))], 2)
        api = [st for st in d["hours"]["stacks"] if st.get("name") == "api"][0]
        self.assertEqual((api["turns"][hk.index(fx._hour(4))], api["turns"][hk.index(fx._hour(5))]), (1, 0))
        hun = [st for st in d["hours"]["stacks"] if st["kind"] == "unattributed"][0]
        self.assertEqual(hun["turns"][hk.index(fx._hour(70))], 4, "a pre-attribution hour's turns are unattributed, whole")

    def test_the_key_billed_dollars_ride_per_bucket_in_the_total_scope(self):
        led = json.loads((km.jd.STATE / "spend.json").read_text())
        day = fx._day(3)
        led["days"][day]["bySid"][WEB]["key"] = {"usd": 9.0, "turns": 18, "tok": 180000}
        led["days"][day]["key"] = {"usd": 9.0, "turns": 18, "tok": 180000}
        (km.jd.STATE / "spend.json").write_text(json.dumps(led))
        d = km._spend_detail_local(now=NOW)
        web = [st for st in d["days"]["stacks"] if st.get("name") == "web"][0]
        i = d["days"]["keys"].index(day)
        self.assertAlmostEqual(web["keyUsd"][i], 9.0, places=3)
        self.assertEqual(sum(1 for v in web["keyUsd"] if v), 1, "only the one day carries key dollars")
        s = [s for s in d["sessions"] if s["name"] == "web"][0]
        self.assertAlmostEqual(s["key"]["usd"], sum(web["keyUsd"]), places=3, msg="the key column's 90-day sum agrees")


class MergedStacks(_Ledger):
    def test_the_merge_carries_the_arrays_and_an_older_peer_reads_as_a_dash_and_a_note(self):
        local = km._spend_detail_local(now=NOW)
        # a peer of this build, and one of an OLDER build whose stacks carry neither turns nor keyUsd
        peer_new = json.loads(json.dumps(local)); peer_new["host"] = "PEERHOST"
        peer_old = json.loads(json.dumps(local)); peer_old["host"] = "OLDHOST"
        for rng in ("days", "hours"):
            for st in peer_old[rng]["stacks"]:
                st.pop("turns", None); st.pop("keyUsd", None)
        hosts = [{"host": h, "status": "ok", "tzOffsetMin": local["tzOffsetMin"], "scope": local["scope"]}
                 for h in ("TESTHOST", "PEERHOST", "OLDHOST")]
        merged = km._merge_spend_details([("TESTHOST", local), ("PEERHOST", peer_new), ("OLDHOST", peer_old)], hosts, local)
        days = merged["days"]
        webs = {st["host"]: st for st in days["stacks"] if st.get("name") == "web"}
        self.assertEqual(sorted(webs), ["OLDHOST", "PEERHOST", "TESTHOST"])
        self.assertEqual(sum(webs["TESTHOST"]["turns"]), sum(webs["PEERHOST"]["turns"]))
        self.assertGreater(sum(webs["TESTHOST"]["turns"]), 0)
        self.assertEqual(len(webs["TESTHOST"]["keyUsd"]), len(days["keys"]))
        # the older peer's stacks carry NO turns and NO key dollars: the modal shows a dash there, never a zero that
        # would read as a count and undercount the total, and the host row carries the note's flag
        self.assertNotIn("turns", webs["OLDHOST"])
        self.assertNotIn("keyUsd", webs["OLDHOST"])
        by_host = {h["host"]: h for h in merged["hosts"]}
        self.assertTrue(by_host["OLDHOST"].get("noTurns"))
        self.assertNotIn("noTurns", by_host["TESTHOST"])
        self.assertNotIn("noTurns", by_host["PEERHOST"])
        # the unattributed stack is summed across hosts: with an older peer among them its turns are unknown too
        una = [st for st in days["stacks"] if st["kind"] == "unattributed"][0]
        self.assertNotIn("turns", una)
        self.assertGreater(sum(una["usd"]), 0)
        # without the older peer, every array rides and the unattributed turns are the two peers' sum
        merged2 = km._merge_spend_details([("TESTHOST", local), ("PEERHOST", peer_new)], hosts[:2], local)
        una2 = [st for st in merged2["days"]["stacks"] if st["kind"] == "unattributed"][0]
        self.assertEqual(sum(una2["turns"]), 2 * local["unattributed"]["turns"])
        self.assertFalse(any(h.get("noTurns") for h in merged2["hosts"]))


if __name__ == "__main__":
    unittest.main()
