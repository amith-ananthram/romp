#!/usr/bin/env python3
"""Click-to-cite (the user 2026-07-01): a feed card click that resolves to a LIVE goal node attaches a
`cite:{itemId,title}` to the chat `focus` message, so the chat seeds a dismissible composer citation chip.
_cite_for is the resolver; a cleared/missing node yields no chip (so a cleared card never re-opens on send).
Synthetic fixtures only (placeholder UUIDs / TESTHOST)."""
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_cite", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


class CiteFor(unittest.TestCase):
    def setUp(self):
        self._orig = km.jd.load_goals

    def tearDown(self):
        km.jd.load_goals = self._orig

    def _store(self, nodes):
        km.jd.load_goals = lambda fsid: {"nodes": nodes}

    def test_live_goal_node_resolves_to_a_citation(self):
        self._store({SID + ":g1": {"text": "Fix the compaction rendering"}})
        self.assertEqual(km._cite_for(SID + ":g1"),
                         {"itemId": SID + ":g1", "title": "Fix the compaction rendering"})

    def test_a_sub_goal_cites_itself_not_the_top(self):
        # granularity is free: clicking a sub-goal cites the SUB node's own text
        self._store({SID + ":g1": {"text": "Top goal"},
                     SID + ":g2": {"text": "A specific sub-goal", "parentId": SID + ":g1"}})
        self.assertEqual(km._cite_for(SID + ":g2")["title"], "A specific sub-goal")

    def test_cleared_goal_is_not_cited(self):
        # a cleared card is archived out of the live store's intent → no chip (and its follow-up would no-op)
        self._store({SID + ":g1": {"text": "Done and cleared", "cleared": True}})
        self.assertIsNone(km._cite_for(SID + ":g1"))

    def test_unknown_node_or_empty_id_is_not_cited(self):
        self._store({SID + ":g1": {"text": "exists"}})
        self.assertIsNone(km._cite_for(SID + ":gX"), "a node absent from the live store → None")
        self.assertIsNone(km._cite_for(""), "no itemId → None")

    def test_titleless_node_is_not_cited(self):
        self._store({SID + ":g1": {"text": "   "}})
        self.assertIsNone(km._cite_for(SID + ":g1"))


class ChatBodyHasChipStrip(unittest.TestCase):
    """The WEB dashboard's composer HTML is a hand-ported copy in _chat_body (separate from the VS Code
    extension's page-skeleton.ts). The citation chip renders into #composer-chips, so that element MUST be in
    the served HTML or renderComposerChips silently no-ops and no chip ever shows (the bug on 2026-07-01)."""

    def test_composer_has_the_citation_chip_strip(self):
        html = km._chat_body()
        self.assertIn('id="composer-chips"', html, "#composer-chips must exist for the chip to render")
        self.assertIn('id="composer-input"', html, "the strip sits alongside the textarea")


class ShowOnTimelineFocus(unittest.TestCase):
    def setUp(self):
        self._orig = km.jd.load_goals

    def tearDown(self):
        km.jd.load_goals = self._orig

    def test_focus_carries_the_citation_for_a_goal_click(self):
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {"text": "Cited goal"}}}
        f = km._show_on_timeline_focus({"sid": SID, "itemId": SID + ":g1", "t": 123, "anchor": "prompt"})
        self.assertEqual(f["type"], "focus")
        self.assertEqual(f["id"], SID)
        self.assertEqual(f["cite"], {"itemId": SID + ":g1", "title": "Cited goal"})

    def test_focus_has_no_cite_for_a_non_goal_target(self):
        km.jd.load_goals = lambda fsid: {"nodes": {}}
        f = km._show_on_timeline_focus({"sid": SID, "itemId": SID + ":reply7", "t": 123})
        self.assertNotIn("cite", f, "a target that isn't a live goal node seeds no chip")


