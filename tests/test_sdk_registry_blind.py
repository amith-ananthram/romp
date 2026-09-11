#!/usr/bin/env python3
"""The SDK registry's blindness stand-down (review of the tmux backend's removal, 2026-09-11).

sdk_backend.list_regs answers [] for a MISSING sdk/ directory (a fresh root before the first write) and
serves its cache on a listing fault; the kernel must not read either as "no session": an sdk/ renamed
aside, unmounted, or a state root moved under a running kernel would otherwise empty the live map in
one tick, and the death sweep would stamp every live SDK session dead and the dead-wait sweep file
blocks on every card. The kernel checks the directory ITSELF (_sdk_records_blind, the SDK module
untouched): the previous rows are served, liveReadFailures rises, one stderr line per episode, and the
three death writers stand down. A fresh root with no sdk/ and no names is genuine emptiness and boots
clean. Also here: a send to a sid no backend owns refuses before any park, and both send routes answer
that refusal as ok:false / a warn, never as a delivered message.

All fixtures synthetic (placeholder UUIDs, invented names)."""
import contextlib
import pathlib
import io
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_sdkblind", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "11111111-2222-3333-4444-666666666666"


class _FakeSdk:
    """The SDK backend's live_sessions as list_regs shapes it: the alive regs under sdk/, and {} when the
    directory is missing or cannot be listed (the module's own contract, which the kernel must see through)."""
    def live_sessions(self):
        out = {}
        try:
            names = os.listdir(jd.SDKDIR)
        except OSError:
            return out
        for n in names:
            try:
                reg = json.loads((jd.SDKDIR / n).read_text())
            except Exception:
                continue
            if reg.get("alive"):
                out[reg["sid"]] = {"state": "waiting", "since": "", "model": "", "effort": "", "mode": "",
                                   "connected": True, "spawning": False, "ctx": None, "subagents": [], "bgTasks": []}
        return out

    def owns(self, sid):
        return (jd.SDKDIR / (sid + ".json")).exists()


def _reg(sid, alive=True):
    jd.SDKDIR.mkdir(parents=True, exist_ok=True)
    (jd.SDKDIR / (sid + ".json")).write_text(json.dumps({"sid": sid, "alive": alive, "name": "web"}))


def _name(sid, name="web"):
    jd.NAMES.mkdir(parents=True, exist_ok=True)
    (jd.NAMES / sid).write_text(name + "\t/tmp\t\t\n")


def _marker(sid):
    try:
        return json.loads((jd.STATE / "gone" / (sid + ".json")).read_text())
    except OSError:
        return None


class _Root(unittest.TestCase):
    """A private state root per test, the fake SDK backend on km._sdk, no Codex backend."""
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._state = jd.STATE
        jd._rebind_state(pathlib.Path(self.td))
        self.fake = _FakeSdk()
        self._saved = (km._sdk, km._codex, dict(km._LIVE_LAST_ROWS), dict(km._LIVE_READ_FAILS), km._prev_live_sids[0])
        km._sdk = lambda: self.fake
        km._codex = lambda: None
        km._LIVE_LAST_ROWS.clear(); km._LIVE_LAST_RAW.clear(); km._VANISHED_SAID.clear()
        km._LIVE_READ_FAILS["count"] = 0
        km._LIVE_READ_FAILS["last"] = {}
        km._prev_live_sids[0] = None
        self.err = io.StringIO()

    def tearDown(self):
        km._sdk, km._codex, rows, fails, km._prev_live_sids[0] = self._saved
        km._LIVE_LAST_ROWS.clear(); km._LIVE_LAST_ROWS.update(rows)
        km._LIVE_READ_FAILS.clear(); km._LIVE_READ_FAILS.update(fails)
        jd._rebind_state(self._state)
        for root, dirs, _files in os.walk(self.td):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(self.td, ignore_errors=True)

    def live(self):
        with contextlib.redirect_stderr(self.err):
            return km._live_map()


class GenuineEmptiness(_Root):
    def test_a_fresh_root_with_no_registry_and_no_names_is_not_blind(self):
        self.assertFalse(jd.SDKDIR.exists())
        self.assertFalse(km._sdk_records_blind())
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0, "an empty world counts no failure")
        self.assertEqual(self.err.getvalue(), "")

    def test_an_existing_empty_registry_directory_is_an_authoritative_empty_map(self):
        jd.SDKDIR.mkdir(parents=True)
        _name(SID)                                   # a names entry with no reg: dead history, not blindness
        self.assertFalse(km._sdk_records_blind())
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)


