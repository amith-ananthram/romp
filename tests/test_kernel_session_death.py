#!/usr/bin/env python3
"""Session death is a recorded, corroborated EVENT (cluster D of the stuck-card program, 2026-08-13).

A dead session used to leave no terminal state row, so nothing downstream ever finalized: cards sat
in Working forever with unretirable holds (RC7), and old sessions' cards vanished rather than ended
(RC8). Now death is one owned record — a plain idle row (byte-identical to a Stop-hook idle: ONE
state vocabulary) plus a load-bearing marker in STATE/gone/ with a closed reader list — written by
the backend that owns the liveness fact and corroborated before every stamp:
  * SDK sids: the kill gesture only (reg alive:True — dormant-revivable AND crash-looped — is never
    stamped by anyone, so the boot-resume contract is untouched by construction);
  * Codex sids: the set-diff is a TRIGGER; the Codex backend's registry answers — its dead mark
    stamps, a row still owned blocks the stamp, and while the registry cannot be read
    (_codex_records_blind) every writer stands down, loudly and counted;
  * a names entry with no registry row anywhere is dead history (a session no backend can revive)
    and stamps;
  * a boot pass over names/ covers deaths no kernel was up to see, re-deaths after revival, and the
    upgrade backfill.
Idempotence keys on the marker being the NEWEST event (die → revive → die is recordable every
cycle); the re-anchor hook's supersededBy row vetoes the stamp outright (a /clear'd lane is a
supersession the episode machinery owns, not a death).

All fixtures synthetic (placeholder UUIDs, invented names).
"""
import contextlib
import io
import json
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


class _FakeCodex:
    """The Codex backend as the death writers read it: a registry of sids, each still owned (alive) or
    marked dead, or a registry the backend could not read."""
    def __init__(self, rows=None, unreadable=False):
        self.rows = dict(rows or {})            # sid → alive
        self._registry_unreadable = unreadable

    def _session(self, sid):
        return self.rows.get(sid) if sid in self.rows else None   # a row (truthy or not) vs no row

    def owns(self, sid):
        return bool(self.rows.get(sid))


class CodexRecordsBlind(unittest.TestCase):
    """The one predicate every death writer stands down on: the Codex records cannot be read right now."""
    def tearDown(self):
        try:
            (jd.STATE / "codex" / "registry.json").unlink()
        except OSError:
            pass

    def test_a_readable_registry_is_not_blind(self):
        self.assertFalse(km._codex_records_blind(_FakeCodex({SID: True})))
        self.assertFalse(km._codex_records_blind(_FakeCodex()))

    def test_an_unreadable_registry_is_blind(self):
        self.assertTrue(km._codex_records_blind(_FakeCodex(unreadable=True)))

    def test_no_module_is_blind_only_while_a_registry_file_exists(self):
        self.assertFalse(km._codex_records_blind(None), "no module and no records: nothing to be blind to")
        (jd.STATE / "codex").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "codex" / "registry.json").write_text("{}")
        self.assertTrue(km._codex_records_blind(None), "records exist that this kernel cannot read")


class DeathSweepTick(unittest.TestCase):
    def setUp(self):
        self._saved_codex = km._codex
        km._prev_live_sids[0] = None
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        km._codex = self._saved_codex
        km._prev_live_sids[0] = None
        _wipe(SID)
        _wipe(SID2)

    def _codex(self, rows=None, unreadable=False):
        fake = _FakeCodex(rows, unreadable)
        km._codex = lambda: fake

    def _depart(self, sid=SID):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._death_sweep_tick(NOW, {sid: {}})
            km._death_sweep_tick(NOW + 5, {})
        return err.getvalue()

    def test_a_departed_codex_sid_its_registry_marks_dead_is_stamped(self):
        self._codex({SID: False})
        self._depart()
        self.assertIsNotNone(_marker(SID), "left the map + the owner's record says dead → stamped")

    def test_a_departed_names_only_sid_is_dead_history_and_is_stamped(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        self._codex({})
        self._depart()
        self.assertIsNotNone(_marker(SID), "no registry row anywhere: a session no backend can revive")

    def test_the_owner_saying_alive_blocks_the_stamp(self):
        self._codex({SID: True})
        self._depart()
        self.assertIsNone(_marker(SID), "our snapshot blinked; the session is alive")

    def test_an_sdk_owned_sid_is_never_stamped_here(self):
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": True}))
        self._codex({})
        self._depart()
        self.assertIsNone(_marker(SID),
                          "SDK deaths are the kill gesture's to stamp — alive:True is revivable/"
                          "crash-looped and the boot-resume contract rides on never stamping it")

    def test_a_blind_codex_registry_stamps_nothing_and_says_so_once(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        self._codex({}, unreadable=True)
        err = self._depart()
        self.assertIsNone(_marker(SID), "silence is not an answer: a Codex sid and dead history look alike")
        self.assertEqual(err.count("death-sweep: the Codex registry cannot be read"), 1, err)
        self.assertIn("1 departed sid(s)", err)


class DeathBootPass(unittest.TestCase):
    def tearDown(self):
        _wipe(SID)
        _wipe(SID2)
        km._codex = self._saved_codex

    def setUp(self):
        self._saved_codex = km._codex
        km._codex = lambda: _FakeCodex({})
        jd.NAMES.mkdir(parents=True, exist_ok=True)

    def test_a_names_only_sid_dead_before_boot_is_stamped(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID), "the RC7 case: dead history no kernel was up to see")

    def test_a_codex_sid_its_registry_still_owns_is_left_alone(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        km._codex = lambda: _FakeCodex({SID: True})
        km._death_boot_pass(NOW)
        self.assertIsNone(_marker(SID))

    def test_a_codex_dead_mark_is_stamped(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        km._codex = lambda: _FakeCodex({SID: False})
        km._death_boot_pass(NOW)
        self.assertIsNotNone(_marker(SID))

    def test_a_blind_registry_skips_every_regless_sid_loudly(self):
        (jd.NAMES / SID).write_text("web\t/tmp\t#123456\t#fff\n")
        (jd.NAMES / SID2).write_text("api\t/tmp\t#123456\t#fff\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID2 + ".json")).write_text(json.dumps({"sid": SID2, "alive": False}))
        km._codex = lambda: _FakeCodex({}, unreadable=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            km._death_boot_pass(NOW)
        self.assertIsNone(_marker(SID), "reg-less: stood down while the Codex records cannot be read")
        self.assertIsNotNone(_marker(SID2), "the SDK reg's alive:False is its own affirmative answer")
        self.assertIn("death-boot: the Codex registry cannot be read", err.getvalue())

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