class FollowupPreview(unittest.TestCase):
    """Clicking the composer chip fetches the EXACT prompt romp will send from GET /followup-preview, so the
    user can audit the injected context. The endpoint is _followup_body (the same builder the send path uses),
    so the preview can't drift from what's actually sent (the user 2026-07-01)."""

    def setUp(self):
        self._orig = km.jd.load_goals

    def tearDown(self):
        km.jd.load_goals = self._orig

    def test_preview_body_is_the_real_send_body(self):
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {"text": "Audit the citation flow"}}}
        body = km._followup_body(SID + ":g1", None, "does the context look right?")
        self.assertIn("> Audit the citation flow", body, "the injected goal-context quote is shown")
        self.assertIn("does the context look right?", body, "the user's draft is in place")
        self.assertIn("<!-- romp-goal-id: " + SID + ":g1 -->", body, "the hidden reopen marker is visible in the audit")

    def test_get_route_is_wired(self):
        import inspect
        src = inspect.getsource(km.Handler.do_GET)
        self.assertIn('p == "/followup-preview"', src)
        self.assertIn("_followup_body(iid, None, text", src, "built from the real send-path builder")

    def test_typed_followup_quotes_the_distilled_summary_not_the_minting_message(self):
        # the user 2026-07-04: a typed follow-up cites the card's HEADLINE (the distiller's takeaway), so the
        # context matches what you're reading + clicked — not your original minting message.
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {
            "text": "Ship the thing", "quote": "please ship the thing by friday",
            "summary": "Shipped v2 with the new flag defaulted on."}}}
        body = km._followup_body(SID + ":g1", None, "also update the changelog")
        self.assertIn("> Shipped v2 with the new flag defaulted on.", body, "quotes the distilled summary")
        self.assertNotIn("please ship the thing by friday", body, "not the original minting message")

    def test_blocked_card_quotes_its_decision_brief(self):
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {
            "text": "Pick a DB", "quote": "help me choose a database", "blocked": True,
            "blockSummary": "Postgres vs SQLite — need your call on scale."}}}
        body = km._followup_body(SID + ":g1", None, "go with Postgres")
        self.assertIn("> Postgres vs SQLite", body, "a blocked card quotes its decision brief")

    def test_no_summary_yet_falls_back_to_the_minting_quote(self):
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {
            "text": "Ship the thing", "quote": "please ship the thing by friday"}}}
        body = km._followup_body(SID + ":g1", None, "any update?")
        self.assertIn("> please ship the thing by friday", body, "no summary yet → the minting quote")

    def test_a_nudge_keeps_its_own_context_not_the_summary(self):
        # only TYPED follow-ups switch to the summary — and since 2026-07-24 a nudge (injected) quotes
        # the goal's TITLE form, not the minting message either: the raw mint head is often a truncated
        # mid-conversation fragment, and a nudge is romp speaking, not the user re-raising their thread.
        # The summary stays out regardless: a stale takeaway ("Shipped v2.") on a reopened goal would
        # tell the agent the work is done in the same breath as asking why it isn't.
        km.jd.load_goals = lambda fsid: {"nodes": {SID + ":g1": {
            "text": "Ship the thing", "quote": "please ship the thing by friday", "summary": "Shipped v2."}}}
        body = km._followup_body(SID + ":g1", None, "status?", injected=True)
        self.assertIn("> Ship the thing", body, "a nudge quotes the goal title form")
        self.assertNotIn("please ship the thing by friday", body, "never the raw minting fragment (2026-07-24)")
        self.assertNotIn("Shipped v2.", body)


class ClearDropsCitation(unittest.TestCase):
    """Clearing a card tells the chat to drop any composer citation chip pointing INTO it (the user
    2026-07-01): chips can cite a SUB-goal (wireNodeZones sends the clicked node's own id), so a single
    clear pushes dropCitation{itemId, itemIds: the card's whole subtree, read BEFORE the clear archives
    it}; Clear-all pushes dropCitationsAll."""

    def test_clear_handlers_push_drop_to_the_chat(self):
        with open(os.path.join(BIN, "romp-kernel")) as fh:
            src = fh.read()
        self.assertIn('_gone = _subtree_item_ids(str(msg["itemId"]))', src,
                      "the subtree is collected BEFORE _clear_ask archives it out of the live store")
        self.assertIn('_send_to_app("chat", {"type": "dropCitation", "itemId": str(msg["itemId"]), "itemIds": _gone})', src,
                      "a single askClear pushes dropCitation with the cleared card's whole subtree")
        self.assertIn('_send_to_app("chat", {"type": "dropCitationsAll"})', src,
                      "Clear-all pushes dropCitationsAll")

    def test_subtree_item_ids_walks_the_whole_card(self):
        import json, tempfile
        from pathlib import Path
        td = Path(tempfile.mkdtemp())
        saved = km.jd.GOALDIR
        km.jd.GOALDIR = td
        try:
            top, sub, subsub, other = SID + ":g1", SID + ":g2", SID + ":g3", SID + ":g9"
            (td / (SID + ".json")).write_text(json.dumps({"rompUuid": SID, "seq": 4, "nodes": {
                top: {"id": top, "text": "T", "parentId": None},
                sub: {"id": sub, "text": "S", "parentId": top},
                subsub: {"id": subsub, "text": "SS", "parentId": sub},
                other: {"id": other, "text": "O", "parentId": None}},
                "placements": {}, "status": {}}))
            got = km._subtree_item_ids(top)
            self.assertEqual(sorted(got), sorted([top, sub, subsub]),
                             "the whole subtree, and never an unrelated sibling card")
        finally:
            km.jd.GOALDIR = saved

    def test_subtree_item_ids_degrades_to_the_top_alone(self):
        self.assertEqual(km._subtree_item_ids("no-such-sid:g1"), ["no-such-sid:g1"],
                         "unreadable store → the top id alone, so a top-citing chip still drops")


if __name__ == "__main__":
    unittest.main()