class RegistryDirectoryGone(_Root):
    def _seed(self):
        _reg(SID); _reg(SID2); _name(SID); _name(SID2)
        rows = self.live()
        self.assertEqual(set(rows), {SID, SID2})
        return rows

    def test_a_registry_renamed_aside_serves_the_previous_rows_counts_and_says_so_once(self):
        rows = self._seed()
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        self.assertTrue(km._sdk_records_blind())
        self.assertEqual(self.live(), rows, "the previous rows are served, not an empty map")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 1)
        self.assertEqual(self.err.getvalue().count("liveness: the sdk backend's read failed"), 1)
        self.assertEqual(self.live(), rows)
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2, "every stood-down read counts")
        self.assertEqual(self.err.getvalue().count("liveness:"), 1, "said once per episode")
        os.rename(jd.SDKDIR.with_name("sdk.aside"), jd.SDKDIR)
        self.assertEqual(self.live(), rows, "the directory back: a fresh read, no stand-down")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2)

    def test_the_death_sweep_stamps_nothing_while_the_registry_directory_is_gone(self):
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)              # arms the set-diff trigger
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, {})        # every sid "departed" at once
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertFalse((jd.STATE / "states" / (SID + ".jsonl")).exists(), "no idle row either")
        self.assertIn("death-sweep: the SDK registry directory cannot be read", self.err.getvalue())

    def test_dead_wait_corroboration_stands_down_and_is_tallied_once_per_pass(self):
        self._seed()
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        stats = {}
        self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats))
        self.assertIsNone(km._dead_wait_corroborated(SID2, stats=stats))
        self.assertEqual(stats, {"sdk": 2})
        with contextlib.redirect_stderr(self.err):
            self.assertIsNone(km._dead_wait_corroborated(SID))
        self.assertIn("dead-wait: the SDK registry directory cannot be read for", self.err.getvalue())

    def test_a_boot_creates_the_registry_directory_and_names_only_sids_are_dead_history(self):
        # a root whose sessions were all terminal ones, or a fresh root before its first SDK write: names on
        # record, no sdk/ yet. The boot pass creates the directory (the kernel owns its existence from then
        # on, so a missing sdk/ after boot is a vanished one), and the names-only sids stamp as dead history
        # (decision f) with no stand-down and no failure counted.
        _name(SID); _name(SID2)
        km._LIVE_LAST_ROWS.clear()                   # a fresh process: no previous rows to go on
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertTrue(jd.SDKDIR.is_dir(), "the boot created sdk/")
        self.assertIsNotNone(_marker(SID)); self.assertIsNotNone(_marker(SID2))
        self.assertEqual(self.live(), {})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0, "a legitimate steady state counts no failure")
        self.assertNotIn("death-boot: the SDK registry directory cannot be read", self.err.getvalue())

    def test_a_directory_recreated_around_the_moved_one_is_still_blind(self):
        # after sdk/ vanishes, the SDK backend's next routine reg write re-creates it with one gutted reg
        # (no alive field); the other sessions' regs went with the moved directory. The directory lists,
        # so the existence check alone would read "not blind" and the sweep would stamp every other live
        # session dead while its thread still runs.
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)              # arms the set-diff trigger
        os.rename(jd.SDKDIR, jd.SDKDIR.with_name("sdk.aside"))
        jd.SDKDIR.mkdir()
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "lastSid": "abcd"}))
        self.assertTrue(km._sdk_records_blind(), "a previously live sid with its names entry and no reg: the collapse")
        self.assertEqual(self.live(), rows, "the previous rows are served")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 1)
        with contextlib.redirect_stderr(self.err):
            km._death_sweep_tick(NOW + 1, {})
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertIsNone(km._dead_wait_corroborated(SID2))

    def test_a_boot_with_the_registry_moved_aside_after_live_sessions_stamps_nothing(self):
        # sdk/ renamed for a backup while names/ stands, and the kernel restarts: the new process has no previous
        # rows, the boot creates an empty sdk/, and a names sid with no reg looks exactly like dead history —
        # except that it shows RECENT LIFE (states rows minutes old, a lease, a goal store just written).
        _name(SID); _name(SID2)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 120, "state": "working"}) + "\n")
        (jd.STATE / "leases").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "leases" / (SID2 + ".json")).write_text(json.dumps({"sid": SID2}))
        os.utime(jd.STATE / "leases" / (SID2 + ".json"), (NOW - 60, NOW - 60))
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
            stats = {}
            self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats, now=NOW))
        self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
        self.assertEqual(stats, {"sdk": 1})
        self.assertEqual(km._LIVE_READ_FAILS["count"], 2, "both stand-downs counted")
        self.assertEqual(self.err.getvalue().count("death-boot: the SDK registry holds no regs while 2 session(s) show recent life"), 1)

    def test_a_terminal_era_root_with_stale_states_still_stamps_at_boot(self):
        _name(SID)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 30 * 86400, "state": "waiting"}) + "\n")
        km._LIVE_LAST_ROWS.clear()
        with contextlib.redirect_stderr(self.err):
            km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID), "no life within the horizon: dead history")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)

    def test_dead_wait_reads_a_names_only_sid_with_stale_life_as_dead_history(self):
        _name(SID)
        d = jd.STATE / "states"; d.mkdir(parents=True, exist_ok=True)
        (d / (SID + ".jsonl")).write_text(json.dumps({"t": NOW - 30 * 86400, "state": "waiting"}) + "\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)   # the registry exists and is empty: nothing recent, so history
        self.assertIs(km._dead_wait_corroborated(SID, now=NOW), True)

    def test_a_reg_deleted_by_hand_while_others_stand_ends_that_session_and_freezes_nothing(self):
        rows = self._seed()
        os.unlink(jd.SDKDIR / (SID2 + ".json"))       # one dormant session's reg, removed out of band
        with contextlib.redirect_stderr(self.err):
            self.assertFalse(km._sdk_records_blind(), "a partial vanish is not the collapse")
        fresh = self.live()
        self.assertEqual(set(fresh), {SID}, "the fresh read is served, not the frozen previous rows")
        self.assertEqual(km._LIVE_READ_FAILS["count"], 0)
        self.assertEqual(self.err.getvalue().count("vanished while the other regs stand"), 1)
        self.live()
        self.assertEqual(self.err.getvalue().count("vanished"), 1, "said once per sid")
        self.assertIn(SID2, self.err.getvalue())

    @unittest.skipIf(os.geteuid() == 0, "root reads an unreadable directory")
    def test_the_boot_pass_and_the_sweep_stand_down_on_an_unlistable_directory_without_raising(self):
        rows = self._seed()
        km._death_sweep_tick(NOW, rows)
        os.chmod(jd.SDKDIR, 0)
        try:
            with contextlib.redirect_stderr(self.err):
                km._death_boot_pass(NOW)             # no raise: the guard is consulted before any per-sid read
                km._death_sweep_tick(NOW + 1, {})
                stats = {}
                self.assertIsNone(km._dead_wait_corroborated(SID, stats=stats))
            self.assertIsNone(_marker(SID)); self.assertIsNone(_marker(SID2))
            self.assertEqual(stats, {"sdk": 1})
            self.assertIn("death-boot: the SDK registry directory cannot be read", self.err.getvalue())
            self.assertIn("death-sweep: the SDK registry directory cannot be read", self.err.getvalue())
        finally:
            os.chmod(jd.SDKDIR, stat.S_IRWXU)

    @unittest.skipIf(os.geteuid() == 0, "root reads an unreadable directory")
    def test_an_unlistable_registry_directory_is_blind_too(self):
        rows = self._seed()
        os.chmod(jd.SDKDIR, 0)
        try:
            self.assertTrue(km._sdk_records_blind())
            self.assertEqual(self.live(), rows)
            self.assertEqual(km._LIVE_READ_FAILS["count"], 1)
        finally:
            os.chmod(jd.SDKDIR, stat.S_IRWXU)


class UnownedSendRefuses(_Root):
    def test_send_or_park_refuses_an_unowned_sid_before_any_park_even_with_an_open_turn(self):
        saved = (km._working_now, km._compacting_now, dict(km._pending_ops))
        km._working_now = lambda sid: True             # a dead session whose transcript ends on an open turn
        km._compacting_now = lambda sid: False
        try:
            with contextlib.redirect_stderr(self.err):
                self.assertIsNone(km._send_or_park(km._UNOWNED, SID, "hello"))
            self.assertNotIn(SID, km._pending_ops, "nothing parked for a session nobody runs")
            self.assertIn("no backend owns this session", self.err.getvalue())
        finally:
            km._working_now, km._compacting_now, ops = saved
            km._pending_ops.clear(); km._pending_ops.update(ops)

    def test_the_ws_send_op_warns_on_a_refused_handover(self):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of)
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web"
        try:
            with contextlib.redirect_stderr(self.err):
                km._drive({"type": "sendMessage", "id": SID, "text": "hello"}, client)
        finally:
            km.Sessions.backend_for, km._push_soon, km._name_of = saved
        warns = [m for m in sent if m.get("type") == "warn"]
        self.assertTrue(warns, sent)
        self.assertIn("not delivered", warns[0]["text"])


