#!/usr/bin/env python3
"""The awaiting-peer MATRIX (the user 2026-08-24, after three reports of idle sessions reading
"awaiting a peer"): message kind (delegate/coordinate/question) x direction (sent/received) x
reply-expectation x closer involvement -> does awaiting-peer fire, via which writer, retiring on
which event. The rule under test: awaiting-a-peer requires an un-answered kind=question the session
ITSELF sent — the wait-map's post-2026-08-15 semantics — and the judge writers (the closer's verdict
path, the nudge planner's awaiting op) may not widen it: a DELEGATE transfers ownership, a
COORDINATE requests nothing, and an idle recipient reads idle. Every kept state names its exact
retiring event (the peer's any-kind reply); the write-time supersede key is pinned on BOTH stamp
readers (the 2026-08-19 audit fixed only one twin). All fixtures SYNTHETIC."""
import json
import os
import tempfile
import time
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
km = load_source("romp_kernel_awmx", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"   # the session under test (a worker)
MGR = "66666666-7777-8888-9999-aaaaaaaaaaaa"   # its manager peer
NOW = 1_781_100_000
T0 = NOW - 3600


def _msg(i, f, t_, ts, kind=None, body="x"):
    r = {"id": "m%d" % i, "ev": "sent", "from": "web", "from_id": f, "to_id": t_, "t": ts, "body": body}
    if kind:
        r["kind"] = kind
    return json.dumps(r)


def _bounced(i, ts, why="not published: the mail service stopped before the message reached the inbox"):
    """The bus's terminal row for message m<i>: refused, or destroyed unread (review find, 2026-09-08)."""
    return json.dumps({"id": "m%d" % i, "ev": "bounced", "t": ts, "why": why})


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        self._reset_caches()

    def tearDown(self):
        jd._rebind_state(self._saved)
        self._reset_caches()
        self.td.cleanup()

    def _reset_caches(self):
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        km._SESSION_STAMP_CACHE.clear()

    def _log(self, rows):
        jd.MESSAGES.write_text("\n".join(rows) + ("\n" if rows else ""))
        self._reset_caches()

    def _store(self):
        s = {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
             "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "x", "text": "Ship the exporter"}], [])
        return s, SID + ":g1"

    def _close_peer(self, s, why="waiting to hear back from the manager"):
        # one closer sweep filing awaiting kind=peer on menu goal 1 — the writer under test
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": why, "kind": "peer"}}}, t=T0 + 500)


class MatrixWaitMap(_Base):
    """The deterministic writer (fixed 2026-08-15): sent-question-only, any-kind reply retires."""

    def test_sent_kind_grid(self):
        for kind, fires in (("delegate", False), ("coordinate", False), ("question", True)):
            self._log([_msg(1, SID, MGR, T0, kind)])
            g = km._wait_for_graph(NOW, {SID, MGR})
            self.assertEqual(SID in g, fires, "sent %s -> edge %s" % (kind, fires))
            if fires:
                self.assertEqual(g[SID]["peerSid"], MGR)

    def test_received_kind_grid_recipient_never_waits(self):
        # direction=received: no inbound kind may mark the RECIPIENT as awaiting — idle reads idle
        for kind in ("delegate", "coordinate", "question"):
            self._log([_msg(1, MGR, SID, T0, kind)])
            self.assertNotIn(SID, km._wait_for_graph(NOW, {SID, MGR}),
                             "an inbound %s never marks the recipient awaiting" % kind)

    def test_retiring_event_is_the_reply_of_any_kind(self):
        self._log([_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 60, "coordinate")])
        self.assertNotIn(SID, km._wait_for_graph(NOW, {SID, MGR}),
                         "the peer's reply — any kind — is the exact retiring event")


class MatrixCloserGate(_Base):
    """The closer's verdict path (strand 1/2): kind=peer admits ONLY over an open sent-question."""

    def _stamp(self, s, gid):
        return s["nodes"][gid].get("awaitingWhy"), s["nodes"][gid].get("awaitingKind")

    def test_no_postal_history_drops_the_peer_stamp(self):
        s, gid = self._store()
        self._close_peer(s)
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "an idle session with no open ask reads idle — the closer stands down")
        self.assertFalse(any(r.get("kind") == "awaiting" for r in s["nodes"][gid].get("log", [])),
                         "nothing is filed at all, not even a lifted row")

    def test_sent_delegate_drops_sent_coordinate_drops_sent_question_admits(self):
        for kind, admitted in (("delegate", False), ("coordinate", False), ("question", True)):
            s, gid = self._store()
            self._log([_msg(1, SID, MGR, T0 + 10, kind)])
            self._close_peer(s)
            why, k = self._stamp(s, gid)
            if admitted:
                self.assertEqual(k, "peer", "an open sent-question admits the closer's peer stamp")
                self.assertTrue(why)
                self.assertEqual(s["nodes"][gid].get("awaitingPeers"), [MGR],
                                 "…and records WHICH peer it awaits — the pair-aware supersede's key")
            else:
                self.assertEqual((why, k), (None, None),
                                 "a sent %s never mints awaiting-peer (ownership moved / nothing asked)" % kind)

    def test_an_answered_question_no_longer_admits(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 20, "coordinate")])
        self._close_peer(s)
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "the reply already landed — there is no outstanding ask to wait on")

    def test_received_question_alone_never_admits(self):
        # the strand-2 shape: a worker that dispatched NOTHING replies (coordinate) to its manager's
        # mail; the closer's old "or asked" gloss read that reply as a peer wait
        s, gid = self._store()
        self._log([_msg(1, MGR, SID, T0 + 10, "question"), _msg(2, SID, MGR, T0 + 20, "coordinate")])
        self._close_peer(s, why="reported results; continues when the manager responds")
        self.assertEqual(self._stamp(s, gid), (None, None),
                         "an idle recipient reads idle — its own reply is not an ask")

    def test_other_kinds_pass_the_gate_untouched(self):
        s, gid = self._store()
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": "CI run 12 still going", "kind": "job"}}}, t=T0 + 500)
        self.assertEqual(self._stamp(s, gid), ("CI run 12 still going", "job"),
                         "the gate is peer-scoped: job/agents/task/timer file as before")

    def test_a_rejected_reassert_is_a_stand_down_not_a_lift(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        self._close_peer(s)                       # admitted while the ask is open
        self.assertEqual(self._stamp(s, gid)[1], "peer")
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 20, "coordinate")])
        self._close_peer(s)                       # re-assert now that the ask is answered: rejected
        self.assertEqual(self._stamp(s, gid)[1], "peer",
                         "the standing stamp is NOT lifted by the stand-down (no new information "
                         "was filed); its retirement stays the read-side answered supersede")

    def test_the_two_readers_of_the_postal_log_agree(self):
        # jd._open_peer_asks mirrors km._postal_wait_maps (question-only + alias re-key); pin them
        # against one fixture so they cannot drift apart. The comparison is the raw MAPS, not the
        # alive-filtered _wait_for_graph: the gate deliberately keeps a dead/unknown peer's open ask
        # (the dead-wait sweep owns that ending, not the write gate).
        def km_open(sid):
            last_any, last_ask, _aw = km._postal_wait_maps()
            return any(f == sid and last_any.get((p, sid), 0) < meta[0]
                       for (f, p), meta in last_ask.items())
        grid = [
            [_msg(1, SID, MGR, T0, "delegate")],
            [_msg(1, SID, MGR, T0, "coordinate")],
            [_msg(1, SID, MGR, T0, "question")],
            [_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "delegate")],
            [_msg(1, SID, MGR, T0, None, "QUESTION: which port?")],       # legacy kindless ask
            [_msg(1, SID, "peer:otherbox", T0, "question")],              # cross-host, relay-keyed
            [_msg(1, SID, MGR, T0, "question"), _bounced(1, T0 + 1)],     # refused: never reached anyone
            [_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "coordinate"), _bounced(2, T0 + 6)],
        ]
        for rows in grid:
            self._log(rows)
            self.assertEqual(jd._open_peer_asks(SID), km_open(SID),
                             "gate and wait-maps disagree on: %s" % rows)
        # and where the peer IS alive, the user-facing graph agrees with the gate too
        self._log([_msg(1, SID, MGR, T0, "question")])
        self.assertTrue(jd._open_peer_asks(SID) and SID in km._wait_for_graph(NOW, {SID, MGR}))

    def test_a_refused_question_is_no_open_ask_for_either_reader(self):
        # A sent row a terminal `bounced` row closed never reached the recipient (review find,
        # 2026-09-08): the bus gave up on it (a write a crash cut short, a file it could not read) or
        # destroyed it unread, so
        # neither reader counts it as an ask, nor as an answer. Mutant: either reader's `ended` filter
        # removed (the agreement test above would still pass with both wrong).
        def km_open(sid):
            last_any, last_ask, _aw = km._postal_wait_maps()
            return any(f == sid and last_any.get((p, sid), 0) < meta[0]
                       for (f, p), meta in last_ask.items())
        self._log([_msg(1, SID, MGR, T0, "question"), _bounced(1, T0 + 1)])
        self.assertFalse(jd._open_peer_asks(SID), "the gate: no open ask")
        self.assertFalse(km_open(SID), "the wait maps: no open ask")
        self.assertNotIn(SID, km._wait_for_graph(NOW, {SID, MGR}), "the card wears none")
        self._log([_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "coordinate"), _bounced(2, T0 + 6)])
        self.assertTrue(jd._open_peer_asks(SID), "a bounced reply answers nothing: the gate")
        self.assertTrue(km_open(SID), "a bounced reply answers nothing: the wait maps")


