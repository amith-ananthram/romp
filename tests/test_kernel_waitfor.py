#!/usr/bin/env python3
"""The fleet WAIT-FOR graph (the user 2026-06-22): a session X 'waits on' peer Y when X's latest message to
Y has no reply back and Y is ALIVE. It's a functional graph (each X → one Y), so following the chains
detects deadlock CYCLES. build_feed attaches it per working card (waitingOn) for the 'waiting on <thread>'
chip + the auto-nudge gate. Self-contained: drives _wait_for_graph against a synthetic messages.jsonl.

Note: a DIRECT 2-cycle (X↔Y) is impossible by construction — whoever messaged most recently is the waiter,
the other's older message counts as answered — so a real deadlock is a 3+ chain that loops."""
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
km = load_source("romp_kernel_wf", os.path.join(BIN, "romp-kernel"))
jd = km.jd

X = "aaaaaaaa-0000-0000-0000-000000000001"
Y = "bbbbbbbb-0000-0000-0000-000000000002"
Z = "cccccccc-0000-0000-0000-000000000003"


class WaitFor(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"

    def tearDown(self):
        jd.MESSAGES = self.saved
        self.td.cleanup()

    def _msgs(self, rows):
        # rows are (from, to, t) or (from, to, t, body); default body is a reply-required QUESTION so the
        # base wait-edge tests fire (only QUESTION/ASK messages create a wait — the user 2026-06-22)
        def rec(i, r):
            body = r[3] if len(r) > 3 else "QUESTION: status?"
            return json.dumps({"from_id": r[0], "to_id": r[1], "t": r[2], "id": "m%d" % i, "body": body})
        jd.MESSAGES.write_text("\n".join(rec(i, r) for i, r in enumerate(rows)) + "\n")

    def test_unanswered_outbound_to_live_peer_is_a_wait(self):
        self._msgs([(X, Y, 100)])                         # X → Y QUESTION, no reply back
        g = km._wait_for_graph(0, {X, Y})
        self.assertEqual(g.get(X, {}).get("peerSid"), Y, "X waits on Y")
        self.assertFalse(g[X]["inCycle"])
        self.assertNotIn(Y, g, "Y isn't waiting on anyone")

    def test_only_reply_required_messages_create_a_wait(self):
        self._msgs([(X, Y, 100, "COORDINATE: heads-up, I landed the thing")])   # FYI, no reply expected
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {}, "a COORDINATE/FYI is not a wait")
        self._msgs([(X, Y, 100, "QUESTION: can you confirm the schema?")])      # reply REQUIRED
        self.assertEqual(km._wait_for_graph(0, {X, Y}).get(X, {}).get("peerSid"), Y, "a QUESTION is a wait")

    def test_a_reply_of_any_kind_answers_the_question(self):
        # X asks; Y replies with a COORDINATE — any reply clears X's wait, and Y's non-question reply doesn't
        # make Y wait on X (so an actively-coordinating session doesn't show a spurious chip)
        self._msgs([(X, Y, 100, "QUESTION: status?"), (Y, X, 200, "COORDINATE: here you go, done")])
        g = km._wait_for_graph(0, {X, Y})
        self.assertNotIn(X, g, "any reply answers X's question")
        self.assertNotIn(Y, g, "Y's reply was a COORDINATE, not a question → Y isn't waiting")

    def test_a_reply_flips_the_wait_to_the_replier(self):
        # Y replies to X → X's outbound is answered (X no longer waits), but Y's reply is now the unanswered
        # latest, so the ball is in X's court: Y waits on X. (A reply is a message too; the graph can't tell
        # an answer from a counter-question, so it conservatively treats the last sender as waiting.)
        self._msgs([(X, Y, 100), (Y, X, 200)])
        g = km._wait_for_graph(0, {X, Y})
        self.assertNotIn(X, g, "X is no longer waiting — Y replied")
        self.assertEqual(g.get(Y, {}).get("peerSid"), X, "now Y waits on X's response")

    def test_dead_peer_is_not_a_wait(self):
        self._msgs([(X, Y, 100)])
        self.assertEqual(km._wait_for_graph(0, {X}), {}, "Y not alive → X isn't waiting on it")

    def test_three_way_cycle_is_a_deadlock(self):
        self._msgs([(X, Y, 100), (Y, Z, 100), (Z, X, 100)])   # X→Y→Z→X, each the latest unanswered
        g = km._wait_for_graph(0, {X, Y, Z})
        self.assertEqual((g[X]["peerSid"], g[Y]["peerSid"], g[Z]["peerSid"]), (Y, Z, X))
        self.assertTrue(all(g[s]["inCycle"] for s in (X, Y, Z)), "X→Y→Z→X is a deadlock cycle")

    def test_chain_to_a_sink_is_not_a_cycle(self):
        self._msgs([(X, Y, 100), (Y, Z, 100)])            # X→Y→Z, Z a sink (waits on no one)
        g = km._wait_for_graph(0, {X, Y, Z})
        self.assertEqual((g[X]["peerSid"], g[Y]["peerSid"]), (Y, Z))
        self.assertFalse(g[X]["inCycle"] or g[Y]["inCycle"], "a chain to a sink is not a deadlock")
        self.assertNotIn(Z, g)

    def test_picks_the_most_recent_unanswered_peer(self):
        self._msgs([(X, Y, 100), (X, Z, 200)])            # X waits on both; the chip shows the most-recent (Z)
        g = km._wait_for_graph(0, {X, Y, Z})
        self.assertEqual(g[X]["peerSid"], Z, "X's primary wait is its most-recent unanswered outbound")



