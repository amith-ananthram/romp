#!/usr/bin/env python3
"""A block addressed to a PEER is a peer wait, not the user's needs-you (T334, the user 2026-09-10 via the
philosophy in CLAUDE.md: waiting on a peer or another session is not the human being the bottleneck). Pinned
here on synthetic stores, postal logs and names (placeholder uuids, the notes-api demo's session names):
- the closer's and the planner's block on a node whose session has an open question to a live peer files the
  awaiting/peer stamp naming that peer, in place of the block; with several open asks the block's words pick
  among them, never invent one;
- with no open ask, a block under a courier-planted goal is addressed to the peer that delegated it, even when
  the text names the user (the manager relays); a standalone session's block stays the user's, exactly as before;
- rows already filed convert once per boot; the manager debtor's escalation waits for its idle turn."""
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_peer_wait", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781300000
T0 = NOW - 3600
WORKER = "11111111-2222-3333-4444-aaaaaaaaaaaa"     # the session whose block is judged
MANAGER = "11111111-2222-3333-4444-bbbbbbbbbbbb"    # the peer that delegated its goal
OTHER = "11111111-2222-3333-4444-cccccccccccc"      # another peer it asked something


def mail(from_id, to_id, t, kind="question", mid=None):
    return {"id": mid or "m-%s-%s-%d" % (from_id[-4:], to_id[-4:], t), "from_id": from_id, "to_id": to_id,
            "from": "api", "to": "web", "t": t, "kind": kind}


def node(nid, text, parent=None, t=T0, **kw):
    d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
         "trail": [], "t": t, "mt": t, "log": []}
    d.update(kw)
    return d


class _Peer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = (jd.STATE, km.NAMES)
        jd._rebind_state(td)                             # STATE and every dir derived from it, the repo's rule for tests
        jd.NAMES.mkdir(); jd.GOALDIR.mkdir(); jd.MESSAGES.parent.mkdir(parents=True)
        km.NAMES = jd.NAMES
        for sid, name in ((WORKER, "api"), (MANAGER, "web"), (OTHER, "tests")):
            (jd.NAMES / sid).write_text("%s\t/TESTDIR\t#abcdef\n" % name)
        self.rows = []
        self._write_mail()

    def tearDown(self):
        jd._rebind_state(self.saved[0]); km.NAMES = self.saved[1]
        jd._PEER_ASK_CACHE[0] = None
        jd._DELEG_CACHE[0] = None
        self.td.cleanup()

    def _write_mail(self):
        jd.MESSAGES.write_text("".join(json.dumps(r) + "\n" for r in self.rows))
        jd._PEER_ASK_CACHE[0] = None
        jd._DELEG_CACHE[0] = None

    def ask(self, from_id, to_id, t, kind="question"):
        self.rows.append(mail(from_id, to_id, t, kind))
        self._write_mail()

    def store(self, delegated=False, blocked=False):
        top = WORKER + ":g1"; step = WORKER + ":g2"
        kw = {"origin": {"peer": MANAGER, "goalId": MANAGER + ":g7", "msgId": "m-deleg"}} if delegated else {}
        st = {"rompUuid": WORKER, "seq": 2, "placements": {}, "status": {top: "working"}, "confirming": [],
              "nodes": {top: node(top, "Ship the exporter", **kw),
                        step: node(step, "Ask which client to change", parent=top, t=T0 + 60)}}
        if blocked:
            st["nodes"][step]["blocked"] = True
            st["nodes"][step]["blockWhy"] = "asked the manager which client"
            st["nodes"][step]["log"] = [{"kind": "block", "src": "closer", "ev_t": T0 + 400, "why": "asked the manager which client"}]
        return st, top, step


class CloserBlocks(_Peer):
    def _close(self, st, step, why):
        menu = [st["nodes"][step]]
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, menu, {"done": {}, "block": {1: why}, "awaiting": {}}, t=T0 + 400)
        return st["nodes"][step]

    def test_an_open_question_to_a_live_peer_makes_the_block_a_peer_wait(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        nd = self._close(st, step, "cannot move further without an answer")
        self.assertFalse(nd["blocked"], "no needs-you")
        self.assertEqual(nd.get("awaitingKind"), "peer")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        self.assertNotIn("relayWanted", nd, "the open ask to the delegator is the edge: nothing to relay")
        rows = [e for e in nd["log"] if e.get("kind") in ("block", "awaiting")]
        self.assertEqual([(e["kind"], e["src"]) for e in rows], [("awaiting", "closer")])

    def test_an_unrelated_open_ask_does_not_capture_a_block_in_the_delegators_work(self):
        # the worker asked another peer about ports; its block toward its manager names nobody: the manager, relayed
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300)
        nd = self._close(st, step, "cannot move further without an answer")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        self.assertEqual(nd["relayWanted"]["peer"], MANAGER)
        st2, top2, step2 = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300)
        nd2 = self._close(st2, step2, "waiting on tests for the port")      # names the peer it asked: that peer
        self.assertEqual(list(nd2.get("awaitingPeers") or ()), [OTHER])
        self.assertNotIn("relayWanted", nd2)

    def test_words_pick_among_several_open_asks_and_never_invent_a_peer(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300)
        self.ask(WORKER, MANAGER, T0 + 310)
        nd = self._close(st, step, "waiting on tests to say which fixture")   # "tests" is OTHER's session name
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [OTHER])
        st2, top2, step2 = self.store(delegated=True)
        nd2 = self._close(st2, step2, "waiting on an answer")                  # no name: the latest ask
        self.assertEqual(list(nd2.get("awaitingPeers") or ()), [MANAGER])
        st3, top3, step3 = self.store(delegated=True)
        nd3 = self._close(st3, step3, "the user or the contest judge must say")   # names nobody who asked: the latest
        self.assertEqual(list(nd3.get("awaitingPeers") or ()), [MANAGER], "words never invent a peer")
        st4, top4, step4 = self.store(delegated=True)
        nd4 = self._close(st4, step4, "the apitests harness is red")            # "tests" inside a longer word: no match
        self.assertEqual(list(nd4.get("awaitingPeers") or ()), [MANAGER])

    def test_with_no_open_ask_a_delegated_goals_block_goes_to_the_peer_that_delegated_it(self):
        st, top, step = self.store(delegated=True)
        nd = self._close(st, step, "PR is green and cannot move further without you")
        self.assertFalse(nd["blocked"])
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        rw = dict(nd["relayWanted"])
        self.assertTrue(rw.pop("id", "").startswith("%d-" % (T0 + 400)), "the marker's identity: the block's evidence time and a nonce")
        self.assertEqual(rw, {"peer": MANAGER, "why": "PR is green and cannot move further without you", "t": T0 + 400},
                         "no question was ever sent: the kernel relays it as the worker's own")
        st2, top2, step2 = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        nd2 = self._close(st2, step2, "cannot move further without an answer")
        self.assertNotIn("relayWanted", nd2, "the open ask already is the edge: nothing to relay")

    def test_a_block_naming_the_user_in_a_delegated_goal_still_goes_to_the_manager(self):
        # the requirement: a worker's card never reaches the user except through the escalation event
        st, top, step = self.store(delegated=True)
        nd = self._close(st, step, "only the user can decide this; please relay the question to them")
        self.assertFalse(nd["blocked"], "the manager relays")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])

    def test_a_standalone_sessions_block_is_the_users_exactly_as_before(self):
        st, top, step = self.store()
        nd = self._close(st, step, "which client should change?")
        self.assertTrue(nd["blocked"])
        self.assertIsNone(nd.get("awaitingKind"))
        self.assertEqual([e["src"] for e in nd["log"] if e.get("kind") == "block"], ["closer"])

    def test_the_users_own_session_keeps_its_block_whatever_questions_it_has_out(self):
        # a standalone session with an unrelated open question: its decision must never hide behind "Awaiting <peer>"
        st, top, step = self.store()
        self.ask(WORKER, OTHER, T0 + 300)
        nd = self._close(st, step, "need your call on breaking the v1 API")
        self.assertTrue(nd["blocked"])
        self.assertIsNone(nd.get("awaitingKind"))

    def test_an_empty_why_is_never_redirected(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300)
        nd = self._close(st, step, "")
        self.assertTrue(nd["blocked"], "a block with nothing to relay stays a block")

    def test_the_ladders_own_escalation_block_is_never_lifted_by_a_re_asserted_judge_block(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        st["nodes"][top]["blocked"] = True
        st["nodes"][top]["log"] = [{"kind": "block", "src": "nudge", "ev_t": T0 + 500, "why": "web has not answered"}]
        menu = [st["nodes"][top]]
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, menu, {"done": {}, "block": {1: "still waiting on the manager"}, "awaiting": {}}, t=T0 + 900)
        self.assertTrue(st["nodes"][top]["blocked"], "the once-ever escalation stands")
        self.assertEqual([e["kind"] for e in st["nodes"][top]["log"]], ["block"], "nothing filed over it")

    def test_a_delegate_or_coordinate_row_is_no_open_ask(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300, kind="coordinate")
        self.ask(WORKER, OTHER, T0 + 301, kind="delegate")
        nd = self._close(st, step, "which client should change?")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "a heads-up or a handoff asks nothing back: the delegator")

    def test_an_answered_question_is_no_open_ask(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, OTHER, T0 + 300)
        self.ask(OTHER, WORKER, T0 + 350, kind="coordinate")                    # any reply answers
        nd = self._close(st, step, "which client should change?")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "the answered ask is gone; the delegator remains")

    def test_a_re_asserted_block_on_an_already_blocked_node_unblocks_it_into_the_wait(self):
        st, top, step = self.store(delegated=True, blocked=True)
        nd = self._close(st, step, "still waiting on the manager")
        self.assertFalse(nd["blocked"], "romp's unblock lifts the old block, the peer wait replaces it")
        kinds = [(e["kind"], e["src"]) for e in nd["log"] if e.get("kind") in ("block", "unblock", "awaiting")]
        self.assertEqual(kinds, [("block", "closer"), ("unblock", "romp"), ("awaiting", "closer")])

    def test_the_delegate_mail_is_the_relation_when_the_courier_planted_nothing(self):
        # today's stores hold no planted origin; the DELEGATE row the manager sent is the primary record. A top anchored
        # on a machine record (the dispatch mail) minted after that delegate belongs to the relation; a top the user
        # typed (askAnchor human) never does, whatever mail the session received; a top minted before any delegate
        # predates it
        self.ask(MANAGER, WORKER, T0 - 100, kind="delegate")
        st, top, step = self.store()
        st["nodes"][top]["askAnchor"] = "machine"
        nd = self._close(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        self.assertEqual(nd["relayWanted"]["peer"], MANAGER)
        for anchor in ("human", None, "absent", "scheduled"):        # only the latch's positive machine verdict qualifies
            st2, top2, step2 = self.store()
            if anchor is None:
                st2["nodes"][top2].pop("askAnchor", None)
            else:
                st2["nodes"][top2]["askAnchor"] = anchor
            nd2 = self._close(st2, step2, "cannot move further without you")
            self.assertTrue(nd2["blocked"], "anchor %r: the user's, fail open" % anchor)
        st3, top3, step3 = self.store()
        st3["nodes"][top3]["askAnchor"] = "machine"; st3["nodes"][top3]["t"] = T0 - 500
        nd3 = self._close(st3, step3, "cannot move further without you")
        self.assertTrue(nd3["blocked"], "minted before any delegate reached the session: not the relation")
        self.assertTrue(km._delegated_to(MANAGER, WORKER), "the kernel reads the same record for the ladder")

    def test_a_script_mailers_pseudo_sid_is_never_the_delegating_peer(self):
        # a cron mailer delegates as ext:<label>: no session behind it could ever be asked, so the block stays the user's
        self.ask("ext:morning", WORKER, T0 - 100, kind="delegate")
        st, top, step = self.store()
        st["nodes"][top]["askAnchor"] = "machine"
        nd = self._close(st, step, "cannot move further without you")
        self.assertTrue(nd["blocked"])
        st2, top2, step2 = self.store(delegated=True)
        st2["nodes"][top2]["origin"]["peer"] = "ext:morning"
        nd2 = self._close(st2, step2, "cannot move further without you")
        self.assertTrue(nd2["blocked"], "a planted ext: origin is no session either")

    def test_a_block_on_a_handoff_tracker_waits_on_the_peer_it_was_handed_to(self):
        # a manager blocked on its own "delegated to <worker>" tracker waits on that worker (the delegate edge the
        # worker's report ends), never on whoever delegated to the manager
        st, top, step = self.store(delegated=True)
        st["nodes"][step]["handoff"] = {"peer": OTHER, "goalId": OTHER + ":g3"}
        nd = self._close(st, step, "waiting on their report")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [OTHER])
        self.assertNotIn("relayWanted", nd, "the delegate already is the edge: nothing to relay")

    def test_an_ask_sent_once_the_goal_existed_counts_for_a_step_minted_later(self):
        # the worker asked its manager after the goal was minted but before this step: still the edge, no relay
        st, top, step = self.store(delegated=True)
        st["nodes"][step]["t"] = T0 + 500
        self.ask(WORKER, MANAGER, T0 + 300)
        nd = self._close(st, step, "cannot move further without an answer")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        self.assertNotIn("relayWanted", nd, "the earlier question is the edge: nothing to relay twice")

    def test_a_late_closer_never_re_stamps_or_re_relays_a_wait_the_diary_already_ended(self):
        st, top, step = self.store(delegated=True)
        nd = self._close(st, step, "cannot move further without you")          # stamped and relay wanted, at T0 + 400
        self.assertIn("relayWanted", nd)
        del nd["relayWanted"]
        jd.record_verdict(st, nd, "romp", "awaiting", T0 + 900, why="", lift=True, end_ev=T0 + 900)   # the reply lifted it
        self.assertIsNone(nd.get("awaitingKind"))
        menu = [nd]
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, menu, {"done": {}, "block": {1: "still cannot move further without you"}, "awaiting": {}}, t=T0 + 400)
        self.assertIsNone(nd.get("awaitingKind"), "a closer auditing the OLD turn stands down before the newer lift")
        self.assertNotIn("relayWanted", nd)
        self.assertFalse(nd.get("blocked"))

    def test_the_planners_block_op_files_a_peer_wait_with_no_mt_bump_and_a_users_block_with_one(self):
        st, top, step = self.store(delegated=True)
        mt0, trail0 = st["nodes"][step]["mt"], list(st["nodes"][step]["trail"])
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_plan(st, "seg-9", T0 + 900, [{"do": "block", "why": "needs the manager's call", "goal": 1}], [st["nodes"][step]])
        nd = st["nodes"][step]
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        self.assertFalse(nd["blocked"])
        self.assertEqual((nd["mt"], nd["trail"]), (mt0, trail0), "an annotation: no mt bump, no trail entry")
        st2, top2, step2 = self.store()                                          # standalone: the user's block, as before
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_plan(st2, "seg-9", T0 + 900, [{"do": "block", "why": "needs your call", "goal": 1}], [st2["nodes"][step2]])
        nd2 = st2["nodes"][step2]
        self.assertTrue(nd2["blocked"])
        self.assertEqual(nd2["mt"], T0 + 900)
        self.assertIn("seg-9", nd2["trail"])

    def test_the_planner_shares_the_one_block_writer(self):
        import inspect
        src = inspect.getsource(jd.apply_plan)
        self.assertIn('file_block(store, nodes[t], "planner", o["why"], seg_t, seg=seg_id)', src)
        st, top, step = self.store(delegated=True)
        kind, landed = jd.file_block(st, st["nodes"][step], "planner", "needs the manager's call", T0 + 400)
        self.assertEqual((kind, landed), ("peer", True))
        self.assertEqual(list(st["nodes"][step]["awaitingPeers"] or ()), [MANAGER])


