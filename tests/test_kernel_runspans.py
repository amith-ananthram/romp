#!/usr/bin/env python3
"""The judging band is built from the per-call LOG as RUN SPANS (the user 2026-06-19, g70): each judge
call plotted at its real wall-clock [sent, recv], glossed from the nearest artifact mark — so a judge that
RAN shows up WHEN it ran (distiller lag, coordinating-courier classifications) instead of back-placed onto
the work it summarizes. Self-contained: drives _run_judging with a synthetic judge-usage.jsonl.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_rs", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"


class RunJudging(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)

    def tearDown(self):
        jd.STATE = self.saved
        jd._active.clear()                               # the in-flight registry is module-level — reset it
        self.td.cleanup()

    def _usage(self, rows):
        with open(jd.STATE / "judge-usage.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_one_span_per_call_at_sent_recv_with_borrowed_gloss(self):
        # a distiller that RAN at [195,205] on a goal that completed back at t=100 — the span is the RUN
        # interval, not the back-placed completion; the text is borrowed from the artifact mark
        self._usage([{"judge": "distiller", "fsid": SID, "t": 205, "sent": 195, "recv": 205,
                      "ms": 10000, "in": 50, "out": 20}])
        semantic = [{"judge": "distiller", "sid": SID, "t": 100, "kind": "distill", "text": "the key takeaway"}]
        marks = km._run_judging(0, {SID}, semantic)
        self.assertEqual(len(marks), 1)
        m = marks[0]
        self.assertEqual((m["t"], m["t1"]), (195, 205), "the mark is the real [sent, recv] RUN span")
        self.assertEqual(m["text"], "the key takeaway", "gloss borrowed from the nearest artifact mark")
        self.assertEqual(m["kind"], "distill")
        self.assertEqual((m["sent"], m["recv"], m["ms"]), (195, 205, 10000))

    def test_a_coordinating_courier_call_shows_even_with_no_artifact_mark(self):
        # a courier classification of a COORDINATING message plants no node → no artifact mark, but its RUN
        # must still surface (this is exactly what the old artifact-derived band missed)
        self._usage([{"judge": "courier", "fsid": SID, "t": 301, "sent": 299, "recv": 301}])
        marks = km._run_judging(0, {SID}, [])
        self.assertEqual(len(marks), 1, "the courier run is visible even though no goal was planted")
        self.assertEqual(marks[0]["text"], "", "no artifact → empty gloss, but the run shows")
        self.assertEqual((marks[0]["t"], marks[0]["t1"]), (299, 301))

    def test_drops_dead_sessions_and_calls_before_the_window(self):
        self._usage([
            {"judge": "planner", "fsid": SID, "t": 51, "sent": 49, "recv": 51},      # alive + in window
            {"judge": "planner", "fsid": "deadbeef", "t": 51, "sent": 49, "recv": 51},  # dead session → dropped
            {"judge": "planner", "fsid": SID, "t": 6, "sent": 4, "recv": 6},         # recv < t0 → dropped
        ])
        marks = km._run_judging(10, {SID}, [])
        self.assertEqual(len(marks), 1, "only the alive, in-window call")
        self.assertEqual(marks[0]["sent"], 49)

    def test_missing_sent_recv_falls_back_to_a_point_at_t(self):
        # a pre-recording row (no sent/recv) → a point at the logged time, never dropped
        self._usage([{"judge": "captioner", "fsid": SID, "t": 100, "ms": 800}])
        marks = km._run_judging(0, {SID}, [])
        self.assertEqual(len(marks), 1)
        self.assertEqual((marks[0]["t"], marks[0]["t1"]), (100, 100), "no span → a point at t")

    def test_an_in_flight_run_shows_as_an_open_span_growing_to_now(self):
        # a judge call STILL running has no usage line yet — it must surface NOW as an open span (open:True,
        # recv:None, t1≈now) so the bar appears WHEN it starts, not back-dated once it ends (the user 2026-06-23)
        self._usage([])
        rid = jd._active_begin("grouper", SID, 500)
        try:
            marks = km._run_judging(0, {SID}, [])
        finally:
            jd._active_end(rid)
        self.assertEqual(len(marks), 1, "the in-flight run is drawn live")
        m = marks[0]
        self.assertEqual((m["judge"], m["t"], m["open"], m["recv"]), ("grouper", 500, True, None))
        self.assertGreaterEqual(m["t1"], 500, "the open span grows to now (t1 >= sent)")

    def test_a_completed_run_is_not_also_double_drawn_as_open(self):
        # the brief log-then-deregister window: the SAME run is both in the usage log AND still registered.
        # Dedup by (sid, judge, sent) → it draws ONCE (the completed span), never two overlapping bars.
        self._usage([{"judge": "closer", "fsid": SID, "t": 610, "sent": 600, "recv": 610}])
        rid = jd._active_begin("closer", SID, 600)       # same (sid, judge, sent) as the completed line
        try:
            marks = km._run_judging(0, {SID}, [])
        finally:
            jd._active_end(rid)
        self.assertEqual(len(marks), 1, "deduped — the completed span only, not a second open one")
        self.assertEqual((marks[0]["t"], marks[0]["recv"]), (600, 610), "the completed span wins")

    def test_an_in_flight_run_for_a_dead_session_is_dropped(self):
        self._usage([])
        rid = jd._active_begin("planner", "deadbeef", 500)
        try:
            marks = km._run_judging(0, {SID}, [])
        finally:
            jd._active_end(rid)
        self.assertEqual(marks, [], "an in-flight run on a non-alive session is not drawn")


class PureDelegationTop(unittest.TestCase):
    """A top-level node whose whole subtree is just courier handoff-tracking (work delegated to peers) is pure
    coordination — _pure_delegation_top flags it so the feed never shows it as an inbox card (the user
    2026-06-23). A top with any own-work leaf still shows."""

    def test_childless_handoff_top_is_pure(self):     # the g53 case: '↪ delegated to <peer>' with no own work
        nodes = {"t": {"id": "t", "parentId": None, "handoff": {"peer": "p", "msgId": "m"}}}
        self.assertTrue(km._pure_delegation_top(nodes, "t"))

    def test_umbrella_of_only_handoffs_is_pure(self):
        nodes = {"t": {"id": "t", "parentId": None},                 # umbrella carries no handoff itself
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}},
                 "b": {"id": "b", "parentId": "t", "handoff": {"peer": "q", "msgId": "2"}}}
        self.assertTrue(km._pure_delegation_top(nodes, "t"))

    def test_top_with_an_own_work_leaf_is_not_pure(self):
        nodes = {"t": {"id": "t", "parentId": None},
                 "h": {"id": "h", "parentId": "t", "handoff": {"peer": "p", "msgId": "m"}},
                 "w": {"id": "w", "parentId": "t"}}                  # own-work leaf, no handoff → still a card
        self.assertFalse(km._pure_delegation_top(nodes, "t"))

    def test_plain_top_is_not_pure(self):
        self.assertFalse(km._pure_delegation_top({"t": {"id": "t", "parentId": None}}, "t"))

    def test_a_dictated_ask_top_hosting_only_trackers_still_shows(self):
        # T101 (the user 2026-08-26): the ask is the card unit — one ask fanned to two workers is
        # ONE card with two handoff children, and the courier plants the trackers UNDER the ask
        # instead of minting recipient tops. A top that IS the dictated ask (its promptUuid root)
        # is therefore never "pure coordination", even when every leaf is a tracker: suppressing
        # it left the fully-delegated ask with no card anywhere.
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "hu"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}},
                 "b": {"id": "b", "parentId": "t", "handoff": {"peer": "q", "msgId": "2"}}}
        self.assertFalse(km._pure_delegation_top(nodes, "t"))

    def test_a_mint_proven_user_ask_top_shows_too(self):
        # the courier-minted shape: T105's userAsk (the chain-proven root record) marks the
        # ask-unit even when the mint carried no prompt anchor
        nodes = {"t": {"id": "t", "parentId": None, "userAsk": {"text": "the ask", "sid": "s"}},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        self.assertFalse(km._pure_delegation_top(nodes, "t"))

    def test_a_prompt_stamped_tracker_top_stays_pure(self):
        # the exemption never reaches a top that is ITSELF a handoff tracker — a '↪ delegated'
        # record wearing a stray prompt anchor is still coordination, not the ask
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "hu",
                       "handoff": {"peer": "p", "msgId": "m"}}}
        self.assertTrue(km._pure_delegation_top(nodes, "t"))

    def test_a_courier_planted_top_without_userask_stays_pure(self):
        # promptUuid is the g200 LANDABLE-ANCHOR field — apply_courier stamps the delegate MAIL's
        # anchor on every planted top — so bare-promptUuid truthiness would read every
        # courier-planted mid-chain top as "the dictated ask" and re-show the exact coordination
        # card the 2026-06-23 rule suppresses. A courier top's dictated-ask evidence is T105's
        # chain-proven userAsk, never its own anchor: without one it stays pure coordination.
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "mail-anchor-uuid",
                       "origin": {"peer": "p0", "goalId": "g0", "msgId": "m0"}},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}},
                 "b": {"id": "b", "parentId": "t", "handoff": {"peer": "q", "msgId": "2"}}}
        self.assertTrue(km._pure_delegation_top(nodes, "t"),
                        "a mail anchor is not the dictated ask — the courier top stays suppressed")

    def test_a_courier_top_with_the_chain_proven_userask_still_shows(self):
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "mail-anchor-uuid",
                       "origin": {"peer": "p0", "goalId": "g0", "msgId": "m0"},
                       "userAsk": {"text": "the ask", "sid": "s"}},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        self.assertFalse(km._pure_delegation_top(nodes, "t"),
                         "T105's userAsk IS the courier top's dictated-ask evidence")

    def test_a_segment_anchored_top_resolving_machine_stays_pure(self):
        # _seg_anchor gives an AUTONOMOUS segment's mint the segment HEAD — an assistant atom's
        # uuid ("a minted node always gets an anchor"). When the caller can resolve the anchor
        # against the session's cached parse and it is provably NOT a human-dictated prompt
        # record, the exemption must not fire.
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "a9"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        saved = km._parse_cached
        km._parse_cached = lambda p: {"turns": [{"atoms": [
            {"uuid": "a9", "type": "assistant",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "wrapping up the sweep"}]}}]}]}
        try:
            self.assertTrue(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"),
                            "a segment-head anchor resolving to the agent's own atom is not the ask")
        finally:
            km._parse_cached = saved

    def test_a_mail_record_anchor_resolving_peer_stays_pure(self):
        # the same resolution refuses a peer-mail anchor (mail rides user-type atoms)
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "pm"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        saved = km._parse_cached
        km._parse_cached = lambda p: {"turns": [{"atoms": [
            {"uuid": "pm", "type": "user", "author": {"peer": "p0", "kind": "delegate"},
             "message": {"role": "user", "content": "DELEGATE: the fanned piece"}}]}]}
        try:
            self.assertTrue(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"))
        finally:
            km._parse_cached = saved

    def test_a_dictated_prompt_resolution_keeps_the_card(self):
        # the positive control: the anchor resolves to a HUMAN record → the ask keeps its card
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "hu"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        saved = km._parse_cached
        km._parse_cached = lambda p: {"turns": [{"atoms": [
            {"uuid": "hu", "type": "user", "author": "human",
             "message": {"role": "user", "content": "please ship the drag-range round"}}]}]}
        try:
            self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"))
        finally:
            km._parse_cached = saved

    def test_an_unresolvable_anchor_fails_open_to_the_card(self):
        # UNKNOWN is not FALSE: with no cached parse (cold kernel, no path threaded, a rewound-away
        # record) the anchor is unresolvable — suppressing on doubt would hide a real dictated ask,
        # the exact no-card-anywhere hole T101's exemption exists to close. Uncertainty shows.
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "hu"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": "p", "msgId": "1"}}}
        saved = km._parse_cached
        km._parse_cached = lambda p: None
        try:
            self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"))
        finally:
            km._parse_cached = saved


class AskAnchorLatch(unittest.TestCase):
    """The ask-unit exemption's anchor verdict LATCHES on the node. _dictated_prompt_uuid answers
    None both for durable doubt AND for 'the parse is not warm this beat', and the exemption fails
    open on None — so a machine-anchored coordination card would flap shown→hidden on every
    restart/cache-cold beat with NO new information (the cards-move-on-new-information rule). Once
    a WARM parse answers, the judge stamps the verdict durably (askAnchor: human/machine/absent)
    through the planner apply path — the store's normal judge-side writer, never build_feed, which
    stays read-only — and the kernel reads the latch before ever consulting cache temperature.
    Cache-cold with NO latch keeps the fail-open, so the flap happens at most once per node ever,
    not per beat."""

    MACHINE = {"turns": [{"atoms": [
        {"uuid": "a9", "type": "assistant",
         "message": {"role": "assistant",
                     "content": [{"type": "text", "text": "wrapping up the sweep"}]}},
        {"uuid": "hu", "type": "user", "author": "human",
         "message": {"role": "user", "content": "please ship the drag-range round"}}]}]}

    def _fanned(self, **top_kw):
        top = {"id": "t", "parentId": None, "promptUuid": "a9"}
        top.update(top_kw)
        return {"t": top, "a": {"id": "a", "parentId": "t",
                                "handoff": {"peer": "p", "msgId": "1"}}}

    def test_the_latch_stamps_all_three_verdicts_and_only_where_the_exemption_reads(self):
        store = {"rompUuid": SID, "nodes": {
            "th": {"id": "th", "parentId": None, "promptUuid": "hu"},
            "tm": {"id": "tm", "parentId": None, "promptUuid": "a9"},
            "tg": {"id": "tg", "parentId": None, "promptUuid": "gone-uuid"},
            "tc": {"id": "tc", "parentId": None, "promptUuid": "a9",       # courier top: origin —
                   "origin": {"peer": "p0", "goalId": "g0", "msgId": "m0"}},  # the exemption reads
            "tk": {"id": "tk", "parentId": None, "promptUuid": "a9",       # userAsk there, no anchor
                   "handoff": {"peer": "p", "msgId": "2"}},                # tracker top: never asks
            "kid": {"id": "kid", "parentId": "th", "promptUuid": "a9"},    # not a top
            "tl": {"id": "tl", "parentId": None, "promptUuid": "a9",
                   "askAnchor": "human"}},                                 # already latched: kept
            "placements": {}, "status": {}}
        n = jd._latch_ask_anchors(SID, self.MACHINE, store)
        self.assertEqual(n, 3, "exactly the exemption-relevant unlatched tops latch")
        self.assertEqual(store["nodes"]["th"].get("askAnchor"), "human")
        self.assertEqual(store["nodes"]["tm"].get("askAnchor"), "machine")
        self.assertEqual(store["nodes"]["tg"].get("askAnchor"), "absent")
        for nid in ("tc", "tk", "kid"):
            self.assertNotIn("askAnchor", store["nodes"][nid], nid)
        self.assertEqual(store["nodes"]["tl"].get("askAnchor"), "human",
                         "a latched verdict is never re-derived")

    def test_an_atomless_parse_latches_nothing(self):
        # a missing/unreadable transcript is NO EVIDENCE of absence — 'absent' latched off one
        # would freeze a fail-open verdict a later warm parse could have resolved machine
        store = {"rompUuid": SID, "nodes": {
            "t": {"id": "t", "parentId": None, "promptUuid": "hu"}},
            "placements": {}, "status": {}}
        self.assertEqual(jd._latch_ask_anchors(SID, {"turns": []}, store), 0)
        self.assertNotIn("askAnchor", store["nodes"]["t"])

    def test_a_latched_machine_verdict_is_stable_across_cache_temperature(self):
        # warm → cold → warm: the judge latched from its warm parse; the kernel's cold beat must
        # answer from the latch, not re-derive from the (cold) cache and fail open into a flap
        store = {"rompUuid": SID, "nodes": self._fanned(), "placements": {}, "status": {}}
        jd._latch_ask_anchors(SID, self.MACHINE, store)
        self.assertEqual(store["nodes"]["t"]["askAnchor"], "machine")
        saved = km._parse_cached
        try:
            for temp in (None,                                            # restart / cache-cold beat
                         lambda: self.MACHINE):                           # …and warm again
                km._parse_cached = (lambda p: None) if temp is None else (lambda p: temp())
                self.assertTrue(
                    km._pure_delegation_top(store["nodes"], "t", sid=SID, path="/dev/null"),
                    "the latched machine anchor holds — no shown→hidden flap on temperature")
        finally:
            km._parse_cached = saved

    def test_latched_human_and_absent_keep_the_card_cold_or_warm(self):
        saved = km._parse_cached
        try:
            km._parse_cached = lambda p: None
            for verdict in ("human", "absent"):
                nodes = self._fanned(askAnchor=verdict)
                self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"),
                                 verdict)
            # the latch outranks a later contradicting cache read: it was filed from a warm parse,
            # and re-deriving per build is exactly the flapping input the latch retires
            km._parse_cached = lambda p: self.MACHINE
            nodes = self._fanned(askAnchor="human")
            self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path="/dev/null"))
        finally:
            km._parse_cached = saved

    def test_the_planner_pass_is_the_latch_writer(self):
        # the write rides an existing legitimate judge-side writer (_plan_session's end-of-pass
        # save) — never a build_feed-side write; build_feed stays read-only
        from inspect import getsource
        self.assertIn("_latch_ask_anchors", getsource(jd._plan_session))
        self.assertNotIn("_latch_ask_anchors", getsource(km.build_feed))


if __name__ == "__main__":
    unittest.main()
