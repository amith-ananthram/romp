#!/usr/bin/env python3
"""Session death is a recorded, corroborated EVENT (cluster D of the stuck-card program, 2026-08-13).

A dead session used to leave no terminal state row, so nothing downstream ever finalized: cards sat
in Working forever with unretirable holds (RC7), and old sessions' cards vanished rather than ended
(RC8). Now death is one owned record — a plain idle row (byte-identical to a Stop-hook idle: ONE
state vocabulary) plus a load-bearing marker in STATE/gone/ with a closed reader list — written by
the backend that owns the liveness fact and corroborated before every stamp:
  * SDK sids: the kill gesture only (reg alive:True — dormant-revivable AND crash-looped — is never
    stamped by anyone, so the boot-resume contract is untouched by construction);
  * tmux sids: the set-diff is a TRIGGER; the tmux server itself answers per batch via
    TmuxBackend.alive_sids() — identity-true (@romp-session-id, never a NAME: same-named
    generations coexist), where the no-server exit IS the authoritative zero-sessions answer;
  * a boot pass over names/ covers deaths no kernel was up to see, re-deaths after revival, and the
    upgrade backfill.
Idempotence keys on the marker being the NEWEST event (die → revive → die is recordable every
cycle); the re-anchor hook's supersededBy row vetoes the stamp outright (a /clear'd lane is a
supersession the episode machinery owns, not a death).

All fixtures synthetic (placeholder UUIDs, invented names).
"""
import json
import os
import tempfile
import types
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_death", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
SID2 = "99999999-8888-7777-6666-555555555555"


def _marker(sid):
    try:
        return json.loads((jd.STATE / "gone" / (sid + ".json")).read_text())
    except OSError:
        return None


def _states(sid):
    try:
        return [json.loads(l) for l in
                (jd.STATE / "states" / (sid + ".jsonl")).read_text().splitlines()]
    except OSError:
        return []


def _wipe(sid):
    for p in (jd.STATE / "gone" / (sid + ".json"), jd.STATE / "states" / (sid + ".jsonl"),
              jd.SDKDIR / (sid + ".json"), jd.NAMES / sid):
        try:
            p.unlink()
        except OSError:
            pass


class RecordDeath(unittest.TestCase):
    def tearDown(self):
        _wipe(SID)

    def test_stamps_the_marker_and_one_plain_idle_row(self):
        self.assertTrue(km._record_death(SID, NOW, "kill"))
        m = _marker(SID)
        self.assertEqual((m["t"], m["by"]), (NOW - 1, "kill"))
        rows = _states(SID)
        self.assertEqual(rows[-1], {"t": NOW - 1, "state": "idle"},
                         "byte-identical to a Stop-hook idle — ONE state vocabulary, no death flavor")

    def test_a_second_death_with_no_revival_is_a_no_op(self):
        km._record_death(SID, NOW, "kill")
        self.assertFalse(km._record_death(SID, NOW + 50, "gone"),
                         "the writer's own idle row is AT the marker's t, never newer")
        self.assertEqual(_marker(SID)["t"], NOW - 1)

    def test_death_after_revival_restamps_and_rearms_the_finalize(self):
        km._record_death(SID, NOW, "kill")
        m = _marker(SID)
        m["endedAt"] = m["t"]
        (jd.STATE / "gone" / (SID + ".json")).write_text(json.dumps(m))   # finalized once
        with open(jd.STATE / "states" / (SID + ".jsonl"), "a") as f:      # the revival's own row
            f.write(json.dumps({"t": NOW + 100, "state": "waiting"}) + "\n")
        self.assertTrue(km._record_death(SID, NOW + 200, "kill"),
                        "a states row newer than the marker re-arms the writer — die→revive→die is "
                        "recordable every cycle (the first design's presence key made it invisible)")
        m2 = _marker(SID)
        self.assertEqual(m2["t"], NOW + 199)
        self.assertNotIn("endedAt", m2, "a re-stamp drops the old finalize — the drain runs again")

    def test_a_supersession_vetoes_the_stamp(self):
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        with open(sdir / (SID + ".jsonl"), "a") as f:
            f.write(json.dumps({"t": NOW, "state": "working"}) + "\n")
            f.write(json.dumps({"t": NOW + 10, "supersededBy": SID2}) + "\n")
        self.assertFalse(km._record_death(SID, NOW + 20, "gone"),
                         "a re-anchored lane is a SUPERSESSION the episode machinery owns — at the "
                         "pane it looks exactly like a death, and only the recorded event tells them apart")
        self.assertIsNone(_marker(SID))


