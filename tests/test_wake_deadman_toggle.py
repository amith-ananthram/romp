#!/usr/bin/env python3
"""The awaiting dead-man runs whether or not auto-nudge is on, and reaches a stamped top the
all-delegated gate would otherwise skip (2026-09-05).

The 6h wake in _wake_goal is the designed backstop for waits whose ending romp cannot observe
(kind=job: external compute). It was unreachable twice over for the live specimen — a kind=job
stamp that stood 17 hours over nothing pending: (a) _auto_nudge_tick returned before the goal walk
because the user had auto-nudge OFF, and (b) even with it on, the walk skipped the top via
_all_outstanding_delegated (its children complete, the top carrying a courier `handoff`), a gate
that exists to suppress the plain status nudge for peer work — a job/agents/task/timer wait is the
session's OWN wait, not delegated work. The toggle governs the nudge (an injected status check the
user opted out of); the dead-man is the reachability floor every Working card is promised, so it
runs from the pusher's tick regardless, the way _interrupt_block_tick does — but with nudges OFF it
injects NOTHING (romp interrupts only when the human is the bottleneck, and the user said no
unprompted messages): it files the stamp's lift, the exact row the orphan lift files, so the card
returns to plain Working and the closer is re-nominated. With nudges ON the injected check-in is
unchanged. Either way it acts once per stamp episode (the wake record / the lifted stamp), never
again per tick. SYNTHETIC fixtures only; a PRIVATE sid (goal-store fixture rule)."""
import contextlib
import errno
import io
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
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wdt", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "55555555-6666-7777-8888-999999999999"     # private to this module (the fixture rule)
NOW = 1_787_900_000
H = 3600


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append((sid, body))

    def pending_queued(self, sid):
        return []


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        self.saved = {k: getattr(km, k) for k in (
            "_alive_sessions", "_wait_for_graph", "_session_flag", "_compacting_now", "_api_error",
            "_session_working", "_interrupt_suppresses_nudge", "_backend_rewind_pending", "_last_state",
            "_session_awaiting", "_turn_romp_injected", "_closer_settled", "_revivers_pending",
            "_pending_ops", "_log_nudge_event", "_push_all", "_mark_views_dirty", "_path_of",
            "_debt_backstop_tick", "_PREV_ALIVE")}
        self.saved_jd = {k: getattr(jd, k) for k in ("STATE", "GOALDIR", "parsed_session", "_segs", "plan_units")}
        self.saved_backend = km.Sessions.backend_for
        jd.STATE = td
        jd.GOALDIR = td / "goals"; jd.GOALDIR.mkdir(parents=True)
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        self.fb = _FakeBackend()
        km.Sessions.backend_for = lambda sid: self.fb
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": "/nonexistent.jsonl"}]
        km._wait_for_graph = lambda now, sids: {}
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: None
        km._turn_romp_injected = lambda tn: False
        km._closer_settled = lambda *a: True
        km._revivers_pending = lambda *a, **k: ""
        km._pending_ops = {}
        km._log_nudge_event = lambda *a, **k: None
        km._push_all = lambda *a, **k: None
        km._mark_views_dirty = lambda *a, **k: None
        km._path_of = lambda sid, now=None: "/nonexistent.jsonl"
        km._debt_backstop_tick = lambda now: None
        km._PREV_ALIVE = {SID}                       # no death transition pending
        jd._segs = lambda tn, store: []
        jd.plan_units = lambda session, store: []
        self.turns = [{"id": "t1", "ended": True, "end": NOW - 8 * H, "t": NOW - 8 * H - 10, "atoms": []}]
        jd.parsed_session = lambda sid, paths, now: {"turns": self.turns}
        self.gid = SID + ":g1"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        for k, v in self.saved_jd.items():
            setattr(jd, k, v)
        km.Sessions.backend_for = self.saved_backend
        km._SESSION_STAMP_CACHE.clear(); km._autonudge_cache.clear()
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        self.td.cleanup()

    def _toggle(self, enabled):
        (jd.STATE / "auto-nudge.json").write_text(json.dumps({"enabled": enabled, "nudged": {}}))
        km._autonudge_cache.clear()

    def _seed(self, kind="job", age=7 * H, delegated=True, stamped=True):
        """A working top whose only open leaf is a courier handoff (all-delegated), carrying a stamp."""
        at = NOW - age
        why = "the index rebuild is still running; picking the result up when it lands"
        top = {"id": self.gid, "text": "rebuild the notes-api index", "parentId": None,
               "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": 100, "mt": 100,
               "log": []}
        if stamped:
            top.update({"awaitingWhy": why, "awaitingAt": at, **({"awaitingKind": kind} if kind else {})})
            top["log"].append({"ev_t": at, "src": "closer", "kind": "awaiting", "why": why,
                               **({"awaitKind": kind} if kind else {}), "at": at})
        nodes = {self.gid: top}
        if delegated:
            kid = self.gid + "c"
            nodes[kid] = {"id": kid, "text": "web session: wire the watcher", "parentId": self.gid,
                          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
                          "t": 100, "mt": 100, "log": [],
                          "handoff": {"to": "web", "msgId": "11111111-2222-3333-4444-000000000001"}}
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({
            "rompUuid": SID, "seq": 1, "placements": {}, "status": {self.gid: "working"}, "nodes": nodes}))
        km._SESSION_STAMP_CACHE.clear()

    def _tick(self, now=NOW):
        km._auto_nudge_tick(now, {SID: {"state": ""}})

    def _wakes(self):
        return [b for _s, b in self.fb.sent if km.AWAITING_BACKSTOP_TEXT in b]

    def _node(self):
        return jd.load_goals(SID)["nodes"][self.gid]

    def _lifts(self):
        return [e for e in self._node().get("log") or [] if e.get("kind") == "awaiting" and e.get("lift")]


