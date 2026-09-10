#!/usr/bin/env python3
"""The DEBT reminder (the user 2026-07-26): an unanswered postal question/delegate paints "Awaiting
<peer>" on the SENDER's cards and parks them (the auto-nudge deliberately skips peer-waiting cards) —
but nothing ever told the RECIPIENT it owed a reply, so a mis-declared kind (a "question" whose prose
said no reply was needed) parked its sender silently for a day. The fix: an idle session sitting on
unanswered inbound asks from LIVE peers gets ONE reminder in the asker's terms, deduped per ask event
(auto-nudge.json debtNudged) — either honest exit (an answer, or "nothing needed") is the reply that
releases the asker's wait. All fixtures SYNTHETIC (placeholder UUIDs, the notes-api demo names)."""
import json
import os
import unittest
from romp_load import load_source
from pathlib import Path
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_debt", os.path.join(BIN, "romp-kernel"))

DEBTOR = "11111111-2222-3333-4444-555555555555"     # the idle session that owes replies
ASKER = "66666666-7777-8888-9999-000000000000"      # the live peer parked on the wait
ASKER2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
NOW = 1781100000
T_ASK = NOW - 1800


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append((sid, body))


class DebtBase(unittest.TestCase):
    def setUp(self):
        self._saved = (km._postal_wait_maps, km._name_of, km._auto_nudge_data,
                       km._write_auto_nudge, km.Sessions.backend_for,
                       getattr(km, "_postal_returned", None))   # absent before 2026-09-08: the seam degrades
        self._d = {"nudged": {}}
        km._auto_nudge_data = lambda: self._d
        km._write_auto_nudge = lambda d: self._d.update(d) or True   # the writer's verdict: the reminder
        #                                            sends only when its record landed (a None here reads as refused)
        km._name_of = lambda sid: {ASKER: "web", ASKER2: "api", DEBTOR: "tests"}.get(sid)
        self.rec = _Recorder()
        km.Sessions.backend_for = lambda sid: self.rec
        self._maps = ({}, {}, {})   # (last_any, last_ask, last_await)
        km._postal_wait_maps = lambda: self._maps
        self._returned = {}         # {(asker, debtor): {ask t: return t}} — the maps' returns, same seam
        km._postal_returned = lambda: self._returned

    def tearDown(self):
        (km._postal_wait_maps, km._name_of, km._auto_nudge_data,
         km._write_auto_nudge, km.Sessions.backend_for, _ret) = self._saved
        if _ret is None:
            del km._postal_returned
        else:
            km._postal_returned = _ret

    def _ask(self, kind="question", head="Which port should the staging server use?",
             ts=T_ASK, asker=ASKER, answered_at=None, returned_at=None):
        last_any = {(asker, DEBTOR): ts}
        if answered_at is not None:
            last_any[(DEBTOR, asker)] = answered_at
        # an ask the bus returned makes no last_ask entry and lands in the returns, as the real maps have it
        last_ask = {} if returned_at is not None else {(asker, DEBTOR): (ts, kind, head)}
        self._returned = {(asker, DEBTOR): {ts: returned_at}} if returned_at is not None else {}
        self._maps = (last_any, last_ask, {})
        km._postal_wait_maps = lambda: self._maps


class DebtAsks(DebtBase):
    def test_an_unanswered_ask_from_a_live_peer_is_owed(self):
        self._ask()
        self.assertEqual(km._debt_asks(DEBTOR, {ASKER, DEBTOR}),
                         [(ASKER, "web", T_ASK, "question", "Which port should the staging server use?")])

    def test_any_later_message_back_settles_the_debt(self):
        # same rule as the sender's chip: a reply of ANY kind after the ask answers it
        self._ask(answered_at=T_ASK + 60)
        self.assertEqual(km._debt_asks(DEBTOR, {ASKER, DEBTOR}), [])

    def test_a_dead_asker_is_no_debt(self):
        # answering a dead session releases nobody — mirror the wait edge's alive gate
        self._ask()
        self.assertEqual(km._debt_asks(DEBTOR, {DEBTOR}), [])

    def test_legacy_two_tuple_records_still_read(self):
        # a cached (ts, kind) record from before the head rode along must not crash the scan
        self._maps = ({(ASKER, DEBTOR): T_ASK}, {(ASKER, DEBTOR): (T_ASK, "question")}, {})
        km._postal_wait_maps = lambda: self._maps
        self.assertEqual(km._debt_asks(DEBTOR, {ASKER}),
                         [(ASKER, "web", T_ASK, "question", "")])


