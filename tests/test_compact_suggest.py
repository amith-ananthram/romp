#!/usr/bin/env python3
"""The kernel-level compaction SUGGESTION (the user 2026-08-30, via the nightly optimizer). Every
session is told — once idle at least an hour, first past 400k context tokens, again past 800k —
that a /compact at a natural boundary would keep it snappy, its call. Event-keyed end to end: the
CROSSING arms it; a per-threshold latch on the auto-nudge blob makes each fire once per episode; the
idle gate reads the settle event's age AT FIRE TIME; and the latch re-arms only on the session's own
context RESET (compact//clear/rewind), observed as the authoritative token counter falling back
below the latched threshold — ignored = silent forever, acted+regrown+idle = one fresh suggestion
per threshold (the manager's amendment). Muted sessions, comment-thread forks, and mid-turn
sessions are excluded at fire time. Session tags are NOT a gate (T248, the user 2026-09-07: a
"workers" roster is a convention they run on top of romp, not something romp knows about; the
first cut skipped every member of a *_workers tag). The voice render is pinned in
test_injected_voice.py. SYNTHETIC fixtures only."""
import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
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
km = load_source("romp_kernel_compsug", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-eeeeeeeeeeee"
NOW = 1_788_200_000
IDLE = NOW - 7200          # settled two hours ago — past the hour gate
FRESH = NOW - 120          # settled two minutes ago — inside it


class _Fixture(unittest.TestCase):
    """The shared hermetic world: opted-in install, idle-settled session, captured sends."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        km._autonudge_cache.clear()
        km._set_compact_suggest(True)          # T208: default-OFF per install — these tests opt in
        km._autonudge_cache.clear()
        self._orig = {n: getattr(km, n) for n in ("_settle_event_key", "_thread_reg")}
        km._settle_event_key = lambda sid: IDLE
        km._thread_reg = lambda sid: {}
        self._orig_backend = km.Sessions.backend_for
        self.sent = []
        test = self

        class FakeBackend:
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(lambda sid: FakeBackend())

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._orig_backend
        jd.STATE = self.saved_state
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _tick(self, tokens, state="idle"):
        return km._compact_suggest_tick(SID, {"state": state, "ctxTokens": tokens}, NOW)

    def _latched(self):
        km._autonudge_cache.clear()
        return (km._auto_nudge_data().get("compactSuggested") or {}).get(SID, [])


class CompactSuggest(_Fixture):
    # ── the crossing latch ──
    def test_a_crossing_fires_once_and_latches(self):
        self.assertTrue(self._tick(450_000))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("`romp compact ", self.sent[0])
        # T207 (the user 2026-08-31, who saw the bare send render as their own blue bubble): the
        # send carries the sibling injectors' marker tail so the chat classifies it "romp" (gray
        # bubble), romp-auto = background kernel injection; the PROSE stays the bare constant the
        # injected-voice index scans.
        self.assertIn("<!-- romp-injected -->", self.sent[0])
        self.assertIn("<!-- romp-note:", self.sent[0])
        self.assertNotIn("<!-- romp-auto -->", self.sent[0],
                         "romp-auto means AUTO-NUDGES: its chat gist says 'nudged for a status "
                         "update', which this is not — injected alone renders the prose's first line")
        self.assertNotIn("<!-- romp-system -->", self.sent[0],
                         "person-voiced, not the machine-voiced notice family")
        self.assertNotIn("romp-goal-id", self.sent[0], "the suggestion tracks nothing")
        _prose = self.sent[0].split("<!--")[0].strip()
        self.assertEqual(_prose, km._compact_suggest_body(km._name_of(SID) or SID[:8]).strip())
        self.assertIn("`romp compact ", _prose,
                      "the named command WORKS from the recipient's own shell (T212: /compact is a "
                      "terminal affordance no SDK session can type, and only SDK sessions get this)")
        self.assertNotIn("/compact", _prose.replace("romp compact", ""),
                         "…and the untypeable slash form is gone")
        self.assertEqual(self._latched(), [400_000], "latched on the durable record")
        self.assertFalse(self._tick(460_000), "same episode → never a second fire")
        self.assertEqual(len(self.sent), 1)

    def test_the_second_threshold_is_its_own_episode(self):
        self.assertTrue(self._tick(450_000))
        self.assertTrue(self._tick(820_000), "the 800k crossing is new information")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(sorted(self._latched()), [400_000, 800_000])
        self.assertFalse(self._tick(950_000), "…and both latches now stand: ignored = silent")

    def test_found_past_both_thresholds_fires_one_message(self):
        self.assertTrue(self._tick(900_000))
        self.assertEqual(len(self.sent), 1, "two suggestions in one tick would read as nagging")
        self.assertEqual(sorted(self._latched()), [400_000, 800_000])

    # ── the re-arm: the session's own context reset, never a nag ──
    def test_a_compact_rearms_exactly_the_thresholds_it_fell_below(self):
        self.assertTrue(self._tick(900_000))
        self.assertFalse(self._tick(500_000), "compacted to 500k: below 800k, still above 400k — "
                                              "nothing due (400k stands latched)")
        self.assertEqual(self._latched(), [400_000], "the 800k latch was pruned by the reset")
        self.assertTrue(self._tick(820_000), "regrown past 800k after the reset → one fresh fire")
        self.assertEqual(len(self.sent), 2)

    def test_a_full_reset_rearms_both(self):
        self.assertTrue(self._tick(900_000))
        self.assertFalse(self._tick(50_000), "fresh context — nothing due, latches prune")
        self.assertEqual(self._latched(), [])
        self.assertTrue(self._tick(450_000), "the next 400k crossing is a genuinely new episode")
        self.assertEqual(len(self.sent), 2)

    # ── the idle gate, at fire time ──
    def test_a_fresh_settle_holds_the_fire_without_spending_the_crossing(self):
        km._settle_event_key = lambda sid: FRESH
        self.assertFalse(self._tick(450_000), "settled minutes ago — not idle enough")
        self.assertEqual(self.sent, [])
        self.assertEqual(self._latched(), [], "the crossing stays ARMED — never latch a fire "
                                              "that never went out")
        km._settle_event_key = lambda sid: IDLE
        self.assertTrue(self._tick(450_000), "an hour idle later, the same crossing fires")

    def test_no_settle_evidence_no_fire(self):
        km._settle_event_key = lambda sid: 0
        self.assertFalse(self._tick(450_000))
        self.assertEqual(self.sent, [])

    # ── the exclusions ──
    def test_a_session_inside_a_workers_tag_is_suggested_like_any_other(self):
        # T248 (the user 2026-09-07): a session on an attached machine inside a "workers" roster tag is
        # an ordinary session to romp — the tag is the user's own convention. Red before: the first
        # cut's _worker_tag_member gate returned False for every member of a *_workers tag.
        (jd.STATE / "timeline-views.json").write_text(json.dumps(
            {"active": "all", "tags": [{"id": "t1", "name": "notes_workers",
                                        "members": [{"host": "", "sid": SID}]}]}))
        self.assertTrue(self._tick(450_000), "a tag is not a gate")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(hasattr(km, "_worker_tag_member"), "the tag-roster gate is gone, not bypassed")

    def test_a_muted_session_is_never_suggested(self):
        # the user's explicit per-session opt-out (hideFromFeed) — every sibling injector honors
        # it; a routed review pass caught this tick missing the check (2026-09-01)
        (jd.STATE / "session-flags.json").write_text(json.dumps({SID: {"hideFromFeed": True}}))
        km._flags_cache.clear()
        self.assertFalse(self._tick(450_000))
        self.assertEqual(self.sent, [])
        self.assertEqual(self._latched(), [], "…and the crossing stays armed, never spent")

    def test_a_comment_thread_fork_is_never_suggested(self):
        km._thread_reg = lambda sid: {"threadOf": "11111111-2222-3333-4444-ffffffffffff"}
        self.assertFalse(self._tick(450_000))
        self.assertEqual(self.sent, [])

    def test_a_mid_turn_session_is_never_suggested(self):
        self.assertFalse(self._tick(450_000, state="working"))
        self.assertEqual(self.sent, [])
        self.assertEqual(self._latched(), [], "…and the crossing stays armed for its settle")

    def test_no_token_count_no_fire(self):
        self.assertFalse(self._tick(None))
        self.assertFalse(self._tick(0))
        self.assertEqual(self.sent, [])

    # ── the T208 default: OFF for a fresh install, ON only by this install's own config ──
    def test_a_fresh_install_is_off_and_never_fires(self):
        (jd.STATE / "auto-nudge.json").unlink()        # a virgin install: no blob at all
        km._autonudge_cache.clear()
        self.assertFalse(km._compact_suggest_on(), "absent key reads OFF — shipping the feature "
                                                   "turns it on nowhere (the user's ruling)")
        self.assertFalse(self._tick(900_000), "idle, far past both thresholds — still nothing")
        self.assertEqual(self.sent, [])
        km._autonudge_cache.clear()
        self.assertEqual(self._latched(), [], "…and no latch spent while off")

    def test_the_designed_toggle_flips_it_on_and_off(self):
        km._set_compact_suggest(False)
        km._autonudge_cache.clear()
        self.assertFalse(self._tick(450_000))
        km._set_compact_suggest(True)
        km._autonudge_cache.clear()
        self.assertTrue(self._tick(450_000), "the same crossing fires once opted in")

    # ── the per-install config keys: "how to do it exactly" is config too ──
    def test_custom_thresholds_replace_the_defaults(self):
        d = dict(km._auto_nudge_data())
        d["compactSuggestTokens"] = [100_000, 200_000]
        km._write_auto_nudge(d)
        km._autonudge_cache.clear()
        self.assertTrue(self._tick(150_000), "past the CUSTOM first threshold, under the default")
        self.assertEqual(self._latched(), [100_000])
        self.assertTrue(self._tick(250_000), "…and the custom second is its own episode")

    def test_a_junk_thresholds_shape_falls_to_the_defaults(self):
        d = dict(km._auto_nudge_data())
        d["compactSuggestTokens"] = ["soon", -3]
        km._write_auto_nudge(d)
        km._autonudge_cache.clear()
        self.assertFalse(self._tick(150_000), "junk config never lowers the bar")
        self.assertTrue(self._tick(450_000), "the shipped defaults stand")

    def test_a_custom_idle_window_replaces_the_default(self):
        d = dict(km._auto_nudge_data())
        d["compactSuggestIdleS"] = 30
        km._write_auto_nudge(d)
        km._autonudge_cache.clear()
        km._settle_event_key = lambda sid: FRESH       # two minutes idle — under the default hour
        self.assertTrue(self._tick(450_000), "…but past the custom 30s window")

    # ── the fingerprint row ──
    def test_a_fire_logs_a_countable_row(self):
        self._tick(450_000)
        p = jd.STATE / "nudge-events.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        self.assertEqual([r["verdict"] for r in rows], ["compact-suggested"])
        self.assertEqual(rows[0]["evT"], 450_000, "the row carries the token count it fired on")


class ConcurrentSendRace(_Fixture):
    """The double-send race (2026-09-01): the tick checked the compactSuggested
    latch under _NUDGE_LOCK, released it, sent, and latched only after — so two concurrent entries
    (the pusher's periodic pass racing a settings-WS re-tick) both passed the same unlatched check
    and injected the suggestion twice into one session. The fire is now CLAIMED under the lock
    before the send (a fresh re-read re-derives what is still due, so the loser stands down), and
    a failed send rolls the claim back for the next tick's retry. Deterministic, events only — the
    first entry is held between its check and its send until the second has fully landed (the
    TwoFlushRace idiom, test_setting_gesture_order.py)."""

    def test_two_concurrent_entries_inject_once(self):
        first_at_gate = threading.Event()
        second_done = threading.Event()

        def gated(sid):
            # _settle_event_key sits between the latch check and the send on every entry path;
            # stalling the first entry here holds it in exactly the window the race needs
            if threading.current_thread().name == "first":
                first_at_gate.set()
                second_done.wait(10)
            return IDLE

        km._settle_event_key = gated
        got = {}
        t = threading.Thread(target=lambda: got.__setitem__("first", self._tick(450_000)),
                             name="first")
        t.start()
        try:
            self.assertTrue(first_at_gate.wait(10), "the first entry passed its latch check")
            got["second"] = self._tick(450_000)     # the concurrent entry runs to completion
        finally:
            second_done.set()
            t.join(10)
        self.assertFalse(t.is_alive(), "both entries finished")
        self.assertEqual(len(self.sent), 1,
                         "two entries past the same unlatched check must still inject exactly once")
        self.assertEqual(sorted((got["first"], got["second"])), [False, True],
                         "exactly one entry claims the fire; the other stands down")
        self.assertEqual(self._latched(), [400_000], "one claim, latched once")

    def test_a_failed_send_unlatches_so_the_next_tick_retries(self):
        test = self
        attempts = []

        class BlinkingBackend:
            def send(self, sid, body):
                attempts.append(body)
                if len(attempts) == 1:
                    raise RuntimeError("stream blink")
                test.sent.append(body)

        be = BlinkingBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self._tick(450_000), "a failed send reports no fire")
        self.assertEqual(self._latched(), [],
                         "the claim rolls back — no durable latch for a send that never went out")
        self.assertTrue(self._tick(450_000), "the next tick retries the same crossing")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(attempts), 2)


class DebtWriterRace(_Fixture):
    """The claim's SIDE DOOR (2026-09-01): the ledger's debt writers ran an
    unlocked read→write span — a snapshot of the whole blob held across a LIVE send, written back
    whole — so a debt reminder straddling a concurrent claim erased the compactSuggested latch
    (last-writer-wins over every key), and the next tick re-derived the crossing as due and sent
    the suggestion a SECOND time; the mirror straddle of a failed send's rollback RESURRECTED a
    latch whose fire never went out, so the retry stood down forever. The writers now hold
    _NUDGE_LOCK for their record write and RE-READ the blob fresh under it, mutating only the
    debt keys they own — the claim survives the straddle, the rollback survives it too, and the
    debt writer's own record still lands (neither writer's output is lost). Deterministic, events
    only — the debt writer is held inside its send across the other entry's whole claim (the
    TwoFlushRace idiom). SYNTHETIC sids throughout."""

    YSID = "11111111-2222-3333-4444-ffffffffffff"      # the debtor owing a reply
    ASKER = "11111111-2222-3333-4444-aaaaaaaaaaaa"     # the peer still waiting on it
    DEBT_KEY = "%s>%s:1000" % (ASKER, YSID)

    def setUp(self):
        super().setUp()
        self._orig_debt_asks = km._debt_asks
        # one synthetic unanswered ask the debtor owes — the writers' blob read-modify-write is
        # the thing under test, not the postal wait maps
        km._debt_asks = lambda sid, alive: [
            (self.ASKER, "peer", 1000, "question", "where do things stand?")]

    def tearDown(self):
        km._debt_asks = self._orig_debt_asks
        super().tearDown()

    def _debt_recorded(self):
        km._autonudge_cache.clear()
        return self.DEBT_KEY in (km._auto_nudge_data().get("debtNudged") or {})

    def test_a_debt_writer_straddling_a_claim_never_erases_it(self):
        b_in_send = threading.Event()
        claim_done = threading.Event()
        sent_y, got = [], {}
        test = self

        class XBackend:                                # the suggestion's recipient
            def send(self, sid, body):
                test.sent.append(body)

        class YBackend:                                # the debtor: the reminder send holds the
            def send(self, sid, body):                 # writer in exactly the straddle window —
                sent_y.append(body)                    # blob read behind it, record write ahead
                b_in_send.set()
                claim_done.wait(10)
        km.Sessions.backend_for = staticmethod(
            lambda sid: XBackend() if sid == SID else YBackend())

        tb = threading.Thread(target=lambda: got.__setitem__(
            "debt", km._fire_debt_reminder(self.YSID, NOW, {self.ASKER})), name="debt")
        tb.start()
        try:
            self.assertTrue(b_in_send.wait(10), "the debt writer reached its send")
            got["tick"] = self._tick(450_000)          # claim + send, inside the straddle
            self.assertTrue(got["tick"])
            self.assertEqual(self._latched(), [400_000], "the claim landed")
        finally:
            claim_done.set()
            tb.join(10)
        self.assertFalse(tb.is_alive(), "the debt writer finished")
        self.assertTrue(got["debt"], "the reminder went out")
        self.assertEqual(self._latched(), [400_000],
                         "the debt record write must not erase the concurrent claim")
        self.assertFalse(self._tick(450_000),
                         "the crossing stays claimed — no re-derived second fire")
        self.assertEqual(len(self.sent), 1, "one suggestion, never two")
        self.assertEqual(len(sent_y), 1)
        self.assertTrue(self._debt_recorded(),
                        "…and the debt writer's own key lands too — both writers' outputs "
                        "survive the interleaving")

    def test_a_debt_writer_straddling_a_rollback_never_resurrects_the_latch(self):
        claim_landed = threading.Event()
        b_in_send = threading.Event()
        rollback_done = threading.Event()
        sent_y, got = [], {}
        test = self

        class XGatedFail:                              # the claimed fire never goes out — and the
            def send(self, sid, body):                 # failure is held until the debt writer has
                claim_landed.set()                     # read the blob WITH the claim in it
                test.assertTrue(b_in_send.wait(10), "the debt writer read while the claim stood")
                raise RuntimeError("stream blink")

        class YBackend:
            def send(self, sid, body):
                sent_y.append(body)
                b_in_send.set()
                rollback_done.wait(10)                 # record write held past the rollback
        km.Sessions.backend_for = staticmethod(
            lambda sid: XGatedFail() if sid == SID else YBackend())

        def run_claimer():
            with contextlib.redirect_stderr(io.StringIO()):
                got["tick"] = self._tick(450_000)

        ta = threading.Thread(target=run_claimer, name="claimer")
        ta.start()
        self.assertTrue(claim_landed.wait(10), "the claim landed before the failing send")
        tb = threading.Thread(target=lambda: got.__setitem__(
            "debt", km._fire_debt_reminder(self.YSID, NOW, {self.ASKER})), name="debt")
        tb.start()
        try:
            ta.join(10)                                # the send fails; the rollback lands
            self.assertFalse(ta.is_alive(), "the claimer finished")
            self.assertEqual(self._latched(), [], "the rollback landed")
        finally:
            rollback_done.set()
            tb.join(10)
        self.assertFalse(tb.is_alive(), "the debt writer finished")
        self.assertFalse(got["tick"], "a failed send reports no fire")
        self.assertTrue(got["debt"], "the reminder went out")
        self.assertEqual(self._latched(), [],
                         "the debt record write must not resurrect a latch whose fire never "
                         "went out")

        class XBackend:                                # a working backend for the retry
            def send(self, sid, body):
                test.sent.append(body)
        km.Sessions.backend_for = staticmethod(
            lambda sid: XBackend() if sid == SID else YBackend())
        self.assertTrue(self._tick(450_000), "the next tick retries the same crossing")
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self._debt_recorded(), "…and the debt key still lands")


if __name__ == "__main__":
    unittest.main()
