#!/usr/bin/env python3
"""The grouper's split + retitle ops (the user 2026-07-14, the nimbus card): the grouper could merge
and nest cards but never split one, so a tangent that drifted into a card (a security audit inside
"Get the board connected") was ratcheted in forever, under a title naming only the first ask. Three
pieces pinned here:
  - parse + apply: split promotes an indented step (subtree and all) to its own top card, optionally
    retitled; retitle renames a top in place; both are guarded (split never on a top, retitle never on
    a step).
  - the gate: a ONE-card board used to skip the grouper entirely (len(tops) < 2); an overgrown card
    (>= GROUP_SPLIT_MIN open direct steps) now runs it, and that card's open-step set joins the
    groupedSig signature so new filings re-arm the pass while the top set alone is unchanged.
  - the prompt documents both ops.
Synthetic store only; group_llm is stubbed (never a live model call)."""
import os
import unittest
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_groupsplit", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
T0 = 1780000000


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _mk(s, text, parent=None, done=False, t=T0):
    s["seq"] = s.get("seq", 0) + 1
    nid = "%s:g%d" % (SID, s["seq"])
    s["nodes"][nid] = {"id": nid, "text": text, "parentId": parent, "nodeComplete": done,
                       "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    return nid


def _drifted_card(s, steps=8):
    """One card, `steps` open direct steps — the last one a drifted tangent with its own child."""
    card = _mk(s, "Get the board connected")
    for i in range(steps - 1):
        _mk(s, "board step %d" % i, parent=card, t=T0 + i)
    tangent = _mk(s, "Audit the Mac's network exposure", parent=card, t=T0 + 500)
    _mk(s, "killed the stray public server", parent=tangent, t=T0 + 510)
    return card, tangent


class ParseSplitRetitle(unittest.TestCase):
    def test_split_parses_with_and_without_retitle(self):
        ops = jd._parse_group('{"ops":[{"why":"own effort","do":"split","goal":3},'
                              '{"why":"own effort","do":"split","goal":4,"retitle":"Secure the Mac"}]}', 9)
        self.assertEqual(ops, [{"do": "split", "why": "own effort", "goal": 3},
                               {"do": "split", "why": "own effort", "goal": 4, "retitle": "Secure the Mac"}])

    def test_split_out_of_range_or_junk_retitle_handled(self):
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"split","goal":12}]}', 9), [])
        ops = jd._parse_group('{"ops":[{"why":"x","do":"split","goal":2,"retitle":"!!!"}]}', 9)
        self.assertEqual(ops, [{"do": "split", "why": "x", "goal": 2}], "no-alpha retitle is dropped")

    def test_retitle_parses_and_needs_real_text(self):
        ops = jd._parse_group('{"ops":[{"why":"outgrew it","do":"retitle","goal":1,'
                              '"text":"Board setup and network security"}]}', 9)
        self.assertEqual(ops, [{"do": "retitle", "why": "outgrew it", "goal": 1,
                                "text": "Board setup and network security"}])
        self.assertEqual(jd._parse_group('{"ops":[{"why":"x","do":"retitle","goal":1,"text":"…"}]}', 9), [])