class DebtReminder(DebtBase):
    def test_the_reminder_names_the_asker_and_quotes_their_words(self):
        self._ask()
        self.assertTrue(km._fire_debt_reminder(DEBTOR, NOW, {ASKER, DEBTOR}))
        (sid, body), = self.rec.sent
        self.assertEqual(sid, DEBTOR)
        self.assertIn("web asked you something", body)
        self.assertIn("> Which port should the staging server use?", body,
                      "the asker's own first words are quoted back")
        self.assertIn("Reply to web now", body)
        self.assertIn("don't actually need anything", body, "the nothing-needed exit is offered")
        self.assertIn("<!-- romp-injected -->", body)
        self.assertIn("<!-- romp-system -->", body,
                      "the planner treats the response as housekeeping, never a fresh card")

    def test_a_handoff_reads_as_a_handoff(self):
        self._ask(kind="delegate", head="Take over the fixtures backfill.")
        km._fire_debt_reminder(DEBTOR, NOW, {ASKER, DEBTOR})
        (_sid, body), = self.rec.sent
        self.assertIn("web handed you some work", body)

    def test_one_reminder_per_ask_ever(self):
        self._ask()
        self.assertTrue(km._fire_debt_reminder(DEBTOR, NOW, {ASKER, DEBTOR}))
        self.assertFalse(km._fire_debt_reminder(DEBTOR, NOW + 60, {ASKER, DEBTOR}),
                         "an ignored reminder escalates on the SENDER's card, never by repeating")
        self.assertEqual(len(self.rec.sent), 1)
        key = "%s>%s:%d" % (ASKER, DEBTOR, T_ASK)
        self.assertEqual(self._d["debtNudged"][key], NOW, "the dedup record carries the fire time")

    def test_a_newer_ask_from_the_same_peer_re_arms(self):
        self._ask()
        km._fire_debt_reminder(DEBTOR, NOW, {ASKER, DEBTOR})
        self._ask(ts=T_ASK + 900, head="Second thing: which region?")
        self.assertTrue(km._fire_debt_reminder(DEBTOR, NOW + 60, {ASKER, DEBTOR}),
                        "a new ask is a new event with its own reminder")
        self.assertIn("which region", self.rec.sent[-1][1])

    def test_several_debts_ride_one_message(self):
        last_any = {(ASKER, DEBTOR): T_ASK, (ASKER2, DEBTOR): T_ASK + 5}
        last_ask = {(ASKER, DEBTOR): (T_ASK, "question", "Which port?"),
                    (ASKER2, DEBTOR): (T_ASK + 5, "delegate", "Take the backfill.")}
        self._maps = (last_any, last_ask, {})
        km._postal_wait_maps = lambda: self._maps
        self.assertTrue(km._fire_debt_reminder(DEBTOR, NOW, {ASKER, ASKER2, DEBTOR}))
        self.assertEqual(len(self.rec.sent), 1, "debts coalesce into one message")
        body = self.rec.sent[0][1]
        self.assertIn("web asked you", body)
        self.assertIn("api handed you", body)
        self.assertIn("Reply to each of them now", body)