class TwinsKeyMailByStableId(_Base):
    """Both readers of the postal log key a cross-host row on the recipient's STABLE id (2026-09-08):
    the row's own `to_sid`, else the name alias AT the row's send time (jd._alias_at). The alias used
    to be last-write-wins over the whole log, so a name a NEW session reused re-keyed every OLD
    message to the new sid — the judge's admit gate then read an answered question as an open ask to
    a stranger, and would have stamped awaitingPeers with the wrong sid."""

    X = "11111111-2222-3333-4444-000000000003"   # wore "api" on TESTHOST when SID asked
    Y = "11111111-2222-3333-4444-000000000004"   # reused the name later
    C = "11111111-2222-3333-4444-000000000005"   # whoever Y mailed (how the alias learns Y)

    def _row(self, **kw):
        r = {"ev": "sent", "id": "m%d" % kw.pop("i"), "body": "x"}
        r.update(kw)
        return json.dumps(r)

    def _reused_name(self):
        return [
            self._row(i=1, **{"from": "web"}, from_id=SID, to_id="peer:TESTHOST", toName="TESTHOST:api",
                      t=T0, kind="question"),
            self._row(i=2, **{"from": "api"}, from_id=self.X, to_id=SID, t=T0 + 50, kind="coordinate",
                      from_host="TESTHOST"),
            self._row(i=3, **{"from": "api"}, from_id=self.Y, to_id=self.C, t=T0 + 400, kind="coordinate",
                      from_host="TESTHOST"),
        ]

    def test_the_judge_gate_keeps_an_answered_ask_answered_through_a_name_reuse(self):
        self._log(self._reused_name())
        self.assertEqual(jd._open_ask_peers(SID), [],
                         "X answered; main: [Y] — the ask re-keyed to the new wearer, whom SID never asked")
        _any, last_ask, alias = jd._postal_ask_maps()
        self.assertIn((SID, self.X), last_ask)
        self.assertEqual(jd._alias_at(alias, "TESTHOST:api", T0), self.X)
        self.assertEqual(jd._alias_at(alias, "TESTHOST:api", T0 + 500), self.Y)

    def test_the_judge_gate_reads_to_sid(self):
        self._log([self._row(i=1, **{"from": "web"}, from_id=SID, to_id="peer:TESTHOST",
                             toName="TESTHOST:api", to_sid=self.X, t=T0, kind="question")])
        self.assertEqual(jd._open_ask_peers(SID), [self.X],
                         "the open ask names the sid the send resolved (main: ['peer:TESTHOST:api'])")

    def test_both_readers_agree_on_the_reused_name(self):
        def km_open(sid):
            last_any, last_ask, _aw = km._postal_wait_maps()
            return sorted(p for (f, p), meta in last_ask.items()
                          if f == sid and last_any.get((p, sid), 0) < meta[0])
        for rows in (self._reused_name(),
                     self._reused_name()[:1],                      # X never spoke: raw relay key on both
                     [self._row(i=1, **{"from": "web"}, from_id=SID, to_id="peer:TESTHOST",
                                toName="TESTHOST:api", to_sid=self.X, t=T0, kind="question")]):
            self._log(rows)
            self.assertEqual(jd._open_ask_peers(SID), km_open(SID), "gate and wait-maps disagree on: %s" % rows)

    def test_alias_at_rule(self):
        hist = {"TESTHOST:api": [(150, self.X), (400, self.Y)]}
        self.assertEqual(jd._alias_at(hist, "TESTHOST:api", 100), self.X, "before any sighting: the earliest known")
        self.assertEqual(jd._alias_at(hist, "TESTHOST:api", 150), self.X)
        self.assertEqual(jd._alias_at(hist, "TESTHOST:api", 399), self.X)
        self.assertEqual(jd._alias_at(hist, "TESTHOST:api", 400), self.Y, "at the sighting: the new wearer")
        self.assertEqual(jd._alias_at(hist, "TESTHOST:api", 9_000), self.Y)
        self.assertIsNone(jd._alias_at(hist, "TESTHOST:web", 100), "a name never seen resolves to nothing")

    def test_alias_settle_rule(self):
        # review find, 2026-09-08 (untested until now): the settle SORTS each name's sightings by t and
        # keeps one entry per WEARER CHANGE, so a chatty peer name is one entry, not one per row, and a
        # name that went X -> Y -> X keeps all three changes. Result-identical for _alias_at at every t.
        raw = {"TESTHOST:api": [(400, self.Y), (150, self.X), (450, self.Y), (200, self.X), (900, self.X)],
               "TESTHOST:web": [(300, self.C), (100, self.C)]}
        probe = {k: [(t, jd._alias_at({k: sorted(v)}, k, t)) for t in (50, 150, 200, 400, 450, 900, 5_000)]
                 for k, v in raw.items()}                       # the sorted, uncollapsed reading
        jd._alias_settle(raw)
        self.assertEqual(raw["TESTHOST:api"], [(150, self.X), (400, self.Y), (900, self.X)],
                         "sorted; consecutive sightings of one sid collapse; a returning wearer is a new entry")
        self.assertEqual(raw["TESTHOST:web"], [(100, self.C)], "one wearer, however many rows")
        for k, want in probe.items():
            self.assertEqual([(t, jd._alias_at(raw, k, t)) for t, _ in want], want,
                             "the sid in force at every t is unchanged by the collapse: %s" % k)

    def test_learn_alias_reads_only_rows_a_remote_sender_stamped(self):
        # the history is built from the peer's OWN stamps (from_host + from + from_id); a local row, a
        # relay row (no from_host) and a row without a name file nothing, and a garbage t reads as 0
        alias = {}
        for o in ({"from": "web", "from_id": SID, "to_id": MGR, "t": 10},                  # local: no from_host
                  {"from": "web", "from_id": SID, "to_id": "peer:TESTHOST", "toName": "TESTHOST:api",
                   "to_sid": self.X, "t": 20},                                              # a relay row
                  {"from_host": "TESTHOST", "from_id": self.X, "t": 30},                    # no name
                  {"from_host": "TESTHOST", "from": "api", "from_id": self.X, "t": "nope"},
                  {"from_host": "TESTHOST", "from": "api", "from_id": self.Y, "t": 40}):
            jd._learn_alias(alias, o)
        self.assertEqual(alias, {"TESTHOST:api": [(0, self.X), (40, self.Y)]})

    def test_a_row_that_came_back_still_teaches_the_alias(self):
        # 2026-09-08: identity is not word — a remote sender's row names who wore the name even when the
        # message itself came back or was recalled unread. The alias half is a guard on the exclusion's
        # reach (green on the base by design); the last_any half is the fix (base: the row counted as
        # X's word toward SID)
        for terminal in ({"id": "m2", "ev": "bounced", "t": T0 + 60, "to": "web",
                          "why": "recipient exited; unread mail destroyed by the orphan sweep"},
                         {"id": "m2", "ev": "recall", "t": T0 + 60}):
            self._log([self._row(i=2, **{"from": "api"}, from_id=self.X, to_id=SID, t=T0 + 50,
                                 kind="coordinate", from_host="TESTHOST"),
                       json.dumps(terminal)])
            last_any, _ask, alias = jd._postal_ask_maps()
            self.assertEqual(jd._alias_at(alias, "TESTHOST:api", T0 + 50), self.X, "the judge still learned the wearer")
            self.assertNotIn((self.X, SID), last_any, "…while the row is not X's word: %s" % terminal["ev"])
            k_any, _k_ask, _k_aw = km._postal_wait_maps()
            self.assertEqual(km._postal_peer_names().get(self.X), "TESTHOST:api", "the kernel's display join too")
            self.assertNotIn((self.X, SID), k_any)