class DeadmanIgnoresTheToggle(_Base):
    def test_a_7h_job_stamp_on_an_all_delegated_top_files_a_lift_and_injects_nothing(self):
        self._toggle(False)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "nudges off: no unprompted message, whatever the clock says")
        self.assertIsNone(self._node().get("awaitingWhy"), "the wait romp cannot vouch for is withdrawn")
        row = self._lifts()[-1]
        self.assertEqual((row.get("src"), row.get("ev_t")), ("romp", NOW - 7 * H),
                         "the orphan lift's exact row: romp/awaiting/lift at the stamp's anchor")
        self.assertEqual(jd.load_goals(SID)["status"].get(self.gid, "working"), "working",
                         "no block — every procedural block copy would misstate what happened")
        self.assertNotIn(self.gid, km._auto_nudge_data()["nudged"], "no injection → no wake record")

    def test_the_lift_journals_the_wake_moment_as_its_horizon(self):
        # the dead-man's ruling is "no ending event arrived through NOW", so the clock is the horizon
        # it journals (record_verdict end_ev). A closer auditing a segment triggered anywhere inside
        # (anchor, wake) then stands down — with no horizon the fallback read the row's ev_t, the
        # stamp's anchor, and that re-assert stood across the whole 6h window (pre-fold: it files).
        # Journaled as read, fraction and all (review find, 2026-09-08): floored to the second, the
        # horizon disowned an assert triggered in the wake's own second, which stood and was re-lifted
        self._toggle(False)
        self._seed(kind="job", age=7 * H)
        self._tick(now=NOW + 0.5)
        self.assertEqual(self._lifts()[-1].get("endEv"), NOW + 0.5,
                         "the wake moment, as read, is what the lift ruled through")
        s = jd.load_goals(SID)
        jd.apply_close(s, jd.open_menu(s), {"done": {}, "block": {},
                                            "awaiting": {1: {"why": "still watching the rebuild", "kind": "job"}}},
                       t=NOW - 3 * H)
        self.assertIsNone(s["nodes"][self.gid].get("awaitingWhy"),
                          "a segment inside (anchor, wake) predates the dead-man's ruling — the writer yields")

    def test_a_second_cycle_does_not_file_again(self):
        self._toggle(False)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self._tick(NOW + 5)
        self._tick(NOW + 60)
        self.assertEqual(len(self._lifts()), 1, "once per stamp — the lifted stamp no longer reads as one")
        self.assertEqual(self.fb.sent, [])

    def test_a_5h_stamp_is_still_patient(self):
        self._toggle(False)
        self._seed(kind="job", age=5 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "the 6h constant stands")
        self.assertIsNotNone(self._node().get("awaitingWhy"), "…and nothing is filed either")

    def test_the_toggle_still_governs_the_plain_nudge(self):
        # an unstamped working top with nudges off: nothing fires — the opt-out is the nudge's
        self._toggle(False)
        self._seed(stamped=False, delegated=False)
        self._tick()
        self.assertEqual(self.fb.sent, [], "no injected status check while auto-nudge is off")

    def test_the_kinds_take_the_same_path(self):
        for kind in ("agents", "task", "timer"):
            self._toggle(False)
            self._seed(kind=kind, age=7 * H)
            self._tick()
            self.assertEqual(len(self._lifts()), 1, "kind=%s is the session's own wait" % kind)
            self.assertEqual(self.fb.sent, [])