class ApplySplitRetitle(unittest.TestCase):
    def test_split_promotes_the_step_with_its_subtree(self):
        s = _store()
        card, tangent = _drifted_card(s)
        menu = jd._group_menu(s, jd._group_tops(s))
        n = {nd["id"]: i for i, nd in enumerate(menu, 1)}
        applied = jd.apply_group(s, menu, [{"do": "split", "why": "its own effort",
                                            "goal": n[tangent], "retitle": "Secure the Mac's network"}], T0 + 900)
        self.assertEqual(applied, 1)
        self.assertIsNone(s["nodes"][tangent]["parentId"], "the tangent is a top card now")
        self.assertEqual(s["nodes"][tangent]["text"], "Secure the Mac's network")
        kid = next(nd for nd in s["nodes"].values() if nd["text"] == "killed the stray public server")
        self.assertEqual(kid["parentId"], tangent, "the subtree came with it")
        self.assertIsNone(s["nodes"][card]["parentId"], "the original card is untouched")

    def test_split_on_a_top_line_is_skipped(self):
        s = _store()
        _drifted_card(s)
        menu = jd._group_menu(s, jd._group_tops(s))
        self.assertEqual(jd.apply_group(s, menu, [{"do": "split", "why": "x", "goal": 1}], T0 + 900), 0)

    def test_retitle_renames_a_top_in_place(self):
        s = _store()
        card, tangent = _drifted_card(s)
        menu = jd._group_menu(s, jd._group_tops(s))
        applied = jd.apply_group(s, menu, [{"do": "retitle", "why": "the thread outgrew the title",
                                            "goal": 1, "text": "Board setup and network security"}], T0 + 900)
        self.assertEqual(applied, 1)
        self.assertEqual(s["nodes"][card]["text"], "Board setup and network security")

    def test_retitle_on_a_step_is_skipped(self):
        s = _store()
        card, tangent = _drifted_card(s)
        menu = jd._group_menu(s, jd._group_tops(s))
        n = {nd["id"]: i for i, nd in enumerate(menu, 1)}
        applied = jd.apply_group(s, menu, [{"do": "retitle", "why": "x", "goal": n[tangent],
                                            "text": "hijack a step title"}], T0 + 900)
        self.assertEqual(applied, 0)
        self.assertEqual(s["nodes"][tangent]["text"], "Audit the Mac's network exposure")


class OvergrownGate(unittest.TestCase):
    """A one-card board runs the grouper only when the card is overgrown, and re-arms as it grows."""

    def setUp(self):
        self._saved = jd.group_llm
        self.calls = []

    def tearDown(self):
        jd.group_llm = self._saved

    def _stub(self, reply='{"ops": []}'):
        def f(menu_text, judge="grouper"):
            self.calls.append(menu_text)
            return reply
        jd.group_llm = f

    def test_small_single_card_never_calls_the_model(self):
        self._stub()
        s = _store()
        card = _mk(s, "small card")
        for i in range(jd.GROUP_SPLIT_MIN - 2):
            _mk(s, "step %d" % i, parent=card)
        self.assertEqual(jd._group_store(s, SID, T0 + 900), 0)
        self.assertEqual(self.calls, [], "under the threshold the old <2-tops skip holds")

    def test_overgrown_single_card_reaches_the_model_and_regates(self):
        self._stub()
        s = _store()
        _drifted_card(s, steps=jd.GROUP_SPLIT_MIN)
        jd._group_store(s, SID, T0 + 900)
        self.assertEqual(len(self.calls), 1, "an overgrown one-card board runs the grouper")
        jd._group_store(s, SID, T0 + 901)
        self.assertEqual(len(self.calls), 1, "unchanged signature → gated, no re-ask")

    def test_new_filing_under_the_overgrown_card_rearms(self):
        self._stub()
        s = _store()
        card, _ = _drifted_card(s, steps=jd.GROUP_SPLIT_MIN)
        jd._group_store(s, SID, T0 + 900)
        _mk(s, "another drifted step", parent=card, t=T0 + 950)
        jd._group_store(s, SID, T0 + 960)
        self.assertEqual(len(self.calls), 2, "the open-step set is part of the signature")

    def test_split_applies_end_to_end_and_snapshots_the_new_sig(self):
        s = _store()
        card, tangent = _drifted_card(s, steps=jd.GROUP_SPLIT_MIN)
        menu = jd._group_menu(s, jd._group_tops(s))
        n = {nd["id"]: i for i, nd in enumerate(menu, 1)}
        self._stub('{"ops":[{"why":"its own effort","do":"split","goal":%d,'
                   '"retitle":"Secure the Mac"}]}' % n[tangent])
        applied = jd._group_store(s, SID, T0 + 900)
        self.assertEqual(applied, 1)
        self.assertIsNone(s["nodes"][tangent]["parentId"])
        self.assertIn(tangent, s["groupedSig"], "the promoted card joined the top-set snapshot")
        jd._group_store(s, SID, T0 + 901)
        self.assertEqual(len(self.calls), 1, "post-split signature is current — no immediate re-ask")