class RefusalInTheBrief(unittest.TestCase):
    """A refused relay's note reaches the card: the block brief is fed the owed question with the note in brackets."""

    def test_the_owed_question_carries_the_refusal(self):
        self.assertEqual(jd._owed_why({"blockWhy": "which client?", "relayRefusal": "web could not be asked: no live session"}),
                         "which client? (web could not be asked: no live session)")
        self.assertEqual(jd._owed_why({"blockWhy": "which client?"}), "which client?", "no refusal, the why alone")
        src = inspect.getsource(jd._distill_session)
        self.assertIn("_owed_why(d)", src, "the several-blocks list feeds it")
        self.assertIn("_owed_why(blkd[0])", src, "the lone block feeds it")


class UsersFollowUp(_Peer):
    """The user's own follow-up on a delegated card (follow up pressed on the worker's card): a block after it is theirs,
    never a peer wait, never relayed; a follow-up older than the delegation changes nothing."""

    def _close(self, st, step, why, t=T0 + 400):
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: why}, "awaiting": {}}, t=t)
        return st["nodes"][step]

    def test_a_follow_up_newer_than_the_delegation_keeps_the_block_the_users(self):
        st, top, step = self.store(delegated=True)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", T0 + 300, msg=True)   # follow up pressed on the card after the mint
        nd = self._close(st, step, "cannot move further without you")
        self.assertTrue(nd["blocked"])
        self.assertIsNone(nd.get("awaitingKind"))
        self.assertNotIn("relayWanted", nd)

    def test_a_follow_up_older_than_the_delegation_changes_nothing(self):
        st, top, step = self.store(delegated=True)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", T0 - 100, msg=True)   # before the mint
        nd = self._close(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])

    def test_the_workers_own_ask_after_the_follow_up_is_the_newer_edge(self):
        st, top, step = self.store(delegated=True)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", T0 + 300, msg=True)   # the user follows up...
        self.ask(WORKER, MANAGER, T0 + 350)                                             # ...the worker asks the manager after it...
        nd = self._close(st, step, "cannot move further without you")                  # ...and blocks at T0 + 400
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "the ask is the newer edge: a peer wait")
        self.assertNotIn("relayWanted", nd, "the ask is the edge: nothing to relay")

    def test_a_follow_up_after_the_workers_ask_is_the_users(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", T0 + 350, msg=True)
        nd = self._close(st, step, "cannot move further without you")
        self.assertTrue(nd["blocked"])
        self.assertIsNone(nd.get("awaitingKind"))

    def test_a_follow_up_after_a_standing_wait_reclaims_it(self):
        st, top, step = self.store(delegated=True)
        nd = self._close(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])
        jd.record_verdict(st, nd, "user", "reopen", T0 + 900, msg=True)   # the user follows up on the waiting card...
        jd.record_verdict(st, nd, "romp", "awaiting", T0 + 900, why="", lift=True, end_ev=T0 + 900)
        nd = self._close(st, step, "still cannot move further", T0 + 1000)   # ...and the next block is theirs
        self.assertTrue(nd["blocked"])
        self.assertNotIn("relayWanted", nd)


class RowsAlreadyFiled(_Peer):
    def test_the_boot_pass_converts_a_closer_block_addressed_to_a_peer_once(self):
        st, top, step = self.store(delegated=True, blocked=True)
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(st))
        self.assertEqual(jd.restamp_peer_wait_blocks_all(NOW), (1, 1))
        got = json.loads((jd.GOALDIR / (WORKER + ".json")).read_text())["nodes"][step]
        self.assertFalse(got["blocked"])
        self.assertEqual(got.get("awaitingKind"), "peer")
        self.assertEqual(list(got.get("awaitingPeers") or ()), [MANAGER])
        self.assertEqual(jd.restamp_peer_wait_blocks_all(NOW + 1), (0, 0), "idempotent")

    def test_the_boot_pass_leaves_a_users_block_and_a_nudge_block_alone(self):
        st, top, step = self.store(blocked=True)                                # standalone: the user's
        st["nodes"][top]["blocked"] = True
        st["nodes"][top]["log"] = [{"kind": "block", "src": "nudge", "ev_t": T0 + 500}]
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(st))
        self.assertEqual(jd.restamp_peer_wait_blocks_all(NOW), (0, 0))
        got = json.loads((jd.GOALDIR / (WORKER + ".json")).read_text())["nodes"]
        self.assertTrue(got[step]["blocked"] and got[top]["blocked"])


class _RelayFixture(_Peer):
    """The relay harness: a fake bus that answers as the local /send does and lands the mail in the manager's maildir
    and the postal log; the real save path (the entry is written once the store is published); the queue directory.
    No tests of its own: the classes below inherit it, each running only its own cases."""

    def setUp(self):
        super().setUp()
        self._orig_send = km._bus_send_relay
        self._orig_recall = km._bus_recall_relay
        self.sent = []
        self.inbox = jd.STATE / "postal" / "mail" / MANAGER / "new"
        self.inbox.mkdir(parents=True)
        test = self

        def fake_bus(payload):                       # the bus's own outcome: the maildir file and the log row
            test.sent.append(payload)
            mid = "relay-%d" % len(test.sent)
            (test.inbox / mid).write_text("X-Relayed: romp\n\n" + payload["body"])
            test.rows.append({"id": mid, "from_id": payload["from_id"], "to_id": payload["to"], "from": payload["from"],
                              "t": NOW, "kind": payload["kind"], "relayed": True, "relayMarker": payload.get("relayMarker")})
            test._write_mail()
            return True, "", False, {"ok": True, "to": payload["to"]}   # the local route's answer (no id)
        km._bus_send_relay = fake_bus

    def tearDown(self):
        km._bus_send_relay = self._orig_send
        km._bus_recall_relay = self._orig_recall
        km._RELAY_SAID.clear()
        km._RELAY_QUIET.clear()
        km._RELAY_BAD["n"] = 0
        super().tearDown()

    def _save(self, st):
        jd.save_goals(WORKER, st)                   # the real path: the entry is written once the store is published

    def _queue(self):
        d = jd._relay_queue_dir()
        return sorted(f.name for f in d.glob("*.json")) if d.is_dir() else []

    def _close(self, st, step, why, t):
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: why}, "awaiting": {}}, t=t)


