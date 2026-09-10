#!/usr/bin/env python3
"""OWED COVERAGE IS COUNTABLE (the user 2026-08-30). The briefer's contract writes one paragraph
per owed item, but the merge allowance ("several that come down to the SAME decision, write ONE
paragraph") made an OMITTED item indistinguishable from a merge: the live specimen rendered owed
decisions 1-3 whole and dropped the 4th entirely, and the user had to ask what was missing. The
standing TAKEAWAY spec is measured wording (see the note above BLOCK_BRIEF_SYS) and stays
untouched; the enforcement is kernel-side: fewer takeaway paragraphs than owed items is the
omission signal (more is fine — the trailing leftover paragraph), and it earns ONE corrective
retry whose per-call note overrides the merge allowance, then a deterministic fallback built
verbatim from the owed pairs — complete by construction, honest-plain over polished-lossy.
Countable only for a multi-item owed LIST; a single why naming several decisions inside its own
prose has no deterministic item count. SYNTHETIC fixtures only."""
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_owedcov", os.path.join(BIN, "romp-judge"))
em = jd.em

NOW = 1_787_800_000
T0 = NOW - 3600
SID = "f44e0002-1111-4222-8333-000000000001"    # private synthetic sid — never the shared placeholder


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _node(nid, text, parent, t=T0, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


WHYS = ["confirm the six-step rollout order or reorder it",
        "pick the scored labels for the size ladder",
        "set the download timing for the hardened instruments",
        "extend the read-view check now, or after the crossings"]

P3 = "BACKGROUND: b.\n\nTAKEAWAY: Decide the rollout order.\n\nPick the labels.\n\nSet the timing."
P4 = P3 + "\n\nDecide the read-view timing."


class OwedCoverage(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / (SID + ".jsonl")
        self.path.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed",
             "message": {"role": "user", "content": "please plan the capture rollout"}},
            {"type": "assistant", "timestamp": iso(T0 + 60), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text",
                                      "text": "Four decisions are open on the rollout plan; "
                                              "each is written up with its options."}]}}]) + "\n")
        self._brief = jd.brief_llm
        self.calls = []                                # [(owed, shortfall)]
        self.replies = []
        test = self

        def fake_brief(goal_text, work, owed, frame=None, user_ask=None, shortfall=None):
            test.calls.append((owed, shortfall))
            return test.replies[min(len(test.calls) - 1, len(test.replies) - 1)]
        jd.brief_llm = fake_brief

    def tearDown(self):
        jd.brief_llm = self._brief
        for d in (jd.GOALDIR, jd.GOALARCHDIR):
            try:
                (d / (SID + ".json")).unlink()
            except OSError:
                pass
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        self.td.cleanup()

    def _world(self, k=4):
        st = {"rompUuid": SID, "seq": 6, "nodes": {}, "placements": {}, "status": {}}
        st["nodes"]["g"] = jd.GuardedNode(_node("g", "plan the capture rollout", None))
        for i in range(k):
            nid = "c%d" % i
            st["nodes"][nid] = jd.GuardedNode(_node(nid, "decision %d" % (i + 1), "g",
                                                    t=T0 + i, mt=T0 + i))
            # blocks land as VERDICTS — a bare flag never rolls the top to blocked
            jd.record_verdict(st, st["nodes"][nid], "closer", "block", T0 + 100 + i, why=WHYS[i])
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        return st

    def _run(self):
        return jd._distill_session(SID, str(self.path), NOW)

    def _stored(self):
        return jd.load_goals(SID)["nodes"]["g"]

    def _errors(self):
        p = jd.STATE / "judge-errors.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_a_complete_brief_stores_in_one_call(self):
        self._world()
        self.replies = [P4]
        self._run()
        self.assertEqual(len(self.calls), 1, "four paragraphs for four items → no retry")
        self.assertIsNone(self.calls[0][1], "…and no shortfall note on the first call")
        nd = self._stored()
        self.assertIn("read-view timing", nd["blockSummary"])
        self.assertEqual(len(nd.get("briefParts") or []), 4)

    def test_a_dropped_item_earns_one_corrective_retry(self):
        # the specimen's shape: decisions 1-3 whole, the 4th omitted entirely
        self._world()
        self.replies = [P3, P4]
        self._run()
        self.assertEqual(len(self.calls), 2, "counted shortfall → exactly one retry")
        self.assertEqual(self.calls[1][1], (3, 4), "the retry names the counted shortfall")
        self.assertIn("read-view timing", self._stored()["blockSummary"],
                      "the retry's complete brief is the one stored")

    def test_a_still_short_retry_falls_back_to_the_verbatim_owed_list(self):
        self._world()
        self.replies = [P3, P3]
        self._run()
        self.assertEqual(len(self.calls), 2, "one retry, never a loop")
        bs = self._stored()["blockSummary"]
        for i, why in enumerate(WHYS):
            self.assertIn(why, bs, "the fallback carries every owed why verbatim — complete "
                                   "by construction, an item can never silently vanish")
            self.assertIn("%d. " % (i + 1), bs, "…in the numbered shape the standing rule renders")
        self.assertEqual(len([p for p in bs.split("\n\n") if p.strip()]), 4)
        rows = self._errors()
        self.assertIn("owed-shortfall", [r.get("err") for r in rows])
        self.assertNotIn("cite-miss", [r.get("err") for r in rows],
                         "romp authored the fallback text — a 'no SOURCE line' row would report "
                         "the stale first draft's tail (the adversarial pass's catch; the fixture "
                         "carries a labeled assistant reply so this guard is genuinely exercised)")

    def test_a_pause_skipped_retry_leaves_the_brief_null_for_the_next_pass(self):
        # the adversarial pass's catch: a retry the pause SKIPPED is not a short reply — counting
        # it as one stored the fallback and logged a retry that never ran. The standing pause
        # discipline holds: leave null, re-enter once the pause clears.
        self._world()
        test = self

        def fake_paused(goal_text, work, owed, frame=None, user_ask=None, shortfall=None):
            test.calls.append((owed, shortfall))
            if shortfall:
                jd._judge_ctx.paused = True
                return ""
            return P3
        jd.brief_llm = fake_paused
        try:
            self._run()
        finally:
            jd._judge_ctx.paused = False
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self._stored().get("blockSummary"),
                          "nothing stored off a skipped call — next pass re-runs the whole brief")

    def test_a_numbered_question_list_passes_the_count(self):
        # the decision-list rule and the shortfall guard reinforce each other: one numbered
        # single-line question per paragraph IS the countable shape — no spurious retry
        self._world()
        self.replies = ["BACKGROUND: b.\n\nTAKEAWAY: 1. Confirm the order?\n\n"
                        "2. Judge-score the labels?\n\n3. Download both now?\n\n"
                        "4. Extend the check now?"]
        self._run()
        self.assertEqual(len(self.calls), 1, "four numbered lines, four paragraphs — complete")
        self.assertEqual(len(self._stored().get("briefParts") or []), 4)

    def test_blankish_separators_count_as_the_feed_renders(self):
        # the kernel counts with the feed's own split (\n\s*\n): a reply whose paragraphs are
        # separated by a whitespace-bearing blank line is complete, not a shortfall
        self._world()
        self.replies = [P4.replace("\n\n", "\n \n")]
        self._run()
        self.assertEqual(len(self.calls), 1, "no spurious retry off a stricter split than the render")

    def test_a_single_item_owed_is_out_of_the_contract(self):
        self._world(k=1)
        self.replies = [P3]
        self._run()
        self.assertEqual(len(self.calls), 1, "a lone why has no deterministic item count — "
                                             "no retry however many paragraphs come back")
        self.assertIsNone(self.calls[0][1])