class OvergrownCountsDoneChildren(unittest.TestCase):
    """The overgrown threshold counts DONE direct steps too (the user 2026-07-17, quartz): a
    cache-fix umbrella accreted 13 children — the whole campaign — but only 5 were open, so the
    open-only count never re-armed the pass and the outgrown title stuck. A big mostly-done card is
    exactly when a retitle is due; the signature's step set stays OPEN-only, so a step completing
    flips it and re-arms the pass."""

    def setUp(self):
        self._saved = jd.group_llm
        self.calls = []

    def tearDown(self):
        jd.group_llm = self._saved

    def _stub(self, reply='{"ops": []}'):
        def f(menu_text, judge="grouper"):
            self.calls.append(menu_text)
            return reply
        jd.group_llm = f

    def _accreted_card(self, s, done, open_):
        card = _mk(s, "Fix the cache-size detection")
        for i in range(done):
            _mk(s, "settled step %d" % i, parent=card, done=True, t=T0 + i)
        for i in range(open_):
            _mk(s, "live step %d" % i, parent=card, t=T0 + 100 + i)
        return card

    def test_mostly_done_card_is_overgrown_despite_few_open_steps(self):
        s = _store()
        card = self._accreted_card(s, done=jd.GROUP_SPLIT_MIN - 2, open_=2)
        over = jd._overgrown_tops(s, jd._group_tops(s))
        self.assertIn(card, over, "done + open direct steps together cross the threshold")
        self.assertEqual(len(over[card]), 2, "…but the signature's step set stays open-only")

    def test_mostly_done_single_card_reaches_the_model(self):
        self._stub()
        s = _store()
        self._accreted_card(s, done=jd.GROUP_SPLIT_MIN - 2, open_=2)
        jd._group_store(s, SID, T0 + 900)
        self.assertEqual(len(self.calls), 1, "a big mostly-done one-card board runs the grouper (retitle chance)")

    def test_a_step_completing_rearms_the_gate(self):
        self._stub()
        s = _store()
        card = self._accreted_card(s, done=jd.GROUP_SPLIT_MIN - 2, open_=2)
        jd._group_store(s, SID, T0 + 900)
        open_step = next(nid for nid, nd in s["nodes"].items()
                         if nd.get("parentId") == card and not nd["nodeComplete"])
        s["nodes"][open_step]["nodeComplete"] = True   # the accretion event: another piece settled
        jd._group_store(s, SID, T0 + 960)
        self.assertEqual(len(self.calls), 2, "a completion changes the open-step set → the pass re-arms")


class PromptPins(unittest.TestCase):
    def test_group_sys_documents_split_and_retitle(self):
        flat = jd.GROUP_SYS.replace("\n", " ")
        for phrase in ("the inverse of group", "promote indented step",
                       "different effort", "Split sparingly",
                       "replace top", "no longer covers what the thread inside it became"):
            self.assertIn(phrase, flat, phrase)

    def test_group_sys_tells_the_grouper_to_retitle_a_receiving_top(self):
        # the user 2026-07-17 (quartz): grouping reinforced a narrow-titled umbrella over a whole
        # campaign and never renamed it — the prompt now says to reread the RECEIVER's title too.
        flat = jd.GROUP_SYS.replace("\n", " ")
        for phrase in ("This applies to a card that **receives** work too",
                       "reread ", "retitle #m to the outcome that covers its steps"):
            self.assertIn(phrase, flat, phrase)


if __name__ == "__main__":
    unittest.main()