class ReminderOutcomes(DebtBase):
    """Piece 3, the outcome side: a past reminder either WORKED (any reply back retires it silently) or
    FAILED (the debtor ended a turn past the fire without replying → the ASKER's card escalates), each
    judged once-ever. The debtor-never-returns case rides the backstop tick."""

    def setUp(self):
        super().setUp()
        self._esc = []
        self._saved_esc = km._debt_escalate
        km._debt_escalate = lambda asker, debtor, ts, now: (self._esc.append((asker, debtor, ts)) or True)
        self._key = "%s>%s:%d" % (ASKER, DEBTOR, T_ASK)

    def tearDown(self):
        km._debt_escalate = self._saved_esc
        super().tearDown()

    def _armed(self, fire_t=NOW - 600):
        self._d["debtNudged"] = {self._key: fire_t}

    def test_an_answered_reminder_retires_silently(self):
        self._ask(answered_at=T_ASK + 60)
        self._armed()
        km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 100, "end": NOW - 90}, NOW)
        self.assertEqual(self._d["debtNudged"], {}, "the reminder worked; nothing escalates")
        self.assertEqual(self._esc, [])

    def test_moving_on_without_replying_escalates_once(self):
        self._ask()
        self._armed(fire_t=NOW - 600)
        lt = {"t": NOW - 300, "end": NOW - 200}        # a turn ENDED after the fire, still no reply
        km._debt_reminder_outcomes(DEBTOR, lt, NOW)
        self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)], "the wait is now the user's")
        self.assertEqual(self._d["debtNudged"], {}, "…and the record retires (once-ever)")

    def test_no_turn_since_the_fire_keeps_waiting(self):
        self._ask()
        self._armed(fire_t=NOW - 600)
        lt = {"t": NOW - 3000, "end": NOW - 2900}      # latest ended turn PREDATES the fire
        km._debt_reminder_outcomes(DEBTOR, lt, NOW)
        self.assertEqual(self._esc, [])
        self.assertIn(self._key, self._d["debtNudged"], "the debtor hasn't had its say yet")

    def test_the_backstop_escalates_a_debtor_that_never_returns(self):
        self._ask()
        self._armed(fire_t=NOW - km.NUDGE_DEFER_BACKSTOP_SECS - 60)
        km._debt_backstop_tick(NOW)
        self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)])
        self.assertEqual(self._d["debtNudged"], {})

    def test_the_backstop_leaves_a_young_reminder_alone(self):
        self._ask()
        self._armed(fire_t=NOW - 600)
        km._debt_backstop_tick(NOW)
        self.assertEqual(self._esc, [])
        self.assertIn(self._key, self._d["debtNudged"])

    def test_the_backstop_drops_a_malformed_key(self):
        self._d["debtNudged"] = {"not-a-key": NOW - 600}
        km._debt_backstop_tick(NOW)
        self.assertEqual(self._d["debtNudged"], {}, "malformed records drop rather than loop forever")

    def test_a_returned_ask_retires_the_reminder_without_escalating(self):
        # 2026-09-08: the debtor exited before the reminder's turn read the ask; ORPHAN_GRACE later the
        # sweep destroyed the mail and wrote the terminal bounced row, and the bus told the asker. The
        # return IS the outcome. The guard — the same turn with no return escalates — is
        # test_moving_on_without_replying_escalates_once above.
        self._ask(returned_at=NOW - 400)
        self._armed(fire_t=NOW - 600)
        lt = {"t": NOW - 300, "end": NOW - 200}        # a turn ended after the fire, no reply
        km._debt_reminder_outcomes(DEBTOR, lt, NOW)
        self.assertEqual(self._esc, [], "main: escalated — a block on the asker's card for an ask that came back")
        self.assertEqual(self._d["debtNudged"], {}, "the record retires, once-ever, like an answered one")

    def test_the_backstop_retires_a_returned_ask_too(self):
        # the debtor-never-returns path (its guard: test_the_backstop_escalates_a_debtor_that_never_returns)
        self._ask(returned_at=NOW - 400)
        self._armed(fire_t=NOW - km.NUDGE_DEFER_BACKSTOP_SECS - 60)
        km._debt_backstop_tick(NOW)
        self.assertEqual(self._esc, [], "main: the 6h backstop flipped the asker's card for a returned ask")
        self.assertEqual(self._d["debtNudged"], {})

    def test_a_return_of_a_different_ask_leaves_the_record_to_its_own_outcome(self):
        # the join is the record's own ask time: a LATER ask on the same pair that came back says nothing
        # about this one, which still escalates when the debtor moves on without replying
        self._ask()
        self._returned = {(ASKER, DEBTOR): {T_ASK + 300: NOW - 400}}
        self._armed(fire_t=NOW - 600)
        km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
        self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)])
        self.assertEqual(self._d["debtNudged"], {})

    def test_a_live_twin_in_the_same_second_keeps_the_record_open(self):
        # the join is per send time: m1 and m2 both at T_ASK on the pair, m1 came back, m2 still waits —
        # the record is m2's as much as m1's, so it stays and escalates on both paths exactly as before
        # (main never retired on a return, so this guards the fold, not main)
        for path in ("turn-end", "backstop"):
            self._esc.clear()
            self._ask()                                              # m2: live, last_ask at T_ASK
            self._returned = {(ASKER, DEBTOR): {T_ASK: NOW - 400}}   # m1: the same second, returned
            if path == "turn-end":
                self._armed(fire_t=NOW - 600)
                km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
            else:
                self._armed(fire_t=NOW - km.NUDGE_DEFER_BACKSTOP_SECS - 60)
                km._debt_backstop_tick(NOW)
            self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)],
                             "%s: the live twin's reminder still escalates (the first fold retired it)" % path)
            self.assertEqual(self._d["debtNudged"], {})