class ShortfallNote(unittest.TestCase):
    """The real brief_llm renders the corrective note — per-call, never a standing reword of the
    measured TAKEAWAY spec (the regression warning above BLOCK_BRIEF_SYS)."""

    def setUp(self):
        self.calls = {}
        self._saved = jd._judge_run

        def fake(model, sys_p, user, judge=None, tier=None, mark=None, **kw):
            self.calls.update(user=user)
            return "BACKGROUND: b\n\nTAKEAWAY: t"
        jd._judge_run = fake

    def tearDown(self):
        jd._judge_run = self._saved

    def test_the_note_names_the_count_and_overrides_the_merge_allowance(self):
        jd.brief_llm("g", "w", [("a", "why a"), ("b", "why b")], shortfall=(1, 2))
        self.assertIn("covered 1 of the 2 owed items", self.calls["user"])
        self.assertIn("exactly 2 numbered paragraphs", self.calls["user"])
        self.assertIn("yes/no", self.calls["user"],
                      "the retry converges on the numbered-question shape the standing rule asks for")
        self.assertIn("Even when items come down to the same decision", self.calls["user"],
                      "the override is per-call: the standing spec keeps its measured merge clause")

    def test_no_shortfall_no_note(self):
        jd.brief_llm("g", "w", [("a", "why a"), ("b", "why b")])
        self.assertNotIn("owed items in <owed>", self.calls["user"])


if __name__ == "__main__":
    unittest.main()