class MatrixPlannerGate(_Base):
    """The nudge planner's awaiting op: kind=peer demotes to kindless without an open ask (the op's
    anti-false-interrupt job survives; the false classification does not)."""

    def test_peer_without_ask_demotes_to_kindless(self):
        s, gid = self._store()
        jd.apply_plan(s, "s2", T0 + 100,
                      [{"do": "awaiting", "goal": 1, "why": "holding for the manager's next batch",
                        "kind": "peer"}], jd.open_menu(s))
        nd = s["nodes"][gid]
        self.assertEqual(nd.get("awaitingWhy"), "holding for the manager's next batch",
                         "the why survives — silence would convert to a false needs-you block")
        self.assertIsNone(nd.get("awaitingKind"), "…but the peer claim does not")

    def test_peer_with_open_ask_files_as_peer(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        jd.apply_plan(s, "s2", T0 + 100,
                      [{"do": "awaiting", "goal": 1, "why": "asked the manager which port",
                        "kind": "peer"}], jd.open_menu(s))
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer")


class MatrixSupersedeTwins(_Base):
    """The write-time supersede key holds on BOTH stamp readers (the 2026-08-19 audit fixed
    _goal_awaiting_stamp_full only; _session_stamp_read stayed anchor-keyed, so the card and the
    chip answered one fact two ways — found 2026-08-24)."""

    def _stamped_store_on_disk(self, ev_t):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        menu = jd.open_menu(s)
        jd.apply_close(s, menu, {"done": {}, "block": {},
                                 "awaiting": {1: {"why": "asked the manager which port", "kind": "peer"}}},
                       t=ev_t)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(s))
        km._SESSION_STAMP_CACHE.clear()
        return s, gid

    def test_a_restamp_written_after_the_reply_survives_on_both_readers(self):
        # anchor (ev_t) BEFORE the peer's reply; write time (now) after it. Anchor-keyed reading
        # superseded this instantly; write-time reading keeps it on the card AND the chip.
        s, gid = self._stamped_store_on_disk(ev_t=T0 + 100)
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 200, "coordinate")])
        answered = km._peer_answered_at(SID)
        self.assertEqual(answered, T0 + 200, "the reply is the supersede clock")
        nodes = s["nodes"]
        full = km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered)
        self.assertIsNotNone(full, "write-time keyed: a stamp filed after the reply stands (card)")
        got = km._session_stamp_read(SID)
        self.assertEqual(got[0][2], "asked the manager which port",
                         "…and the session-level reader agrees (chip/lane/pip) — the twins match")

    def test_a_stamp_the_reply_postdates_retires_on_both_readers(self):
        # the reply lands AFTER the stamp's write time (a future-t fixture row beats time.time()):
        # both readers retire it — the peer's answer is the exact retiring event
        s, gid = self._stamped_store_on_disk(ev_t=T0 + 100)
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, future, "coordinate")])
        answered = km._peer_answered_at(SID)
        self.assertEqual(answered, future)
        nodes = s["nodes"]
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                          "the peer answered after the stamp was written -> superseded (card)")
        self.assertIsNone(km._session_stamp_read(SID)[0][0],
                          "…and on the session surfaces alike — one fact, one answer")