class NudgesOnKeepTheCheckIn(_Base):
    def test_with_nudges_on_the_all_delegated_top_still_takes_its_wake(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self.assertEqual(len(self._wakes()), 1, "nudges on: today's injected check-in, unchanged")
        self.assertIsNotNone(self._node().get("awaitingWhy"), "the check-in is the action — the stamp stands")
        self.assertEqual(self._lifts(), [])
        rec = km._auto_nudge_data()["nudged"][self.gid]
        self.assertTrue(rec.get("wake"), "…recorded as a wake episode in the shared ledger")
        self.assertEqual(rec.get("anchor"), NOW - 7 * H)
        self.assertNotIn(self.gid, km._auto_nudge_data().get("walkGates", {}),
                         "the walk reached the goal — no all-delegated hold is journaled for it")

    def test_a_second_cycle_is_silent_with_nudges_on(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)
        self._tick()
        self._tick(NOW + 5)
        self._tick(NOW + 60)
        self.assertEqual(len(self._wakes()), 1, "once per stamp episode — keyed on the wake record")


class OwnWaitOutranksTheDelegatedGate(_Base):

    def test_a_peer_stamp_keeps_the_gate(self):
        # a peer wait IS the delegated shape the gate exists for — it stays behind it
        self._toggle(True)
        self._seed(kind="peer", age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [])
        self.assertEqual(km._auto_nudge_data()["walkGates"][self.gid]["gate"], "all-delegated")

    def test_a_kindless_stamp_keeps_the_gate(self):
        self._toggle(True)
        self._seed(kind=None, age=7 * H)
        self._tick()
        self.assertEqual(self.fb.sent, [], "a kindless stamp may be a peer wait — conservative, as before")


class UnreadableLedgerPausesThePass(_Base):
    """…but never from a ledger the reader cannot vouch for (2026-09-07). A read fault on auto-nudge.json
    used to fabricate the default — enabled:True, nudged:{} — so the walk fired every due wake and nudge
    AGAIN each tick (the dedupe map read as empty; the record write then persisted the fabrication) and
    read an explicit OFF as ON. The pass now stands down whole on an UNPROVED snapshot, says so once per
    fault episode (stderr + the dashboard's bell), and resumes on the first pass whose read proves — the
    file reading again is the event; there is no timer. Pinned here because this is where the firing
    fixture lives; the ledger's own read/write contract is tests/test_ledger_unproved_reads.py."""

    def setUp(self):
        super().setUp()
        # the pause latch + the once-per-episode registries (absent on a kernel before the fix, so these
        # tests fail there on the DEFECT — a fired wake — not in setUp)
        vars(km).get("_auto_nudge_paused", [None])[0] = None
        for reg in ("_ledger_fault_warned", "_ledger_refusal_warned", "_ledger_write_failed"):
            vars(km).get(reg, {}).clear()
        self.problems = len(km._SDK_BOOT_PROBLEMS)
        self.ledger = jd.STATE / "auto-nudge.json"

    def _fault(self):
        real, target = Path.read_text, str(self.ledger)

        def failing(p, *a, **k):
            if str(p) == target:
                raise OSError(errno.EIO, "Input/output error")
            return real(p, *a, **k)
        Path.read_text = failing
        self.addCleanup(setattr, Path, "read_text", real)
        return lambda: setattr(Path, "read_text", real)

    def test_no_wake_fires_from_an_unproved_snapshot_and_the_first_proved_read_resumes(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)                # the shape NudgesOnKeepTheCheckIn fires one wake for
        before = self.ledger.read_bytes()
        heal = self._fault()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._tick(); self._tick(NOW + 5); self._tick(NOW + 10)
        self.assertEqual(self.fb.sent, [], "nothing injected from a snapshot the reader cannot vouch for")
        self.assertEqual(self.ledger.read_bytes(), before, "and nothing written over the file")
        self.assertEqual(err.getvalue().count("paused, not defaulted on"), 1, "loud once per fault episode, not per tick")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), self.problems + 1, "…and once in the dashboard's bell")
        self.assertIn("Input/output error", km._SDK_BOOT_PROBLEMS[-1]["text"])
        heal()
        with contextlib.redirect_stderr(err):
            self._tick(NOW + 60)
        self.assertEqual(len(self._wakes()), 1, "the first pass whose read proves fires — the event, no timer")
        self.assertIn("nudging resumed", err.getvalue())
        self.assertTrue(km._auto_nudge_data()["nudged"][self.gid].get("wake"), "recorded, so the next tick dedupes")

    def test_an_explicit_off_is_never_read_as_on(self):
        self._toggle(False)
        self._seed(stamped=False, delegated=False)       # an unstamped working top: the plain nudge's shape
        self._fault()
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(self.fb.sent, [], "OFF on disk, unreadable now: no fabricated ON fires a status check")

    def test_a_drop_refused_during_a_fault_replays_on_the_first_proved_pass(self):
        # the awaiting lift and the follow-up reopen drop a SPENT record from OUTSIDE the paused pass, off an
        # event that fires once; their goal-store write lands, the ledger drop is refused under the fault,
        # and nothing retried it: the record stayed latched and an idle session's card sat in Working with
        # no reviver (review find, 2026-09-08). Now a refused drop is parked and the first proved pass
        # replays it; a LIVE record parked alongside is kept, as the drop always keeps live records.
        self._toggle(False)                              # wake-only, no store: the walk writes nothing itself
        spent, live = self.gid, SID + ":g2"
        d = json.loads(self.ledger.read_bytes())
        d["nudged"] = {spent: {"count": 3, "lastTurnId": "t1", "failed": True, "failedAt": NOW - H},
                       live: {"count": 1, "lastTurnId": "t1"}}
        self.ledger.write_text(json.dumps(d))
        km._autonudge_cache.clear()
        before = self.ledger.read_bytes()
        vars(km).get("_auto_nudge_drops_pending", set()).clear()          # absent before the fix (see setUp)
        self.addCleanup(lambda: vars(km).get("_auto_nudge_drops_pending", set()).clear())
        heal = self._fault()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            verdicts = [km._drop_auto_nudge_rec(spent), km._drop_auto_nudge_rec(live)]
        self.assertEqual(self.ledger.read_bytes(), before, "under the fault the drops are refused: the file keeps its bytes")
        heal()
        with contextlib.redirect_stderr(err):
            self._tick()
        nudged = json.loads(self.ledger.read_text())["nudged"]
        self.assertNotIn(spent, nudged, "the first proved pass replays the drop: the spent record is gone")
        self.assertIn(live, nudged, "…and the live record (the ladder's memory) is kept, as ever")
        self.assertEqual(verdicts, [False, False], "the callers were told the drop did not land")
        self.assertEqual(self.fb.sent, [], "nothing injected: the replay is bookkeeping")

    # ── a fault that lands MID-pass: the head read proved, a leg's read did not ──────────────────
    # The pass gate covers a fault at the head. Two legs record what they fired only AFTER deciding to
    # send (the debt reminder) or send only after a durable claim (the compaction suggestion); each
    # checks the tag itself, because a fault between the head and its own read hands it a tagged copy —
    # and a send whose record or claim the writer refuses is a send repeated every tick.

    def _fault_after_the_first_read(self):
        """The pass's head read proves; from the reader's second call on, the file has moved on (a peer
        wrote it — a new stat key) and the new bytes cannot be read, so every leg sees a tagged copy of
        the last proved snapshot. Deterministic: the wrapper counts the reader's calls."""
        real, calls, ledger, test = km._auto_nudge_data, [0], self.ledger, self

        def flaky():
            calls[0] += 1
            if calls[0] == 2:
                ledger.write_text(json.dumps(json.loads(ledger.read_bytes()), indent=1))
                test.moved_on = ledger.read_bytes()
                test._fault()
            return real()
        km._auto_nudge_data = flaky
        self.addCleanup(setattr, km, "_auto_nudge_data", real)

    def _owes_a_reply(self):
        orig = km._debt_asks
        km._debt_asks = lambda sid, alive: [("66666666-7777-8888-9999-000000000000", "web", NOW - 1800,
                                              "question", "Which port should the staging server use?")]
        self.addCleanup(setattr, km, "_debt_asks", orig)

    def test_control_an_owed_reply_draws_the_reminder_when_the_ledger_reads(self):
        self._toggle(True)
        self._owes_a_reply()
        self._tick()
        self.assertEqual(len(self.fb.sent), 1, "the path is reached: one reminder goes out")
        self.assertIn("web", self.fb.sent[0][1])

    def test_no_debt_reminder_when_the_ledger_faults_mid_pass(self):
        self._toggle(True)
        self._owes_a_reply()
        self._fault_after_the_first_read()
        with contextlib.redirect_stderr(io.StringIO()):
            self._tick()
        self.assertEqual(self.fb.sent, [], "an unrecorded reminder re-fires every tick: no send whose record would be refused")
        self.assertEqual(self.ledger.read_bytes(), self.moved_on, "and the file is untouched")
        self.assertIsNone(vars(km).get("_auto_nudge_paused", [None])[0],
                          "the head gate proved: the leg refused on its own, not the pause")

    def _settled_past_a_crossing(self):
        d = json.loads(self.ledger.read_bytes())
        d["compactSuggestEnabled"] = True                # the per-install opt-in (default OFF)
        self.ledger.write_text(json.dumps(d))
        km._autonudge_cache.clear()
        for n, v in (("_settle_event_key", lambda sid: NOW - 7200), ("_thread_reg", lambda sid: {})):
            orig = getattr(km, n)
            setattr(km, n, v)
            self.addCleanup(setattr, km, n, orig)
        return {SID: {"state": "", "ctxTokens": 450_000}}   # idle, an hour settled, past the first threshold

    def test_control_a_settled_crossing_draws_the_suggestion_when_the_ledger_reads(self):
        self._toggle(True)
        tm = self._settled_past_a_crossing()
        km._auto_nudge_tick(NOW, tm)
        self.assertEqual(len(self.fb.sent), 1, "the path is reached: one suggestion goes out")
        self.assertIn("compact", self.fb.sent[0][1])

    def test_no_compaction_suggestion_when_the_ledger_faults_mid_pass(self):
        self._toggle(True)
        tm = self._settled_past_a_crossing()
        self._fault_after_the_first_read()
        with contextlib.redirect_stderr(io.StringIO()):
            km._auto_nudge_tick(NOW, tm)
        self.assertEqual(self.fb.sent, [], "the claim did not latch: a suggestion whose claim is refused is sent again every tick")
        self.assertEqual(self.ledger.read_bytes(), self.moved_on, "and the file is untouched")
        self.assertIsNone(vars(km).get("_auto_nudge_paused", [None])[0],
                          "the head gate proved: the leg refused on its own, not the pause")