class MetaCommandRefused(_Root):
    """A /model, /effort or /fast to a sid no backend owns is refused before any stamp or park (review find):
    the meta-command arm runs ahead of the send refusal on both routes."""

    def setUp(self):
        super().setUp()
        self._saved2 = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of, dict(km._pending_ops),
                       dict(km._model_switch_pending))
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._pending_ops.clear(); km._model_switch_pending.clear()

    def tearDown(self):
        km.Sessions.backend_for, km._push_soon, km._name_of, ops, pend = self._saved2
        km._pending_ops.clear(); km._pending_ops.update(ops)
        km._model_switch_pending.clear(); km._model_switch_pending.update(pend)
        super().tearDown()

    def _drive(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        with contextlib.redirect_stderr(self.err):
            km._drive(msg, client)
        return [m for m in sent if m.get("type") == "warn"]

    def test_the_ws_send_op_refuses_each_meta_command_without_a_stamp_or_a_park(self):
        for text in ("/model opus", "/effort high", "/fast on"):
            warns = self._drive({"type": "sendMessage", "id": SID, "text": text})
            self.assertTrue(warns and "not delivered" in warns[0]["text"], (text, warns))
        self.assertEqual(km._pending_ops, {}, "nothing parked")
        self.assertNotIn(SID, km._model_switch_pending, "no switching dots on a dead lane")

    def test_the_lane_menus_command_op_refuses_the_same_way(self):
        saved = km._sid_of
        km._sid_of = lambda who: SID if who == "web" else who      # the lane menu keys its ops by session NAME
        try:
            warns = self._drive({"type": "sendCommand", "name": "web", "cmd": "/effort high"})
        finally:
            km._sid_of = saved
        self.assertTrue(warns and "not delivered" in warns[0]["text"], warns)
        self.assertEqual(km._pending_ops, {})


class FollowUpRefused(_Root):
    def test_a_refused_follow_up_moves_nothing(self):
        sent, predicted, reopened = [], [], []
        client = {"send": lambda s: sent.append(json.loads(s)), "wid": "", "cid": "c1"}
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, km._name_of, km._predict_working,
                 jd.optimistic_followup, km._mark_views_dirty)
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km._predict_working = lambda *a, **k: predicted.append(a)
        jd.optimistic_followup = lambda *a, **k: reopened.append(a) or True
        km._mark_views_dirty = lambda *a, **k: None
        try:
            with contextlib.redirect_stderr(self.err):
                km._drive({"type": "askFollowUp", "itemId": SID + ":g1", "text": "go on"}, client)
        finally:
            (km.Sessions.backend_for, km._push_soon, km._name_of, km._predict_working,
             jd.optimistic_followup, km._mark_views_dirty) = saved
        errs = [m for m in sent if m.get("type") == "err"]
        self.assertTrue(errs, sent)
        self.assertEqual((errs[0]["op"], errs[0]["itemId"]), ("askFollowUp", SID + ":g1"), "the shape the feed reverts on")
        self.assertIn("No running backend owns this session", errs[0]["text"])
        self.assertEqual(predicted, [], "no card moves to Working on a refusal")
        self.assertEqual(reopened, [], "no reopen event is written on a refusal")