class TerminalRowsThroughTheRealMaps(unittest.TestCase):
    """The debt readers against the REAL maps over a synthetic log (2026-09-08). The stub seam above hands
    the readers their maps, so it cannot show what the maps make of a `recall` row or of a reply that came
    back. (A) an ask its sender withdrew before the debtor read it is no debt, and its reminder retires
    without escalating, exactly as a bounce; (B) a reply the bus returned (the oversize push bounces it to
    the replier without putting it back in the asker's box), or that the replier recalled unread, is not
    the replier's word: the debt stands, and a reminder the debtor moved on from escalates instead of
    reading answered. Guard: the reply alone settles the debt (test_the_reply_alone_settles_the_debt)."""

    ASK = {"ev": "sent", "id": "m1", "from_id": ASKER, "to_id": DEBTOR, "t": T_ASK, "kind": "question",
           "body": "Which port should the staging server use?"}
    REPLY = {"ev": "sent", "id": "r1", "from_id": DEBTOR, "to_id": ASKER, "t": T_ASK + 60,
             "kind": "coordinate", "body": "8080"}

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._saved = (km.jd.MESSAGES, km._auto_nudge_data, km._write_auto_nudge, km._debt_escalate, km._name_of)
        km.jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        self._d = {"nudged": {}}
        km._auto_nudge_data = lambda: self._d
        km._write_auto_nudge = lambda d: self._d.update(d) or True
        self._esc = []
        km._debt_escalate = lambda asker, debtor, ts, now: (self._esc.append((asker, debtor, ts)) or True)
        km._name_of = lambda sid: {ASKER: "web", DEBTOR: "tests"}.get(sid)
        km._POSTAL_WAIT_CACHE[:] = [None, None]

    def tearDown(self):
        km.jd.MESSAGES, km._auto_nudge_data, km._write_auto_nudge, km._debt_escalate, km._name_of = self._saved
        km._POSTAL_WAIT_CACHE[:] = [None, None]
        self.td.cleanup()

    def _log(self, rows):
        km.jd.MESSAGES.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        km._POSTAL_WAIT_CACHE[:] = [None, None]

    def _armed(self, fire_t=NOW - 600):
        self._d["debtNudged"] = {"%s>%s:%d" % (ASKER, DEBTOR, T_ASK): fire_t}

    def test_a_recalled_ask_is_no_debt_and_its_reminder_retires(self):
        self._log([self.ASK, {"ev": "recall", "id": "m1", "t": T_ASK + 30}])
        self.assertEqual(km._debt_asks(DEBTOR, {ASKER}), [], "base: owed — for an ask the debtor never had")
        self._armed()
        km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
        self.assertEqual(self._esc, [], "base: escalated — a block on the asker's card for an ask it withdrew")
        self.assertEqual(self._d["debtNudged"], {}, "the record retires, once-ever, like a bounced one")

    def test_a_reply_that_came_back_settles_nothing(self):
        for name, terminal in (("bounced", {"ev": "bounced", "id": "r1", "t": T_ASK + 90, "to": "web", "host": "",
                                            "why": "your message is 90000 bytes as delivered, over the limit"}),
                               ("recalled", {"ev": "recall", "id": "r1", "t": T_ASK + 90})):
            self._esc.clear()
            self._log([self.ASK, self.REPLY, terminal])
            self.assertEqual([a[0] for a in km._debt_asks(DEBTOR, {ASKER})], [ASKER],
                             "%s: base — no debt, on a reply the asker never received" % name)
            self._armed(fire_t=NOW - 600)
            km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
            self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)],
                             "%s: base — retired as answered; the debtor moved on, so the wait is the user's" % name)
            self.assertEqual(self._d["debtNudged"], {})

    def test_a_recall_naming_a_relay_mid_leaves_the_debt_owed(self):
        # the OUTBOX arm (review find, 2026-09-08): the relay mid ("px-…") marks a recall whose item may
        # already have been carried and delivered — not terminal, so the debtor still owes and a reminder
        # it moved on from escalates, exactly as before (green on the base by design)
        ask = dict(self.ASK, id="px-1.mail.TESTHOST-A", to_id="peer:TESTHOST-B",
                   toName="TESTHOST-B:tests", to_sid=DEBTOR)
        self._log([ask, {"ev": "recall", "id": "px-1.mail.TESTHOST-A", "t": T_ASK + 30}])
        self.assertEqual([a[0] for a in km._debt_asks(DEBTOR, {ASKER})], [ASKER], "still owed")
        self._armed()
        km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
        self.assertEqual(self._esc, [(ASKER, DEBTOR, T_ASK)], "the debtor moved on: the wait is the user's")
        self.assertEqual(self._d["debtNudged"], {})

    def test_the_reply_alone_settles_the_debt(self):
        self._log([self.ASK, self.REPLY])
        self.assertEqual(km._debt_asks(DEBTOR, {ASKER}), [])
        self._armed()
        km._debt_reminder_outcomes(DEBTOR, {"t": NOW - 300, "end": NOW - 200}, NOW)
        self.assertEqual((self._esc, self._d["debtNudged"]), ([], {}), "the reminder worked; nothing escalates")