class PairAwareSupersede(_Base):
    """Hole (b), 2026-08-24: an identity-carrying stamp ends only on the AWAITED pair's answer; an
    unrelated exchange no longer hides a real wait. Legacy identity-less stamps keep the pair-blind
    read — nothing strands. One predicate for every reader (pinned below). Since 2026-09-08 the
    answered clock walks reply-REQUIRING sends (question/delegate), so the "unrelated exchange" here
    is a QUESTION the other peer answered; a coordinate-only exchange answers nothing (last test)."""

    OTHER = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"    # an unrelated peer on the same log

    def _stamped(self, ev_t=T0 + 100):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question")])
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                       "awaiting": {1: {"why": "asked the manager which port", "kind": "peer"}}}, t=ev_t)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(s))
        km._SESSION_STAMP_CACHE.clear()
        return s, gid

    def test_an_unrelated_pair_answer_no_longer_supersedes(self):
        s, gid = self._stamped()
        future = int(time.time()) + 10_000
        # an answered exchange with a DIFFERENT peer, after the stamp's write time
        self._log([_msg(1, SID, MGR, T0 + 10, "question"),
                   _msg(2, SID, self.OTHER, T0 + 20, "question"),      # PIN MOVED 2026-09-08: was a coordinate
                   _msg(3, self.OTHER, SID, future, "coordinate")])
        answered = km._peer_answered(SID)
        self.assertGreater(answered[0], 0, "the pair-blind scalar WOULD have superseded")
        nodes = s["nodes"]
        self.assertIsNotNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                             "the stamp awaits MGR — mail from another peer cannot end it (card)")
        self.assertEqual(km._session_stamp_read(SID)[0][2], "asked the manager which port",
                         "…and the session-level twin agrees (chip/lane)")

    def test_the_awaited_pair_answer_still_supersedes_instantly(self):
        s, gid = self._stamped()
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, future, "coordinate")])
        answered = km._peer_answered(SID)
        nodes = s["nodes"]
        self.assertIsNone(km._goal_awaiting_stamp_full(nodes, gid, answered_at=answered),
                          "the awaited peer answered after the write -> superseded (card)")
        self.assertIsNone(km._session_stamp_read(SID)[0][0], "…both readers, one predicate")

    def test_a_legacy_identity_less_stamp_keeps_the_pair_blind_read(self):
        s, gid = self._stamped()
        del s["nodes"][gid]["awaitingPeers"]           # a pre-identity stamp, as stored stores hold
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"),
                   _msg(2, SID, self.OTHER, T0 + 20, "question"),      # PIN MOVED 2026-09-08: was a coordinate
                   _msg(3, self.OTHER, SID, future, "coordinate")])
        self.assertIsNone(km._goal_awaiting_stamp_full(s["nodes"], gid, answered_at=km._peer_answered(SID)),
                          "no identity on the stamp -> today's behavior exactly (never strand legacy)")

    def test_a_coordinate_only_exchange_answers_nothing_even_pair_blind(self):
        # 2026-09-08 (the two fixtures above sent OTHER a coordinate before this): the answered clock
        # walks reply-REQUIRING sends, so a heads-up OTHER happened to reply to is not an answer to
        # anything and cannot end even a legacy identity-less stamp. main: superseded (walked last_any).
        s, gid = self._stamped()
        del s["nodes"][gid]["awaitingPeers"]
        future = int(time.time()) + 10_000
        self._log([_msg(1, SID, MGR, T0 + 10, "question"),
                   _msg(2, SID, self.OTHER, T0 + 20, "coordinate"),
                   _msg(3, self.OTHER, SID, future, "coordinate")])
        self.assertEqual(km._peer_answered(SID), (0, {}), "nothing SID asked has been answered")
        self.assertIsNotNone(km._goal_awaiting_stamp_full(s["nodes"], gid, answered_at=km._peer_answered(SID)),
                             "the open question to MGR stands; a stranger's heads-up is not its answer")

    def test_every_reader_shares_the_one_predicate(self):
        # the twins rule, extended (the manager's ask): no reader may inline its own compare — the
        # write-time-vs-answer compare exists ONLY inside _peer_stamp_superseded
        src = open(os.path.join(BIN, "romp-kernel")).read()
        inline = [l for l in src.splitlines()
                  if "_stamp_written_at(nd) <" in l or "_stamp_written_at(nd) >=" in l]
        self.assertEqual(len(inline), 1, "one compare, inside the predicate: %r" % inline)
        self.assertGreaterEqual(src.count("_peer_stamp_superseded(nd,"), 4,
                                "card reader, session twin, sweep lift, fire-gate — all through it")