class SendRouteRefuses(_Root):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _post(self, path, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method="POST",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_post_send_answers_a_refusal_as_ok_false_with_the_reason(self):
        _name(SID)                                   # a names-only sid: no backend owns it
        saved = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        try:
            with contextlib.redirect_stderr(self.err):
                code, resp = self._post("/send", {"id": SID, "text": "hello"})
        finally:
            km.Sessions.backend_for = saved
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, resp)
        self.assertIn("not delivered", resp.get("error", ""))
        self.assertNotIn("queued", resp)

    def test_post_send_refuses_each_meta_command_by_sid_and_by_name(self):
        _name(SID)
        saved = (km.Sessions.__dict__["backend_for"], dict(km._pending_ops), dict(km._model_switch_pending))
        km.Sessions.backend_for = staticmethod(lambda sid: km._UNOWNED)
        km._pending_ops.clear(); km._model_switch_pending.clear()
        try:
            with contextlib.redirect_stderr(self.err):
                for text in ("/model opus", "/effort high", "/fast on"):
                    code, resp = self._post("/send", {"id": SID, "text": text})
                    self.assertEqual((code, resp.get("ok")), (200, False), (text, resp))
                    self.assertIn("the command was not delivered", resp["error"])
                code, resp = self._post("/send", {"name": "ghost", "text": "/model opus"})   # a name no live session answers to
            self.assertEqual((code, resp.get("ok")), (200, False), resp)
            self.assertIn("ghost", resp["error"])
            self.assertEqual(km._pending_ops, {}, "nothing parked for the dead lane")
            self.assertEqual(km._model_switch_pending, {}, "no switching dots stamped")
        finally:
            km.Sessions.backend_for, ops, pend = saved
            km._pending_ops.clear(); km._pending_ops.update(ops)
            km._model_switch_pending.clear(); km._model_switch_pending.update(pend)


if __name__ == "__main__":
    unittest.main()