class DebtEscalate(DebtBase):
    """The block itself, on a real (sandboxed) store: newest eligible waiting top, procedural why naming
    the debtor, through record_verdict + journal like the nudge-failed precedent."""

    def setUp(self):
        super().setUp()
        import tempfile
        from pathlib import Path
        self._td = tempfile.TemporaryDirectory()
        jd = km.jd
        # _rebind_state, NEVER bare GOALDIR/STATE reassignment: the override journal lives at the store
        # tree's PARENT, so a GOALDIR pointed at a bare tmpdir root shares $TMPDIR/overrides with every
        # other run on the machine — one test's block row then replays into all of them (found live,
        # 2026-07-27, as a phantom pre-blocked node in this very class).
        self._saved_state = jd.STATE
        jd._rebind_state(Path(self._td.name))
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        top_old = {"id": ASKER + ":g1", "text": "Ship the notes API", "parentId": None, "t": T_ASK - 900,
                   "mt": T_ASK - 900, "nodeComplete": False, "blocked": False, "cleared": False, "log": []}
        top_new = {"id": ASKER + ":g2", "text": "Started after the ask", "parentId": None, "t": T_ASK + 60,
                   "mt": T_ASK + 60, "nodeComplete": False, "blocked": False, "cleared": False, "log": []}
        store = {"rompUuid": ASKER, "seq": 2, "placements": {},
                 "status": {top_old["id"]: "working", top_new["id"]: "working"},
                 "nodes": {top_old["id"]: top_old, top_new["id"]: top_new}}
        (jd.GOALDIR / (ASKER + ".json")).write_text(json.dumps(store))

    def tearDown(self):
        km.jd._rebind_state(self._saved_state)
        self._td.cleanup()
        super().tearDown()

    def test_the_newest_waiting_top_blocks_with_a_procedural_why(self):
        self.assertTrue(km._debt_escalate(ASKER, DEBTOR, T_ASK, NOW))
        store = km.jd.load_goals(ASKER)
        old, new = store["nodes"][ASKER + ":g1"], store["nodes"][ASKER + ":g2"]
        self.assertTrue(old["blocked"], "the newest top minted at/before the ask carries the block")
        self.assertFalse(new["blocked"], "a top minted AFTER the ask can't be waiting on it")
        why = old.get("blockWhy") or ""
        self.assertIn("tests", why, "the unresponsive debtor is named")
        self.assertIn("despite a reminder", why)
        self.assertTrue(km.jd.procedural_block_why(why),
                        "the fixed head reads as procedural — no invented decision brief")

    def test_nothing_eligible_is_a_quiet_no_op(self):
        import json as _json
        p = km.jd.GOALDIR / (ASKER + ".json")
        s = km.jd._guard_nodes(_json.loads(p.read_text()))
        # coherent completion is a VERDICT in the log (one truth, 2026-08-13), never a bare flag
        for nd in list(s["nodes"].values()):
            km.jd.record_verdict(s, nd, "closer", "done", NOW - 10, why="test done")
        km.jd.rollup_status(s, session_closed=False)
        p.write_text(_json.dumps(s))
        self.assertFalse(km._debt_escalate(ASKER, DEBTOR, T_ASK, NOW))


if __name__ == "__main__":
    unittest.main()