class AliveSids(unittest.TestCase):
    def _probe(self, rc, out="", err=""):
        saved = km._TMUX._run
        km._TMUX._run = lambda args, t=3: types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)
        try:
            return km._TMUX.alive_sids()
        finally:
            km._TMUX._run = saved

    def test_a_healthy_scan_returns_the_sid_set(self):
        self.assertEqual(self._probe(0, out=SID + "\n\n" + SID2 + "\n"), {SID, SID2})

    def test_no_server_is_the_authoritative_zero_answer(self):
        # verified live: `list-sessions` with no server exits 1 with 'error connecting … No such
        # file or directory' — the mass-death/reboot shape, and the boot backfill's normal world
        self.assertEqual(self._probe(1, err="error connecting to /tmp/tmux-1000/default (No such file or directory)"),
                         set())
        self.assertEqual(self._probe(1, err="no server running on /tmp/tmux-1000/default"), set())

    def test_a_real_probe_failure_is_none_so_writers_stand_down(self):
        self.assertIsNone(self._probe(1, err="some other tmux error"))
        saved = km._TMUX._run
        km._TMUX._run = lambda args, t=3: None
        try:
            self.assertIsNone(km._TMUX.alive_sids())
        finally:
            km._TMUX._run = saved


class DeathSweepTick(unittest.TestCase):
    def setUp(self):
        self._saved_avail, self._saved_run = km._TMUX.available, km._TMUX._run
        km._TMUX.available = lambda: True
        km._prev_live_sids[0] = None

    def tearDown(self):
        km._TMUX.available, km._TMUX._run = self._saved_avail, self._saved_run
        km._prev_live_sids[0] = None
        _wipe(SID)
        _wipe(SID2)

    def _scan(self, sids):
        km._TMUX._run = lambda args, t=3: types.SimpleNamespace(
            returncode=0, stdout="\n".join(sids), stderr="")

    def test_a_departed_sid_confirmed_gone_is_stamped(self):
        self._scan([])
        km._death_sweep_tick(NOW, {SID: {}})
        km._death_sweep_tick(NOW + 5, {})
        self.assertIsNotNone(_marker(SID), "left the map + the owner confirms absence → stamped")

    def test_the_owner_saying_alive_blocks_the_stamp(self):
        self._scan([SID])
        km._death_sweep_tick(NOW, {SID: {}})
        km._death_sweep_tick(NOW + 5, {})
        self.assertIsNone(_marker(SID), "our snapshot blinked; the session is alive")

    def test_an_sdk_owned_sid_is_never_stamped_here(self):
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": True}))
        self._scan([])
        km._death_sweep_tick(NOW, {SID: {}})
        km._death_sweep_tick(NOW + 5, {})
        self.assertIsNone(_marker(SID),
                          "SDK deaths are the kill gesture's to stamp — alive:True is revivable/"
                          "crash-looped and the boot-resume contract rides on never stamping it")

    def test_a_failed_probe_stamps_nothing(self):
        km._TMUX._run = lambda args, t=3: None
        km._death_sweep_tick(NOW, {SID: {}})
        km._death_sweep_tick(NOW + 5, {})
        self.assertIsNone(_marker(SID))


class DeathBootPass(unittest.TestCase):
    def tearDown(self):
        _wipe(SID)
        _wipe(SID2)
        km._TMUX.available, km._TMUX._run = self._saved_avail, self._saved_run

    def setUp(self):
        self._saved_avail, self._saved_run = km._TMUX.available, km._TMUX._run
        km._TMUX.available = lambda: True
        km._TMUX._run = lambda args, t=3: types.SimpleNamespace(returncode=0, stdout="", stderr="")
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def test_a_regless_tmux_sid_dead_before_boot_is_stamped(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID), "the RC7 case: a tmux death no kernel was up to see")

    def test_an_alive_true_reg_is_left_for_the_resume_contract(self):
        (jd.NAMES / SID).write_text("api\t/tmp\t#123456\t#fff\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": True}))
        km._death_boot_pass(NOW)
        self.assertIsNone(_marker(SID))

    def test_a_killed_reg_is_stamped(self):
        (jd.NAMES / SID).write_text("api\t/tmp\t#123456\t#fff\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": False}))
        km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID))


if __name__ == "__main__":
    unittest.main()