class DeclaredKindWins(unittest.TestCase):
    """The schema `kind` field is the designed intent source (send_message REQUIRES it); the body regex
    is only the fallback for legacy rows that predate the field (the 2026-07-22 unification)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"

    def tearDown(self):
        jd.MESSAGES = self.saved
        self.td.cleanup()

    def _msgs(self, rows):
        # rows are (from, to, t, body, kind); kind="" omits the field (a legacy row)
        def rec(i, r):
            o = {"from_id": r[0], "to_id": r[1], "t": r[2], "id": "m%d" % i, "body": r[3]}
            if r[4]:
                o["kind"] = r[4]
            return json.dumps(o)
        jd.MESSAGES.write_text("\n".join(rec(i, r) for i, r in enumerate(rows)) + "\n")

    def test_kind_question_creates_the_wait_without_any_lead_word(self):
        self._msgs([(X, Y, 100, "can you confirm the schema shape?", "question")])
        self.assertEqual(km._wait_for_graph(0, {X, Y}).get(X, {}).get("peerSid"), Y,
                         "the declared question is a wait even without a QUESTION: lead word")

    def test_a_declared_coordinate_never_creates_a_wait(self):
        self._msgs([(X, Y, 100, "QUESTION: rhetorical, just flagging the rename", "coordinate")])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {},
                         "the declared kind outranks a question-shaped body")

    def test_legacy_rows_without_kind_keep_the_regex_fallback(self):
        self._msgs([(X, Y, 100, "QUESTION: which port?", "")])
        self.assertEqual(km._wait_for_graph(0, {X, Y}).get(X, {}).get("peerSid"), Y)
        self._msgs([(X, Y, 100, "heads-up: landed the thing", "")])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {})


class _TerminalRowLog(unittest.TestCase):
    """A synthetic postal log with the bus's terminal rows: `bounced` (the bus returned the message) and
    `recall` (the sender withdrew it before anyone read it). Shared by the two classes below."""

    RSID = "dddddddd-0000-0000-0000-000000000004"   # a remote recipient, keyed by the relay row's to_sid

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        km._POSTAL_WAIT_CACHE[:] = [None, None]

    def tearDown(self):
        jd.MESSAGES = self.saved
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        self.td.cleanup()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        km._POSTAL_WAIT_CACHE[:] = [None, None]

    @staticmethod
    def _ask(i, t, to=Y, **extra):
        r = {"ev": "sent", "id": "m%d" % i, "from_id": X, "to_id": to, "t": t, "kind": "question",
             "body": "which port should the api use?"}
        r.update(extra)
        return r

    @staticmethod
    def _reply(i, t, kind="coordinate"):
        return {"ev": "sent", "id": "m%d" % i, "from_id": Y, "to_id": X, "t": t, "kind": kind, "body": "8080"}

    @staticmethod
    def _back(i, t, why="recipient exited; unread mail destroyed by the orphan sweep", **extra):
        # the bus's terminal return as the orphan sweep shapes it: the ORIGINAL id, a `to` name, a `why`
        r = {"ev": "bounced", "id": "m%d" % i, "t": t, "to": "api", "host": "", "why": why}
        r.update(extra)
        return r

    @staticmethod
    def _recall(i, t):
        # the sender unsent it before anyone read it: {t, ev, id} is the row's whole shape, and the id is
        # the sent row's own maildir name. (An OUTBOX recall names the relay mid, "px-…", and is not read
        # as terminal — test_a_recall_naming_a_relay_mid_is_not_terminal.)
        return {"ev": "recall", "id": "m%d" % i, "t": t}


class ReturnedSendClosesTheWait(_TerminalRowLog):
    """A send that came back is not an open ask (2026-09-08). The bus writes a terminal `bounced` row
    naming the message's id when a send is over with nothing ever coming back — the peer refused it, the
    recipient exited and its unread mail was destroyed, the send was refused before it left. The wait
    reader skipped that row (no from_id/to_id), so the sender wore "Awaiting <peer>" for a question the
    peer never received, and its card parked as waiting on a peer while the person may have needed to
    act. The row is the closing EVENT, keyed to the message it names. The two guards this class leans on
    live above: a sent question alone waits (test_unanswered_outbound_to_live_peer_is_a_wait) and a
    reply of any kind answers it (test_a_reply_of_any_kind_answers_the_question). Since the same day a
    maildir `recall` row (the sender withdrew the message before anyone read it) is read as the other
    terminal kind through the same skip — #1071's rule, one skip before last_any, so neither row is its
    sender's word toward the peer either — and ReturnedReplyIsNotTheAnswer below holds the recalled
    reply's half (the bounced reply's is #1071's RefusedSendsAreNoAsk)."""

    def test_a_returned_question_is_no_longer_a_wait(self):
        self._log([self._ask(1, 100), self._back(1, 150)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {},
                         "main: X wears Awaiting Y for a question Y never received")
        last_any, last_ask, last_await = km._postal_wait_maps()
        self.assertNotIn((X, Y), last_ask, "the ask is closed on the chip's map…")
        self.assertNotIn((X, Y), last_await, "…and on the stamp clock's")
        self.assertNotIn((X, Y), last_any, "…and it is no message either: neither an ask nor an answer (#1071's rule)")

    def test_a_recalled_question_is_no_longer_a_wait(self):
        # the sender withdrew the question before the peer read it (unlinked unread from new/): as over
        # as a bounce — the row went unread before, and X wore Awaiting Y for its own withdrawal
        self._log([self._ask(1, 100), self._recall(1, 150)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {},
                         "base: X wears Awaiting Y for a question X itself withdrew")
        last_any, last_ask, last_await = km._postal_wait_maps()
        self.assertNotIn((X, Y), last_ask, "the ask is closed on the chip's map…")
        self.assertNotIn((X, Y), last_await, "…and on the stamp clock's")
        self.assertNotIn((X, Y), last_any, "…and a withdrawn send is not X's word toward Y")

    def test_a_recall_naming_a_relay_mid_is_not_terminal(self):
        # the OUTBOX recall (review find, 2026-09-08): the row names the relay send's mid ("px-" +
        # _unique()), and an outbox item outlives the carry — the exchange relays it without removing it;
        # only the end-to-end ack does — so the far recipient may already hold the message and answer.
        # Not terminal: the ask stays open, the send stays X's word, the debt stays owed, no return clock
        # (as before — green on the base by design, where no recall was read). The maildir recall above
        # still ends it.
        self._log([self._ask(1, 100, id="px-1.mail.TESTHOST-A", to="peer:TESTHOST-B",
                             toName="TESTHOST-B:api", to_sid=self.RSID),
                   {"ev": "recall", "id": "px-1.mail.TESTHOST-A", "t": 160}])
        self.assertEqual(km._wait_for_graph(0, {X, self.RSID}).get(X, {}).get("peerSid"), self.RSID)
        last_any, last_ask, _aw = km._postal_wait_maps()
        self.assertEqual(last_any.get((X, self.RSID)), 100)
        self.assertIn((X, self.RSID), last_ask)
        self.assertEqual([a[0] for a in km._debt_asks(self.RSID, {X})], [X])
        self.assertEqual(km._postal_returned(), {})

    def test_every_return_shape_the_bus_writes_closes_by_id_alone(self):
        # the writers differ in their side fields (the orphan sweep: to/why; a peer's refusal: to/host/why;
        # the oversize bounce: host ""; a failed publish: to_id/why; an unreadable outbox record:
        # host/why) — the join is ev + id and nothing else
        shapes = [
            {"ev": "bounced", "id": "m1", "t": 150, "to": "api", "why": "recipient exited; unread mail destroyed by the orphan sweep"},
            {"ev": "bounced", "id": "m1", "t": 150, "to": "api", "host": "TESTHOST-B", "why": "refused"},
            {"ev": "bounced", "id": "m1", "t": 150, "to": "api", "host": "", "why": "your message is 90000 bytes as delivered, over the limit"},
            {"ev": "bounced", "id": "m1", "t": 150, "to_id": Y, "why": "not published: a message with this id already stands"},
            {"ev": "bounced", "id": "m1", "t": 150, "host": "TESTHOST-B", "why": "the outbox record was unreadable"},
        ]
        for row in shapes:
            self._log([self._ask(1, 100), row])
            self.assertEqual(km._wait_for_graph(0, {X, Y}), {}, "still waiting after: %s" % row)

    def test_a_refused_cross_host_send_closes_the_same_way(self):
        # the relay shape: the row is addressed to the relay and keyed on to_sid; the far host refused it
        self._log([self._ask(1, 100, to="peer:TESTHOST-B", toName="TESTHOST-B:api", to_sid=self.RSID),
                   self._back(1, 160, why="refused", host="TESTHOST-B")])
        self.assertEqual(km._wait_for_graph(0, {X, self.RSID}), {},
                         "main: the asker waits on a live peer whose kernel refused the message")

    def test_the_return_closes_the_message_it_names_not_the_pair(self):
        # an OLDER ask comes back after a newer one went out: the newer, live ask still waits
        self._log([self._ask(1, 100), self._ask(2, 200), self._back(1, 300)])
        g = km._wait_for_graph(0, {X, Y})
        self.assertEqual(g.get(X, {}).get("peerSid"), Y, "the live ask is untouched by the older return")
        self.assertEqual(g[X]["since"], 200, "…and the chip dates from it")

    def test_a_delivery_ack_is_not_a_return(self):
        # `relayed` says the far host took delivery; the ask is as open as before — only `bounced` closes
        self._log([self._ask(1, 100), {"ev": "relayed", "id": "m1", "t": 150, "host": "TESTHOST-B"}])
        self.assertEqual(km._wait_for_graph(0, {X, Y}).get(X, {}).get("peerSid"), Y)

    def test_a_returned_coordinate_changes_nothing(self):
        # a heads-up opened no wait, so its return has none to close — and must not invent one
        self._log([self._ask(1, 100, kind="coordinate"), self._back(1, 150)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {})
        self.assertEqual(km._postal_returned(), {}, "no reply-requiring send came back → no return clock")

    def test_a_bounced_reply_moves_neither_the_answer_nor_the_watermark(self):
        # The reconciliation with #1071 (review, 2026-09-08): the skip sits ABOVE the last_any update in
        # both readers, so a reply that came back is not only no answer (#1071's
        # test_a_bounced_reply_answers_nothing) but leaves the pair's last-activity watermark exactly
        # where it was. This PR's earlier placement updated last_any first and skipped after, so the
        # bounced reply still moved the clock every reader keys on. The pin: the log WITH the returned
        # reply and its bounce reads, on every map and every reader, exactly as the log WITHOUT them.
        def reading():
            last_any, last_ask, last_await = km._postal_wait_maps()
            jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
            j_any, j_ask, _alias = jd._postal_ask_maps()
            return (dict(last_any), dict(last_ask), dict(last_await), km._wait_for_graph(0, {X, Y}),
                    km._peer_answered(X), dict(j_any), dict(j_ask), jd._open_ask_peers(X))
        self.addCleanup(lambda: jd._PEER_ASK_CACHE.__setitem__(slice(None), [None, ({}, {}, {})]))
        fyi = {"ev": "sent", "id": "f0", "from_id": Y, "to_id": X, "t": 50, "kind": "coordinate", "body": "api is up"}
        reply = {"ev": "sent", "id": "r1", "from_id": Y, "to_id": X, "t": 200, "kind": "coordinate", "body": "8080"}
        back = {"ev": "bounced", "id": "r1", "t": 201, "to_id": X,
                "why": "not published: the mail service stopped before the message reached the inbox"}
        # still waiting: the ask stands and the reply never arrived, so the watermark is still the fyi
        self._log([fyi, self._ask(1, 100)])
        before = reading()
        self.assertEqual(before[0].get((Y, X)), 50, "the watermark before the bounce is the fyi")
        self.assertEqual(before[3].get(X, {}).get("peerSid"), Y, "X waits on Y")
        self.assertEqual(before[7], [Y], "the judge's gate sees the open ask")
        self._log([fyi, self._ask(1, 100), reply, back])
        self.assertEqual(reading(), before, "a bounced reply moves nothing: not the answer, not last_any")
        # not waiting: a reply that landed, then a later one that came back; the clock stays at the landed one
        landed = {"ev": "sent", "id": "r0", "from_id": Y, "to_id": X, "t": 150, "kind": "coordinate", "body": "8080"}
        self._log([self._ask(1, 100), landed])
        before = reading()
        self.assertEqual(before[0].get((Y, X)), 150)
        self.assertEqual(before[3], {}, "answered: X waits on nobody")
        self.assertEqual(before[4], (150, {Y: 150}), "the answered clock is the landed reply")
        self._log([self._ask(1, 100), landed, reply, back])
        self.assertEqual(reading(), before,
                         "the earlier placement: last_any[(Y, X)] read 200 and the answered clock moved to it")


class ReturnedReplyIsNotTheAnswer(_TerminalRowLog):
    """A reply the replier recalled before the asker read it answers nothing (2026-09-08). last_any — any
    later Y→X row answers X's ask — counted Y's sent row to X even after a terminal row named it: a recall
    unlinks it from X's new/ unread (the bounced reply — the oversize push bounces it to Y WITHOUT putting
    it back in X's box — is #1071's rule; its skip carries the recall through _learn_return). So X's chip
    cleared, Y's debt read settled, and X's card left the wait on an answer nobody had received. The
    exclusion is per MESSAGE (the id the row names), never per pair or per second: a live reply of Y's
    answers as ever. Guards: the reply answers when nothing names it
    (test_a_reply_of_any_kind_answers_the_question, and test_without_the_return_the_reply_answers)."""

    def test_a_recalled_reply_leaves_the_ask_open(self):
        self._log([self._ask(1, 100), self._reply(2, 200), self._recall(2, 250)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}).get(X, {}).get("peerSid"), Y,
                         "base: X's edge cleared on a reply X never received")
        last_any, _ask, _aw = km._postal_wait_maps()
        self.assertNotIn((Y, X), last_any, "the withdrawn row is not Y's word toward X")
        self.assertEqual([a[0] for a in km._debt_asks(Y, {X})], [X], "Y still owes X the answer (base: owed nothing)")

    def test_a_bounced_reply_leaves_the_debt_owed(self):
        # the bounced reply's edge and maps are #1071's rule and tests (RefusedSendsAreNoAsk, the twins'
        # refused-question test); the debtor's own reader is what this pins (a guard on that rule)
        self._log([self._ask(1, 100), self._reply(2, 200),
                   self._back(2, 250, why="your message is 90000 bytes as delivered, over the limit")])
        self.assertEqual([a[0] for a in km._debt_asks(Y, {X})], [X])

    def test_without_the_return_the_reply_answers(self):
        self._log([self._ask(1, 100), self._reply(2, 200)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {})
        self.assertEqual(km._debt_asks(Y, {X}), [])

    def test_a_live_reply_in_the_same_second_still_answers(self):
        # per message, not per second: Y's second reply, sent the same second as the one that came back
        # and read by X, answers — last_any keys the pair by its newest LIVE row exactly as before
        self._log([self._ask(1, 100), self._reply(2, 200), self._reply(3, 200), self._recall(2, 250)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {})
        self.assertEqual(km._postal_wait_maps()[0].get((Y, X)), 200)

    def test_a_later_live_reply_still_answers(self):
        self._log([self._ask(1, 100), self._reply(2, 200), self._recall(2, 250), self._reply(3, 300)])
        self.assertEqual(km._wait_for_graph(0, {X, Y}), {})
        self.assertEqual(km._postal_wait_maps()[0].get((Y, X)), 300)


if __name__ == "__main__":
    unittest.main()
