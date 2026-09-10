#!/usr/bin/env python3
"""Two bugs in how a remote row reports itself (the user 2026-07-28).

ONE — remembered values were served as live. kernelSha (and everything derived from it: outOfDate,
behindBy, aheadBy) plus the peer's declared mail tier are answers from the LAST SUCCESSFUL poll; the
supervisor only polls a row that is `up`. _remote_public() shipped them regardless of status, so a host
that had been unreachable for hours still reported a commit count and a mail tier beside its own
"disconnected" label — three claims, two unknowable. The values still ship (a blank row is less useful)
but now carry `stale` + `lastOk` so the UI can mark them.

TWO — recoveries never reached disk. _remotes_save() was called only from the ACT routes (attach, detach,
set_trust, checkin), never from the supervisor. So status/fails/gave_up/kernel_sha/detail lived in memory
alone: a row that failed, got persisted mid-failure by some unrelated act, then recovered left the file
frozen on the failure. Observed live — hours after a tunnel was healthily up, remotes.json still read
`starting` with a superseded sha and a by-then-unreachable hostname parked in `detail`.

The periodic save must ALSO not churn: the file is 0600 because every row holds that host's serve token,
and rewriting a credential file every 15s forever is its own defect. Hence the signature gate, which
deliberately ignores last_ok (restamped every pass a host answers).

Synthetic only — placeholder host/token, no network.
"""
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_stale", os.path.join(BIN, "romp-kernel"))


def _row(**kw):
    r = {"host": "TESTHOST", "kernel_port": 29855, "local_port": 51000, "bus_port": 51001,
         "token": "tok", "trust": "trusted", "status": "up", "detail": "", "sids": [],
         "fails": 0, "next_try": 0, "kernel_sha": "abc1234", "proc": None}
    r.update(kw)
    return r