class UnwritableLedgerSendsNothing(_Base):
    """…and nothing goes out whose record cannot LAND (review find, 2026-09-08). The pass gates on the READ
    tag, but a WRITE fault (a full or read-only disk) refused the record only AFTER the nudge had gone out:
    the OSError rose into the per-session wrap as a traceback every tick, the unrecorded goal read as never
    nudged, and the same stall was nudged again after every response cycle for as long as the disk refused
    writes, with the redundancy judge's skip falling through to a fire and the escalation ladder never
    climbing. Now the nudge and the wake record BEFORE they send and send only when the record landed; the
    walk-gate journal, re-derived every tick, waits quietly for the next one. The writer says the fault once
    per episode (stderr + the error center); the first pass after the disk heals sends once and records it."""

    def setUp(self):
        super().setUp()
        vars(km).get("_auto_nudge_paused", [None])[0] = None
        for reg in ("_ledger_fault_warned", "_ledger_refusal_warned", "_ledger_write_failed"):
            vars(km).get(reg, {}).clear()
        self.problems = len(km._SDK_BOOT_PROBLEMS)
        self.ledger = jd.STATE / "auto-nudge.json"

    def _write_fault(self, exc):
        """km._atomic_write raises `exc`'s errno for the ledger only, naming a temp path with a per-call
        sequence the way the real publish does: a fault text built from str(e) would then differ on every
        call, and the once-per-episode assertions below would catch it."""
        real, target, calls = km._atomic_write, str(self.ledger), [0]

        def failing(path, text, mode=None):
            if str(path) == target:
                calls[0] += 1
                raise OSError(exc.errno, exc.strerror, "%s.tmp.1.2.%d" % (target, calls[0]))
            return real(path, text, mode)
        km._atomic_write = failing
        self.addCleanup(setattr, km, "_atomic_write", real)
        return lambda: setattr(km, "_atomic_write", real)

    ENOSPC = OSError(errno.ENOSPC, "No space left on device")
    EACCES = OSError(errno.EACCES, "Permission denied")

    def _nothing_until_the_heal(self, exc, what):
        """Three faulted passes send nothing and say the fault once; the first pass after the heal sends
        once. Returns the goal's landed record."""
        before = self.ledger.read_bytes()
        heal = self._write_fault(exc)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._tick(); self._tick(NOW + 5); self._tick(NOW + 10)
        self.assertEqual(self.fb.sent, [], "a %s whose record cannot land is not sent" % what)
        self.assertEqual(self.ledger.read_bytes(), before, "the file keeps what it holds")
        self.assertEqual(err.getvalue().count("write failed"), 1, "said once per fault episode, not per tick")
        self.assertNotIn("Traceback", err.getvalue(), "no per-session traceback per tick: the leg stood down itself")
        self.assertEqual(len(km._SDK_BOOT_PROBLEMS), self.problems + 1, "one error-center row per episode")
        self.assertIn(exc.strerror, km._SDK_BOOT_PROBLEMS[-1]["text"])
        self.assertNotIn(".tmp.", km._SDK_BOOT_PROBLEMS[-1]["text"], "errno + strerror only: never the temp path")
        heal()
        with contextlib.redirect_stderr(err):
            self._tick(NOW + 60)
        self.assertEqual(len(self.fb.sent), 1, "the first pass after the heal sends once: the landed write is the event")
        self.assertEqual(err.getvalue().count("write failed"), 1, "…and says nothing more")
        return km._auto_nudge_data()["nudged"][self.gid]

    def test_control_a_stalled_goal_draws_one_nudge_when_the_ledger_writes(self):
        self._toggle(True)
        self._seed(stamped=False, delegated=False)       # an unstamped working top: the plain nudge's shape
        self._tick(); self._tick(NOW + 5)
        self.assertEqual(len(self.fb.sent), 1, "the path is reached: one status check goes out, once")
        self.assertEqual(km._auto_nudge_data()["nudged"][self.gid].get("lastTurnId"), "t1", "…and is recorded")

    def test_enospc_a_nudge_is_not_sent_until_its_record_lands(self):
        self._toggle(True)
        self._seed(stamped=False, delegated=False)
        rec = self._nothing_until_the_heal(self.ENOSPC, "nudge")
        self.assertEqual((rec.get("count"), rec.get("lastTurnId")), (1, "t1"), "the healed pass's record")
        self.assertNotIn(km.AWAITING_BACKSTOP_TEXT, self.fb.sent[0][1], "…for the plain nudge it sent")

    def test_eacces_a_nudge_is_not_sent_until_its_record_lands(self):
        self._toggle(True)
        self._seed(stamped=False, delegated=False)
        rec = self._nothing_until_the_heal(self.EACCES, "nudge")
        self.assertEqual(rec.get("count"), 1)

    def test_a_wake_is_not_sent_until_its_record_lands(self):
        self._toggle(True)
        self._seed(kind="job", age=7 * H)                # the shape NudgesOnKeepTheCheckIn fires one wake for
        rec = self._nothing_until_the_heal(self.ENOSPC, "wake")
        self.assertTrue(rec.get("wake"), "the healed pass's wake record")
        self.assertEqual(len(self._wakes()), 1)

    def test_a_debt_reminder_is_not_sent_until_its_record_lands(self):
        # the same shape on the debt leg: it recorded after the send, and a refused record let the reminder
        # out again every tick (the read-tag check above it covers only a fault the READER saw)
        self._toggle(True)
        orig = km._debt_asks
        km._debt_asks = lambda sid, alive: [("66666666-7777-8888-9999-000000000000", "web", NOW - 1800,
                                              "question", "Which port should the staging server use?")]
        self.addCleanup(setattr, km, "_debt_asks", orig)
        before = self.ledger.read_bytes()
        heal = self._write_fault(self.ENOSPC)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._tick(); self._tick(NOW + 5)
        self.assertEqual(self.fb.sent, [], "a reminder whose record cannot land is not sent")
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(err.getvalue().count("write failed"), 1)
        self.assertNotIn("Traceback", err.getvalue())
        heal()
        with contextlib.redirect_stderr(err):
            self._tick(NOW + 60); self._tick(NOW + 65)
        self.assertEqual(len(self.fb.sent), 1, "the first pass after the heal sends once, and the record dedupes the next")
        self.assertIn("web", self.fb.sent[0][1])
        self.assertEqual(len(km._auto_nudge_data().get("debtNudged") or {}), 1)

    def test_the_walk_gate_journal_waits_for_the_next_tick_without_a_traceback(self):
        # a gated session journals its gate (walkGates) on the way out of the walk; that entry is
        # re-derived every tick, so a refused write is simply written next time, never a traceback per tick
        self._toggle(True)
        self._seed(stamped=False, delegated=False)
        km._compacting_now = lambda sid: True             # the session is gated: no nudge, a journal entry
        heal = self._write_fault(self.ENOSPC)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._tick(); self._tick(NOW + 5)
        self.assertEqual(self.fb.sent, [])
        self.assertEqual(err.getvalue().count("write failed"), 1)
        self.assertNotIn("Traceback", err.getvalue(), "the journal write's fault is the writer's line, nothing more")
        self.assertNotIn(SID, km._auto_nudge_data().get("walkGates", {}), "nothing landed while the disk refused")
        heal()
        with contextlib.redirect_stderr(err):
            self._tick(NOW + 60)
        self.assertEqual(km._auto_nudge_data()["walkGates"][SID]["gate"], "needs-input", "the next tick journals it")
        self.assertEqual(self.fb.sent, [], "still gated: nothing sent")


if __name__ == "__main__":
    unittest.main()
