#!/usr/bin/env python3
"""T211 (2026-09-01): kernel restarts REPLAYED recurring-cron prompts. A resumed CLI catch-up-fires
the slot that passed while its previous process was dead — its last-fired memory dies with the
process — so under immediate deploy restarts every restart bought one spurious fire per recurring
cron (a real nightly fired ~21x in one merge-heavy day; each extra fire landed within ~1s of a
"reviving …" revive and was preceded by the fresh process's own startup records). The fix is the
UserPromptSubmit replay gate (_prompt_submit_hook): the DELIVERY moment durably records
(schedule identity, slot epoch) in the reg's cronDelivered map, and a fire recomputing an
already-recorded slot is blocked before the model runs. These tests drive the dispatch's repro
shape exactly: arm → deliver → simulate the restart (a fresh SdkSession over the same reg — the
boot re-arm) → the replay is refused; and the converse — armed but never delivered → still fires
after the restart. Synthetic fixtures; PRIVATE sid (the goal-store fixture rule)."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the load — the module resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_t211", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "7a7a7a7a-1111-4222-8333-000000000211"          # private synthetic sid — never the shared one
PROMPT = "nightly sweep of the notes-api test corpus"
CRON = "0 0 1 1 *"          # Jan 1 00:00 — its most recent slot is FIXED for the whole test run,
#                             so a delivery and its replay compute the same slot deterministically
CORRUPT = b'{"sid": "trunca'


def _epoch(y, mo, d, h=0, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, -1, -1, -1))


class CronPrevDue(unittest.TestCase):
    """The slot function is the gate's event identity — pin its truth table."""

    def test_nightly_before_and_after_the_slot(self):
        # 2026-03-10 is an arbitrary fixed date; cron 02:37 nightly
        self.assertEqual(sb.cron_prev_due("37 2 * * *", _epoch(2026, 3, 10, 12, 0)),
                         _epoch(2026, 3, 10, 2, 37))
        self.assertEqual(sb.cron_prev_due("37 2 * * *", _epoch(2026, 3, 10, 2, 36)),
                         _epoch(2026, 3, 9, 2, 37), "before today's slot → yesterday's")
        self.assertEqual(sb.cron_prev_due("37 2 * * *", _epoch(2026, 3, 10, 2, 37)),
                         _epoch(2026, 3, 10, 2, 37), "the due minute itself counts")

    def test_every_minute_floors_to_the_minute(self):
        now = _epoch(2026, 3, 10, 12, 5) + 42
        self.assertEqual(sb.cron_prev_due("* * * * *", now), _epoch(2026, 3, 10, 12, 5))

    def test_steps_ranges_lists(self):
        self.assertEqual(sb.cron_prev_due("*/15 * * * *", _epoch(2026, 3, 10, 12, 40)),
                         _epoch(2026, 3, 10, 12, 30))
        self.assertEqual(sb.cron_prev_due("5,35 9-17 * * *", _epoch(2026, 3, 10, 8, 0)),
                         _epoch(2026, 3, 9, 17, 35), "before the range opens → yesterday's last")

    def test_weekly_dow(self):
        # 2026-03-10 is a Tuesday; cron Monday 09:00 → Monday 2026-03-09
        self.assertEqual(sb.cron_prev_due("0 9 * * 1", _epoch(2026, 3, 10, 12, 0)),
                         _epoch(2026, 3, 9, 9, 0))
        self.assertEqual(sb.cron_prev_due("0 9 * * 7", _epoch(2026, 3, 10, 12, 0)),
                         _epoch(2026, 3, 8, 9, 0), "dow 7 is Sunday")

    def test_dom_dow_both_restricted_is_vixie_or(self):
        # Friday 2026-03-13: matches BOTH "the 13th" and "Friday"; from the 14th, the most
        # recent "13th OR Friday" hit is the 13th itself
        self.assertEqual(sb.cron_prev_due("0 0 13 * 5", _epoch(2026, 3, 14, 12, 0)),
                         _epoch(2026, 3, 13, 0, 0))
        # from Thursday the 12th, the previous FRIDAY (the 6th) beats the previous 13th (Feb 13)
        self.assertEqual(sb.cron_prev_due("0 0 13 * 5", _epoch(2026, 3, 12, 12, 0)),
                         _epoch(2026, 3, 6, 0, 0))

    def test_month_restriction_reaches_back(self):
        self.assertEqual(sb.cron_prev_due("0 0 1 1 *", _epoch(2026, 6, 1, 0, 0)),
                         _epoch(2026, 1, 1, 0, 0))

    def test_unparseable_and_impossible_shapes_return_none(self):
        for bad in ("", "x", "61 2 * * *", "1 2 3", "1 2 3 4 5 6", "5/ * * * *", "9-3 * * * *"):
            self.assertIsNone(sb.cron_prev_due(bad, time.time()), bad)
        self.assertIsNone(sb.cron_prev_due("0 0 30 2 *", time.time()),
                          "a never-matching date terminates via the lookback bound")