class RememberedIsNotLive(unittest.TestCase):
    def test_an_up_row_is_not_stale(self):
        self.assertFalse(km._remote_public(_row(status="up"))["stale"],
                         "an up row polled THIS pass — its sha and drift are current")

    def test_every_not_up_status_marks_the_row_stale(self):
        # down/starting/no-kernel/error/gave-up all mean "did not poll this pass" — the cached sha is
        # a memory in every one of them, so none may present it as fact.
        for st in ("down", "starting", "no-kernel", "error", "gave-up"):
            with self.subTest(status=st):
                self.assertTrue(km._remote_public(_row(status=st))["stale"],
                                "%s did not poll — its cached sha is a memory" % st)

    def test_a_missing_status_defaults_to_stale_not_live(self):
        r = _row()
        del r["status"]
        self.assertTrue(km._remote_public(r)["stale"],
                        "unknown state must fail toward 'remembered', never toward 'live'")

    def test_last_ok_rides_the_public_view_so_the_ui_can_date_what_it_shows(self):
        self.assertEqual(km._remote_public(_row(status="down", last_ok=1785272930.7))["lastOk"],
                         1785272930)

    def test_never_seen_up_reports_zero_rather_than_a_fabricated_time(self):
        self.assertEqual(km._remote_public(_row(status="down"))["lastOk"], 0)

    def test_the_cached_values_are_still_served(self):
        # The fix is to MARK them, not to blank them: a row that goes empty on disconnect is a
        # regression in its own right (you lose the last thing you knew).
        pub = km._remote_public(_row(status="down"))
        self.assertEqual(pub["kernelSha"], "abc1234")
        self.assertEqual(pub["trust"], "trusted")

    def test_the_supervisor_stamps_last_ok_when_a_host_answers(self):
        # lastOk is only ever as good as the write that sets it: without this, `stale` is honest but
        # undated, and the hover falls back to "never confirmed" for a host seen a minute ago.
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        body = src[src.index("def _tunnel_supervisor("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn('r["last_ok"] = time.time()', body,
                      "the up branch must record the moment the cached sha/tier were true")


class SupervisorStatePersists(unittest.TestCase):
    def setUp(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes["TESTHOST"] = _row()
        km._remotes_save()          # baseline: disk agrees with memory

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()

    def _disk(self):
        return json.loads(km.REMOTES_FILE.read_text())

    def test_an_idle_healthy_fleet_writes_nothing(self):
        self.assertFalse(km._remotes_save_if_changed(),
                         "nothing changed — a 0600 credential file must not be rewritten every pass")

    def test_a_last_ok_restamp_alone_does_not_rewrite_the_file(self):
        # The supervisor restamps last_ok every pass a host answers. Counting it as a change would
        # defeat the whole gate and rewrite the token file every 15s forever.
        with km._remotes_lock:
            km._remotes["TESTHOST"]["last_ok"] = 1785272930.0
        self.assertFalse(km._remotes_save_if_changed())

    def test_a_views_poll_stamp_alone_does_not_rewrite_the_file(self):
        # _poll_remote_views restamps _views_at on every real read — once a minute per up host. Counted, it
        # rewrote the token file every minute forever, the churn _NOT_SAVED's usage entry names, by the
        # other poll.
        with km._remotes_lock:
            km._remotes["TESTHOST"]["_views_at"] = 1785272930.0
        self.assertFalse(km._remotes_save_if_changed())
        self.assertIn("_views_at", km._NOT_SAVED)

    def test_a_changed_views_reading_reaches_disk_without_its_stamp_and_survives_a_boot(self):
        # `views` IS persisted, on purpose: _views_client serves every cached reading, status aside, so a
        # down host's tags keep excluding from the untagged view across a kernel restart — _remotes_load
        # keeps the key, and the loaded row starts down with no stamp, so the boot's first poll re-reads.
        reading = {"tags": [{"id": "g100", "name": "web", "color": "", "members": ["s1"]}]}
        with km._remotes_lock:
            km._remotes["TESTHOST"]["views"] = reading
            km._remotes["TESTHOST"]["_views_at"] = 1785272930.0
        self.assertTrue(km._remotes_save_if_changed(), "a changed reading is a change")
        row = self._disk()[0]
        self.assertEqual(row["views"], reading)
        self.assertNotIn("_views_at", row)
        self.assertNotIn("views", km._NOT_SAVED)
        with km._remotes_lock:
            km._remotes.clear()
        km._remotes_load()
        with km._remotes_lock:
            r = km._remotes["TESTHOST"]
            self.assertEqual(r["views"], reading)
            self.assertEqual(r["status"], "down")
            self.assertNotIn("_views_at", r)
        served = [t for t in km._views_client().get("remoteTags") or [] if t.get("host") == "TESTHOST"]
        self.assertEqual([t["name"] for t in served], ["web"], "the down host's cached tags are in the union after the boot")

    def test_a_file_an_older_build_wrote_loads_without_the_keys_this_build_does_not_save(self):
        # _NOT_SAVED keeps _views_at out of the SAVED row, but a remotes.json the previous build wrote
        # carries one, and _remotes_load copied every key of the row — so the first boot on this build
        # restored the stamp, and _poll_remote_views' gate served the cached reading for up to
        # REMOTE_VIEWS_EVERY past it instead of re-reading. Every unsaved key goes on load.
        reading = {"tags": [{"id": "g100", "name": "web", "color": "", "members": ["s1"]}]}
        rows = self._disk()
        rows[0].update({"views": reading, "_views_at": time.time(), "misses": 3, "ok_polls": 9,
                        "usage": {"five_hour": {}}, "_usage_at": time.time()})
        km.REMOTES_FILE.write_text(json.dumps(rows))
        with km._remotes_lock:
            km._remotes.clear()
        km._remotes_load()
        with km._remotes_lock:
            r = km._remotes["TESTHOST"]
        self.assertEqual(r["views"], reading, "the reading itself is kept: a down host's tags survive the boot")
        for k in km._NOT_SAVED:
            if k != "proc":                              # the live-Popen slot, set to None on load
                self.assertNotIn(k, r, "%s loaded from an older file" % k)
        # and the gate reads at once: the poll dials the tunnel instead of serving the loaded reading
        dials = []

        class Dial:
            def __init__(self, *a, **kw):
                dials.append(a)
                raise OSError("no tunnel in a test")

        saved = km.http.client.HTTPConnection
        km.http.client.HTTPConnection = Dial
        try:
            self.assertEqual(km._poll_remote_views(r), reading, "the failed dial keeps the last good reading, as ever")
        finally:
            km.http.client.HTTPConnection = saved
        self.assertEqual(len(dials), 1, "a real read was attempted: the older file's stamp did not gate it")

    def test_a_status_change_reaches_disk(self):
        # THE BUG: this is the drop the file used to miss entirely.
        with km._remotes_lock:
            km._remotes["TESTHOST"]["status"] = "down"
            km._remotes["TESTHOST"]["detail"] = "ssh: Network is unreachable"
        self.assertTrue(km._remotes_save_if_changed())
        self.assertEqual(self._disk()[0]["status"], "down")

    def test_a_recovery_reaches_disk_too(self):
        # The half that was observed live: the file sat on a failure long after the row healed.
        with km._remotes_lock:
            km._remotes["TESTHOST"].update({"status": "starting", "fails": 1,
                                            "detail": "ssh: Network is unreachable"})
        km._remotes_save_if_changed()
        self.assertEqual(self._disk()[0]["status"], "starting")
        with km._remotes_lock:
            km._remotes["TESTHOST"].update({"status": "up", "fails": 0, "detail": "",
                                            "kernel_sha": "def5678"})
        self.assertTrue(km._remotes_save_if_changed(), "a recovery is a change and must persist")
        row = self._disk()[0]
        self.assertEqual(row["status"], "up")
        self.assertEqual(row["kernel_sha"], "def5678")
        self.assertEqual(row["detail"], "", "the parked error must not outlive the failure on disk")

    def test_last_ok_is_written_when_a_real_change_triggers_the_save(self):
        # Excluded from the SIGNATURE, not from the file: the save fired by a drop carries the last
        # moment the host answered, which is exactly when that stamp is worth having.
        with km._remotes_lock:
            km._remotes["TESTHOST"]["last_ok"] = 1785272930.0
            km._remotes["TESTHOST"]["status"] = "down"
        km._remotes_save_if_changed()
        self.assertEqual(self._disk()[0]["last_ok"], 1785272930.0)

    def test_the_saved_file_never_carries_the_live_popen(self):
        with km._remotes_lock:
            km._remotes["TESTHOST"]["status"] = "down"
        km._remotes_save_if_changed()
        self.assertNotIn("proc", self._disk()[0])

    def test_the_supervisor_actually_calls_the_periodic_save(self):
        # Source pin: the whole point is that supervisor-owned state reaches disk. Without this call
        # every assertion above passes while the real bug is untouched.
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        body = src[src.index("def _tunnel_supervisor("):]
        body = body[:body.index("\ndef ", 1)]
        self.assertIn("_remotes_save_if_changed()", body)


if __name__ == "__main__":
    unittest.main()