class CrossHostDelegation(_Base):
    """Hole (a), 2026-08-24: a cross-host delegate plants the sender-side tracking node from the
    sent row (declared kind, horizon-bounded, idempotent), and the recipient's REPLY mail — never
    the relay ack — completes it."""

    RHOST, RNAME = "TESTHOST-B", "web"
    RSID = "eeeeeeee-ffff-0000-1111-222222222222"     # the remote recipient's sid, learned on reply

    def _xrow(self, i, ts, kind="delegate", body="own the exporter work", to_sid=None):
        r = {"id": "px-%d.mail.TESTHOST-A" % i, "ev": "sent", "from": "api",
             "from_id": SID, "to_id": "peer:%s" % self.RHOST,
             "toName": "%s:%s" % (self.RHOST, self.RNAME), "t": ts,
             "kind": kind, "body": body}
        if to_sid:
            r["to_sid"] = to_sid                       # a relay row since 2026-09-08 names the recipient by id
        return json.dumps(r)

    def _reply(self, i, ts):
        return json.dumps({"id": "rx-%d.mail.%s" % (i, self.RHOST), "ev": "sent", "from": self.RNAME,
                           "from_id": self.RSID, "from_host": self.RHOST, "to_id": SID,
                           "t": ts, "kind": "coordinate", "body": "done: exporter shipped"})

    def _fleet_stub(self):
        # run_courier/run_propagate discover the fleet from names+transcripts; stub the discovery
        # to the one local sender (the recipient is REMOTE by construction)
        self._saved_discover = jd.discover
        jd.discover = lambda now: [(SID, "/tmp/none.jsonl", None, "api")]
        self.addCleanup(lambda: setattr(jd, "discover", self._saved_discover))

    def _handoffs(self):
        st = jd.load_goals(SID)
        return [nd for nd in st["nodes"].values() if isinstance(nd.get("handoff"), dict)]

    def test_a_cross_host_delegate_plants_the_tracking_node(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        hs = self._handoffs()
        self.assertEqual(len(hs), 1, "the sent row is the authoritative record — planted from it")
        self.assertEqual(hs[0]["handoff"]["peer"], "TESTHOST-B:web",
                         "the identity is toName — displays resolve it, the remote arm re-keys from it")
        self.assertIn("↪ delegated to TESTHOST-B:web", hs[0]["text"])
        self.assertNotIn("tracked", hs[0]["handoff"],
                         "tracked never rides the relay (its primary view lives on the sender's "
                         "kernel); the ONE thing that crosses beyond the mail itself is the "
                         "kernel-walked root-ask record, T126 — proof, not machinery")
        jd.run_courier(now=T0 + 200)
        self.assertEqual(len(self._handoffs()), 1, "idempotent by msgId — one plant per message ever")

    def test_declared_only_and_horizon_bounded(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10, kind="coordinate"),
                   self._xrow(2, T0 - jd.COURIER_RETRY_HORIZON - 60)])
        jd.run_courier(now=T0 + 100)
        self.assertEqual(self._handoffs(), [], "a non-delegate never plants; ancient rows never backfill")

    def test_the_reply_completes_and_the_relay_ack_does_not(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        # the far host's delivery ack lands — the ASK arrived; the work is NOT done
        rows = [self._xrow(1, T0 + 10),
                json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "relayed", "t": T0 + 100})]
        self._log(rows)
        jd.run_propagate(now=T0 + 200)
        self.assertFalse(self._handoffs()[0].get("nodeComplete"),
                         "relayed = delivered, not completed — delivery cannot check work off")
        # the recipient's reply mail is the report-back event
        rows.append(self._reply(1, T0 + 300))
        self._log(rows)
        jd.run_propagate(now=T0 + 400)
        nd = self._handoffs()[0]
        self.assertTrue(nd.get("nodeComplete"), "the reply completes the tracking node")
        self.assertIn("reported back by TESTHOST-B:web", nd.get("doneWhy") or "")

    def test_an_unrelated_peers_reply_never_completes(self):
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10)])
        jd.run_courier(now=T0 + 100)
        other = json.dumps({"id": "rx-9.mail.TESTHOST-C", "ev": "sent", "from": "tests",
                            "from_id": "dddddddd-0000-1111-2222-333333333333",
                            "from_host": "TESTHOST-C", "to_id": SID, "t": T0 + 300,
                            "kind": "coordinate", "body": "unrelated news"})
        self._log([self._xrow(1, T0 + 10), other])
        jd.run_propagate(now=T0 + 400)
        self.assertFalse(self._handoffs()[0].get("nodeComplete"),
                         "only the delegated peer's own reply is the report-back event")

    def test_a_row_that_carries_to_sid_plants_it_as_the_trackers_exact_key(self):
        # review find, 2026-09-08: the plant had the row's to_sid in hand and dropped it, so the remote
        # arm re-derived the recipient by NAME while the kernel's wait maps read the same row by sid
        self._fleet_stub()
        # two real dispatches, so each keeps its own tracker: the plant collapses a byte-identical
        # OPEN twin (same peer, label AND recorded body) into one node by design (2026-08-28)
        self._log([self._xrow(1, T0 + 10, to_sid=self.RSID),
                   self._xrow(2, T0 + 20, body="own the importer work")])
        jd.run_courier(now=T0 + 100)
        by_mid = {nd["handoff"]["msgId"]: nd["handoff"] for nd in self._handoffs()}
        h = by_mid["px-1.mail.TESTHOST-A"]
        self.assertEqual((h["peer"], h["toSid"]), ("TESTHOST-B:web", self.RSID),   # KeyError before the fix
                         "the display identity stays toName; the exact key rides beside it")
        self.assertNotIn("toSid", by_mid["px-2.mail.TESTHOST-A"],
                         "a legacy row plants no toSid: the arm falls to the send-time alias for it")

    def test_both_readers_complete_a_to_sid_handoff_on_the_recreated_peers_first_mail(self):
        # the twins rule, for handoffs (review find, 2026-09-08): a peer recreated under the same name
        # (new sid RSID) whose FIRST mail to this host is its report-back. The kernel's wait maps key
        # the row on to_sid and read it answered; the courier must agree, anchoring the NAME at send
        # time picked the old wearer, found no reply, and left the tracker open while the stamp lifted.
        self._fleet_stub()
        old = "eeeeeeee-ffff-0000-1111-333333333333"    # the name's earlier wearer, sighted before the send
        rows = [json.dumps({"id": "rx-0.mail.%s" % self.RHOST, "ev": "sent", "from": self.RNAME,
                            "from_id": old, "from_host": self.RHOST, "to_id": SID, "t": T0 - 500,
                            "kind": "coordinate", "body": "hello from the first web"}),
                self._xrow(1, T0 + 10, to_sid=self.RSID)]
        self._log(rows)
        jd.run_courier(now=T0 + 100)
        rows.append(self._reply(1, T0 + 300))            # RSID's first sighting is the report-back
        self._log(rows)
        self.assertEqual(km._peer_answered_at(SID), T0 + 300, "the wait maps read the row answered")
        self.assertEqual(jd.run_propagate(now=T0 + 400), 1,
                         "the courier agrees (name-anchored: 0: keyed to the old wearer, who never replied)")
        nd = self._handoffs()[0]
        self.assertTrue(nd.get("nodeComplete"))
        self.assertIn("reported back by TESTHOST-B:web", nd.get("doneWhy") or "")

    def test_a_delegate_that_came_back_plants_no_tracker(self):
        # 2026-09-08: the far host refused the handoff (a terminal bounced row names it) before the
        # courier's pass — a tracker planted from it would wait on a report-back nothing can bring
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10),
                   json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "bounced", "t": T0 + 50,
                               "to": self.RNAME, "host": self.RHOST, "why": "refused"}),
                   self._xrow(2, T0 + 20, body="own the importer work")])
        jd.run_courier(now=T0 + 100)
        self.assertEqual([nd["handoff"]["msgId"] for nd in self._handoffs()], ["px-2.mail.TESTHOST-A"],
                         "main: both planted — the returned delegate got a tracker no event could close")

    def test_a_recalled_outbox_delegate_still_plants_the_tracker(self):
        # review find, 2026-09-08: an OUTBOX recall names the relay mid ("px-…"), and the outbox item
        # outlives the carry (the exchange relays it without removing it; only the end-to-end ack does),
        # so the far host may already have delivered it and the peer may report back — NOT terminal,
        # and the tracker is planted as before (green on the base by design, where no recall was read).
        # The bus owns the follow-up: refuse to recall a carried item, or stamp the row with its box.
        self._fleet_stub()
        self._log([self._xrow(1, T0 + 10),
                   json.dumps({"id": "px-1.mail.TESTHOST-A", "ev": "recall", "t": T0 + 50}),
                   self._xrow(2, T0 + 20, body="own the importer work")])
        jd.run_courier(now=T0 + 100)
        self.assertEqual(sorted(nd["handoff"]["msgId"] for nd in self._handoffs()),
                         ["px-1.mail.TESTHOST-A", "px-2.mail.TESTHOST-A"],
                         "a recalled outbox delegate keeps its tracker: the recall is not read as terminal")