class _Gate(unittest.TestCase):
    """A real backend + session over a private reg; 'restart' = a FRESH SdkSession over the same
    reg (fresh procGen — exactly what boot re-arm/ensure_scheduled builds after a kernel death)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {
            "sid": SID, "name": "sched", "cwd": "/tmp", "alive": True,
            "sessionCrons": [{"id": "c1", "cron": CRON, "prompt": PROMPT, "kind": "cron",
                              "recurring": True, "armedAt": time.time() - 3600,
                              "dueEpoch": None, "procGen": "gen-A"}]})
        self._path = sb._reg_path(Path(self.d), SID)

    def _session(self):
        return sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))

    def _fire(self, sess, prompt=PROMPT):
        return asyncio.run(sess._prompt_submit_hook({"prompt": prompt}, None, None))

    def _reg(self):
        return sb.read_reg(Path(self.d), SID) or {}


class ReplayAcrossRestarts(_Gate):
    """The dispatch's required repro, red before the gate existed."""

    def test_delivered_slot_blocks_the_restart_replay(self):
        s1 = self._session()
        self.assertEqual(self._fire(s1), {}, "the first delivery of a slot goes through")
        slot = sb.cron_prev_due(CRON, time.time())
        key = sb.cron_slot_key(CRON, PROMPT)
        self.assertEqual(self._reg().get("cronDelivered"), {key: slot},
                         "delivery — not arming — writes the durable slot record")
        s2 = self._session()                       # the kernel restarted; boot re-arm resumed it
        out = self._fire(s2)                       # the fresh CLI catch-up re-fires the slot
        self.assertEqual(out.get("decision"), "block", "the replay is refused")
        self.assertIn("already ran", out.get("reason", ""))
        self.assertEqual(self._reg().get("cronDelivered"), {key: slot},
                         "a refused replay changes nothing")
        self.assertTrue(any("blocked a replayed schedule fire" in m for m in self.logs),
                        "the refusal is loud, never silent")

    def test_armed_but_undelivered_still_fires_after_the_restart(self):
        s2 = self._session()                       # died before ever delivering; resumed post-restart
        self.assertEqual(self._fire(s2), {},
                         "no delivery record → the catch-up fire is the recovery, not a replay")
        self.assertTrue(self._reg().get("cronDelivered"), "…and it records at that moment")

    def test_same_process_double_fire_is_also_blocked(self):
        s1 = self._session()
        self._fire(s1)
        self.assertEqual(self._fire(s1).get("decision"), "block",
                         "the key is the slot, not the process generation")

    def test_the_next_slot_is_not_a_replay(self):
        key = sb.cron_slot_key(CRON, PROMPT)
        prev_year_slot = _epoch(time.localtime().tm_year - 1, 1, 1)
        reg = self._reg()
        reg["cronDelivered"] = {key: prev_year_slot}
        sb.write_reg(Path(self.d), SID, reg)
        s2 = self._session()
        self.assertEqual(self._fire(s2), {}, "a NEWER slot due → delivery, not replay")
        self.assertEqual(self._reg()["cronDelivered"][key], sb.cron_prev_due(CRON, time.time()),
                         "the record advances to the delivered slot")


class GateScope(_Gate):
    """What the gate must NOT touch."""

    def test_non_matching_prompts_pass_untouched(self):
        s = self._session()
        self.assertEqual(self._fire(s, "an ordinary user message"), {})
        self.assertNotIn("cronDelivered", self._reg(), "no matching schedule → no write at all")

    def test_one_shots_never_engage_the_gate(self):
        reg = self._reg()
        reg["sessionCrons"] = [{"id": "w1", "cron": "", "prompt": PROMPT, "recurring": False,
                                "armedAt": time.time(), "dueEpoch": time.time() + 60,
                                "procGen": "gen-A", "src": "toolhook"}]
        sb.write_reg(Path(self.d), SID, reg)
        s = self._session()
        self.assertEqual(self._fire(s), {})
        self.assertNotIn("cronDelivered", self._reg(),
                         "one-shots are deliver_lost_wakeups' domain (strip-first, already safe)")

    def test_unparseable_schedule_stands_down_open(self):
        reg = self._reg()
        reg["sessionCrons"][0]["cron"] = "@daily"
        sb.write_reg(Path(self.d), SID, reg)
        s = self._session()
        self.assertEqual(self._fire(s), {}, "a shape romp can't reason about exactly → allow")
        self.assertNotIn("cronDelivered", self._reg())

    def test_long_prompts_match_on_the_recorded_500(self):
        long_prompt = "x" * 480 + PROMPT           # the reg stores prompt[:500]
        reg = self._reg()
        reg["sessionCrons"][0]["prompt"] = long_prompt[:500]
        sb.write_reg(Path(self.d), SID, reg)
        s1 = self._session()
        self.assertEqual(self._fire(s1, long_prompt), {})
        s2 = self._session()
        self.assertEqual(self._fire(s2, long_prompt).get("decision"), "block")

    def test_stale_keys_gc_with_the_armed_set(self):
        reg = self._reg()
        reg["cronDelivered"] = {"deadbeefdeadbeef": 12345.0}   # a schedule no longer armed
        sb.write_reg(Path(self.d), SID, reg)
        s = self._session()
        self._fire(s)
        self.assertNotIn("deadbeefdeadbeef", self._reg()["cronDelivered"],
                         "the map never outgrows the armed set + this delivery")


class FailsOpen(_Gate):
    """Uncertainty must cost a possible duplicate, never a swallowed slot."""

    def test_unreadable_reg_allows_and_says_so(self):
        s = self._session()
        self._path.write_bytes(CORRUPT)
        self.assertEqual(self._fire(s), {}, "fail OPEN")
        self.assertEqual(self._path.read_bytes(), CORRUPT, "…and never writes through the corruption")
        self.assertTrue(any("unreadable" in m for m in self.logs), "loudly")


if __name__ == "__main__":
    unittest.main()
