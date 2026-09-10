#!/usr/bin/env python3
"""A peer's reply lifts the awaiting stamp again — for handoffs, not just questions (2026-08-18).

The 2026-08-15 change that stopped DELEGATES from making chip edges (ownership transferred is not a
dependency) also emptied last_ask of them — which silently removed _peer_answered_at's release, so a
delegated peer's reply no longer superseded a judge kind=peer stamp: the audit found a cross-host
handoff answered in 23 minutes still wearing Awaiting six hours later, with the 6h wake as the only
exit. The release reads every reply-REQUIRING outbound (question or delegate: last_await, 2026-09-08 —
it walked last_any first, until a coordinate the asker sent after the answer re-opened the pair),
restoring the designed exact ending event for questions and handoffs alike, while the chip edge stays
question-only exactly as intended.

Also pinned: an UNRESOLVABLE cross-host ask (the peer never sent a row, so the alias map cannot know
its sid) keys on the NAMED recipient — two asks to different sessions on one detached host used to
collapse onto the single (from, relay) pair, the later silently overwriting the earlier (a
29.6h-invisible ask found eaten this way). Synthetic rows only (placeholder ids, TESTHOST).

Since 2026-09-08 (mail keyed by the recipient's stable id): a relay row's own `to_sid` keys it exactly,
and the name→sid alias older rows fall back to is anchored at each row's SEND time (jd._alias_at), so a
name a NEW session reused no longer re-keys every OLD message to the new sid."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_awaitrel", os.path.join(BIN, "romp-kernel"))

A = "11111111-2222-3333-4444-000000000001"
B = "11111111-2222-3333-4444-000000000002"


class _AwaitBase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = km.jd.MESSAGES
        km.jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {}, {})]

    def tearDown(self):
        km.jd.MESSAGES = self._saved
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {}, {})]
        self.td.cleanup()

    def _write(self, rows):
        km.jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        km.jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        km._POSTAL_WAIT_CACHE[:] = [None, ({}, {}, {})]


class AwaitRelease(_AwaitBase):
    def test_a_delegated_peers_reply_supersedes_the_stamp(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own this now"},
            {"from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "done, shipped"},
        ])
        last_any, last_ask, last_await = km._postal_wait_maps()
        self.assertNotIn((A, B), last_ask, "a DELEGATE still makes no chip edge — ownership transferred")
        self.assertEqual(last_await.get((A, B)), 100, "…but it IS a reply-requiring send")
        self.assertEqual(km._peer_answered_at(A), 200, "…and the reply IS the stamp's ending event again")

    def test_a_reply_older_than_the_newest_reply_requiring_send_does_not_supersede(self):
        # the newest send that AWAITS something is unanswered → nothing supersedes (the rule this
        # test always pinned, now stated on the send that carries the wait)
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own this"},
            {"from_id": B, "to_id": A, "t": 150, "kind": "coordinate", "body": "ack"},
            {"from_id": A, "to_id": B, "t": 300, "kind": "question", "body": "and the port?"},
        ])
        self.assertEqual(km._peer_answered_at(A), 0, "the newest reply-requiring send is unanswered")

    def test_a_coordinate_after_the_answer_does_not_reopen_the_wait(self):
        # PIN MOVED 2026-09-08 (this fixture asserted 0 before): A's "one more thing" is a COORDINATE
        # — by declaration it requests nothing — so the handoff answered at 150 stays answered. Read
        # off last_any, the coordinate was "a newer outbound awaiting a reply" and the stamp the
        # answer had ended stood until the 6h backstop. main: 0.
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own this"},
            {"from_id": B, "to_id": A, "t": 150, "kind": "coordinate", "body": "ack"},
            {"from_id": A, "to_id": B, "t": 300, "kind": "coordinate", "body": "one more thing"},
        ])
        self.assertEqual(km._peer_answered_at(A), 150, "the delegate's answer is the clock; a heads-up is not a wait")

    def test_question_release_unchanged(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"from_id": B, "to_id": A, "t": 180, "kind": "coordinate", "body": "8080"},
        ])
        last_any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, B), last_ask, "a QUESTION still makes the chip edge")
        self.assertEqual(km._peer_answered_at(A), 180)

    def test_unresolvable_relay_asks_key_per_named_recipient(self):
        self._write([
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:web", "t": 100,
             "kind": "question", "body": "status of the web thing?"},
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "t": 120,
             "kind": "question", "body": "status of the api thing?"},
        ])
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, "peer:TESTHOST:web"), last_ask, "the earlier ask survives")
        self.assertIn((A, "peer:TESTHOST:api"), last_ask, "…beside the later one — no overwrite")

    def test_the_alias_rekeys_everything_once_the_peer_speaks(self):
        W = "11111111-2222-3333-4444-000000000003"
        self._write([
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:web", "t": 100,
             "kind": "question", "body": "status?"},
            {"from_id": W, "to_id": A, "t": 150, "kind": "coordinate", "body": "all good",
             "from_host": "TESTHOST", "from": "web"},
        ])
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, W), last_ask, "the maps rebuild from the full log — the row re-keys to the real sid")
        self.assertNotIn((A, "peer:TESTHOST:web"), last_ask)
        self.assertEqual(km._peer_answered_at(A), 150, "and the reply releases the wait")


class AnswerClockIgnoresCoordinates(_AwaitBase):
    """The answered-supersede walks reply-REQUIRING sends (2026-09-08). On main it walked last_any, so a
    coordinate the asker sent after the answer landed made the answer read stale — and the card kept
    "Awaiting <peer>" although the peer had answered, until the 6h backstop."""

    def _stamp(self, written_at):
        # a closer stamp awaiting B, written at `written_at` (the write time is the supersede key)
        return {"awaitingAt": written_at, "awaitingKind": "peer", "awaitingPeers": [B],
                "awaitingWhy": "asked the api session which port", "log": [{"kind": "awaiting", "at": written_at}]}

    def test_thanks_after_the_answer_leaves_the_question_answered(self):
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "8080"},
            {"from_id": A, "to_id": B, "t": 300, "kind": "coordinate", "body": "thanks, going with 8080"},
        ])
        self.assertEqual(km._peer_answered_at(A), 200, "main: 0 — the thanks re-opened the pair")
        self.assertEqual(km._peer_answered(A), (200, {B: 200}), "…pair-aware alike")
        self.assertTrue(km._peer_stamp_superseded(self._stamp(120), km._peer_answered(A)),
                        "a stamp written between the ask and its answer is ended by the answer (main: stands)")
        self.assertFalse(km._peer_stamp_superseded(self._stamp(250), km._peer_answered(A)),
                         "a stamp written AFTER the answer is fresher than it — it stands, as before")

    def test_a_later_question_does_reopen(self):
        # the boundary: a NEW reply-requiring send after the answer is a new wait
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "8080"},
            {"from_id": A, "to_id": B, "t": 300, "kind": "question", "body": "and the host?"},
        ])
        self.assertEqual(km._peer_answered_at(A), 0)
        self.assertFalse(km._peer_stamp_superseded(self._stamp(120), km._peer_answered(A)))

    def test_a_kindless_legacy_row_counts_by_its_ask_prefix(self):
        # rows from before the schema `kind`: the QUESTION/ASK lead word is the ask tell (last_ask's
        # own rule); a kindless row without it requests nothing
        self._write([
            {"from_id": A, "to_id": B, "t": 100, "body": "QUESTION: which port?"},
            {"from_id": B, "to_id": A, "t": 200, "body": "8080"},
            {"from_id": A, "to_id": B, "t": 300, "body": "thanks"},
        ])
        _any, _ask, last_await = km._postal_wait_maps()
        self.assertEqual(last_await.get((A, B)), 100)
        self.assertEqual(km._peer_answered_at(A), 200)


class MailKeyedByStableId(_AwaitBase):
    """Cross-host rows key on the recipient's stable id (2026-09-08): the row's own `to_sid` when it has
    one; else the name alias AT the row's send time. Last-write-wins re-keyed every old message to
    whichever session most recently wore the name."""

    X = "11111111-2222-3333-4444-000000000003"   # the session that wore "api" on TESTHOST first
    Y = "11111111-2222-3333-4444-000000000004"   # a later session that reused the name
    C = "11111111-2222-3333-4444-000000000005"   # an unrelated local session Y mailed

    def _reused_name(self):
        # A asks "api" (an OLDER row: no to_sid); X — then wearing the name — answers; later a NEW
        # session Y wears "api" and mails someone else, which is how the alias learns Y
        return [
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "t": 100,
             "kind": "question", "body": "status of the api thing?"},
            {"from_id": self.X, "to_id": A, "t": 150, "kind": "coordinate", "body": "shipped",
             "from_host": "TESTHOST", "from": "api"},
            {"from_id": self.Y, "to_id": self.C, "t": 400, "kind": "coordinate", "body": "hello from the new api",
             "from_host": "TESTHOST", "from": "api"},
        ]

    def test_a_reused_name_leaves_the_old_ask_keyed_to_the_session_it_went_to(self):
        self._write(self._reused_name())
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, self.X), last_ask, "keyed to X, who wore the name when the ask was sent (main: Y)")
        self.assertNotIn((A, self.Y), last_ask)
        self.assertEqual(km._peer_answered_at(A), 150, "X's reply answers it (main: 0 — the reply sits under X, the ask under Y)")
        self.assertEqual(km._peer_answered(A)[1], {self.X: 150})
        self.assertNotIn(A, km._wait_for_graph(1_000_000, {A, self.X, self.Y, self.C}),
                         "no edge: the ask was answered (main: A → Y, a stranger A never asked, forever)")

    def test_an_ask_sent_after_the_reuse_keys_to_the_new_wearer(self):
        # the ASSERTIONS hold on main too (last-write-wins also lands on Y); the test itself fails there
        # on the 3-tuple unpack. The anchor picks the most recent sighting at or before the send.
        self._write(self._reused_name() + [
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "t": 500,
             "kind": "question", "body": "new api: status?"}])
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, self.Y), last_ask)
        self.assertIn((A, self.X), last_ask, "…while the old ask keeps its own key")

    def test_upgrade_time_a_legacy_ask_to_a_recreated_peer_stays_keyed_to_the_prior_wearer(self):
        # review find, 2026-09-08: the residual the PR body names, pinned so the boundary is visible:
        # an ask sent BEFORE the upgrade (no to_sid) to a name a recreated session now wears, where the
        # new wearer's FIRST sighting on this host is its own reply. The anchor can only pick the prior
        # wearer (the log records no wearer deaths, and nothing sighted the new one before the send), so
        # the ask reads open on both readers; main's last-write-wins happened to read it answered. The
        # same exchange on a post-upgrade row keys by to_sid and reads answered: new rows cannot recur.
        W1 = self.X                                   # wore "api" before the ask; never replied to it
        W2 = self.Y                                   # recreated under the name; its first row is the reply
        legacy = [
            {"from_id": W1, "to_id": self.C, "t": 50, "kind": "coordinate", "body": "hello from the first api",
             "from_host": "TESTHOST", "from": "api"},
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "t": 100,
             "kind": "question", "body": "status?"},
            {"from_id": W2, "to_id": A, "t": 150, "kind": "coordinate", "body": "shipped",
             "from_host": "TESTHOST", "from": "api"},
        ]
        self._write(legacy)
        km.jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertIn((A, W1), last_ask, "anchored at the send: the prior wearer (main: W2, the answerer)")
        self.assertEqual(km._peer_answered_at(A), 0, "the documented residual: open until the 6h wake (main: 150)")
        self.assertEqual(km.jd._open_ask_peers(A), [W1], "the judge's gate agrees with the wait maps")
        # the same exchange as a post-upgrade row: the relay wrote the recipient's id, no alias involved
        self._write([dict(legacy[1], to_sid=W2)] + [legacy[0], legacy[2]])
        km.jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        self.assertEqual(km._peer_answered_at(A), 150, "to_sid keys the row to W2; W2's reply answers it")
        self.assertEqual(km.jd._open_ask_peers(A), [])

    def test_to_sid_keys_the_row_without_any_alias(self):
        # a relay row since 2026-09-08 carries the sid the send resolved: no peer row is needed to
        # place it, and a later name reuse cannot move it
        self._write([
            {"from_id": A, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api", "to_sid": self.X, "t": 100,
             "kind": "question", "body": "status?"},
            {"from_id": self.Y, "to_id": self.C, "t": 400, "kind": "coordinate", "body": "hi",
             "from_host": "TESTHOST", "from": "api"},
        ])
        _any, last_ask, last_await = km._postal_wait_maps()
        self.assertIn((A, self.X), last_ask, "main: (A, Y) — the alias re-key won; to_sid was never read")
        self.assertNotIn((A, "peer:TESTHOST:api"), last_ask)
        self.assertNotIn((A, self.Y), last_ask)
        self.assertEqual(last_await.get((A, self.X)), 100)


class RefusedSendsAreNoAsk(_AwaitBase):
    """A sent row whose id a terminal `bounced` row closed never reached the recipient (review find,
    2026-09-08): the bus closes a message it had to give up on — a peer's refusal, the orphan sweep's
    destroy, an unreadable inbox file, a write a crash cut short — with a `bounced` row on the same id
    (a publish it refuses outright writes no row at all). The readers ignored `ev`,
    so a refused QUESTION read as an open ask (the asker's card wore it, the debt reminder counted it)
    until the recipient happened to send anything, and a bounced reply read as answering the pair.
    Mutant: the `ended` filter removed."""

    BOUNCED = "not published: the mail service stopped before the message reached the inbox"

    def test_a_bounced_question_is_no_open_ask(self):
        self._write([
            {"ev": "sent", "id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"ev": "bounced", "id": "m1", "t": 101, "to_id": B, "why": self.BOUNCED},
        ])
        last_any, last_ask, _aw = km._postal_wait_maps()
        self.assertNotIn((A, B), last_ask, "a refused question is no ask: no reply can ever close it")
        self.assertNotIn((A, B), last_any, "and no message, either")
        self.assertEqual(km._wait_for_graph(1000, {A, B}), {}, "so the asker wears no open ask")

    def test_a_standing_question_beside_a_bounced_one_still_asks(self):
        self._write([
            {"ev": "sent", "id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"ev": "bounced", "id": "m1", "t": 101, "to_id": B, "why": self.BOUNCED},
            {"ev": "sent", "id": "m2", "from_id": A, "to_id": B, "t": 200, "kind": "question", "body": "which port? (retry)"},
        ])
        _any, last_ask, _aw = km._postal_wait_maps()
        self.assertEqual(last_ask[(A, B)][0], 200, "the retry that did land is the open ask")
        self.assertEqual(km._wait_for_graph(1000, {A, B})[A]["since"], 200)

    def test_a_bounced_reply_answers_nothing(self):
        self._write([
            {"ev": "sent", "id": "m1", "from_id": B, "to_id": A, "t": 100, "kind": "question", "body": "status?"},
            {"ev": "sent", "id": "m2", "from_id": A, "to_id": B, "t": 200, "kind": "coordinate", "body": "all green"},
            {"ev": "bounced", "id": "m2", "t": 201, "to_id": B, "why": self.BOUNCED},
        ])
        graph = km._wait_for_graph(1000, {A, B})
        self.assertEqual(graph[B]["peerSid"], A, "the asker still waits: the reply never reached it")
        self.assertEqual(km._peer_answered_at(B), 0)


def _stamp(written_at):
    return {"awaitingAt": written_at, "awaitingKind": "peer", "awaitingPeers": [B],
            "awaitingWhy": "asked the api session which port", "log": [{"kind": "awaiting", "at": written_at}]}


class ReturnedSendEndsTheWait(_AwaitBase):
    """The bus RETURNING a reply-requiring send is the pair's other ending event (2026-09-08). A
    terminal `bounced` row names the message's id when the send is over with nothing ever coming back
    (refused by the peer, recipient gone, never left). The answered clock walked replies only, so a
    closer stamp awaiting that peer stood — the card parked on a wait no event could end — until the
    6h backstop. Now the return supersedes a stamp filed before it, exactly as a reply does, and only
    for the message it names. A `recall` row — the sender withdrew the message before anyone read it —
    is the other terminal kind and ends the wait the same way (2026-09-08, the second fold)."""

    def _stamp(self, written_at):
        return _stamp(written_at)

    @staticmethod
    def _recall(mid, t):
        return {"ev": "recall", "id": mid, "t": t}     # the sender unsent it unread: the row's whole shape

    @staticmethod
    def _back(mid, t, **extra):
        r = {"ev": "bounced", "id": mid, "t": t, "to": "api", "host": "",
             "why": "recipient exited; unread mail destroyed by the orphan sweep"}
        r.update(extra)
        return r

    def test_a_returned_question_ends_the_wait_at_the_return(self):
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            self._back("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 150, "main: 0 — the pair read as still waiting")
        self.assertEqual(km._peer_answered(A), (150, {B: 150}), "…pair-aware alike")
        self.assertTrue(km._peer_stamp_superseded(self._stamp(120), km._peer_answered(A)),
                        "a stamp written before the return is ended by it (main: stands)")
        self.assertFalse(km._peer_stamp_superseded(self._stamp(160), km._peer_answered(A)),
                         "a stamp written AFTER the return is fresher than it — it stands")

    def test_a_returned_delegate_ends_the_handoff_wait_too(self):
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own the exporter"},
            self._back("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 150, "a handoff that came back is over, like an answered one")

    def test_the_return_ends_only_the_send_it_names(self):
        # an older ask comes back after a newer one went out: the live ask holds the pair open
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"id": "m2", "from_id": A, "to_id": B, "t": 200, "kind": "question", "body": "and the host?"},
            self._back("m1", 300),
        ])
        self.assertEqual(km._peer_answered_at(A), 0, "the newer ask still waits")
        self.assertEqual(km._peer_answered(A), (0, {}))
        self.assertFalse(km._peer_stamp_superseded(self._stamp(250), km._peer_answered(A)))

    def test_a_reply_then_a_later_return_the_newer_ends_the_pair(self):
        # m1 answered at 250; m2 (sent 200) came back at 300: the pair's last obligation ended at 300
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"id": "m2", "from_id": A, "to_id": B, "t": 200, "kind": "delegate", "body": "own the exporter"},
            {"id": "r1", "from_id": B, "to_id": A, "t": 250, "kind": "coordinate", "body": "8080"},
            self._back("m2", 300),
        ])
        self.assertEqual(km._peer_answered_at(A), 300)
        self.assertTrue(km._peer_stamp_superseded(self._stamp(270), km._peer_answered(A)),
                        "a stamp filed after the answer, about the handoff, ends when the handoff comes back")

    def test_a_returned_coordinate_ends_nothing(self):
        # a heads-up opened no wait; its return is not an ending event for the pair
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "coordinate", "body": "fyi"},
            self._back("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 0)
        self.assertEqual(km._peer_answered(A), (0, {}))

    def test_a_delivery_ack_is_not_an_ending(self):
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            {"ev": "relayed", "id": "m1", "t": 150, "host": "TESTHOST-B"},
        ])
        self.assertEqual(km._peer_answered_at(A), 0, "delivered is not answered, and not returned either")

    def test_the_return_clock_is_keyed_like_the_maps(self):
        # a cross-host ask keyed on to_sid: the return lands on the pair key the stamp recorded
        rsid = "11111111-2222-3333-4444-000000000009"
        self._write([
            {"id": "m1", "from_id": A, "to_id": "peer:TESTHOST-B", "toName": "TESTHOST-B:api", "to_sid": rsid,
             "t": 100, "kind": "question", "body": "which port?"},
            self._back("m1", 150, host="TESTHOST-B", why="refused"),
        ])
        self.assertEqual(km._peer_answered(A), (150, {rsid: 150}))
        self.assertEqual(km._postal_returned(), {(A, rsid): {100: 150}},
                         "keyed by pair, then by the send's own time: the debt readers join a record's ask time to it")

    def test_a_reply_after_the_return_is_not_credited(self):
        # the return came first; a later B→A row finds no live reply-requiring send to answer, so the
        # pair's ending stays the return's t (no live send, no reply credit — _pair_wait_ended's contract)
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            self._back("m1", 150),
            {"id": "r1", "from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "8080"},
        ])
        self.assertEqual(km._peer_answered_at(A), 150)
        self.assertEqual(km._peer_answered(A), (150, {B: 150}))

    def test_a_recalled_question_ends_the_wait_at_the_recall(self):
        # the asker withdrew its own question before the peer read it: the wait it opened is over at the
        # recall's t, and a stamp filed before it is superseded exactly as by a bounce
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
            self._recall("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 150, "base: 0 — a recall was not read as terminal, the stamp stood")
        self.assertEqual(km._peer_answered(A), (150, {B: 150}))
        self.assertTrue(km._peer_stamp_superseded(self._stamp(120), km._peer_answered(A)))
        self.assertFalse(km._peer_stamp_superseded(self._stamp(160), km._peer_answered(A)),
                         "a stamp written AFTER the recall is fresher than it — it stands")

    def test_a_recalled_delegate_ends_the_handoff_wait_too(self):
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "delegate", "body": "own the exporter"},
            self._recall("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 150, "a handoff its sender unsent is over, like one that came back")

    def test_a_recalled_coordinate_ends_nothing(self):
        # a heads-up opened no wait; withdrawing it ends none (a guard: green on the base by design)
        self._write([
            {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "coordinate", "body": "fyi"},
            self._recall("m1", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 0)
        self.assertEqual(km._peer_answered(A), (0, {}))
        self.assertEqual(km._postal_returned(), {}, "no reply-requiring send was withdrawn → no return clock")

    def test_a_recall_naming_a_relay_mid_ends_nothing(self):
        # the OUTBOX arm (review find, 2026-09-08): the row names the relay mid ("px-…"), and the item may
        # already have been carried and delivered, so its recall is not terminal — the wait stays open
        # (as on the base, where no recall was read; the maildir recall above ends it)
        rsid = "11111111-2222-3333-4444-000000000009"
        self._write([
            {"id": "px-1.mail.TESTHOST-A", "from_id": A, "to_id": "peer:TESTHOST-B", "toName": "TESTHOST-B:api",
             "to_sid": rsid, "t": 100, "kind": "question", "body": "which port?"},
            self._recall("px-1.mail.TESTHOST-A", 150),
        ])
        self.assertEqual(km._peer_answered_at(A), 0)
        self.assertEqual(km._peer_answered(A), (0, {}))
        self.assertEqual(km._postal_returned(), {})


class ReturnedReplyDoesNotEndTheWait(_AwaitBase):
    """A reply the replier recalled before the asker read it is not the peer's word (2026-09-08): the
    ending clock read last_any[(B, A)] and credited B's sent row even after a terminal row named it — a
    recall unlinks it from A's new/ unread — so A's stamp lifted on an answer A never received and the
    card left a wait that was still on. The bounced reply is #1071's rule and its test
    (RefusedSendsAreNoAsk.test_a_bounced_reply_answers_nothing holds _peer_answered_at at 0); the recall
    rides the same skip through _learn_return. Guard: with nothing naming it the reply answers as ever
    (test_the_reply_alone_answers; AwaitRelease.test_question_release_unchanged)."""

    ROWS = [
        {"id": "m1", "from_id": A, "to_id": B, "t": 100, "kind": "question", "body": "which port?"},
        {"id": "r1", "from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "8080"},
    ]

    def test_the_reply_alone_answers(self):
        self._write(self.ROWS)
        self.assertEqual(km._peer_answered_at(A), 200)
        self.assertTrue(km._peer_stamp_superseded(_stamp(150), km._peer_answered(A)))

    def test_a_recalled_reply_answers_nothing(self):
        self._write(self.ROWS + [ReturnedSendEndsTheWait._recall("r1", 250)])
        self.assertEqual(km._peer_answered_at(A), 0, "base: 200 — the stamp lifted on a reply A never received")
        self.assertEqual(km._peer_answered(A), (0, {}))
        self.assertFalse(km._peer_stamp_superseded(_stamp(150), km._peer_answered(A)),
                         "the stamp stands — A is still waiting on B")

    def test_the_exclusion_is_per_message_not_per_second(self):
        # B's live reply in the SAME second as the one that came back still answers: the join is the id,
        # and last_any keys the pair by its newest live row exactly as before
        self._write(self.ROWS + [
            {"id": "r2", "from_id": B, "to_id": A, "t": 200, "kind": "coordinate", "body": "8080 (resent)"},
            ReturnedSendEndsTheWait._recall("r1", 250),
        ])
        self.assertEqual(km._peer_answered_at(A), 200)
        self.assertEqual(km._peer_answered(A), (200, {B: 200}))


if __name__ == "__main__":
    unittest.main()