class ReturnedAskTwins(_Base):
    """A send the bus RETURNED (a terminal `bounced` row naming its id) is a closed ask on both readers
    (2026-09-08): the closer's admit gate sees no open question to stamp a peer wait over, and the wait
    maps set no chip edge. Before, both twins skipped the row (no from_id/to_id) and the sender read as
    waiting on a peer that never got the message — a card parked on a wait no event could end. The same
    day's second fold: a maildir `recall` row (the sender withdrew the message unread) is the other
    terminal kind, riding #1071's one skip before last_any — so a recalled reply, like a bounced one
    (#1071's own test), leaves the asker's question open on both readers."""

    @staticmethod
    def _back(i, ts, host=""):
        return json.dumps({"id": "m%d" % i, "ev": "bounced", "t": ts, "to": "web", "host": host,
                           "why": "recipient exited; unread mail destroyed by the orphan sweep"})

    @staticmethod
    def _recall(i, ts):
        return json.dumps({"id": "m%d" % i, "ev": "recall", "t": ts})    # the sender unsent it unread

    @staticmethod
    def _km_open(sid):
        last_any, last_ask, _aw = km._postal_wait_maps()
        return any(f == sid and last_any.get((p, sid), 0) < meta[0] for (f, p), meta in last_ask.items())

    def test_the_closer_does_not_stamp_a_peer_wait_over_a_returned_question(self):
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), self._back(1, T0 + 20)])
        self._close_peer(s)
        self.assertEqual((s["nodes"][gid].get("awaitingWhy"), s["nodes"][gid].get("awaitingKind")), (None, None),
                         "main: the gate read the returned question as open and admitted the peer stamp")
        self.assertEqual(jd._open_ask_peers(SID), [], "no open ask: the message never reached the manager")

    def test_both_readers_agree_on_a_returned_ask(self):
        def km_open(sid):
            last_any, last_ask, _aw = km._postal_wait_maps()
            return any(f == sid and last_any.get((p, sid), 0) < meta[0] for (f, p), meta in last_ask.items())
        grid = [   # the single returned question is test_a_refused_question_is_no_open_ask_for_either_reader's
            ([_msg(1, SID, MGR, T0, "question"), _msg(2, SID, MGR, T0 + 5, "question"), self._back(1, T0 + 9)], True),
            ([_msg(1, SID, "peer:otherbox", T0, "question"), self._back(1, T0 + 5, host="otherbox")], False),
        ]
        for rows, open_ in grid:
            self._log(rows)
            self.assertEqual((jd._open_peer_asks(SID), km_open(SID)), (open_, open_),
                             "gate and wait-maps on: %s" % rows)

    def test_the_closer_does_not_stamp_a_peer_wait_over_a_recalled_question(self):
        # 2026-09-08: the worker withdrew its question before the manager read it — as over as a bounce
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), self._recall(1, T0 + 20)])
        self._close_peer(s)
        self.assertEqual((s["nodes"][gid].get("awaitingWhy"), s["nodes"][gid].get("awaitingKind")), (None, None),
                         "base: the gate read the withdrawn question as open and admitted the peer stamp")
        self.assertEqual(jd._open_ask_peers(SID), [], "no open ask: the worker unsent it")

    def test_both_readers_agree_on_a_recalled_ask(self):
        grid = [
            ([_msg(1, SID, MGR, T0, "question"), self._recall(1, T0 + 5)], False),
            ([_msg(1, SID, MGR, T0, "question"), _msg(2, SID, MGR, T0 + 5, "question"), self._recall(1, T0 + 9)], True),
            # an OUTBOX recall (the row names the relay mid, "px-…") is not terminal: the item may already
            # have been carried, so the cross-host ask stays open on both readers (review find, 2026-09-08)
            ([json.dumps({"id": "px-1.mail.TESTHOST", "ev": "sent", "from": "web", "from_id": SID,
                          "to_id": "peer:otherbox", "toName": "otherbox:web", "t": T0, "body": "x", "kind": "question"}),
              json.dumps({"id": "px-1.mail.TESTHOST", "ev": "recall", "t": T0 + 5})], True),
        ]
        for rows, open_ in grid:
            self._log(rows)
            self.assertEqual((jd._open_peer_asks(SID), self._km_open(SID)), (open_, open_),
                             "gate and wait-maps on: %s" % rows)

    def test_both_readers_agree_a_recalled_reply_answers_nothing(self):
        # the manager recalled its reply before the worker read it: the worker never received it, so its
        # question is as open as before on both readers; the first row is the guard. (The BOUNCED reply
        # is #1071's rule and test: test_a_refused_question_is_no_open_ask_for_either_reader's second half.)
        grid = [
            ([_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "coordinate")], False),
            ([_msg(1, SID, MGR, T0, "question"), _msg(2, MGR, SID, T0 + 5, "coordinate"), self._recall(2, T0 + 9)], True),
        ]
        for rows, open_ in grid:
            self._log(rows)
            self.assertEqual((jd._open_peer_asks(SID), self._km_open(SID)), (open_, open_),
                             "gate and wait-maps on: %s" % rows)

    def test_the_closer_still_stamps_when_the_reply_was_recalled(self):
        # the admit gate's other direction, at the closer's WRITE: the wait IS on when the only answer was
        # withdrawn before it arrived (the bounced reply's gate predicate is #1071's test)
        s, gid = self._store()
        self._log([_msg(1, SID, MGR, T0 + 10, "question"), _msg(2, MGR, SID, T0 + 20, "coordinate"),
                   self._recall(2, T0 + 30)])
        self._close_peer(s)
        self.assertEqual(s["nodes"][gid].get("awaitingKind"), "peer",
                         "base: the recalled reply read as the answer and the gate dropped the stamp")


if __name__ == "__main__":
    unittest.main()
