#!/usr/bin/env python3
"""A card appears only for work that traces to something the user asked for (T319, the user 2026-09-10,
whose feed filled with cards titled after the workflows their sessions ran on their own: adversarial-review
rounds, pull-request audits, guard reviews, each attributed to the authoring session and leading nowhere the
user had asked to go). The planner had minted those as top-level goals off the session's narration of its
own process. The rule at minting time is EVENT FIRST, TEXT SECOND: in a segment whose trigger is not a human
ask, every top mint demotes to a step under the goal the turn ran in; in a human-triggered segment a mint
demotes when the segment's assistant turns started background work (an Agent/Task/Workflow tool_use, the
event model's own launch record), whatever the words; token overlap only picks which launch supplies the
step's why and, with no goal the turn ran in, which open goal is the parent. Demoted steps carry
born {kind: session, via, why}. With nothing the user asked for to nest under, the mint files nothing.

Synthetic fixtures only: placeholder uuids, invented workflow names, an invented project."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_session_started", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600
ASK = "Add retries to the notes-api client so a flaky network does not drop a note"
WF_SCRIPT = ("export const meta = { name: 'review-notes-api-retry', description: 'Lens reviewers over the retry diff, "
             "each finding verified' }\nconst r = await agent('review')\nreturn r")
WF_UNRELATED = "export const meta = { name: 'orchard-count', description: 'Count the apples in the orchard by row' }\nreturn 1"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn", launch=None):
    content = [{"type": "text", "text": text}]
    if launch:
        content.append({"type": "tool_use", "id": "toolu_" + uuid, "name": launch[0], "input": launch[1]})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": content, "stop_reason": stop}}


def tresult(t, uuid, parent, tool_use_id, text="Workflow launched in background. Task ID: w1"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "toolUseResult": {"isAsync": True, "status": "async_launched", "workflowName": "review-notes-api-retry"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}


PLACE_ASK = '{"ops":[{"why":"the user asked for retries","do":"mint","text":"Add retries to the notes-api client"}]}'
# the planner's realistic reply for an ENDED segment planned in one work-run: the ask's placement first, the
# step under it, then the review as what the prompt calls a distinct deliverable
REVIEW_AS_TOP = ('{"ops":[{"why":"the user asked for retries","do":"mint","text":"Add retries to the notes-api client"},'
                 '{"why":"progress on the retries","do":"sub","ref":1,"text":"Wrote the retry loop"},'
                 '{"why":"a distinct review deliverable","do":"mint","text":"Adversarial review of the retry diff"}]}')
# ...and for a segment whose ask the prompt-run already placed (the live case): the step and the review only
REVIEW_LATER = ('{"ops":[{"why":"progress on the retries","do":"sub","under":1,"text":"Wrote the retry loop"},'
                '{"why":"a distinct review deliverable","do":"mint","text":"Adversarial review of the retry diff"}]}')


SKIP = '{"ops":[{"why":"skip","do":"skip"}]}'


class _Harness(unittest.TestCase):
    def _run(self, records, replies):
        """replies: [(needle, reply), ...]: the planner (and the opener) answer by what the call's text carries,
        the first matching needle wins; a call matching none is a skip."""
        calls = []
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            def fake(text, *a, **k):
                calls.append(text)
                for needle, reply in replies:
                    if needle in text:
                        return reply
                return SKIP
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            return calls, store

    @staticmethod
    def _tops(store):
        return [nd for nd in store["nodes"].values() if nd.get("parentId") is None]

    @staticmethod
    def _by_text(store, frag):
        return next((nd for nd in store["nodes"].values() if frag.lower() in (nd.get("text") or "").lower()), None)


class WorkflowMidGoal(_Harness):
    def _records(self, script=WF_SCRIPT):
        return [
            uline(T0, ASK, "u1"),
            aline(T0 + 60, "Adding the retry loop now.", "a1", "u1", stop="tool_use",
                  launch=("Workflow", {"script": script, "description": "ignored by the tool"})),
            tresult(T0 + 61, "u2", "a1", "toolu_a1"),
            aline(T0 + 400, "Wrote the retry loop and launched an adversarial review workflow over the diff; its findings come back in the background.", "a2", "u2"),
            uline(T0 + 900, "thanks, carry on", "u3", "a2"),
            aline(T0 + 910, "Carrying on.", "a3", "u3"),
        ]

    def test_a_workflow_the_session_started_nests_under_the_ask_with_its_why(self):
        calls, store = self._run(self._records(), [("adversarial review workflow", REVIEW_AS_TOP), (ASK[:40], PLACE_ASK)])
        tops = self._tops(store)
        self.assertEqual([nd["text"] for nd in tops], ["Add retries to the notes-api client"], "no new top for the session's own review")
        self.assertIsNone(tops[0].get("born"), "the ask's own mint is the user's")
        step = self._by_text(store, "Wrote the retry loop")
        self.assertEqual(step["parentId"], tops[0]["id"], "the same-reply ref still lands on the ask")
        rev = self._by_text(store, "Adversarial review")
        self.assertIsNotNone(rev, "the review still exists, as a step")
        self.assertEqual(rev["parentId"], tops[0]["id"], "under the goal the turn ran in")
        self.assertEqual(rev["born"]["parentText"], "Add retries to the notes-api client", "the face can name the request even if the parent goes")
        self.assertEqual(rev["born"]["kind"], "session")
        self.assertEqual(rev["born"]["via"], "workflow")
        self.assertIn("Lens reviewers over the retry diff", rev["born"]["why"], "the launch's own words supply the why")
        self.assertIn("workflow", rev["born"]["why"])

    def test_the_live_case_places_the_ask_first_and_nests_everything_the_work_run_mints(self):
        # pass 1: the turn is open (the launch went out, no end yet) → the prompt-run places the ask alone
        open_recs = self._records()[:3]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            saved = (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE = td / "goals", td / "pcache"
            calls = []
            def fake(text, *a, **k):
                calls.append(text)
                if "adversarial review workflow" in text:
                    return REVIEW_LATER
                return PLACE_ASK if ASK[:40] in text else SKIP
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            try:
                tpath.write_text("\n".join(json.dumps(r) for r in open_recs) + "\n")
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                mid = jd.load_goals(SID)
                self.assertEqual([nd["text"] for nd in self._tops(mid)], ["Add retries to the notes-api client"], "placed on landing")
                # pass 2: the turn ended with the review narration → the work-run's mint nests under the placed ask
                tpath.write_text("\n".join(json.dumps(r) for r in self._records()) + "\n")
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
        tops = self._tops(store)
        self.assertEqual([nd["text"] for nd in tops], ["Add retries to the notes-api client"])
        rev = self._by_text(store, "Adversarial review")
        self.assertIsNotNone(rev)
        self.assertEqual(rev["parentId"], tops[0]["id"])
        self.assertEqual(rev["born"]["via"], "workflow")

    def test_in_a_human_segment_a_mint_matching_no_launch_keeps_its_card(self):
        # the manager's second ruling: in a human-triggered segment only a mint that matches a launch (its words in
        # the mint's text or why) is the session's process; a mint like no launch may be a second thing the user
        # asked for, so it stays a card (the trigger-less rule below still demotes everything)
        calls, store = self._run(self._records(script=WF_UNRELATED), [("adversarial review workflow", REVIEW_AS_TOP), (ASK[:40], PLACE_ASK)])
        self.assertEqual(sorted(nd["text"] for nd in self._tops(store)),
                         ["Add retries to the notes-api client", "Adversarial review of the retry diff"])
        self.assertIsNone(self._by_text(store, "Adversarial review").get("born"))

    def test_the_asks_own_mint_is_chosen_by_its_words_not_its_position(self):
        # a review-first reply: the mint nearest the user's words is the ask and keeps the card; the review nests
        swapped = ('{"ops":[{"why":"a distinct review deliverable","do":"mint","text":"Adversarial review of the retry diff"},'
                   '{"why":"the user asked for retries","do":"mint","text":"Add retries to the notes-api client"},'
                   '{"why":"progress on the retries","do":"sub","ref":2,"text":"Wrote the retry loop"}]}')
        calls, store = self._run(self._records(), [("adversarial review workflow", swapped), (ASK[:40], PLACE_ASK)])
        tops = self._tops(store)
        self.assertEqual([nd["text"] for nd in tops], ["Add retries to the notes-api client"])
        rev = self._by_text(store, "Adversarial review")
        self.assertEqual(rev["parentId"], tops[0]["id"])
        self.assertEqual(rev["born"]["via"], "workflow")
        step = self._by_text(store, "Wrote the retry loop")
        self.assertEqual(step["parentId"], tops[0]["id"], "the same-reply ref followed the ask's remapped position")

    def test_the_planners_ask_mark_wins_over_words_a_paraphrased_title_keeps_the_card(self):
        # the planner marks the ask's mint ("ask": true); a paraphrased title shares no word with the user's message,
        # and the review-first order would once have crowned the review. The mark crowns the paraphrase.
        marked = ('{"ops":[{"why":"a distinct review deliverable","do":"mint","text":"Adversarial review of the retry diff"},'
                  '{"why":"the user asked for resilience","do":"mint","ask":true,"text":"Make the client resilient to flaky networks"}]}')
        calls, store = self._run(self._records(), [("adversarial review workflow", marked), (ASK[:40], PLACE_ASK)])
        tops = self._tops(store)
        self.assertEqual([nd["text"] for nd in tops], ["Make the client resilient to flaky networks"])
        rev = self._by_text(store, "Adversarial review")
        self.assertEqual(rev["parentId"], tops[0]["id"])
        self.assertEqual(rev["born"]["parentText"], "Make the client resilient to flaky networks")

    def test_a_launch_favoured_mint_is_never_crowned_even_when_marked_or_tied(self):
        # every mint shares no word with the user's message (a tie); the review's words fit the launch, so it is
        # never the ask, whatever its position or a stray mark; the other mint is crowned
        tied = ('{"ops":[{"why":"a distinct review deliverable","do":"mint","ask":true,"text":"Adversarial review of the retry diff"},'
                '{"why":"the user asked for resilience","do":"mint","text":"Make the client resilient to flaky networks"}]}')
        calls, store = self._run(self._records(), [("adversarial review workflow", tied), (ASK[:40], PLACE_ASK)])
        tops = self._tops(store)
        self.assertEqual([nd["text"] for nd in tops], ["Make the client resilient to flaky networks"])
        self.assertEqual(self._by_text(store, "Adversarial review")["parentId"], tops[0]["id"])

    def test_the_parser_carries_the_ask_mark_and_the_prompt_asks_for_it(self):
        ops = jd._parse_plan('{"ops":[{"why":"w","do":"mint","ask":true,"text":"Add retries"},{"why":"w","do":"mint","text":"Other"}]}', 0)
        self.assertEqual([o.get("ask") for o in ops], [True, None])
        self.assertIn('Put "ask": true on the one mint that is the deliverable the user\'s own message asked for', jd.PLAN_SYS)

    def test_a_two_ask_message_with_a_launch_keeps_both_asks_and_nests_the_review(self):
        records = [
            uline(T0, ASK + " and also write me a comparison of the two retry libraries", "u1"),
            aline(T0 + 60, "Adding the retry loop now.", "a1", "u1", stop="tool_use", launch=("Workflow", {"script": WF_SCRIPT})),
            tresult(T0 + 61, "u2", "a1", "toolu_a1"),
            aline(T0 + 400, "Wrote the retry loop, the comparison, and launched an adversarial review workflow over the diff.", "a2", "u2"),
        ]
        three = ('{"ops":[{"why":"the user asked for retries","do":"mint","text":"Add retries to the notes-api client"},'
                 '{"why":"a write-up the user asked to read","do":"mint","text":"Compare the two retry libraries"},'
                 '{"why":"a distinct review deliverable","do":"mint","text":"Adversarial review of the retry diff"}]}')
        calls, store = self._run(records, [("adversarial review workflow", three)])
        self.assertEqual(sorted(nd["text"] for nd in self._tops(store)),
                         ["Add retries to the notes-api client", "Compare the two retry libraries"], "two asks, two cards")
        rev = self._by_text(store, "Adversarial review")
        self.assertEqual(rev["parentId"], self._by_text(store, "Add retries")["id"])
        self.assertIsNone(self._by_text(store, "Compare the two").get("born"))

    def test_the_planner_prompt_names_the_rule_in_one_sentence(self):
        self.assertIn("The session's own background workflows, review rounds and agents are its process, not deliverables: "
                      "file them as steps under the goal they serve, never as a new top-level goal.", jd.PLAN_SYS)

    def test_a_harness_report_is_not_what_the_user_asked_in_the_text_the_judges_read(self):
        # the completion notification folds into the unit as a report, never under USER ASKED (the synthesis's
        # route b: the planner read the notification's summary as the ask and minted the review as a deliverable)
        atoms = [
            {"type": "user", "author": "human", "message": {"content": [{"type": "text", "text": ASK}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Wrote the retry loop."}]}},
            {"type": "user", "author": "system", "message": {"content": [{"type": "text", "text": "<task-notification><summary>Workflow review-notes-api-retry completed</summary></task-notification>"}]}},
        ]
        text = jd._unit_text(atoms)
        self.assertIn("USER ASKED: " + ASK, text)
        self.assertNotIn("review-notes-api-retry", text.split("\n")[0], "the notification is not part of the ask line")
        self.assertIn("BACKGROUND REPORTED (not the user): <task-notification>", text)

    def test_a_foreground_agent_is_no_launch_so_a_second_ask_keeps_its_card(self):
        # the turn WAITED on an Explore agent (no run_in_background, a synchronous result): the event model records
        # no task for it, and neither does the rule; the user's second ask in the same message stays a card
        records = [
            uline(T0, ASK + " and also write me a comparison of the two retry libraries", "u1"),
            aline(T0 + 60, "Reading the client first.", "a1", "u1", stop="tool_use",
                  launch=("Agent", {"description": "Explore the retry code", "prompt": "Read the client"})),
            {"type": "user", "timestamp": iso(T0 + 120), "uuid": "u2", "parentUuid": "a1",
             "toolUseResult": {"status": "completed"},
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_a1", "content": "the client has no retry"}]}},
            aline(T0 + 400, "Wrote the retry loop and the comparison of the two libraries.", "a2", "u2"),
        ]
        two = ('{"ops":[{"why":"the user asked for retries","do":"mint","text":"Add retries to the notes-api client"},'
               '{"why":"a write-up the user asked to read","do":"mint","text":"Compare the two retry libraries"}]}')
        calls, store = self._run(records, [("comparison of the two", two)])
        self.assertEqual(sorted(nd["text"] for nd in self._tops(store)),
                         ["Add retries to the notes-api client", "Compare the two retry libraries"], "two asks, two cards")
        self.assertTrue(all(nd.get("born") is None for nd in store["nodes"].values()))

    def test_a_human_segment_without_a_launch_still_mints_its_top(self):
        # the user's own second ask in a turn with no background launch keeps the old behaviour: a new top
        records = [
            uline(T0, ASK, "u1"),
            aline(T0 + 60, "Wrote the retry loop.", "a1", "u1"),
            uline(T0 + 900, "also write me a comparison of the two retry libraries", "u2", "a1"),
            aline(T0 + 950, "Here is the comparison of the two libraries.", "a2", "u2"),
        ]
        calls, store = self._run(records, [("comparison of the two", '{"ops":[{"why":"a write-up the user asked to read","do":"mint","text":"Compare the two retry libraries"}]}'), (ASK[:40], PLACE_ASK)])
        self.assertEqual(len(self._tops(store)), 2, "a real second ask is a second card")
        cmp_ = self._by_text(store, "Compare the two")
        self.assertIsNone(cmp_.get("born"), "a user's ask is not session-started")


class TriggerlessSegments(unittest.TestCase):
    """A segment with no human trigger: the seam tail a completion notification wakes (seamOf names the settled
    top), or an autonomous stretch. The trigger is the event: every mint demotes, no launch needed."""

    def _store(self):
        t1, t2 = SID + ":g1", SID + ":g2"
        return {"rompUuid": SID, "seq": 2, "placements": {}, "nodes": {
            t1: {"id": t1, "text": "Add retries to the notes-api client", "parentId": None, "nodeComplete": False, "cleared": False, "t": T0, "promptUuid": "u1"},
            t2: {"id": t2, "text": "Rename the widget colours", "parentId": None, "nodeComplete": False, "cleared": False, "t": T0 + 100, "promptUuid": "u9"}}}

    def _menu(self, store):
        return [{"id": nid} for nid in store["nodes"]]

    MINT = {"do": "mint", "why": "a nightly review round", "text": "Guard review of the notes-api retry loop"}

    def test_a_seam_tail_nests_under_the_top_it_ran_in(self):
        store = self._store()
        seg = {"id": "s1", "trigger": None, "atoms": [], "seamOf": {"top": SID + ":g2", "text": "..."}}
        ops = jd._demote_session_mints([dict(self.MINT), {"do": "skip", "why": ""}], seg, store, self._menu(store), None, False)
        self.assertEqual([o["do"] for o in ops], ["sub", "skip"])
        self.assertEqual(ops[0]["parentId"], SID + ":g2", "the seam's own top, whatever the words say")
        self.assertEqual(ops[0]["born"], {"kind": "session", "via": "work", "why": "a nightly review round", "parentText": "Rename the widget colours"})

    def test_without_a_placement_the_open_top_nearest_in_words_is_the_parent(self):
        store = self._store()
        seg = {"id": "s1", "trigger": None, "atoms": []}
        ops = jd._demote_session_mints([dict(self.MINT)], seg, store, self._menu(store), None, False)
        self.assertEqual(ops[0]["parentId"], SID + ":g1", "retries and notes-api: the retry goal")

    def test_a_launch_in_the_segment_supplies_the_why_and_picks_the_parent(self):
        store = self._store()
        seg = {"id": "s1", "trigger": None, "atoms": [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Workflow", "input": {"script": "export const meta = { name: 'colour-audit', description: 'Audit the widget colours rename' }"}}]}}]}
        ops = jd._demote_session_mints([dict(self.MINT)], seg, store, self._menu(store), None, False)
        self.assertEqual(ops[0]["parentId"], SID + ":g2", "the launch's words pick the parent among open tops")
        self.assertEqual(ops[0]["born"]["via"], "workflow")
        self.assertIn("Audit the widget colours rename", ops[0]["born"]["why"])

    def test_with_nothing_the_user_asked_for_the_mint_files_nothing(self):
        store = {"rompUuid": SID, "seq": 0, "placements": {}, "nodes": {}}
        seg = {"id": "s1", "trigger": None, "atoms": []}
        self.assertEqual(jd._demote_session_mints([dict(self.MINT)], seg, store, [], None, False), [], "no card, no node")
        # ...and the ops chained onto the dropped mint go with it: a sub by ref would otherwise land as a top
        chained = [dict(self.MINT), {"do": "sub", "ref": 1, "why": "ran it", "text": "Ran the lens reviewers"}, {"do": "done", "ref": 1, "why": "finished"}]
        self.assertEqual(jd._demote_session_mints(chained, seg, store, [], None, False), [])

    def test_a_demoted_mint_keeps_its_refs_and_a_later_mint_shifts_none(self):
        store = self._store()
        seg = {"id": "s1", "trigger": None, "atoms": [], "seamOf": {"top": SID + ":g2", "text": "..."}}
        ops = [dict(self.MINT), {"do": "sub", "ref": 1, "why": "ran it", "text": "Ran the lens reviewers"},
               {"do": "done", "ref": 1, "why": "finished"}, {"do": "sub", "under": 1, "why": "aside", "text": "Noted a flake"}]
        got = jd._demote_session_mints(ops, seg, store, self._menu(store), None, False)
        self.assertEqual([o["do"] for o in got], ["sub", "sub", "done", "sub"])
        self.assertEqual(got[0]["parentId"], SID + ":g2")
        self.assertEqual(got[0]["born"]["parentText"], "Rename the widget colours")
        self.assertEqual(got[1]["ref"], 1, "the demoted mint still created node 1")
        self.assertEqual(got[2]["ref"], 1)
        self.assertEqual(got[3]["under"], 1, "a menu-targeted sub is untouched")

    def test_a_scheduled_prompt_is_the_users_and_its_mints_are_never_demoted(self):
        store = self._store()
        seg = {"id": "s1", "trigger": "c1", "atoms": [
            {"type": "user", "uuid": "c1", "author": "sdk", "message": {"content": [{"type": "text", "text": "[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT] nightly review"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Workflow", "input": {"script": "export const meta = { name: 'nightly', description: 'Nightly guard review' }"}}]}}]}
        ops = [dict(self.MINT)]
        self.assertEqual(jd._demote_session_mints(ops, seg, store, self._menu(store), None, False), ops, "user-chained: kept as a top, never dropped")

    def test_a_trigger_less_segment_demotes_even_a_mint_unlike_its_launch(self):
        # the trigger is the event: no launch words needed; the unrelated launch still supplies the why
        store = self._store()
        seg = {"id": "s1", "trigger": None, "atoms": [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Workflow", "input": {"script": WF_UNRELATED}}]}}], "seamOf": {"top": SID + ":g1", "text": "..."}}
        ops = jd._demote_session_mints([dict(self.MINT)], seg, store, self._menu(store), None, False)
        self.assertEqual(ops[0]["do"], "sub"); self.assertEqual(ops[0]["parentId"], SID + ":g1")
        self.assertIn("Count the apples", ops[0]["born"]["why"])

    def test_a_demoted_mint_that_lands_on_a_twin_keeps_every_later_ref_in_place(self):
        # the demoted step's title matches an open sibling under the parent: apply_plan lands on the twin (no new node)
        # and the reply's created positions must still hold, so `sub ref 1` nests under the twin, never as a fresh top
        store = self._store()
        parent = SID + ":g1"; twin = SID + ":g5"
        store["nodes"][twin] = {"id": twin, "text": "Guard review of the notes-api retry loop", "parentId": parent, "nodeComplete": False, "cleared": False, "t": T0 + 50}
        ops = [{"do": "sub", "parentId": parent, "why": "round two", "text": "Guard review of the notes-api retry loop", "born": {"kind": "session", "via": "work", "why": "round two"}},
               {"do": "sub", "ref": 1, "why": "ran it", "text": "Ran the lens reviewers"}]
        jd.apply_plan(store, "s9", T0 + 900, ops, self._menu(store), prompt_uuid=None, quote=None)
        tops = [nd for nd in store["nodes"].values() if nd.get("parentId") is None]
        self.assertEqual(len(tops), 2, "no fresh top")
        ran = next(nd for nd in store["nodes"].values() if nd["text"] == "Ran the lens reviewers")
        self.assertEqual(ran["parentId"], twin, "the ref followed the twin the demoted step landed on")

    def test_a_human_segment_without_a_launch_is_left_alone(self):
        store = self._store()
        seg = {"id": "s1", "trigger": "u1", "atoms": []}
        ops = [dict(self.MINT)]
        self.assertEqual(jd._demote_session_mints(ops, seg, store, self._menu(store), None, True), ops)

    def test_a_notification_never_roots_a_mint(self):
        seg = {"trigger": "n1", "atoms": [
            {"type": "user", "uuid": "n1", "author": "system", "message": {"content": [{"type": "text", "text": "<task-notification>done</task-notification>"}]}},
            {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "Folding the findings in."}]}}]}
        self.assertEqual(jd._mint_anchor_uuid(seg), "a1", "the root claim moves to the session's own first record")


class LaunchReader(unittest.TestCase):
    def test_reads_background_launches_only_with_the_event_models_criterion(self):
        seg = {"atoms": [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Workflow", "input": {"script": WF_SCRIPT}},
                {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"description": "Audit the open pull requests", "prompt": "...", "run_in_background": True}},
                {"type": "tool_use", "id": "t3", "name": "Task", "input": {"prompt": "Guard review of the tree\nsecond line"}},
                {"type": "tool_use", "id": "t4", "name": "Workflow", "input": {"scriptPath": "/tmp/TESTHOST/scripts/pr-audit-round.js"}},
                {"type": "tool_use", "id": "t5", "name": "Agent", "input": {"description": "Explore the retry code (foreground)", "prompt": "read"}},
                {"type": "tool_use", "id": "t6", "name": "Bash", "input": {"command": "ls"}}]}},
            # t3's ack is asynchronous: a launch; t5's result is synchronous: the turn waited, no launch
            {"type": "user", "toolUseResult": {"isAsync": True, "status": "async_launched"},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "t3", "content": "Agent launched in background"}]}},
            {"type": "user", "toolUseResult": {"status": "completed"},
             "message": {"content": [{"type": "tool_result", "tool_use_id": "t5", "content": "the client has no retry"}]}},
            {"type": "assistant", "isApiError": True, "message": {"content": [{"type": "tool_use", "id": "t7", "name": "Agent", "input": {"description": "never counted", "run_in_background": True}}]}},
        ]}
        got = jd._seg_launches(seg)
        self.assertEqual([l["via"] for l in got], ["workflow", "agent", "agent", "workflow"])
        self.assertEqual(got[0]["desc"], "Lens reviewers over the retry diff, each finding verified")
        self.assertEqual(got[1]["desc"], "Audit the open pull requests", "run_in_background on the input")
        self.assertEqual(got[2]["desc"], "Guard review of the tree", "an asynchronous ack; the prompt's first line names it")
        self.assertEqual(got[3]["desc"], "pr-audit-round", "a script file: its name")
        self.assertFalse(any("foreground" in l["desc"] for l in got), "the agent the turn waited on is no launch")

    def test_overlap_is_a_share_of_the_smaller_set(self):
        self.assertEqual(jd._overlap("review the retry diff", "the retry diff review"), 1.0)
        self.assertEqual(jd._overlap("", "anything"), 0.0)
        self.assertLess(jd._overlap("count the apples in the orchard", "review the retry diff"), 0.34)


if __name__ == "__main__":
    unittest.main(verbosity=2)
