#!/usr/bin/env python3
"""T354: a kernel restart must not re-bill a hosted session's CLI lifetime. A host ATTACH keeps the CLI process, whose
total_cost_usd is cumulative; the kernel persists the session's cost watermark on its registry row at every result,
seeds from it at the attach when it names the surviving CLI, records nothing for a first result with no matching
watermark, and still records a fresh process's whole first total.

SYNTHETIC fixtures only: placeholder ids, an invented session, a fake host hello.
"""
import asyncio
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-00000000a354"


class _ResultMessage:
    subtype = "success"


class _AssistantMessage:
    pass


def _result(total, tokens_in):
    r = _ResultMessage()
    r.total_cost_usd = total
    r.model_usage = {"claude-x": {"inputTokens": tokens_in, "outputTokens": 0, "cacheReadInputTokens": 0,
                                  "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.0}}
    r.usage = {"input_tokens": 9999}
    return r


class Rebill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")   # this backend never connects; the pin is the suite's rule
        self.lines = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.lines.append(str(m)))
        self.be._forward = lambda sess, msg: None
        self.be._turn_completed = lambda sid: None
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})

    def _session(self, attach=False, hello_cli=("4242", "s1")):
        s = sb.SdkSession(self.be, {"sid": SID, "name": "web", "cwd": self.d})
        async def _noop(): pass
        s._do_refresh_context = _noop
        s._do_refresh_usage = _noop
        if attach:
            s._host_is_attach = True
            s._host = types.SimpleNamespace(hello={"host": {"pid": 1, "start": "h"}, "cli": {"pid": hello_cli[0], "start": hello_cli[1]}},
                                            ack_offset=0, exit_info=None, detach_mode=False)
        s._seed_spend_watermarks()                          # the connect-time step
        return s

    def _run(self, s, r):
        async def go():
            s._on_message(r, _AssistantMessage, _ResultMessage, type("S", (), {}))
            await asyncio.sleep(0)
        asyncio.run(go())

    def _day(self):
        p = Path(self.d, "spend.json")
        if not p.exists():
            return {}
        return json.loads(p.read_text())["days"].get(time.strftime("%Y-%m-%d"), {})

    def _turns(self):
        p = Path(self.d, "turns.jsonl")
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def _cost_state(self):
        return (sb.read_reg(Path(self.d), SID) or {}).get("costState")

    def test_a_fresh_process_seeds_zero_and_records_its_whole_first_total_and_persists_the_watermark(self):
        s = self._session()
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "fresh"))
        self._run(s, _result(3.5, 100))
        self.assertAlmostEqual(self._day()["usd"], 3.5, msg="a fresh process: the first total is the turn's own")
        cs = self._cost_state()
        self.assertEqual((cs["total"], cs["cli"], cs["tokens"]["input_tokens"]), (3.5, "", 100), "the watermark, on the registry, every result")
        self._run(s, _result(5.0, 160))
        self.assertAlmostEqual(self._day()["usd"], 5.0)
        self.assertEqual(self._cost_state()["total"], 5.0)
        rows = self._turns()
        self.assertEqual([r["cumulativeUsd"] for r in rows], [3.5, 5.0], "each turn row carries the CLI's cumulative total")
        self.assertEqual(rows[0].get("spendBaseline"), "fresh")
        self.assertNotIn("spendBaseline", rows[1], "only a first result names its baseline")

    def test_an_attach_to_the_surviving_cli_seeds_from_the_registry_and_records_one_turns_delta(self):
        # the previous kernel's last result left the watermark for CLI 4242:s1 at $500 (the staircase's cause was
        # seeding zero here: the lifetime's $512.50 would have been one turn)
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {"input_tokens": 90000}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True, hello_cli=("4242", "s1"))
        self.assertEqual((s._last_cost_total, s._spend_baseline), (0.0, "attach-pending"), "nothing folded until the hello names the CLI")
        self._run(s, _result(512.5, 90400))
        self.assertAlmostEqual(self._day()["usd"], 12.5, msg="only this turn's delta over the seeded watermark")
        self.assertEqual(self._day()["tokIn"], 400, "the token watermarks seed too")
        self.assertEqual(s._spend_baseline, "seeded")
        self.assertEqual(self._cost_state()["total"], 512.5)
        self.assertTrue(any("attached to its surviving CLI (4242:s1): watermarks seeded at the registry's cumulative $500.00" in l for l in self.lines), self.lines)
        self.assertEqual(self._turns()[0]["spendBaseline"], "seeded")
        self.assertEqual(self._turns()[0]["cumulativeUsd"], 512.5)
        self.assertEqual([r for r in self.be.problems() if "spend" in str(r.get("text") or "")], [], "an info line, never a problem row")

    def test_an_attach_with_no_matching_watermark_records_nothing_for_the_first_result_and_says_so(self):
        for cs in (None, {"total": 300.0, "tokens": {}, "cli": "other:proc", "t": 1}):
            sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})
            if cs:
                self.be._update_reg(SID, costState=cs)
            Path(self.d, "spend.json").unlink(missing_ok=True)
            Path(self.d, "turns.jsonl").unlink(missing_ok=True)
            self.lines.clear()
            s = self._session(attach=True, hello_cli=("4242", "s1"))
            self._run(s, _result(512.5, 90400))
            self.assertEqual(self._day(), {}, "the lifetime's total is not a turn: nothing folded (%r)" % cs)
            self.assertEqual(s._spend_baseline, "attach-unknown")
            self.assertTrue(any("attached to a surviving CLI (4242:s1) with no matching watermark on record" in l for l in self.lines), self.lines)
            self.assertTrue(any("this turn's own cost is unknowable and nothing was folded" in l for l in self.lines), self.lines)
            self.assertEqual(self._cost_state()["total"], 512.5, "the watermark is set from here…")
            self.assertEqual(self._cost_state()["cli"], "4242:s1")
            rows = self._turns()
            self.assertEqual((rows[0]["spendBaseline"], rows[0]["cumulativeUsd"], rows[0]["usd"]), ("attach-unknown", 512.5, 0.0))
            self._run(s, _result(520.0, 91000))
            self.assertAlmostEqual(self._day()["usd"], 7.5, msg="…so the next result records its own delta")
            self.assertEqual(self._day()["tokIn"], 600)

    def test_a_first_result_above_the_mark_names_which_process_it_was(self):
        self.be._update_reg(SID, costState={"total": 500.0, "tokens": {}, "cli": "4242:s1", "t": 1})
        s = self._session(attach=True)
        self._run(s, _result(900.0, 10))                    # a $400 turn over the seed: recorded, said as the attach case
        self.assertAlmostEqual(self._day()["usd"], 400.0)
        self.assertTrue(any("first result after a host attach cost $400.00" in l and "seeded from the registry at $500.00" in l for l in self.lines), self.lines)
        self.lines.clear()
        s2 = self._session()
        self._run(s2, _result(300.0, 10))                   # a fresh process's big first turn: the old text
        self.assertTrue(any("first result after connect cost $300.00" in l and "a fresh CLI process starts its cost at zero" in l for l in self.lines), self.lines)

    def test_the_seed_and_the_result_are_pinned_to_the_registry_road(self):
        import inspect
        seed = inspect.getsource(sb.SdkSession._seed_spend_watermarks)
        self.assertIn('self._spend_baseline = "attach-pending"', seed)
        self.assertIn("self._last_cost_total = 0.0   # a fresh CLI process starts its cumulative cost at zero", seed, "the fresh seed is unchanged")
        on = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn("self._seed_from_reg_cost_state()", on)
        self.assertIn("self._persist_cost_state(total)", on, "the watermark is written at every result")
        self.assertIn('costState={"total": float(total), "tokens": dict(self._last_usage_totals),', inspect.getsource(sb.SdkSession._persist_cost_state))


if __name__ == "__main__":
    unittest.main()