class RelayEndToEnd(_RelayFixture):
    """A worker's closer block under a delegated goal with no open ask: one relayed question in the manager's inbox,
    the manager's reply lifts the wait, and a re-asserted block never relays twice."""

    def test_the_relay_creates_the_edge_the_reply_lifts_and_a_re_asserted_block_never_relays_twice(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "PR is green and cannot move further without you", T0 + 400)
        self.assertEqual(self._queue(), [], "nothing queued before the store is saved")
        self._save(st)
        self.assertEqual(len(self._queue()), 1, "the judge queued the node once its store was published")
        self.assertEqual(km._relay_tick(NOW), 1)
        self.assertEqual(self._queue(), [], "consumed")
        self.assertEqual([{k: v for k, v in s.items() if k != "relayMarker"} for s in self.sent], [{"to": MANAGER, "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                                      "body": "api cannot move further: PR is green and cannot move further without you"}],
                         "the worker's own words with a plain lead-in, as the worker, marked relayed")
        self.assertEqual(len(list(self.inbox.iterdir())), 1, "one question in the manager's inbox")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertNotIn("relayWanted", nd)
        self.assertEqual(nd["relayed"]["peer"], MANAGER)
        self.assertEqual(jd._open_ask_peers(WORKER), [MANAGER], "the edge every ending keys on now exists")
        self.assertEqual(km._relay_tick(NOW + 1), 0, "once")
        # the closer re-asserts the block on the standing wait: no second relay
        st2 = jd.load_goals(WORKER)
        self._close(st2, step, "still cannot move further without you", NOW + 5)
        self.assertNotIn("relayWanted", st2["nodes"][step])
        self.assertEqual(list(st2["nodes"][step]["awaitingPeers"]), [MANAGER])
        # the manager replies: the pair-aware supersede lifts the wait (the stamp's WRITE time is the wall clock, so
        # the reply must postdate it: a reply the gate had already weighed never supersedes)
        import time as _time
        self.rows.append(mail(MANAGER, WORKER, int(_time.time()) + 60, kind="coordinate"))
        self._write_mail()
        self.assertTrue(km._peer_stamp_superseded(st2["nodes"][step], km._peer_answered(WORKER)), "the reply ends the wait")
        self.assertEqual(jd._open_ask_peers(WORKER), [], "answered: no open ask")

    def test_a_transient_failure_keeps_the_entry_and_is_said_once(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (False, "ConnectionRefusedError", False, {})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(km._relay_tick(NOW), 0)
            self.assertEqual(km._relay_tick(NOW + 1), 0)
        self.assertEqual(err.getvalue().count("retried next tick"), 1, "said once")
        self.assertIn("relayWanted", jd.load_goals(WORKER)["nodes"][step], "the marker stands for the next tick")
        self.assertEqual(len(self._queue()), 1, "the entry stays queued")

    def test_a_definitive_refusal_reverts_to_the_users_block(self):
        # the delegating peer is dead or not a session: nobody can be asked, so the block is the user's after all
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (False, "bus /send 404: no live session answers to that id", True, {})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertTrue(nd["blocked"], "the needs-you is back")
        self.assertIn("cannot move further without you", nd["blockWhy"])
        self.assertIn("web could not be asked", nd["relayRefusal"])
        self.assertNotIn("relayWanted", nd)
        self.assertEqual(nd["mt"], NOW, "bumped like every other block writer")
        self.assertEqual(nd["relayDone"]["outcome"], "refused", "the removal is a record the merge honours")
        self.assertEqual(self._queue(), [])

    def test_a_later_relay_on_the_node_clears_the_refusals_note(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        fake = km._bus_send_relay
        km._bus_send_relay = lambda payload: (False, "bus /send 404: no live recipient", True, {})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        self.assertIn("relayRefusal", st["nodes"][step])
        jd.record_verdict(st, st["nodes"][step], "romp", "unblock", NOW + 5)          # the block lifts (the manager is live again)...
        self._close(st, step, "a new question", NOW + 60)                              # ...and the next block relays
        self._save(st)
        km._bus_send_relay = fake
        self.assertEqual(km._relay_tick(NOW + 70), 1)
        self.assertNotIn("relayRefusal", jd.load_goals(WORKER)["nodes"][step], "the old note is gone with the new relay")

    def test_a_wait_that_ended_before_the_tick_is_never_relayed(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        jd.record_verdict(st, st["nodes"][step], "romp", "done", T0 + 500)         # resolved before the tick ran
        self._save(st)
        self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(self.sent, [], "nothing sent")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertNotIn("relayWanted", nd, "the marker stood down")
        self.assertEqual(nd["relayDone"]["outcome"], "stood-down")


class RelayEdges(_RelayFixture):
    """The manager's third review: the guard reads the state before the write, two writers never lose an entry, an
    unsaved marker keeps its entry, a parked relay is pending until it lands or comes back, malformed entries drop alone."""

    def test_a_block_after_the_lift_relays_again(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        self.assertEqual(km._relay_tick(NOW), 1)
        st = jd.load_goals(WORKER)
        self.rows.append(mail(MANAGER, WORKER, NOW + 5, kind="coordinate"))    # the manager answers the relayed question...
        self._write_mail()
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)   # ...and the wait lifts
        self.assertIsNone(st["nodes"][step].get("awaitingKind"))
        self._close(st, step, "now stuck on the second question", NOW + 60)   # a NEW block on the same node
        self.assertEqual(st["nodes"][step]["relayWanted"]["why"], "now stuck on the second question", "a second relay")
        self._save(st)
        entry = json.loads((jd._relay_queue_dir() / self._queue()[0]).read_text())
        self.assertEqual(entry["marker"], st["nodes"][step]["relayWanted"]["id"], "the entry names the new marker")
        self.assertEqual(entry["rev"], st["rev"], "and the revision whose publish carried it")
        self.assertEqual(km._relay_tick(NOW + 90), 1, "the second question goes out")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual(nd["relayed"]["marker"], entry["marker"], "the record names the marker it settled")

    def test_a_lift_before_any_tick_then_a_new_block_relays_the_new_words_once(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)   # block...
        first = st["nodes"][step]["relayWanted"]["id"]
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", T0 + 500, why="", lift=True, end_ev=T0 + 500)   # ...lift...
        self._close(st, step, "second question", T0 + 600)  # ...re-block, all on one loaded store, no tick between
        rw = st["nodes"][step]["relayWanted"]
        self.assertEqual((rw["why"], rw["id"] != first), ("second question", True), "a fresh marker for the new wait")
        self.assertIn(first, st["nodes"][step]["relaySettled"], "the ended wait's unsent marker is settled")
        self._save(st)
        self.assertEqual(km._relay_tick(NOW), 1)
        self.assertEqual([p["body"] for p in self.sent], ["api cannot move further: second question"], "the NEW words")
        self.assertEqual(km._relay_tick(NOW + 1), 0, "once")

    def test_two_nodes_blocked_in_one_pass_toward_one_peer_get_two_questions(self):
        st, top, step = self.store(delegated=True)
        other = WORKER + ":g3"
        st["nodes"][other] = node(other, "Ask which port to use", parent=top, t=T0 + 70)
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step], st["nodes"][other]],
                           {"done": {}, "block": {1: "which client?", 2: "which port?"}, "awaiting": {}}, t=T0 + 400)
        ids = (st["nodes"][step]["relayWanted"]["id"], st["nodes"][other]["relayWanted"]["id"])
        self.assertNotEqual(ids[0], ids[1], "one evidence time, one peer, two nodes: two markers")
        self._save(st)
        self.assertEqual(len(self._queue()), 2)
        self.assertEqual(km._relay_tick(NOW), 2)
        self.assertEqual(sorted(p["body"] for p in self.sent),
                         ["api cannot move further: which client?", "api cannot move further: which port?"])

    def test_two_holders_filing_one_wait_mint_one_marker(self):
        st, top, step = self.store(delegated=True)
        self._save(st)
        a = jd.load_goals(WORKER)
        b = jd.load_goals(WORKER)
        self._close(a, step, "cannot move further without you", T0 + 400)
        self._save(a)
        self._close(b, step, "cannot move further without you", T0 + 400)   # the same evidence, the same peer
        self._save(b)
        self.assertEqual(a["nodes"][step]["relayWanted"]["id"], b["nodes"][step]["relayWanted"]["id"], "one id")
        self.assertEqual(len(self._queue()), 1)
        self.assertEqual(km._relay_tick(NOW), 1)
        self.assertEqual(len(self.sent), 1, "one question")
        self.assertEqual(km._relay_tick(NOW + 1), 0)

    def test_a_re_block_with_the_same_words_after_a_lift_is_a_new_marker(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        self.assertEqual(km._relay_tick(NOW), 1)
        st = jd.load_goals(WORKER)
        first = st["nodes"][step]["relayed"]
        self.rows.append(mail(MANAGER, WORKER, NOW + 5, kind="coordinate"))
        self._write_mail()
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "cannot move further without you", NOW + 60)     # the SAME words, a new block
        rw = st["nodes"][step]["relayWanted"]
        self.assertNotEqual(rw["id"], first["marker"], "its own identity")
        theirs = jd.load_goals(WORKER)                     # another holder publishes in between...
        theirs["nodes"][top]["text"] = "Ship the exporter (renamed)"
        base = st["_baseRev"]
        self._save(theirs)
        self._save(st)                                     # ...so this publish REBASES onto the disk holding the first record
        st2 = jd.load_goals(WORKER)
        self.assertEqual(st2["rev"], base + 2, "the rebase happened")
        self.assertIn("relayWanted", st2["nodes"][step], "the earlier record settles only the marker it names")
        self.assertEqual(km._relay_tick(NOW + 90), 1, "relayed again")

    def test_an_entry_is_kept_only_when_the_disk_predates_its_publish_and_bounded_after(self):
        st, top, step = self.store(delegated=True)
        self._save(st)                                     # the store on disk (rev 1) carries no marker...
        rev = jd.load_goals(WORKER)["rev"]
        jd._relay_write_entry(WORKER, step, "mk-1", rev + 1)   # ...and an entry names a publish the disk does not show yet
        self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(len(self._queue()), 1, "kept: the publish that wrote it is not on disk")
        st = jd.load_goals(WORKER)
        st["nodes"][step]["text"] = "Ask which client to change (edited)"
        self._save(st)                                     # rev 2 published, still no marker and no record
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(self._queue(), [], "bounded: the revision passed without the marker, the entry is spent")
        self.assertIn("gone with no record", err.getvalue())

    def test_an_entry_is_spent_by_a_record_naming_its_marker_or_a_newer_marker(self):
        st, top, step = self.store(delegated=True)
        st["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "x", "t": NOW, "marker": "mk-1"}
        self._save(st)
        jd._relay_write_entry(WORKER, step, "mk-1", 1)
        self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(self._queue(), [], "the record names the marker: spent")
        st = jd.load_goals(WORKER)
        st["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-3"}
        self._save(st)
        jd._relay_write_entry(WORKER, step, "mk-2", 1)     # an older marker's entry: the node moved on to mk-3
        self.assertEqual(km._relay_tick(NOW), 0, "nothing is sent for the stale entry")
        entry = json.loads((jd._relay_queue_dir() / self._queue()[0]).read_text())
        self.assertEqual(entry["marker"], "mk-3", "the entry is REWRITTEN for the live marker, never unlinked (the path may "
                                                  "already be the newer flush)")
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayWanted"]["id"], "mk-3", "the newer marker stands")
        self.assertEqual(km._relay_tick(NOW + 1), 0)       # mk-3 has no standing stamp here: the next tick stands it down
        self.assertEqual(self._queue(), [])
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayDone"]["marker"], "mk-3")

    def test_two_writers_never_lose_an_entry(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        other = WORKER + ":g9"
        st["nodes"][other] = node(other, "Another question", parent=top, t=T0 + 700,
                                  relayWanted={"peer": MANAGER, "why": "q", "t": T0 + 700, "id": "mk-other"})
        self._save(st)
        real = km._bus_send_relay
        def slow_bus(payload):                            # the judge appends an entry WHILE the tick is mid-send
            jd._relay_write_entry(WORKER, other, "mk-other", st["rev"])
            return real(payload)
        km._bus_send_relay = slow_bus
        self.assertGreaterEqual(km._relay_tick(NOW), 1)
        self.assertIn(jd._relay_entry_path(WORKER, other).name, self._queue(),
                      "the entry appended mid-tick survives for the next tick: two writers never rewrite one list")

    def test_a_parked_relay_is_pending_until_it_lands_or_comes_back(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "relay-far-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0, "parked is not sent")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual((nd["relayWanted"]["pendingMid"], nd["relayWanted"]["pendingHost"]), ("relay-far-1", "TESTHOST"))
        self.assertEqual(len(self._queue()), 1, "pending")
        self.rows.append({"id": "relay-far-1", "ev": "bounced", "t": NOW + 30, "to_id": MANAGER, "from_id": WORKER})
        self._write_mail()
        self.assertEqual(km._relay_tick(NOW + 60), 0)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertTrue(nd["blocked"], "the bounce is the definitive refusal: the user's block")
        self.assertEqual(self._queue(), [])

    def test_a_parked_relay_needs_delivery_proof_a_later_message_is_not_its_answer(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "relay-far-2", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append(mail(MANAGER, WORKER, NOW + 40, kind="coordinate"))   # the peer writes, but the parked question
        self._write_mail()                                                     #   cannot have been read
        self.assertEqual(km._relay_tick(NOW + 60), 0, "still pending: no delivery proof")
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayWanted"]["pendingMid"], "relay-far-2")
        self.rows.append({"t": NOW + 70, "ev": "relayed", "id": "relay-far-2", "host": "TESTHOST"})
        self._write_mail()
        self.assertEqual(km._relay_tick(NOW + 90), 1, "the far host's ack completes it")
        self.assertEqual(self._queue(), [])

    def test_a_relay_in_flight_to_a_host_that_is_up_is_pending_until_the_far_host_delivers(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-3", "note": "relaying to 'web' on TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0, "the relay leg's answer is pending, host up or not")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual(nd["relayWanted"]["pendingMid"], "px-far-3")
        self.assertEqual(km._relay_tick(NOW + 10), 0, "still pending: no delivered row, no answer")
        self.rows.append({"t": NOW + 30, "ev": "relayed", "id": "px-far-3", "host": "TESTHOST"})   # the far host's ack
        self._write_mail()
        self.assertEqual(km._relay_tick(NOW + 60), 1, "delivered: the relay stands as sent")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual((nd["relayed"]["mid"], nd["relayed"]["marker"]), ("px-far-3", st["nodes"][step]["relayWanted"]["id"]))
        self.assertNotIn("relayWanted", nd)
        self.assertEqual(self._queue(), [])

    def test_a_relay_in_flight_that_bounces_reverts_to_the_users_block(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-4", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append({"t": NOW + 30, "ev": "bounced", "id": "px-far-4", "host": "TESTHOST", "why": "no live session"})
        self._write_mail()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 60), 0)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertTrue(nd["blocked"], "the bounce is the definitive refusal: the user's block")
        self.assertEqual((nd["relayDone"]["outcome"], nd["relayDone"]["marker"]), ("refused", st["nodes"][step]["relayWanted"]["id"]))
        self.assertEqual(self._queue(), [])

    def test_an_entry_whose_node_is_gone_is_spent(self):
        st, top, step = self.store(delegated=True)
        self._save(st)
        jd._relay_write_entry(WORKER, WORKER + ":gone", "mk-x", 1)
        self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(self._queue(), [], "a rewound or archived node's entry is spent, nothing sent")
        self.assertEqual(self.sent, [])

    def test_a_pending_relay_the_worker_withdrew_reverts_as_withdrawn(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-5", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append({"t": NOW + 30, "ev": "recall", "id": "px-far-5"})
        self._write_mail()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 60), 0)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertTrue(nd["blocked"])
        self.assertIn("was withdrawn by api", nd["relayRefusal"])
        self.assertEqual(nd["log"][-1]["why"], "cannot move further without you", "the block keeps its own why")

    def test_a_bounce_carries_the_far_hosts_reason_into_the_users_block(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-6", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append({"t": NOW + 30, "ev": "bounced", "id": "px-far-6", "host": "TESTHOST", "why": "no live session named web"})
        self._write_mail()
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 60)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertIn("came back from TESTHOST: no live session named web", nd["relayRefusal"])
        self.assertEqual(nd["log"][-1]["why"], "cannot move further without you", "the mechanism's note stays out of the why")
        self.assertEqual(nd["log"][-1]["ev_t"], NOW + 30, "filed at the bounce row's time: a user reopen after it outranks it")

    def test_a_bounce_on_a_wait_another_holder_ended_stands_down(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-6b", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append({"t": NOW + 30, "ev": "bounced", "id": "px-far-6b", "host": "TESTHOST", "why": "no live session"})
        self._write_mail()
        real = km._relay_ended_since
        km._relay_ended_since = lambda sid, nid, t: True   # the fresh read finds the wait ended at or after the bounce
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(km._relay_tick(NOW + 60), 0)
        finally:
            km._relay_ended_since = real
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertFalse(nd["blocked"], "nothing to revert to")
        self.assertEqual((nd["relayDone"]["outcome"], nd["relayDone"]["pending"]), ("stood-down", "bounced"))

    def test_ended_since_reads_the_node_on_disk(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        self.assertFalse(km._relay_ended_since(WORKER, step, NOW))
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._save(st)
        self.assertTrue(km._relay_ended_since(WORKER, step, NOW), "a lift at or after the bounce's time")
        self.assertFalse(km._relay_ended_since(WORKER, step, NOW + 10), "a lift before it is not an ending since")

    def test_the_pending_stamp_survives_a_holders_save_so_the_question_is_sent_once(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        holder = jd.load_goals(WORKER)                     # a judge holder loads before the tick...
        calls = []
        km._bus_send_relay = lambda payload: calls.append(payload) or (True, "", False, {"ok": True, "id": "px-far-8", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        holder["nodes"][top]["text"] = "Ship the exporter (renamed)"
        self._save(holder)                                 # ...and saves after it: its copy lacked the pending stamp
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayWanted"]["pendingMid"], "px-far-8", "the merge kept the stamp")
        self.assertEqual(km._relay_tick(NOW + 10), 0)
        self.assertEqual(len(calls), 1, "the far-host manager gets the question once")

    def test_a_record_lands_before_its_entry_is_spent(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        real = jd.save_goals
        state = {"raised": False}
        def failing_save(fsid, store):
            if not state["raised"]:
                state["raised"] = True
                raise OSError("disk full")
            return real(fsid, store)
        jd.save_goals = failing_save
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(km._relay_tick(NOW), 0, "the save raised: nothing counted")
            self.assertEqual(len(self._queue()), 1, "the entry is kept for the next tick")
            self.assertEqual(len(self.sent), 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(km._relay_tick(NOW + 1), 1, "the bus's own row of the first send is adopted")
        finally:
            jd.save_goals = real
        self.assertEqual(len(self.sent), 1, "never a second send: the manager holds the question once")
        self.assertEqual(self._queue(), [])
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual((nd["relayed"]["peer"], nd["relayed"]["mid"]), (MANAGER, "relay-1"))

    def test_a_send_with_an_unknown_outcome_is_settled_by_the_bus_row_next_tick(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        real = km._bus_send_relay
        def lossy_bus(payload):                            # the bus delivered, the answer never came back
            real(payload)
            return False, "timed out", False, {"unknown": True}
        km._bus_send_relay = lossy_bus
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(km._relay_tick(NOW), 0)
        self.assertIn("outcome is unknown", err.getvalue())
        self.assertEqual(len(self._queue()), 1)
        self.assertEqual(km._relay_tick(NOW + 1), 1, "the row names the marker: adopted")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self._queue(), [])

    def test_a_stalled_bus_is_not_asked_again_before_its_timeout_and_the_row_settles_it(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        real = km._bus_send_relay
        calls = []
        def stalled_bus(payload):                          # the request went in; no answer within the read timeout
            calls.append(payload)
            return False, "timed out", False, {"unknown": True}
        km._bus_send_relay = stalled_bus
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(km._relay_tick(NOW), 0)
            self.assertEqual(km._relay_tick(NOW + 1), 0)
            self.assertEqual(km._relay_tick(NOW + 12), 0)
        self.assertEqual(len(calls), 1, "held: nothing is sent again inside the bus's own timeout")
        self.assertEqual(err.getvalue().count("outcome is unknown"), 1, "said once")
        real(calls[0])                                     # the bus drains: its row lands, the manager holds one question
        self.assertEqual(km._relay_tick(NOW + 13), 1, "the row names the marker: adopted")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.sent), 1)

    def test_a_judges_save_inside_the_hold_keeps_the_hold(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        holder = jd.load_goals(WORKER)                     # a judge pass loads before the tick...
        calls = []
        km._bus_send_relay = lambda payload: calls.append(payload) or (False, "timed out", False, {"unknown": True})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)     # ...the bus stalls: the marker carries the hold...
        holder["nodes"][top]["text"] = "Ship the exporter (renamed)"
        self._save(holder)                                 # ...and the judge saves inside the hold
        rw = jd.load_goals(WORKER)["nodes"][step]["relayWanted"]
        self.assertEqual((rw["unknownAt"], rw["attempts"]), (NOW, 1), "the merge carried the tick's fields")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 2), 0)
        self.assertEqual(len(calls), 1, "the stalled bus is not asked again")

    def test_a_stalled_bus_that_never_wrote_is_asked_again_after_the_hold(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        calls = []
        km._bus_send_relay = lambda payload: calls.append(payload) or (False, "timed out", False, {"unknown": True})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)
            self.assertEqual(km._relay_tick(NOW + km.RELAY_UNKNOWN_HOLD - 1), 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(km._relay_tick(NOW + km.RELAY_UNKNOWN_HOLD), 0)
        self.assertEqual(len(calls), 2, "the hold passed with no row: asked again")
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayWanted"]["attempts"], 2)

    def test_a_far_route_duplicate_answer_is_pending_on_the_named_host(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-dup-1", "duplicate": True, "host": "TESTHOST",
                                                                "note": "already relayed to TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        rw = jd.load_goals(WORKER)["nodes"][step]["relayWanted"]
        self.assertEqual((rw["pendingMid"], rw["pendingHost"]), ("px-dup-1", "TESTHOST"), "a later bounce names the host")

    def test_a_relay_leg_send_with_a_lost_answer_is_adopted_as_pending(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        marker = st["nodes"][step]["relayWanted"]["id"]
        def lossy_relay_leg(payload):                      # the relay leg wrote its sent row, then the answer was lost
            self.rows.append({"t": NOW, "ev": "sent", "id": "px-lost-1", "from_id": WORKER, "to_id": "peer:TESTHOST",
                              "to_sid": MANAGER, "kind": "question", "relayed": True, "relayMarker": payload["relayMarker"]})
            self._write_mail()
            return False, "timed out", False, {"unknown": True}
        km._bus_send_relay = lossy_relay_leg
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)
        km._bus_send_relay = lambda payload: self.fail("a second send")
        self.assertEqual(km._relay_tick(NOW + 1), 0)
        rw = jd.load_goals(WORKER)["nodes"][step]["relayWanted"]
        self.assertEqual((rw["id"], rw["pendingMid"], rw["pendingHost"]), (marker, "px-lost-1", "TESTHOST"))

    def test_a_later_markers_transient_failure_is_said_after_an_earlier_one_stood_down(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (False, "bus down", False, {})
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(km._relay_tick(NOW), 0)
        self.assertIn("retried next tick", err.getvalue())
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)   # the wait ends
        self._save(st)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 6), 0)   # stood down
        st = jd.load_goals(WORKER)                         # a fresh holder files the next block
        self._close(st, step, "now stuck again", NOW + 60)
        self._save(st)
        with contextlib.redirect_stderr(io.StringIO()) as err2:
            self.assertEqual(km._relay_tick(NOW + 61), 0)
        self.assertIn("retried next tick", err2.getvalue(), "the new marker's failure is said once too")

    def test_a_quiet_pass_is_not_repeated_until_something_moves(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-far-9", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)           # pending, parked
        real = jd.load_goals
        loads = []
        jd.load_goals = lambda sid: loads.append(sid) or real(sid)
        try:
            self.assertEqual(km._relay_tick(NOW + 1), 0)
            self.assertEqual(loads, [WORKER], "the first pass reads the store and finds nothing to do")
            self.assertEqual(km._relay_tick(NOW + 2), 0)
            self.assertEqual(km._relay_tick(NOW + 3), 0)
            self.assertEqual(loads, [WORKER], "nothing moved: no store parse per tick for a parked relay")
            self.rows.append({"t": NOW + 30, "ev": "relayed", "id": "px-far-9", "host": "TESTHOST"})
            self._write_mail()                             # the log moved: the far host delivered
            self.assertEqual(km._relay_tick(NOW + 60), 1)
            self.assertEqual(loads, [WORKER, WORKER])
        finally:
            jd.load_goals = real

    def test_a_pending_relay_whose_wait_ended_is_recalled(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-r1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        recalls = []
        real = km._bus_recall_relay
        km._bus_recall_relay = lambda sid, mid: recalls.append((sid, mid)) or "withdrawn"
        try:
            st = jd.load_goals(WORKER)
            jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)   # ended another way
            self._save(st)
            self.assertEqual(km._relay_tick(NOW + 10), 0)
        finally:
            km._bus_recall_relay = real
        self.assertEqual(recalls, [(WORKER, "px-r1")], "the parked question is withdrawn from the far host's outbox")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual((nd["relayDone"]["outcome"], nd["relayDone"]["recall"]), ("stood-down", "withdrawn"))
        self.assertEqual(self._queue(), [])

    def test_a_pending_relay_the_far_host_delivered_settles_though_the_wait_ended(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-r2", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._save(st)
        self.rows.append({"t": NOW + 30, "ev": "relayed", "id": "px-r2", "host": "TESTHOST"})
        self._write_mail()
        km._bus_recall_relay = lambda sid, mid: self.fail("nothing to recall: it was delivered")
        self.assertEqual(km._relay_tick(NOW + 60), 1, "delivered settles it, whatever the wait did")
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayed"]["mid"], "px-r2")

    def test_a_recovered_row_behind_the_ack_never_ends_the_walk(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-r3", "note": "relaying"})
        self.assertEqual(km._relay_tick(NOW), 0)
        self.rows.append({"t": NOW + 30, "ev": "relayed", "id": "px-r3", "host": "TESTHOST"})
        self.rows.append({"t": NOW - 5000, "ev": "sent", "id": "old-mail", "from_id": WORKER, "to_id": MANAGER, "recovered": True})
        self._write_mail()                                 # the rowless rebuild appended an old row behind the ack
        self.assertEqual(km._relay_tick(NOW + 60), 1, "the ack is found past the recovered row")

    def test_a_lost_relay_leg_send_whose_wait_ended_is_adopted_then_recalled(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        marker = st["nodes"][step]["relayWanted"]["id"]
        def lossy_relay_leg(payload):
            self.rows.append({"t": NOW, "ev": "sent", "id": "px-lost-2", "from_id": WORKER, "to_id": "peer:TESTHOST",
                              "to_sid": MANAGER, "kind": "question", "relayed": True, "relayMarker": payload["relayMarker"]})
            self._write_mail()
            return False, "timed out", False, {"unknown": True}
        km._bus_send_relay = lossy_relay_leg
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)   # the wait ends
        self._save(st)
        recalls = []
        km._bus_recall_relay = lambda sid, mid: recalls.append(mid) or "withdrawn"
        km._bus_send_relay = lambda payload: self.fail("a second send")
        self.assertEqual(km._relay_tick(NOW + 10), 0)
        self.assertEqual(recalls, ["px-lost-2"], "adopted as pending, then recalled: the far host never delivers it")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual((nd["relayDone"]["outcome"], nd["relayDone"]["recall"], nd["relayDone"]["marker"]), ("stood-down", "withdrawn", marker))

    def test_a_row_the_bus_bounced_as_not_parked_was_never_sent(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        marker = st["nodes"][step]["relayWanted"]["id"]
        self.rows.append({"t": NOW - 5, "ev": "sent", "id": "px-np", "from_id": WORKER, "to_id": "peer:TESTHOST",
                          "to_sid": MANAGER, "kind": "question", "relayed": True, "relayMarker": marker})
        self.rows.append({"t": NOW - 5, "ev": "bounced", "id": "px-np", "host": "TESTHOST",
                          "why": "not parked: the outbox record could not be written", "notParked": True})
        self._write_mail()
        self.assertEqual(km._relay_tick(NOW), 1, "the not-parked row is no send: a fresh one goes out")
        self.assertEqual(len(self.sent), 1)

    def test_an_unknown_recall_keeps_the_entry_for_the_next_tick(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ur", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._save(st)
        answers = ["unknown", "carried"]
        km._bus_recall_relay = lambda sid, mid: answers.pop(0)
        self.assertEqual(km._relay_tick(NOW + 10), 0)
        self.assertEqual(len(self._queue()), 1, "the bus could not be asked: kept")
        self.assertIn("relayWanted", jd.load_goals(WORKER)["nodes"][step])
        self.assertEqual(km._relay_tick(NOW + 11), 0)
        self.assertEqual(len(answers), 1, "held: not asked again inside the hold")
        self.assertEqual(km._relay_tick(NOW + 10 + km.RELAY_UNKNOWN_HOLD), 0)
        self.assertEqual(self._queue(), [])
        self.assertEqual(jd.load_goals(WORKER)["nodes"][step]["relayDone"]["recall"], "carried: could not be withdrawn")

    def test_an_entry_is_spent_only_when_the_file_still_holds_what_was_read(self):
        jd._relay_write_entry(WORKER, WORKER + ":g2", "mk-a", 3)
        f = jd._relay_entry_path(WORKER, WORKER + ":g2")
        e = json.loads(f.read_text())
        jd._relay_write_entry(WORKER, WORKER + ":g2", "mk-b", 4)   # the judge's flush renamed a newer marker's entry over it
        km._relay_spend(f, e)
        self.assertTrue(f.exists(), "the newer entry stays")
        self.assertEqual(json.loads(f.read_text())["marker"], "mk-b")
        km._relay_spend(f, json.loads(f.read_text()))
        self.assertFalse(f.exists(), "spent once it is the one read")

    def test_a_dead_workers_block_is_not_relayed(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        self.assertEqual(km._relay_tick(NOW, alive_ids={MANAGER}), 0, "the worker is not alive: nobody asks")
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self._queue()), 1, "the entry waits")
        self.assertEqual(km._relay_tick(NOW + 1, alive_ids={MANAGER}), 0)
        self.assertEqual(km._relay_tick(NOW + 2, alive_ids={MANAGER, WORKER}), 1, "alive again: sent")

    def test_the_relayed_body_speaks_no_romp_and_drops_a_procedural_why(self):
        self.assertEqual(km._relay_body("api", jd.NUDGE_BLOCK_WHY), "api cannot move further on this and needs your call.")
        self.assertEqual(km._relay_body("api", ""), "api cannot move further on this and needs your call.")
        body = km._relay_body("api", "the goal is blocked until the user approves the schema; card held open; which schema version should ship?")
        self.assertEqual(body, "api cannot move further: which schema version should ship?")
        self.assertEqual(km._relay_body("api", "the dashboard keyboard shortcut collides with discard; which key?"),
                         "api cannot move further: the dashboard keyboard shortcut collides with discard; which key?",
                         "words, not substrings")
        self.assertEqual(km._relay_body("api", "the card is held open."), "api cannot move further on this and needs your call.")
        self.assertEqual(km._relay_body("api", "the cards are nudged and the goals cleared; which port?"),
                         "api cannot move further: which port?", "inflections are the same words")
        self.assertEqual(km._relay_body("api", "the goal is blocked; which goal should ship first?"),
                         "api cannot move further: which goal should ship first?", "a question is always kept")
        self.assertEqual(km._relay_body("api", "No answer from the payments vendor yet; should I switch providers?"),
                         "api cannot move further: No answer from the payments vendor yet; should I switch providers?",
                         "a real question under the debt ladder's prefix is not procedural")
        self.assertEqual(km._relay_body("api", jd.DEBT_BLOCK_WHY_PREFIX + "web after two reminders"),
                         "api cannot move further on this and needs your call.")

    def test_a_malformed_entry_drops_alone(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        (jd._relay_queue_dir() / "garbage.json").write_text("{not json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW), 1, "the good entry was relayed")
        self.assertEqual(self._queue(), [])
        self.assertEqual(km._RELAY_BAD["n"], 1)

    def test_the_boot_pass_requeues_an_orphaned_marker(self):
        st, top, step = self.store(delegated=True)
        st["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-boot"}
        st["rev"] = 7
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(st))   # a marker on disk with no entry
        self.assertEqual(self._queue(), [])
        self.assertEqual(jd._requeue_relays_all(), 1)
        self.assertEqual(len(self._queue()), 1)
        entry = json.loads((jd._relay_queue_dir() / self._queue()[0]).read_text())
        self.assertEqual((entry["marker"], entry["rev"]), ("mk-boot", 7))
        self.assertEqual(jd._requeue_relays_all(), 0, "idempotent")


class TwoDelegators(_Peer):
    def test_a_top_is_attributed_to_the_mail_its_anchor_names(self):
        # two managers dispatched this worker; each goal's block goes to the manager whose mail anchored it
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="delegate", mid="m-web-1"))
        self.rows.append(mail(OTHER, WORKER, T0 - 100, kind="delegate", mid="m-tests-1"))
        self._write_mail()
        st, top, step = self.store()
        st["nodes"][top]["askAnchor"] = "machine"; st["nodes"][top]["promptMsgId"] = "m-web-1"
        nd = self._close_block(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "web's mail anchored it, though tests wrote later")
        st2, top2, step2 = self.store()
        st2["nodes"][top2]["askAnchor"] = "machine"; st2["nodes"][top2]["promptMsgId"] = ""
        nd2 = self._close_block(st2, step2, "cannot move further without you")
        self.assertEqual(list(nd2.get("awaitingPeers") or ()), [OTHER], "no mail id on the anchor: the latest delegate")

    def test_the_planner_pass_latches_the_anchors_mail_id_from_the_parse(self):
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="delegate", mid="m-web-1"))
        self._write_mail()
        st, top, step = self.store()
        session = {"turns": [{"atoms": [{"uuid": "u1", "type": "user", "message": {"role": "user", "content": [{"type": "text",
                   "text": "Ship the exporter\n<!-- romp-msg-id: m-web-1 -->\n<!-- romp-msg-kind: delegate -->"}]}}]}]}
        st["nodes"][top]["promptUuid"] = "u1"
        self.assertEqual(jd._latch_prompt_msg_ids(session, st), 1)
        self.assertEqual(st["nodes"][top]["promptMsgId"], "m-web-1")
        self.assertEqual(jd._latch_prompt_msg_ids(session, st), 0, "latched once")

    def test_a_batched_inbox_is_attributed_to_its_delegate_marker_not_its_first(self):
        self.rows.append(mail(OTHER, WORKER, T0 - 300, kind="coordinate", mid="m-tests-c"))
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="delegate", mid="m-web-1"))
        self._write_mail()
        st, top, step = self.store()
        text = ("Heads up, the tests are flaky today\n<!-- romp-msg-id: m-tests-c -->\n<!-- romp-msg-kind: coordinate -->\n"
                "Ship the exporter\n<!-- romp-msg-id: m-web-1 -->\n<!-- romp-msg-kind: delegate -->")
        session = {"turns": [{"atoms": [{"uuid": "u1", "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]}]}
        st["nodes"][top]["promptUuid"] = "u1"; st["nodes"][top]["askAnchor"] = "machine"
        self.assertEqual(jd._latch_prompt_msg_ids(session, st), 1)
        self.assertEqual(st["nodes"][top]["promptMsgId"], "m-web-1", "the delegate marker, though a peer's coordinate came first")
        nd = self._close_block(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER])

    def test_a_delivery_with_no_delegate_marker_leaves_the_fallback_to_the_latest_delegate(self):
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="delegate", mid="m-web-1"))
        self._write_mail()
        st, top, step = self.store()
        text = "Heads up\n<!-- romp-msg-id: m-tests-c -->\n<!-- romp-msg-kind: coordinate -->"
        session = {"turns": [{"atoms": [{"uuid": "u1", "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]}]}
        st["nodes"][top]["promptUuid"] = "u1"; st["nodes"][top]["askAnchor"] = "machine"
        jd._latch_prompt_msg_ids(session, st)
        self.assertEqual(st["nodes"][top]["promptMsgId"], "", "no delegate in the delivery")
        nd = self._close_block(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "the latest delegate at or before the mint")

    def _close_block(self, st, step, why):
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: why}, "awaiting": {}}, t=T0 + 400)
        return st["nodes"][step]

    def _session(self, text):
        return {"turns": [{"atoms": [{"uuid": "u1", "type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}]}]}

    def test_two_delegates_in_one_delivery_attribute_to_the_last(self):
        self.rows.append(mail(MANAGER, WORKER, T0 - 300, kind="delegate", mid="m-web-1"))
        self.rows.append(mail(OTHER, WORKER, T0 - 200, kind="delegate", mid="m-tests-1"))
        self._write_mail()
        st, top, step = self.store()
        st["nodes"][top]["promptUuid"] = "u1"; st["nodes"][top]["askAnchor"] = "machine"
        jd._latch_prompt_msg_ids(self._session("A\n<!-- romp-msg-id: m-web-1 -->\n<!-- romp-msg-kind: delegate -->\n"
                                               "B\n<!-- romp-msg-id: m-tests-1 -->\n<!-- romp-msg-kind: delegate -->"), st)
        self.assertEqual(st["nodes"][top]["promptMsgId"], "m-tests-1", "the last dispatch of a drained inbox, the delivery's own peer")
        self.assertEqual(list(self._close_block(st, step, "stuck").get("awaitingPeers") or ()), [OTHER])

    def test_a_delegate_marker_quoted_from_another_sessions_dispatch_names_nobody_here(self):
        third = "11111111-2222-3333-4444-dddddddddddd"
        self.rows.append(mail(OTHER, third, T0 - 400, kind="delegate", mid="m-foreign"))    # a dispatch to a THIRD session
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="", mid="m-web-plain"))      # the manager's plain mail quoting it
        self._write_mail()
        st, top, step = self.store()
        st["nodes"][top]["promptUuid"] = "u1"; st["nodes"][top]["askAnchor"] = "machine"
        jd._latch_prompt_msg_ids(self._session("FYI, here is what I got:\n<!-- romp-msg-id: m-foreign -->\n<!-- romp-msg-kind: delegate -->\n"
                                               "<!-- romp-msg-id: m-web-plain -->"), st)
        self.assertEqual(st["nodes"][top]["promptMsgId"], "", "a quoted foreign dispatch is no dispatch to this session")
        self.assertIsNone(jd._delegate_sender("m-foreign", WORKER))
        self.assertEqual(jd._delegate_sender("m-foreign"), OTHER, "without a recipient, the row's sender as before")
        self.assertEqual(self._close_block(st, step, "stuck").get("awaitingPeers"), None, "no delegate at all: the user's block")

    def test_a_stamp_naming_no_dispatch_to_this_session_leaves_the_fallback(self):
        self.rows.append(mail(MANAGER, WORKER, T0 - 200, kind="delegate", mid="m-web-1"))
        self.rows.append(mail(OTHER, WORKER, T0 - 100, kind="coordinate", mid="m-tests-c"))
        self._write_mail()
        st, top, step = self.store()
        st["nodes"][top]["askAnchor"] = "machine"; st["nodes"][top]["promptMsgId"] = "m-tests-c"   # an older stamp (the first-marker rule)
        nd = self._close_block(st, step, "cannot move further without you")
        self.assertEqual(list(nd.get("awaitingPeers") or ()), [MANAGER], "as for an anchor that names none: the latest delegate")


class MoreRules(_Peer):
    def _close_block(self, st, step, why, t=T0 + 400):
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_close(st, [st["nodes"][step]], {"done": {}, "block": {1: why}, "awaiting": {}}, t=t)
        return st["nodes"][step]

    def test_a_tracker_in_the_users_own_session_keeps_its_block(self):
        st, top, step = self.store()                                          # no delegation: the user's own session
        st["nodes"][step]["handoff"] = {"peer": OTHER, "goalId": OTHER + ":g3"}
        nd = self._close_block(st, step, "need your call before I hand more over")
        self.assertTrue(nd["blocked"], "a decision only the user can make")

    def test_one_stamp_per_standing_wait_whatever_the_words(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        nd = self._close_block(st, step, "cannot move further without an answer")
        nd = self._close_block(st, step, "still waiting, now on the second point too", T0 + 900)
        rows = [e for e in nd["log"] if e.get("kind") == "awaiting"]
        self.assertEqual(len(rows), 1, "the standing wait keeps its since-time; no second stamp")
        self.assertEqual(nd["awaitingWhy"], "cannot move further without an answer")

    def test_a_cross_host_open_ask_is_a_peer_wait_with_no_relay(self):
        st, top, step = self.store(delegated=True)
        self.rows.append({"id": "m-x", "from_id": WORKER, "to_id": "peer:TESTHOST", "toName": "far", "from": "api", "t": T0 + 300, "kind": "question"})
        self._write_mail()
        nd = self._close_block(st, step, "waiting on far")
        self.assertEqual(len(nd.get("awaitingPeers") or ()), 1)
        self.assertTrue(str(nd["awaitingPeers"][0]).startswith("peer:"), "the wait maps' own key for an unresolved far recipient")
        self.assertNotIn("relayWanted", nd, "the ask exists: nothing to relay")

    def test_a_host_qualified_sender_is_never_the_delegating_peer(self):
        self.ask("TESTHOST:" + OTHER, WORKER, T0 - 100, kind="delegate")
        st, top, step = self.store()
        st["nodes"][top]["askAnchor"] = "machine"
        nd = self._close_block(st, step, "cannot move further without you")
        self.assertTrue(nd["blocked"])

    def test_an_interrupt_block_is_never_lifted_into_a_wait(self):
        st, top, step = self.store(delegated=True)
        self.ask(WORKER, MANAGER, T0 + 300)
        st["nodes"][step]["blocked"] = True
        st["nodes"][step]["log"] = [{"kind": "block", "src": "interrupt", "ev_t": T0 + 350, "why": "stopped by the user"}]
        nd = self._close_block(st, step, "still waiting on the manager")
        self.assertTrue(nd["blocked"], "the interrupt stands")
        self.assertEqual([e["kind"] for e in nd["log"]], ["block"])

    def test_the_planners_block_row_carries_its_segment(self):
        st, top, step = self.store()
        with contextlib.redirect_stderr(io.StringIO()):
            jd.apply_plan(st, "seg-7", T0 + 900, [{"do": "block", "why": "needs your call", "goal": 1}], [st["nodes"][step]])
        row = [e for e in st["nodes"][step]["log"] if e.get("kind") == "block"][0]
        self.assertEqual(row.get("seg"), "seg-7")


class SendRouteRelayed(unittest.TestCase):
    """The bus's /send carries relayed into deliver: the maildir header, the row and the inbox read all say so."""

    def test_the_send_route_relays_end_to_end(self):
        ps = load_source("romp_postal_service_pw_route", os.path.join(os.path.dirname(HERE), "postal", "postal_service.py"))
        live = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump([{"id": MANAGER, "name": "web"}], live); live.close()
        prior = os.environ.get("ROMP_SESSIONS_FILE"); os.environ["ROMP_SESSIONS_FILE"] = live.name
        try:
            h = object.__new__(ps.Handler)
            raw = json.dumps({"to": MANAGER, "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                              "body": "api cannot move further: which client?"}).encode()
            h.path = "/send"; h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": ps.SERVE_TOKEN}; h.rfile = io.BytesIO(raw)
            out = []; h._send = lambda obj, code=200: out.append((obj, code))
            h.do_POST()
        finally:
            if prior is None: os.environ.pop("ROMP_SESSIONS_FILE", None)
            else: os.environ["ROMP_SESSIONS_FILE"] = prior
            os.unlink(live.name)
        obj, code = out[0]
        self.assertEqual(code, 200, obj)
        got = [m for m in ps.read_box(MANAGER, False) if m.get("from_id") == WORKER]
        self.assertTrue(got and got[-1].get("relayed"), "the recipient's inbox read carries the mark")
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        self.assertTrue(any(r.get("relayed") and r.get("from_id") == WORKER and r.get("kind") == "question" for r in rows))


class MergeCarriesTheRelay(_Peer):
    """Two writers touch the relay keys (the judge marks relayWanted, the kernel's tick replaces it with relayed): the
    store merge on save carries the newer fact, so a stale copy never resurrects a relay or loses its record."""

    def test_a_stale_judge_copy_does_not_resurrect_a_relay_the_kernel_sent(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "cannot move further", "t": T0 + 400, "id": "mk-a"}
        disk["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "cannot move further", "t": NOW, "marker": "mk-a"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayed"]["peer"], MANAGER)
        self.assertNotIn("relayWanted", mem["nodes"][step])

    def test_a_record_settles_only_the_marker_it_names(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "cannot move further", "t": T0 + 400, "id": "mk-b"}
        disk["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "cannot move further", "t": NOW, "marker": "mk-a"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayWanted"]["id"], "mk-b", "same words, later clock: a NEW marker stands")

    def test_the_newer_record_wins_in_either_direction(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "a", "t": NOW + 50, "marker": "mk-b"}
        disk["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "a", "t": NOW, "marker": "mk-a"}
        mem["nodes"][step]["relayDone"] = {"peer": MANAGER, "why": "a", "t": NOW, "marker": "mk-a", "outcome": "stood-down"}
        disk["nodes"][step]["relayDone"] = {"peer": MANAGER, "why": "a", "t": NOW + 50, "marker": "mk-c", "outcome": "refused"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayed"]["marker"], "mk-b", "memory's newer record stays")
        self.assertEqual(mem["nodes"][step]["relayDone"]["marker"], "mk-c", "disk's newer record is adopted")

    def test_the_same_marker_on_both_sides_keeps_the_kernels_pending_stamp_either_way(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a"}
        disk["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a",
                                              "pendingMid": "px-1", "pendingAt": NOW, "pendingHost": "TESTHOST"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayWanted"]["pendingMid"], "px-1", "a holder's stale copy gains the tick's stamp")
        mem2 = json.loads(json.dumps(st)); disk2 = json.loads(json.dumps(st))
        mem2["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a", "pendingMid": "px-1", "pendingAt": NOW}
        disk2["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk2))
        jd._rebase_onto_disk(WORKER, mem2)
        self.assertEqual(mem2["nodes"][step]["relayWanted"]["pendingMid"], "px-1", "the kernel's own copy keeps it")

    def test_the_ticks_fields_on_a_same_id_marker_are_carried_whole(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a", "attempts": 1, "unknownAt": NOW}
        disk["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a", "attempts": 2,
                                              "unknownAt": NOW + 40, "pendingMid": "px-1", "pendingAt": NOW + 41, "pendingHost": "TESTHOST",
                                              "recallUnknownAt": NOW + 42}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        rw = mem["nodes"][step]["relayWanted"]
        self.assertEqual({k: rw.get(k) for k in jd.RELAY_TICK_KEYS},
                         {"pendingMid": "px-1", "pendingAt": NOW + 41, "pendingHost": "TESTHOST", "unknownAt": NOW + 40, "attempts": 2,
                          "recallUnknownAt": NOW + 42, "recallAttempts": None})
        self.assertEqual(jd.RELAY_TICK_KEYS, ("pendingMid", "pendingAt", "pendingHost", "unknownAt", "attempts", "recallUnknownAt", "recallAttempts"), "named once")
        mem3 = json.loads(json.dumps(st)); disk3 = json.loads(json.dumps(st))
        mem3["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a", "recallUnknownAt": NOW, "recallAttempts": 1}
        disk3["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a", "recallUnknownAt": NOW + 90, "recallAttempts": 3}
        mem3["nodes"][step]["relayRecall"] = [{"id": "mk-0", "pendingMid": "px-r", "unknownAt": NOW, "attempts": 1}]
        disk3["nodes"][step]["relayRecall"] = [{"id": "mk-0", "pendingMid": "px-r", "unknownAt": NOW + 90, "attempts": 4}]
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk3))
        jd._rebase_onto_disk(WORKER, mem3)
        rw3 = mem3["nodes"][step]["relayWanted"]
        self.assertEqual((rw3["recallUnknownAt"], rw3["recallAttempts"]), (NOW + 90, 3), "the recall hold newer-wins, like the send's")
        self.assertEqual((mem3["nodes"][step]["relayRecall"][0]["unknownAt"], mem3["nodes"][step]["relayRecall"][0]["attempts"]), (NOW + 90, 4),
                         "the per-recall hold and count newer-wins too")

    def test_a_recall_done_on_the_disk_filters_the_holders_owed_list_though_the_disk_owes_none(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayRecall"] = [{"id": "mk-a", "peer": MANAGER, "pendingMid": "px-1"}]
        disk["nodes"][step]["relayRecalled"] = [{"mid": "px-1", "outcome": "withdrawn", "t": NOW}]   # the sweep's save: done, none owed
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertNotIn("relayRecall", mem["nodes"][step], "a recall done on the disk is not re-owed by a stale holder")
        self.assertEqual([d["mid"] for d in mem["nodes"][step]["relayRecalled"]], ["px-1"])

    def test_a_settled_marker_is_popped_before_the_disks_live_marker_is_adopted(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-1"}
        disk["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "q", "t": NOW, "marker": "mk-1"}
        disk["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q2", "t": NOW + 50, "id": "mk-2"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayWanted"]["id"], "mk-2", "ours was settled and popped; the live one is adopted")

    def test_two_holders_markers_for_one_wait_converge(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-mine"}
        disk["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-theirs"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayWanted"]["id"], "mk-theirs", "the published one wins: its entry is flushed")
        mem2 = json.loads(json.dumps(st)); disk2 = json.loads(json.dumps(st))
        mem2["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-mine", "pendingMid": "px-9", "pendingAt": NOW}
        disk2["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-theirs"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk2))
        jd._rebase_onto_disk(WORKER, mem2)
        self.assertEqual(mem2["nodes"][step]["relayWanted"]["id"], "mk-mine", "the one already handed to a far host wins")

    def test_the_settled_list_survives_the_record_slot_and_merges_as_a_union(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-1"}
        mem["nodes"][step]["relaySettled"] = ["mk-0"]
        disk["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "q2", "t": NOW + 50, "marker": "mk-2"}   # the slot moved on
        disk["nodes"][step]["relaySettled"] = ["mk-1", "mk-2"]
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertNotIn("relayWanted", mem["nodes"][step], "the list remembers what the slot forgot: mk-1 is settled")
        self.assertEqual(mem["nodes"][step]["relaySettled"], ["mk-0", "mk-1", "mk-2"])

    def test_a_stale_holder_never_re_mints_a_retired_marker(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a"}
        mem["nodes"][step]["relayed"] = {"peer": MANAGER, "why": "old", "t": NOW - 500, "marker": "mk-0"}
        disk["nodes"][step]["relayDone"] = {"peer": MANAGER, "why": "q", "t": NOW, "marker": "mk-a", "outcome": "stood-down"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertNotIn("relayWanted", mem["nodes"][step], "the kernel retired mk-a; the holder's copy follows")
        self.assertEqual(mem["nodes"][step]["relayed"]["marker"], "mk-0", "an unrelated older record is untouched")

    def test_a_stale_kernel_copy_keeps_a_relay_the_judge_just_asked_for(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        disk["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "cannot move further", "t": T0 + 400, "id": "mk-a"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertEqual(mem["nodes"][step]["relayWanted"]["peer"], MANAGER)

    def test_a_removal_the_kernel_recorded_is_never_resurrected(self):
        st, top, step = self.store(delegated=True)
        mem = json.loads(json.dumps(st)); disk = json.loads(json.dumps(st))
        mem["nodes"][step]["relayWanted"] = {"peer": MANAGER, "why": "q", "t": T0 + 400, "id": "mk-a"}
        disk["nodes"][step]["relayDone"] = {"peer": MANAGER, "why": "q", "t": NOW, "outcome": "stood-down", "marker": "mk-a"}
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(disk))
        jd._rebase_onto_disk(WORKER, mem)
        self.assertNotIn("relayWanted", mem["nodes"][step], "the stand-down stands")
        self.assertEqual(mem["nodes"][step]["relayDone"]["outcome"], "stood-down")


class PostalRelayedFlag(unittest.TestCase):
    """The postal deliver and its row carry the relayed mark, so the recipient and the courier can tell a relayed
    question from a typed ask."""

    def test_deliver_marks_a_relayed_question_in_the_header_and_the_row(self):
        ps = load_source("romp_postal_service_pw", os.path.join(os.path.dirname(HERE), "postal", "postal_service.py"))
        mid = ps.deliver(MANAGER, "api", WORKER, "api cannot move further: which client?", kind="question", relayed=True)
        text = (ps._mailbox(MANAGER) / "new" / mid).read_text()
        self.assertIn("X-Relayed: romp\n", text)
        self.assertIn("X-Kind: question\n", text)
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        row = next(r for r in rows if r.get("id") == mid)
        self.assertEqual((row.get("relayed"), row.get("kind"), row.get("from_id"), row.get("to_id")), (True, "question", WORKER, MANAGER))
        got = [m for m in ps.read_box(MANAGER, False) if m.get("from_id") == WORKER]
        self.assertTrue(got and all(m.get("relayed") for m in got), "the inbox read carries the mark to the recipient")
        recs = ps.format_receipts(ps._sent_receipts(WORKER))
        self.assertIn("sent on your behalf", recs, "the worker's own receipts say romp sent it")
        self.assertNotIn("romp-msg-relayed", ps.format_inbox(got, MANAGER) + ps.format_push(got), "no comment nobody reads")
        plain = ps.deliver(MANAGER, "api", WORKER, "a typed question", kind="question")
        self.assertNotIn("X-Relayed", (ps._mailbox(MANAGER) / "new" / plain).read_text())
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        self.assertNotIn("relayed", next(r for r in rows if r.get("id") == plain))


class ManagerEscalation(_Peer):
    """The debt ladder's escalation for a MANAGER debtor waits until nothing is queued for it: the nudge walk already
    runs the outcomes only for an idle debtor, and the one queue its gates miss is delivered mail the manager has not
    read, so with worker mail waiting the record stands. A peer that is not the asker's manager keeps the ladder."""

    def setUp(self):
        super().setUp()
        self._orig = {n: getattr(km, n) for n in ("_mark_views_dirty",)}
        self.inbox = jd.STATE / "postal" / "mail" / MANAGER / "new"
        self.inbox.mkdir(parents=True)
        km._autonudge_cache.clear()
        self.asker, self.debtor, self.ts, self.fire = WORKER, MANAGER, T0 + 300, T0 + 600
        self.ask(self.asker, self.debtor, self.ts)                                     # the unanswered ask
        st, top, step = self.store(delegated=True)                                     # the manager delegated the goal
        (jd.GOALDIR / (self.asker + ".json")).write_text(json.dumps(st))
        (jd.STATE / "auto-nudge.json").write_text(json.dumps(
            {"enabled": True, "nudged": {}, "debtNudged": {"%s>%s:%d" % (self.asker, self.debtor, self.ts): self.fire}}))
        km._autonudge_cache.clear()
        km._mark_views_dirty = lambda *a, **k: None

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(km, n, v)
        km._autonudge_cache.clear()
        super().tearDown()

    def _outcomes(self, unread):
        for f in self.inbox.iterdir():
            f.unlink()
        for i in range(unread):
            (self.inbox / ("m%d" % i)).write_text("{}")
        with contextlib.redirect_stderr(io.StringIO()):
            km._debt_reminder_outcomes(self.debtor, {"id": "t9", "t": self.fire + 100, "end": self.fire + 160, "ended": True}, NOW)
        keys = list((km._auto_nudge_data().get("debtNudged") or {}).keys())
        top = json.loads((jd.GOALDIR / (self.asker + ".json")).read_text())["nodes"][self.asker + ":g1"]
        return keys, top.get("blocked", False)

    def test_a_manager_with_unread_mail_is_not_yet_failing_to_answer(self):
        keys, blocked = self._outcomes(unread=3)
        self.assertEqual(len(keys), 1, "the record stands: mail waits for the manager")
        self.assertFalse(blocked)

    def test_an_idle_managers_turn_ending_without_an_answer_escalates(self):
        keys, blocked = self._outcomes(unread=0)
        self.assertEqual(keys, [], "the record retires on the event")
        self.assertTrue(blocked, "the asker's card reaches the user, through the ladder alone")

    def test_a_peer_that_is_not_the_managers_keeps_the_ladder_as_it_was(self):
        st, top, step = self.store()                                                   # no delegation from the debtor
        (jd.GOALDIR / (self.asker + ".json")).write_text(json.dumps(st))
        (jd.STATE / "postal" / "mail" / OTHER / "new").mkdir(parents=True)
        keys, blocked = self._outcomes(unread=3)
        self.assertEqual(keys, [], "an ordinary peer with mail waiting that moved on still escalates")
        self.assertTrue(blocked)


class SaverFlushesItsOwn(_RelayFixture):
    """The pending relay entries ride the STORE OBJECT: only the saver holding the object writes them, once its own
    publish (which carries the marker) is on disk; another holder's save of the same sid flushes nothing, and the
    list never reaches the file (the manager's fourth review)."""

    def test_another_holders_save_flushes_nothing_of_ours(self):
        st, top, step = self.store(delegated=True)
        self._save(st)
        mine = jd.load_goals(WORKER)                       # the closer's copy
        theirs = jd.load_goals(WORKER)                     # the planner's copy of the same store
        self._close(mine, step, "cannot move further without you", T0 + 400)
        self.assertEqual(mine["_relayPending"], [step], "remembered on the object that holds the marker")
        theirs["nodes"][top]["text"] = "Ship the exporter (renamed)"
        self._save(theirs)                                 # a save of the sid by another holder, no marker in it
        self.assertEqual(self._queue(), [], "nothing of ours flushed by their save")
        self.assertNotIn("relayWanted", jd.load_goals(WORKER)["nodes"][step])
        self._save(mine)                                   # our publish rebases onto theirs and carries the marker
        self.assertEqual(len(self._queue()), 1)
        raw = json.loads((jd.GOALDIR / (WORKER + ".json")).read_text())
        self.assertEqual(raw["nodes"][step]["relayWanted"]["id"], mine["nodes"][step]["relayWanted"]["id"])
        self.assertEqual(raw["rev"], theirs["rev"] + 1, "our publish rebased onto theirs")
        self.assertNotIn("_relayPending", raw, "transient: never serialized")
        self.assertNotIn("_relayPending", mine, "popped by the publish")
        self.assertEqual(km._relay_tick(NOW), 1)

    def test_a_no_op_save_still_flushes_when_the_file_already_holds_the_marker(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "cannot move further without you", T0 + 400)
        self._save(st)
        self.assertEqual(len(self._queue()), 1)
        (jd._relay_queue_dir() / self._queue()[0]).unlink()   # the entry is lost (a failed write, say)
        st["_relayPending"] = [step]                       # the holder still remembers it and saves nothing new
        self._save(st)
        self.assertEqual(len(self._queue()), 1, "the file holds the marker: its entry goes out on the no-op save")

    def test_a_marker_the_publishs_rebase_settled_gets_no_entry(self):
        st, top, step = self.store(delegated=True)
        self._save(st)
        a = jd.load_goals(WORKER)
        self._close(a, step, "cannot move further without you", T0 + 400)
        self._save(a)                                      # holder A publishes the marker and its entry
        self.assertEqual(km._relay_tick(NOW), 1)           # the tick relays it: relayed names the marker on disk
        self.assertEqual(self._queue(), [])
        a["_relayPending"] = [step]                        # A still remembers it (a second flush attempt, say)
        a["nodes"][top]["text"] = "Ship the exporter (renamed)"
        self._save(a)                                      # A's publish rebases: the marker is settled and popped
        self.assertNotIn("relayWanted", jd.load_goals(WORKER)["nodes"][step])
        self.assertEqual(self._queue(), [], "nothing left to relay: no entry")

    def test_a_retired_marker_handed_to_a_far_host_is_recalled_by_the_tick(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ret-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)           # handed to the far host, pending
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "second question", NOW + 60)   # the judge retires the pending marker for a new wait
        nd = st["nodes"][step]
        self.assertEqual([r["pendingMid"] for r in nd["relayRecall"]], ["px-ret-1"], "remembered for the recall")
        self.assertEqual(nd["relayWanted"]["why"], "second question")
        self._save(st)
        recalls = []
        km._bus_recall_relay = lambda sid, mid: recalls.append(mid) or "withdrawn"
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        self.assertEqual(km._relay_tick(NOW + 70), 1, "the new question goes out")
        self.assertEqual(recalls, ["px-ret-1"], "and the stale one is withdrawn from the far host's outbox")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertNotIn("relayRecall", nd)
        self.assertEqual([(d["mid"], d["outcome"]) for d in nd["relayRecalled"]], [("px-ret-1", "withdrawn")])

    def test_a_retired_marker_on_the_users_block_is_recalled_too(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ret-2", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", NOW + 5, msg=True)   # the user follows up: the block is theirs
        self._close(st, step, "still stuck", NOW + 60)
        nd = st["nodes"][step]
        self.assertTrue(nd["blocked"])
        self.assertEqual([r["pendingMid"] for r in nd["relayRecall"]], ["px-ret-2"])
        self._save(st)
        self.assertEqual(len(self._queue()), 2, "the retired marker's entry (spent on the tick) and the recall's own")
        recalls = []
        km._bus_recall_relay = lambda sid, mid: recalls.append(mid) or "carried"
        self.assertEqual(km._relay_tick(NOW + 70), 0)
        self.assertEqual(recalls, ["px-ret-2"])
        self.assertEqual(self._queue(), [], "the recall entry is spent")
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual([(d["mid"], d["outcome"]) for d in nd["relayRecalled"]],
                         [("px-ret-2", "carried: could not be withdrawn")], "a carried recall says so: the stale question reached the manager")

    def test_an_owed_recall_never_loses_its_entry_to_the_live_markers_send(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-owe-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)           # M1 handed to the far host
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "second question", NOW + 60)   # M1 retired (a recall owed), M2 minted, one entry file
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "unknown"  # the bus is restarting: the recall cannot be asked
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        self.assertEqual(km._relay_tick(NOW + 70), 1, "M2's question goes out")
        self.assertEqual(self._queue(), [jd._relay_recall_entry_path(WORKER, step).name], "the marker's entry is spent; the recall keeps its own")
        recalls = []
        km._bus_recall_relay = lambda sid, mid: recalls.append(mid) or "withdrawn"
        self.assertEqual(km._relay_tick(NOW + 70 + km.RELAY_UNKNOWN_HOLD), 0)
        self.assertEqual(recalls, ["px-owe-1"], "the recall is asked again once the hold passed")
        self.assertEqual(self._queue(), [], "and the recall's entry is spent once done")

    def test_a_recall_owed_never_touches_the_live_markers_entry_even_mid_send(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-mid-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)           # M1 handed to the far host
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "second question", NOW + 60)   # M1 retired (a recall owed), M2 minted
        self._save(st)                                     # the marker's entry (M2) and the recall's own entry
        names = self._queue()
        self.assertEqual(len(names), 2, names)
        self.assertTrue(any(n.endswith(".recall.json") for n in names), "the recall rides its own file")
        km._bus_recall_relay = lambda sid, mid: "unknown"
        marker_path = jd._relay_entry_path(WORKER, step)
        def send_while_judge_moves_on(payload):            # WHILE the tick sends M2, the judge retires it and mints M3 over the path
            st3 = jd.load_goals(WORKER)
            jd.record_verdict(st3, st3["nodes"][step], "romp", "awaiting", NOW + 61, why="", lift=True, end_ev=NOW + 61)
            self._close(st3, step, "third question", NOW + 62)
            jd.save_goals(WORKER, st3)
            return True, "", False, {"ok": True, "to": payload["to"]}
        km._bus_send_relay = send_while_judge_moves_on
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 70), 1, "M2 went out")
        entry = json.loads(marker_path.read_text())
        st = jd.load_goals(WORKER)
        self.assertEqual(entry["marker"], st["nodes"][step]["relayWanted"]["id"], "M3's entry survived the tick: never rewritten")
        self.assertTrue(jd._relay_recall_entry_path(WORKER, step).exists(), "the recall keeps its own entry")
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 71), 1, "M3's question goes out on the next tick, no boot needed")

    def test_a_recall_entry_flushed_during_the_ticks_pass_survives_the_spend(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)           # M1 handed to the far host
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "second question", NOW + 60)   # M1 retired (px-1 owed), M2 minted
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "unknown"  # px-1's recall held
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-2", "parked": "TESTHOST"})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 70), 0)   # M2 handed to the far host too (pending px-2)
        jd._relay_entry_path(WORKER, step).unlink()        # M2's entry lost to a failed write (the boot pass would re-queue it): the recall entry is alone in the queue
        asked = []
        def recall_while_judge_retires_m2(sid, mid):       # WHILE the tick withdraws px-1, the judge retires M2 (px-2 owed) and flushes the recall entry over the same path
            asked.append(mid)
            st3 = jd.load_goals(WORKER)
            jd.record_verdict(st3, st3["nodes"][step], "romp", "awaiting", NOW + 71, why="", lift=True, end_ev=NOW + 71)
            self._close(st3, step, "third question", NOW + 72)
            jd.save_goals(WORKER, st3)
            return "withdrawn"
        km._bus_recall_relay = recall_while_judge_retires_m2
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 70 + km.RELAY_UNKNOWN_HOLD)
        self.assertEqual(asked, ["px-1"])
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertEqual([r["pendingMid"] for r in nd["relayRecall"]], ["px-2"], "the store owes px-2 (the tick's save folded it in)")
        self.assertTrue(jd._relay_recall_entry_path(WORKER, step).exists(), "the judge's fresh recall entry survived the tick's spend: its token moved")
        asked.clear()
        km._bus_recall_relay = lambda sid, mid: asked.append(mid) or "withdrawn"
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 71 + km.RELAY_UNKNOWN_HOLD)
        self.assertEqual(asked, ["px-2"], "the next tick asks for px-2, no boot needed")
        self.assertEqual(self._queue(), [], "both entries spent once done")

    def test_a_recall_list_holding_no_dict_never_raises_in_the_tick(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        st = jd.load_goals(WORKER)
        st["nodes"][step]["relayRecall"] = ["not a recall row"]   # a hand-edited or corrupted store, or a future writer's shape
        jd._relay_write_entry(WORKER, step, "recall", st.get("rev") or 0)
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(st))   # written past the merge, which would drop the junk
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(km._relay_tick(NOW), 1, "the question goes out; the junk list is skipped, never raised on")
        self.assertNotIn("Traceback", err.getvalue())
        self.assertFalse(jd._relay_recall_entry_path(WORKER, step).exists(), "the junk list's entry is spent, not kept for good")
        self.assertNotIn("relayRecall", jd.load_goals(WORKER)["nodes"][step], "and the junk list is dropped from the node")
        self.assertEqual(jd._requeue_relays_all(), 0, "the boot pass re-queues nothing for it")
        src = Path(km.__file__).read_text()
        self.assertIn("if jd._relay_owes_recall(nd) and not jd._relay_recall_entry_path(sid, str(e[\"nid\"])).exists():", src,
                      "the re-queue on a spent marker entry reads the rows too, like the sweep, the flush and the boot pass")

    def test_a_question_a_far_host_still_holds_is_noted_beside_the_block(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-car-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "still stuck", NOW + 60)     # M1 retired while parked (a recall owed), M2 minted
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "carried"  # the far host carried it on before it could be withdrawn
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-car-2", "parked": "TESTHOST"})
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 70)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertIn("still parked on TESTHOST", nd["relayCarried"])
        self.assertIn("before it could be withdrawn", nd["relayCarried"])
        self.assertTrue(jd._owed_why(nd).endswith("(%s)" % nd["relayCarried"]), "the brief's owed why carries the note in brackets")
        src = Path(km.__file__).read_text()                   # the card and the modal carry the note as their OWN field: a
        self.assertIn('"relayNote": nodes[nid].get("relayCarried") or None,', src)   #   paragraph appended to the brief broke the
        self.assertIn('"relayNote": nd.get("relayCarried") or None,', src)           #   feed's per-paragraph stamps (fourth verdict)
        self.assertFalse(hasattr(km, "_brief_with_relay_note"), "the brief is the distiller's alone")
        self.assertNotIn("_brief_with_relay_note", src)
        st = jd.load_goals(WORKER)                         # a later relay that reaches the peer drops the note
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 75, why="", lift=True, end_ev=NOW + 75)
        self._close(st, step, "a fresh question", NOW + 80)
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "withdrawn"
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "to": payload["to"]})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(NOW + 90), 1)
        self.assertNotIn("relayCarried", jd.load_goals(WORKER)["nodes"][step])

    def _carried_note_then(self, outcome):
        """A carried note on the node, then M2's wait settles by `outcome`: the note must go either way."""
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-any-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "still stuck", NOW + 60)     # M1 retired while parked, M2 minted
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "carried"
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-any-2", "parked": "TESTHOST"})
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 70)
        self.assertIn("relayCarried", jd.load_goals(WORKER)["nodes"][step])
        km._bus_recall_relay = lambda sid, mid: "withdrawn"
        if outcome == "refused":                           # M2 comes back: the block is the user's, with the refusal noted
            real = km._relay_pending_status
            km._relay_pending_status = lambda sid, peer, mid, at: ("bounced", "no live recipient on TESTHOST", NOW + 80)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    km._relay_tick(NOW + 90)
            finally:
                km._relay_pending_status = real
        else:                                              # M2's wait ended another way: stood down
            st = jd.load_goals(WORKER)
            jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 80, why="", lift=True, end_ev=NOW + 80)
            jd.save_goals(WORKER, st)
            with contextlib.redirect_stderr(io.StringIO()):
                km._relay_tick(NOW + 90)
        nd = jd.load_goals(WORKER)["nodes"][step]
        self.assertNotIn("relayCarried", nd, "any settle of the wait drops the stale line: %s" % outcome)
        self.assertNotIn("relayWanted", nd)
        return nd

    def test_the_parked_note_is_dropped_when_the_users_follow_up_makes_the_block_theirs(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ret-a", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][step], "romp", "awaiting", NOW + 5, why="", lift=True, end_ev=NOW + 5)
        self._close(st, step, "still stuck", NOW + 60)     # M1 retired while parked, M2 minted
        self._save(st)
        km._bus_recall_relay = lambda sid, mid: "carried"
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ret-b", "parked": "TESTHOST"})
        with contextlib.redirect_stderr(io.StringIO()):
            km._relay_tick(NOW + 70)                       # px-ret-a carried: the note; M2 pending on the far host
        st = jd.load_goals(WORKER)
        self.assertIn("relayCarried", st["nodes"][step])
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", NOW + 80, msg=True)   # the user follows up: the next block is theirs
        self._close(st, step, "still stuck after the answer", NOW + 85)   # file_block: the user's block, M2 retired on the judge's road
        nd = st["nodes"][step]
        self.assertTrue(nd["blocked"])
        self.assertNotIn("relayWanted", nd)
        self.assertNotIn("relayCarried", nd, "the retire road drops the note: no kernel settle runs for this wait")
        self.assertNotIn("still parked", jd._owed_why(nd), "so the distiller's owed why never carries the stale line")
        self.assertEqual([r["pendingMid"] for r in nd["relayRecall"]], ["px-ret-b"], "M2's recall is still owed")

    def test_the_parked_note_is_dropped_when_the_next_relay_is_refused(self):
        nd = self._carried_note_then("refused")
        self.assertIn("could not be asked", nd.get("relayRefusal") or "", "the refusal's own note stands")

    def test_the_parked_note_is_dropped_when_the_next_wait_stands_down(self):
        nd = self._carried_note_then("stood-down")
        self.assertEqual((nd.get("relayDone") or {}).get("outcome"), "stood-down")

    def test_an_unanswerable_recall_is_said_once_and_backs_off(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-bo-1", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", NOW + 5, msg=True)   # the user's block: the marker retires
        self._close(st, step, "still stuck", NOW + 60)
        self._save(st)
        calls = []
        km._bus_recall_relay = lambda sid, mid: calls.append(mid) or "unknown"
        with contextlib.redirect_stderr(io.StringIO()) as err:
            t = NOW + 70
            for i in range(km.RELAY_RECALL_TRIES):
                self.assertEqual(km._relay_tick(t), 0)
                t += km.RELAY_UNKNOWN_HOLD
        self.assertEqual(len(calls), km.RELAY_RECALL_TRIES, "one ask per hold up to the tries")
        self.assertEqual(err.getvalue().count("could not be asked"), 1, "said once")
        self.assertEqual(err.getvalue().count("tries unanswered"), 1, "the back-off said once")
        self.assertIn("could not be reached to withdraw it", jd.load_goals(WORKER)["nodes"][step]["relayCarried"], "the eighth try leaves the note the brief and the card show")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(t + km.RELAY_UNKNOWN_HOLD), 0)
        self.assertEqual(len(calls), km.RELAY_RECALL_TRIES, "inside the stretched hold: not asked")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(km._relay_tick(t + km.RELAY_UNKNOWN_HOLD * km.RELAY_RECALL_BACKOFF), 0)
        self.assertEqual(len(calls), km.RELAY_RECALL_TRIES + 1, "asked again once the stretched hold passed")

    def test_the_boot_pass_requeues_a_node_that_owes_recalls(self):
        st, top, step = self.store(delegated=True)
        st["nodes"][step]["relayRecall"] = [{"id": "mk-old", "peer": MANAGER, "pendingMid": "px-boot", "pendingHost": "TESTHOST"}]
        st["rev"] = 3
        (jd.GOALDIR / (WORKER + ".json")).write_text(json.dumps(st))   # recalls owed, no marker, no entry (a lost entry)
        self.assertEqual(jd._requeue_relays_all(), 1)
        entry = json.loads((jd._relay_queue_dir() / self._queue()[0]).read_text())
        self.assertEqual((entry["marker"], entry["rev"]), ("recall", 3))
        self.assertEqual(jd._requeue_relays_all(), 0, "idempotent")
        recalls = []
        km._bus_recall_relay = lambda sid, mid: recalls.append(mid) or "carried"
        self.assertEqual(km._relay_tick(NOW), 0)
        self.assertEqual(recalls, ["px-boot"])
        self.assertEqual(self._queue(), [])

    def test_an_unknown_recall_owed_is_held_like_a_send(self):
        st, top, step = self.store(delegated=True)
        self._close(st, step, "first question", T0 + 400)
        self._save(st)
        km._bus_send_relay = lambda payload: (True, "", False, {"ok": True, "id": "px-ret-3", "parked": "TESTHOST"})
        self.assertEqual(km._relay_tick(NOW), 0)
        st = jd.load_goals(WORKER)
        jd.record_verdict(st, st["nodes"][top], "user", "reopen", NOW + 5, msg=True)
        self._close(st, step, "still stuck", NOW + 60)
        self._save(st)
        calls = []
        km._bus_recall_relay = lambda sid, mid: calls.append(mid) or "unknown"
        self.assertEqual(km._relay_tick(NOW + 70), 0)
        self.assertEqual(km._relay_tick(NOW + 71), 0)
        self.assertEqual(km._relay_tick(NOW + 70 + km.RELAY_UNKNOWN_HOLD - 1), 0)
        self.assertEqual(len(calls), 1, "a hung bus is asked once per hold, not once per tick")
        self.assertEqual(km._relay_tick(NOW + 70 + km.RELAY_UNKNOWN_HOLD), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self._queue()), 1, "still owed")

    def test_a_failed_publish_keeps_the_pending_list_for_the_retry(self):
        st, top, step = self.store(delegated=True)
        self._save(st)
        st = jd.load_goals(WORKER)
        self._close(st, step, "cannot move further without you", T0 + 400)
        real = jd._publish_tmp
        jd._publish_tmp = lambda d, f: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with self.assertRaises(OSError):
                self._save(st)
        finally:
            jd._publish_tmp = real
        self.assertEqual(st["_relayPending"], [step], "kept on the object: the retry publishes and flushes")
        self.assertEqual(self._queue(), [])
        self._save(st)
        self.assertEqual(len(self._queue()), 1)


class FarHostRelayMark(unittest.TestCase):
    """The relayed mark rides the bus's relay leg to a far host and the far side's deliver (trusted, or held and
    approved), so a far-host manager's inbox says a relayed question is one, as a local one does."""

    def _postal(self):
        return load_source("romp_postal_service_pw_far", os.path.join(os.path.dirname(HERE), "postal", "postal_service.py"))

    def test_the_relay_leg_carries_the_mark_and_answers_with_an_id_and_no_to(self):
        ps = self._postal()
        parked = []
        saved = (ps.resolve_recipient, ps.outbox_put, dict(ps.PEERS))
        ps.resolve_recipient = lambda to, frm_id="": {"kind": "relay", "host": "TESTHOST", "agent": {"name": "web", "id": MANAGER}}
        ps.outbox_put = lambda host, msg: parked.append((host, msg)) or True
        ps.PEERS["TESTHOST"] = {"up": True}
        try:
            h = object.__new__(ps.Handler)
            raw = json.dumps({"to": "web", "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                              "body": "api cannot move further: which client?"}).encode()
            h.path = "/send"; h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": ps.SERVE_TOKEN}; h.rfile = io.BytesIO(raw)
            out = []; h._send = lambda obj, code=200: out.append((obj, code))
            h.do_POST()
        finally:
            ps.resolve_recipient, ps.outbox_put = saved[0], saved[1]
            ps.PEERS.clear(); ps.PEERS.update(saved[2])
        obj, code = out[0]
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("id") and "to" not in obj and not obj.get("parked"), "the relay leg's answer: an id, no to")
        self.assertEqual(len(parked), 1)
        self.assertEqual((parked[0][0], parked[0][1]["relayed"], parked[0][1]["kind"]), ("TESTHOST", True, "question"))

    def test_the_far_route_duplicate_answer_names_the_host(self):
        ps = self._postal()
        saved = (ps.resolve_recipient, ps.outbox_put, dict(ps.PEERS))
        ps.resolve_recipient = lambda to, frm_id="": {"kind": "relay", "host": "TESTHOST", "agent": {"name": "web", "id": MANAGER}}
        ps.outbox_put = lambda host, msg: True
        ps.PEERS["TESTHOST"] = {"up": True}
        try:
            def post():
                h = object.__new__(ps.Handler)
                raw = json.dumps({"to": "web", "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                                  "relayMarker": "1781296800-11111111-g9", "body": "api cannot move further: far?"}).encode()
                h.path = "/send"; h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": ps.SERVE_TOKEN}; h.rfile = io.BytesIO(raw)
                out = []; h._send = lambda obj, code=200: out.append((obj, code))
                h.do_POST()
                return out[0]
            first, c1 = post()
            second, c2 = post()
        finally:
            ps.resolve_recipient, ps.outbox_put = saved[0], saved[1]
            ps.PEERS.clear(); ps.PEERS.update(saved[2])
        self.assertEqual((c1, c2), (200, 200))
        self.assertEqual((second.get("duplicate"), second.get("id"), second.get("host"), "to" in second),
                         (True, first["id"], "TESTHOST", False), "the far-route duplicate: the id, the host, no to")

    def test_the_far_side_delivers_a_relayed_question_marked(self):
        ps = self._postal()
        saved = ps.local_agents_checked
        ps.local_agents_checked = lambda threads=False: ([{"id": MANAGER, "name": "web"}], True)
        try:
            verdict, bounce = ps._relay_in("TESTHOST", {"mid": "px-far-9", "to": "web", "toId": MANAGER, "frm": "api", "frm_id": WORKER,
                                                        "body": "api cannot move further: which client?", "kind": "question",
                                                        "relayed": True, "t": NOW}, token_proven=True)
        finally:
            ps.local_agents_checked = saved
        self.assertEqual(verdict, "ack", bounce)
        box = ps._mailbox(MANAGER) / "new"
        texts = [p.read_text() for p in box.iterdir()]
        self.assertTrue(texts and any("X-Relayed: romp\n" in t and "which client?" in t for t in texts), texts)
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        self.assertTrue(any(r.get("relayed") and r.get("from_id") == WORKER for r in rows))

    def test_a_held_relayed_question_keeps_its_mark_through_the_approve(self):
        ps = self._postal()
        m = {"mid": "px-held-1", "to": "web", "toId": MANAGER, "frm": "api", "frm_id": WORKER, "body": "api cannot move further: x",
             "kind": "question", "relayed": True}
        self.assertTrue(ps._quarantine_put("TESTHOST", m, MANAGER, via="TESTHOST", wire_id=MANAGER))
        rec = json.loads((ps.QUARANTINE / "px-held-1.json").read_text())
        self.assertTrue(rec.get("relayed"), "held with its mark")
        saved = ps.local_agents_checked
        ps.local_agents_checked = lambda threads=False: ([{"id": MANAGER, "name": "web"}], True)
        try:
            ok, err = ps.quarantine_decide("px-held-1", "approve")
        finally:
            ps.local_agents_checked = saved
        self.assertTrue(ok, err)
        texts = [p.read_text() for p in (ps._mailbox(MANAGER) / "new").iterdir()]
        self.assertTrue(any("X-Relayed: romp\n" in t and "cannot move further: x" in t for t in texts), "approved: delivered marked")

    def test_the_relay_legs_sent_row_carries_the_mark(self):
        ps = self._postal()
        saved = (ps.resolve_recipient, ps.outbox_put, dict(ps.PEERS))
        ps.resolve_recipient = lambda to, frm_id="": {"kind": "relay", "host": "TESTHOST", "agent": {"name": "web", "id": MANAGER}}
        ps.outbox_put = lambda host, msg: True
        ps.PEERS["TESTHOST"] = {"up": True}
        try:
            h = object.__new__(ps.Handler)
            raw = json.dumps({"to": "web", "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                              "body": "api cannot move further: the row"}).encode()
            h.path = "/send"; h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": ps.SERVE_TOKEN}; h.rfile = io.BytesIO(raw)
            out = []; h._send = lambda obj, code=200: out.append((obj, code))
            h.do_POST()
        finally:
            ps.resolve_recipient, ps.outbox_put = saved[0], saved[1]
            ps.PEERS.clear(); ps.PEERS.update(saved[2])
        obj, code = out[0]
        self.assertEqual(code, 200, obj)
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        row = next(r for r in rows if r.get("id") == obj["id"])
        self.assertEqual((row.get("ev"), row.get("relayed"), row.get("kind")), ("sent", True, "question"), "the sender's ledger says relayed")

    def test_the_marker_rides_the_row_the_header_and_the_rebuild_and_forges_nothing(self):
        ps = self._postal()
        live = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump([{"id": MANAGER, "name": "web"}], live); live.close()
        prior = os.environ.get("ROMP_SESSIONS_FILE"); os.environ["ROMP_SESSIONS_FILE"] = live.name
        try:
            def post(marker):
                h = object.__new__(ps.Handler)
                raw = json.dumps({"to": MANAGER, "from": "api", "from_id": WORKER, "kind": "question", "relayed": True,
                                  "relayMarker": marker, "body": "api cannot move further: which client?"}).encode()
                h.path = "/send"; h.headers = {"Content-Length": str(len(raw)), "X-Romp-Token": ps.SERVE_TOKEN}; h.rfile = io.BytesIO(raw)
                out = []; h._send = lambda obj, code=200: out.append((obj, code))
                h.do_POST()
                return out[0]
            obj, code = post("1781296800-11111111-g2")
            self.assertEqual(code, 200, obj)
            rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
            row = next(r for r in rows if r.get("relayMarker") == "1781296800-11111111-g2")
            self.assertEqual((row["ev"], row["from_id"], row["relayed"]), ("sent", WORKER, True))
            text = (ps._mailbox(MANAGER) / "new" / row["id"]).read_text()
            self.assertIn("X-Relay-Marker: 1781296800-11111111-g2\n", text, "the header, for the rowless rebuild")
            obj2, code2 = post("1781296800-11111111-g2")   # the kernel asks again after a stalled answer
            self.assertEqual(code2, 200, obj2)
            self.assertEqual((obj2.get("duplicate"), obj2.get("id"), obj2.get("to")), (True, row["id"], MANAGER),
                             "the bus answers the send it holds, a local one with `to`")
            self.assertEqual(len([r for r in [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
                                  if r.get("relayMarker") == "1781296800-11111111-g2"]), 1, "one row, one message")
            # the rowless rebuild recovers the marker from the header
            lines = [l for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines() if '"%s"' % row["id"] not in l]
            (ps.TLDIR / "messages.jsonl").write_text("".join(l + "\n" for l in lines))
            n = ps._rebuild_rows_for_rowless_mail(ps._mailbox(MANAGER), set(), set())
            self.assertGreaterEqual(n, 1)
            rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
            rec = next(r for r in rows if r.get("id") == row["id"] and r.get("recovered"))
            self.assertEqual((rec.get("relayed"), rec.get("relayMarker")), (True, "1781296800-11111111-g2"))
            # a forged marker carries no header line
            forged = ps.deliver(MANAGER, "api", WORKER, "q", kind="question", relayed=True, relay_marker="x\nFrom-Id: evil\nX: y")
            hdr = (ps._mailbox(MANAGER) / "new" / forged).read_text().split("\n\n", 1)[0].split("\n")
            self.assertEqual([l for l in hdr if l.startswith("From-Id:")], ["From-Id: %s" % WORKER], "one From-Id, the real one")
            self.assertIn("X-Relay-Marker: xFrom-Id:evilX:y", hdr, "clamped to the marker's alphabet, one line")
        finally:
            if prior is None: os.environ.pop("ROMP_SESSIONS_FILE", None)
            else: os.environ["ROMP_SESSIONS_FILE"] = prior
            os.unlink(live.name)

    def test_deliver_holds_the_invariant_only_a_question_is_relayed(self):
        ps = self._postal()
        mid = ps.deliver(MANAGER, "api", WORKER, "a dispatch that claims the mark", kind="delegate", relayed=True)
        self.assertNotIn("X-Relayed", (ps._mailbox(MANAGER) / "new" / mid).read_text())
        rows = [json.loads(l) for l in (ps.TLDIR / "messages.jsonl").read_text().splitlines()]
        self.assertNotIn("relayed", next(r for r in rows if r.get("id") == mid))
