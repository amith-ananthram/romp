#!/usr/bin/env python3
"""A MACHINE cut — a kernel restart (BOOT_RESUME_NUDGE) or the session's own claude process dying
mid-turn (CRASH_RESUME_NUDGE) — mints the SAME "[Request interrupted by user]" stop record as a genuine
Esc/Stop, but romp caused it and immediately queued a resume notice to CONTINUE the work. So it must NOT
read as a user-chosen stop: no false "you stopped this — romp won't follow up" badge, auto-nudge is NOT
suppressed (the session is continued), and it is never blocked-on-you (the user 2026-07-14: restart-cut
SDK sessions sat inertly in Working wearing that false badge, and auto-nudge stayed off so a genuine
RE-stall was never caught).

Conversely a GENUINE user stop still suppresses the nudge AND flips the focus goal to Blocked (needs-you)
regardless of the auto-nudge toggle — the interrupt-block flip is a needs-you rule, not a nudge feature.
Synthetic fixtures only."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_mc", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_mc", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_mc", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_mc", os.path.join(BIN, "romp_sdk_backend.py"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent, stop):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def uatom(t, text, author="human"):
    """A bare user atom (no parse) for the pure-logic predicates."""
    return {"type": "user", "t": t, "author": author,
            "message": {"role": "user", "content": text}}


def intr(t):
    return uatom(t, "[Request interrupted by user]", author="human")


class InterruptCauseClassifier(unittest.TestCase):
    """_interrupt_cause reads the romp resume notice that FOLLOWS an interrupt record — the same signal
    the chat seam's cause label uses — and names the machine cut, or None for a genuine stop."""

    def test_restart_notice_is_a_restart_cut(self):
        self.assertEqual(km._interrupt_cause(uatom(T0, km.INTR_RESTART_SIG, "romp")), "restart")

    def test_crash_notice_is_a_crash_cut(self):
        self.assertEqual(km._interrupt_cause(uatom(T0, km.INTR_CRASH_SIG, "romp")), "crash")

    def test_a_typed_human_reply_is_a_user_stop(self):
        self.assertIsNone(km._interrupt_cause(uatom(T0, "actually, try the other approach")))

    def test_nothing_after_the_stop_is_a_user_stop(self):
        self.assertIsNone(km._interrupt_cause(None))

    def test_lockstep_with_the_actual_resume_nudges(self):
        # the classifier keys on the SAME text the SDK backend injects — pinned against the real nudges
        # so a wording drift breaks here, not silently in production
        self.assertIn(km.INTR_RESTART_SIG, sb.BOOT_RESUME_NUDGE,
                      "the restart signature must be a substring of BOOT_RESUME_NUDGE")
        self.assertIn(km.INTR_CRASH_SIG, sb.CRASH_RESUME_NUDGE,
                      "the crash signature must be a substring of CRASH_RESUME_NUDGE")
        self.assertEqual(km._interrupt_cause(uatom(T0, sb.BOOT_RESUME_NUDGE, "romp")), "restart")
        self.assertEqual(km._interrupt_cause(uatom(T0, sb.CRASH_RESUME_NUDGE, "romp")), "crash")


class MachineCutSuppression(unittest.TestCase):
    """_interrupt_suppresses_nudge excludes a machine cut from the user-stop tally: romp caused it and
    re-engaged, so it must not suppress the nudge (nor paint the feed's 'interrupted' badge)."""

    def _turns(self, atoms):
        return [{"atoms": atoms}]

    def test_a_restart_cut_does_not_suppress(self):
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 61, sb.BOOT_RESUME_NUDGE, "romp")])
        self.assertFalse(km._interrupt_suppresses_nudge(turns),
                         "romp restarted and re-queued the work — not a user stop")

    def test_a_crash_cut_does_not_suppress(self):
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 61, sb.CRASH_RESUME_NUDGE, "romp")])
        self.assertFalse(km._interrupt_suppresses_nudge(turns),
                         "the claude process died and was resumed — not a user stop")

    def test_a_genuine_stop_still_suppresses(self):
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60)])
        self.assertTrue(km._interrupt_suppresses_nudge(turns),
                        "an Esc with no resume notice after it is the user driving — still suppressed")

    def test_a_user_stop_AFTER_a_machine_cut_suppresses(self):
        # the resumed session was then genuinely stopped by the user — the NEWEST interrupt is a real stop
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 10),
                             uatom(T0 + 11, sb.BOOT_RESUME_NUDGE, "romp"),
                             intr(T0 + 30)])
        self.assertTrue(km._interrupt_suppresses_nudge(turns),
                        "the user stopped the RESUMED turn → they're driving now")

    def test_the_users_message_after_a_machine_cut_still_reads_re_engaged(self):
        # a machine cut then the user speaks: last_human outranks nothing (the cut doesn't count) → not suppressed
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 10),
                             uatom(T0 + 11, sb.BOOT_RESUME_NUDGE, "romp"),
                             uatom(T0 + 40, "ok, take plan B")])
        self.assertFalse(km._interrupt_suppresses_nudge(turns))

    # --- the wedge (the user 2026-08-10): the restart that cuts the turn kills its background tasks
    # too, and their `<task-notification>` records land BETWEEN the stop record and the resume notice.
    # The one-atom lookahead read the notification, found no signature, and filed the machine cut as a
    # user stop — so the focus card re-blocked "you stopped this session mid-turn" at the very moment
    # its finished answer arrived.

    WEDGE = "<task-notification>the restart killed background task b0000000000</task-notification>"

    def test_a_wedged_task_notification_does_not_hide_the_cut(self):
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 61, self.WEDGE, "system"),
                             uatom(T0 + 62, sb.BOOT_RESUME_NUDGE, "romp")])
        self.assertFalse(km._interrupt_suppresses_nudge(turns),
                         "the notice past the wedged notification still names the cut as romp's")

    def test_a_genuine_stop_with_a_trailing_notification_still_suppresses(self):
        # same wedge, no notice anywhere: the stop is real — scanning past the notification must
        # never invent a machine cut
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 61, self.WEDGE, "system")])
        self.assertTrue(km._interrupt_suppresses_nudge(turns))

    def test_a_notification_quoting_a_signature_is_not_a_notice(self):
        # a background task's output tail can QUOTE the signature phrase (romp's own test runs echo the
        # nudge texts) — only romp's OWN notice (author 'romp') names a cause, never a notification body
        quoting = "<task-notification>test output: '%s'</task-notification>" % km.INTR_RESTART_SIG
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 61, quoting, "system")])
        self.assertTrue(km._interrupt_suppresses_nudge(turns),
                        "a genuine stop stays a stop even when a notification echoes the phrase")

    def test_a_notice_past_the_next_stop_record_stays_a_user_stop(self):
        # genuine stop → a romp nudge re-engages → the nudged turn is machine-cut and resumed: the scan
        # for the FIRST record stops at the second record, so the later notice never reaches back to
        # disown the real stop — and the user's stop is still the newest thing THEY did → suppressed
        turns = self._turns([uatom(T0, "wire the thing"), intr(T0 + 10),
                             uatom(T0 + 20, "status? pick it back up", "romp"),
                             intr(T0 + 30),
                             uatom(T0 + 31, sb.BOOT_RESUME_NUDGE, "romp")])
        self.assertTrue(km._interrupt_suppresses_nudge(turns),
                        "the notice belongs to the SECOND cut; the first stop is the user's")


class MidToolUseCut(unittest.TestCase):
    """T219 (the user 2026-09-01, who saw their own card claim they stopped a session a deploy
    restart had cut): a restart cutting a turn MID-TOOL-USE writes TWO stop records back to back —
    '[Request interrupted by user for tool use]' then '[Request interrupted by user]' — and only
    THEN the resume notice. The first record's forward scan hits the second (a terminator) and,
    before the fix, returned 'user stop' without consulting the notice or the backend's machineCut
    stamp: blocked-on-you filed off a machine cut, twice on the live specimen. An adjacent stop
    record settles NOTHING about the one before it — only a human message does — so the scan now
    falls through to the stamp there, and the stamp decides."""

    def _turns(self, atoms):
        return [{"atoms": atoms}]

    def _double_cut(self):
        return [uatom(T0, "wire the thing"),
                uatom(T0 + 60, "[Request interrupted by user for tool use]", author="human"),
                intr(T0 + 63),
                uatom(T0 + 80, sb.BOOT_RESUME_NUDGE, "romp")]

    def test_a_mid_tool_use_restart_cut_is_no_user_stop(self):
        # the stamp covers both records (the backend stamps the cut at/after the CLI writes them)
        stop_t, human_t = km._interrupt_marks_atoms(self._double_cut(), cut_t=T0 + 64,
                                                    cut_cause="restart")
        self.assertEqual(stop_t, 0, "neither back-to-back record reads as the user's stop — "
                                    "no blocked-on-you, no 'you stopped this' prose")

    def test_the_notice_clears_both_twin_records_even_before_the_stamp_lands(self):
        # the read-past-the-twin rule lets record #1's scan reach the notice just past record #2 —
        # the notice covers its own cut's twins, so neither needs the stamp (the stamp remains the
        # decider only inside the notice-not-yet-on-disk window, tested above)
        atoms = self._double_cut()
        stop_t, _ = km._interrupt_marks_atoms(atoms, cut_t=0.0, cut_cause="")
        self.assertEqual(stop_t, 0, "the notice disowns both back-to-back records of its cut")

    def test_a_genuine_double_stop_still_files(self):
        # the user hammering Esc twice with NO machine cut behind it: still a real stop
        atoms = [uatom(T0, "wire the thing"),
                 uatom(T0 + 60, "[Request interrupted by user for tool use]", author="human"),
                 intr(T0 + 63)]
        stop_t, _ = km._interrupt_marks_atoms(atoms, cut_t=0.0, cut_cause="")
        self.assertGreater(stop_t, 0, "no notice, no stamp → the user is driving, exactly as today")

    def test_a_human_terminator_still_settles_without_the_stamp(self):
        # the user SPOKE after the first record: the transcript answered — the stamp is never
        # consulted, the stop tally keys on the record vs the newer human message as before
        atoms = [uatom(T0, "wire the thing"),
                 uatom(T0 + 60, "[Request interrupted by user for tool use]", author="human"),
                 uatom(T0 + 90, "ok, take plan B"),
                 intr(T0 + 120)]
        stop_t, human_t = km._interrupt_marks_atoms(atoms, cut_t=T0 + 200, cut_cause="restart")
        self.assertGreater(stop_t, 0)


class _FeedHarness(unittest.TestCase):
    """Shared transcript + store + feed fixture for the tick-level classes below (no tests of its
    own): a temp project dir, a named session, redirected judge/kernel paths, and helpers to write
    the transcript shapes and read the built card."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        jd.CLOSER_ON = False              # closer-verdict gate idles → the interrupt gates are what's exercised
        jd.GOALDIR.mkdir(parents=True)
        km._downtime[:] = []
        km._parse_cache.clear()
        km._autonudge_cache.clear()
        km._machine_cut_cache.clear()
        km._pending_ops.clear()
        km._write_auto_nudge({"enabled": True, "nudged": {}, "intrBlocked": {}})
        self.tmux = {SID: {"state": "idle", "since": NOW - 100, "model": "", "effort": "",
                           "context": None, "compactPct": None, "color": None}}

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES, jd.CLOSER_ON) = self.saved
        km._pending_ops.clear()
        km._parse_cache.clear()
        km._autonudge_cache.clear()
        self.td.cleanup()

    def _machine_cut(self, notice, wedge=None):
        # a genuine turn CUT mid-flight (interrupt record ends it), then romp's resume notice opens a
        # fresh turn that ran and re-stalled with the goal still working. `wedge`: a user-role record
        # (a `<task-notification>` from a background task the same restart killed) that lands BETWEEN
        # the stop record and the notice (the user 2026-08-10)
        recs = [uline(T0, "wire the thing", "u1"),
                aline(T0 + 20, "digging in", "a1", "u1", "tool_use"),
                uline(T0 + 60, "[Request interrupted by user]", "u2", "a1")]
        prev = "u2"
        if wedge:
            recs.append(uline(T0 + 61, wedge, "u2b", prev))
            prev = "u2b"
        recs += [uline(T0 + 62, notice, "u3", prev),
                 aline(T0 + 80, "picked it back up; still on it", "a2", "u3", "end_turn")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _genuine_stop(self):
        recs = [uline(T0, "wire the thing", "u1"),
                aline(T0 + 20, "digging in", "a1", "u1", "tool_use"),
                uline(T0 + 60, "[Request interrupted by user]", "u2", "a1")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _goal(self):
        g = SID + ":gw"
        store = {"rompUuid": SID, "seq": 1, "lastNode": g, "closedTurns": [],
            "nodes": {g: {"id": g, "text": "wire up the thing", "parentId": None, "nodeComplete": False,
                          "blocked": False, "cleared": False, "trail": [], "t": T0}},
            "placements": {}, "status": {g: "working"}}
        # mirror a caught-up planner (the 2026-07-15 placement gate): the fire path requires every
        # due unit placed, and these fixtures mean "the judges ruled and left the goal working"
        try:
            turns = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]
            for u in jd.plan_units({"turns": turns}, store):
                store["placements"][jd._unit_key(u[0], u[1])] = None
        except Exception:
            pass
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(store))
        return g

    def _stub_send(self):
        sent = []
        saved = km._tmux_send, jd.optimistic_followup
        km._tmux_send = lambda name, body, **kw: sent.append((name, body))
        jd.optimistic_followup = lambda sid, gid: True

        def restore():
            km._tmux_send, jd.optimistic_followup = saved
        return sent, restore

    def _card(self, item_id=None):
        km._parse(str(self.tpath), SID, NOW)                       # warm the cache (stands in for _warm_fleet_bg)
        return next(a for a in km.build_feed(NOW, self.tmux)["asks"]
                    if a["itemId"] == (item_id or SID + ":gw"))


class MachineCutFeedAndNudge(_FeedHarness):
    """End to end through the parse: a restart-cut session is CONTINUED (auto-nudge fires), wears NO
    'interrupted' badge, and is never interrupt-blocked. A genuine user stop flips the focus goal to
    Blocked (needs-you) even with auto-nudge OFF, and wears the badge in the needs-you column."""

    # --- restart / crash cut: continued, no badge, not blocked ------------------------------------

    def test_restart_cut_gets_nudged_to_continue(self):
        self._machine_cut(sb.BOOT_RESUME_NUDGE)
        g = self._goal()
        sent, restore = self._stub_send()
        try:
            km._auto_nudge_tick(NOW, self.tmux)
            self.assertEqual(len(sent), 1, "a restart-cut, re-stalled goal is nudged to continue — not left silent")
            self.assertIn("romp-goal-id: " + g, sent[0][1])
        finally:
            restore()

    def test_restart_cut_wears_no_interrupted_badge(self):
        self._machine_cut(sb.BOOT_RESUME_NUDGE)
        self._goal()
        self.assertFalse(self._card().get("interrupted"),
                         "romp caused the cut and continued — never 'you stopped this, romp won't follow up'")

    def test_restart_cut_card_stays_in_working_not_blocked(self):
        self._machine_cut(sb.BOOT_RESUME_NUDGE)
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "working",
                         "a machine cut is continued, never blocked-on-you")
        self.assertEqual(self._card()["column"], "working")

    def test_crash_cut_is_also_continued_not_blocked(self):
        self._machine_cut(sb.CRASH_RESUME_NUDGE)
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "working")
        self.assertFalse(self._card().get("interrupted"))

    def test_restart_cut_with_a_wedged_task_notification_is_not_blocked(self):
        # end to end through the real parse (author_of assigns the wedge 'system', the notice 'romp'):
        # the notification a dying background task lands between the stop record and the notice must
        # not turn the machine cut into "you stopped this session mid-turn" (the user 2026-08-10 — the
        # audited card re-blocked at the very moment its finished answer arrived)
        self._machine_cut(sb.BOOT_RESUME_NUDGE,
                          wedge="<task-notification>the restart killed a background task"
                                "</task-notification>")
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "working",
                         "a machine cut is continued even when a notification wedges before the notice")
        self.assertFalse(self._card().get("interrupted"))
        self.assertEqual(self._card()["column"], "working")

    # --- genuine user stop: blocked (needs-you) even with auto-nudge OFF --------------------------

    def test_genuine_stop_blocks_the_focus_goal_with_autonudge_off(self):
        km._set_auto_nudge(False)                       # the toggle must not gate the needs-you flip
        self._genuine_stop()
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "blocked",
                         "a user-stopped session needs the user → Blocked, not sitting in Working")

    def test_genuine_stop_card_lands_in_needs_input_with_the_badge(self):
        km._set_auto_nudge(False)
        self._genuine_stop()
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        card = self._card()
        self.assertEqual(card["column"], "needs_input", "the interrupted card comes to the user's attention")
        self.assertTrue(card.get("interrupted"), "and says WHY it's here — the user stopped it mid-turn")

    def test_genuine_stop_block_lifts_when_the_user_re_engages(self):
        km._set_auto_nudge(False)
        self._genuine_stop()
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "blocked")
        # the user speaks → a fresh turn opens; the block WE placed lifts
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(T0 + 200, "keep going with plan B", "u3", "u2")) + "\n")
        km._parse_cache.clear()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][SID + ":gw"], "working",
                         "the user re-engaged → our interrupt block lifts")


class StaleInterruptMarker(_FeedHarness):
    """The once-per-episode intrBlocked marker is VERIFIED against the store each tick, never trusted
    (the user 2026-08-08). The audited shape: a genuine stop blocked the then-focus goal; the judges
    lifted and completed it off newer turns; an injected turn (a task notification) ran and settled
    with the user silent throughout. The marker still pointed at the finished goal, so the tick read
    "already blocked this episode" and skipped the re-block forever — the session's LIVE focus goal
    sat in Working wearing only the 'interrupted' badge, auto-nudge suppressed: invisible-blocked.
    The re-record stamps the CURRENT quiet (the stop joined with the transcript's newest event — an
    injected record can FOLD into the cut turn rather than open one, so the last turn's trigger
    undershoots), landing over the judges' newer rows instead of folding under them."""

    INJECTED = "<task-notification>a background task finished</task-notification>"

    def test_a_wedged_marker_reblocks_the_live_focus_goal(self):
        km._set_auto_nudge(False)
        self._genuine_stop()
        g1 = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g1], "blocked")
        self.assertEqual(km._intr_blocked(SID), g1)
        # the judges move on from newer turns: the stopped goal is lifted and completed, and a second
        # goal is the live focus now — the marker still points at the finished one
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][g1], "unblocker", "unblock", T0 + 70,
                          why="answered in passing: picked the work back up")
        jd.record_verdict(st, st["nodes"][g1], "planner", "done", T0 + 80, why="shipped")
        g2 = SID + ":g2"
        st["nodes"][g2] = {"id": g2, "text": "polish the widget", "parentId": None,
                           "nodeComplete": False, "blocked": False, "cleared": False,
                           "trail": [], "t": T0 + 80}
        st["lastNode"] = g2
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        # an injected turn ran and settled AFTER the stop, the user silent throughout
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(T0 + 200, self.INJECTED, "u3", "u2")) + "\n")
            f.write(json.dumps(aline(T0 + 220, "wrapped that up; the build is green", "a2", "u3",
                                     "end_turn")) + "\n")
        km._parse_cache.clear()
        km._interrupt_block_tick(NOW, self.tmux)
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][g2], "blocked",
                         "the marker was verified stale → the LIVE focus goal re-blocks on the user")
        self.assertEqual(km._intr_blocked(SID), g2, "the marker follows the block it actually placed")
        card = self._card(g2)
        self.assertEqual(card["column"], "needs_input")
        self.assertTrue(card.get("interrupted"), "the needs-you card says the user stopped this session")

    def test_reblock_stands_down_until_newer_quiet_evidence(self):
        km._set_auto_nudge(False)
        self._genuine_stop()
        g1 = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        # a judge lifts OUR block off newer evidence — rows the bare stop's stamp cannot outrank
        st = jd.load_goals(SID)
        jd.record_verdict(st, st["nodes"][g1], "unblocker", "unblock", T0 + 70,
                          why="answered in passing: picked the work back up")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)
        rows = len(jd.load_goals(SID)["nodes"][g1]["log"])
        km._interrupt_block_tick(NOW, self.tmux)
        st = jd.load_goals(SID)
        self.assertEqual(st["status"][g1], "working",
                         "the judges ruled on a newer world — the re-block stands down")
        self.assertEqual(len(st["nodes"][g1]["log"]), rows, "refused WITHOUT appending — no diary spam")
        self.assertIsNone(km._intr_blocked(SID), "the stale marker is cleared, not re-set")
        # an injected turn settles later, the user still silent → the quiet's evidence is newest again
        with open(self.tpath, "a") as f:
            f.write(json.dumps(uline(T0 + 200, self.INJECTED, "u3", "u2")) + "\n")
            f.write(json.dumps(aline(T0 + 220, "wrapped that up", "a2", "u3", "end_turn")) + "\n")
        km._parse_cache.clear()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g1], "blocked",
                         "re-surfaced the moment the stop is the newest information again")


class ResumeNudgeDisarmsTheStopRecord(unittest.TestCase):
    """The resume notices must DISARM the interrupt record they follow (the user 2026-08-08). A machine
    cut writes the same '[Request interrupted by user]' record as a real Esc, and the resumed model
    reads that record as the user's intent: across the fleet's transcripts, roughly one restart-cut
    session in six answered the resume notice by standing down and awaiting direction instead of resuming. The notice must therefore name the record,
    say the user did not write it, and instruct the model to continue without asking."""

    def test_both_nudges_name_and_disown_the_stop_record(self):
        for nudge in (sb.BOOT_RESUME_NUDGE, sb.CRASH_RESUME_NUDGE):
            self.assertIn("[Request interrupted by user]", nudge,
                          "the notice names the record it is disarming, verbatim")
            self.assertIn("nobody asked you to stop", nudge,
                          "…and says plainly the user did not stop the session")
            self.assertIn("without asking", nudge,
                          "…and that resuming needs no permission")

    def test_signatures_survive_the_copy(self):
        self.assertIn(km.INTR_RESTART_SIG, sb.BOOT_RESUME_NUDGE)
        self.assertIn(km.INTR_CRASH_SIG, sb.CRASH_RESUME_NUDGE)
        self.assertNotIn(km.INTR_RESTART_SIG, sb.CRASH_RESUME_NUDGE,
                         "the two causes stay distinguishable")


# ── the race: the cut is real, its resume notice has not reached disk yet ─────────────────────────
# Every test above hands the classifier a transcript that ALREADY contains romp's resume notice. On the
# live machine that notice arrives seconds after the stop record, and every _interrupt_block_tick inside
# that window read a bare trailing stop and blocked the focus card on the user: 30 false interrupt/block
# rows in 30 hours, each followed by an unblock once the notice landed (the user 2026-08-12, for whom this
# came up "again and again"). The backend's machineCut stamp — written when the resume is QUEUED, not when
# it lands — is what closes the window.

class MachineCutStampClassifier(unittest.TestCase):
    """_machine_cut_cause defers to the backend's stamp ONLY where the transcript is inconclusive, and
    time-ordering keeps that exact: the cut precedes the resume it is stamped with, so a stop the user
    makes later is always past the stamp."""

    def _users(self, atoms):
        return atoms

    def test_a_bare_trailing_stop_with_a_stamp_is_the_machine_cut(self):
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60)])
        self.assertEqual(km._machine_cut_cause(users, 1, T0 + 62, "restart"), "restart",
                         "romp queued a resume at +62 for the stop at +60 — its own cut, notice or no notice")

    def test_the_stamped_cause_is_reported_verbatim(self):
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60)])
        self.assertEqual(km._machine_cut_cause(users, 1, T0 + 61, "crash"), "crash")

    def test_a_bare_trailing_stop_with_no_stamp_is_still_a_user_stop(self):
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60)])
        self.assertIsNone(km._machine_cut_cause(users, 1),
                          "no stamp, no notice → the user stopped it; the default must not move")

    def test_a_stop_AFTER_the_stamp_is_the_user_stopping_the_resumed_turn(self):
        # the restart's cut was resumed at +62; the user then hit Esc on that resumed turn at +90.
        # Nothing follows it on disk, so only the ordering tells them apart.
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 62, sb.BOOT_RESUME_NUDGE, "romp"), intr(T0 + 90)])
        self.assertIsNone(km._machine_cut_cause(users, 3, T0 + 62, "restart"),
                          "a stop past the resume romp queued is the user's, and a stale stamp must not eat it")

    def test_the_stamp_never_overrides_a_transcript_that_already_answered(self):
        # a human message after the stop proves it was theirs — the scan terminates there and the stamp,
        # however recent, is never consulted
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 70, "actually, try the other approach")])
        self.assertIsNone(km._machine_cut_cause(users, 1, T0 + 9999, "restart"))

    def test_the_notice_still_wins_when_it_has_landed(self):
        users = self._users([uatom(T0, "wire the thing"), intr(T0 + 60),
                             uatom(T0 + 62, sb.CRASH_RESUME_NUDGE, "romp")])
        self.assertEqual(km._machine_cut_cause(users, 1), "crash",
                         "the transcript answers on its own once the notice is there — no stamp needed")


class MachineCutStampWiring(unittest.TestCase):
    """The stamp is written where romp QUEUES a resume — both choke points — and read back by the kernel.
    Pinned so a future edit cannot move the queueing without the stamp, which would silently reopen the
    window this whole mechanism closes."""

    def test_boot_reconcile_stamps_the_restart_cut(self):
        src = Path(BIN, "romp_sdk_backend.py").read_text()
        cut = src.index("prepend = ([BOOT_RESUME_NUDGE] if cut else [])")
        self.assertIn('append_machine_cut(self.state_dir, sid, "restart")', src[cut:cut + 2000],
                      "the boot reconcile that queues BOOT_RESUME_NUDGE must stamp the cut it is resuming")

    def test_crash_resume_stamps_the_crash_cut(self):
        src = Path(BIN, "romp_sdk_backend.py").read_text()
        cut = src.index("reg[\"queue\"] = [CRASH_RESUME_NUDGE]")
        self.assertIn('append_machine_cut(self.state_dir, sid, "crash")', src[cut:cut + 1200],
                      "the crash resume must stamp its cut too")

    def setUp(self):
        km._machine_cut_cache.clear()      # the reader is mtime+size cached — never read a sibling's file

    def tearDown(self):
        km._machine_cut_cache.clear()

    def test_the_reader_and_the_writer_agree(self):
        # round-trip through the real appender and the real reader: one key, one cause, newest wins
        with tempfile.TemporaryDirectory() as td:
            saved = jd.STATE
            try:
                jd.STATE = Path(td)
                self.assertEqual(km._last_machine_cut(SID), (0.0, ""), "no marker → no claim")
                sb.append_machine_cut(Path(td), SID, "restart", T0 + 10)
                km._machine_cut_cache.clear()
                self.assertEqual(km._last_machine_cut(SID), (float(T0 + 10), "restart"))
                sb.append_machine_cut(Path(td), SID, "crash", T0 + 99)
                km._machine_cut_cache.clear()
                self.assertEqual(km._last_machine_cut(SID), (float(T0 + 99), "crash"),
                                 "the newest cut is the one in force")
            finally:
                jd.STATE = saved

    def test_the_cache_refreshes_when_a_new_cut_is_appended(self):
        # the cache must key on the file's identity, not just its path: a fresh cut written into a file
        # already read this push has to be seen (mtime+size both move), or the window reopens
        with tempfile.TemporaryDirectory() as td:
            saved = jd.STATE
            try:
                jd.STATE = Path(td)
                sb.append_machine_cut(Path(td), SID, "restart", T0 + 10)
                self.assertEqual(km._last_machine_cut(SID), (float(T0 + 10), "restart"))
                sb.append_machine_cut(Path(td), SID, "crash", T0 + 99)   # no manual cache clear
                self.assertEqual(km._last_machine_cut(SID), (float(T0 + 99), "crash"),
                                 "an appended cut must invalidate the cached read")
            finally:
                jd.STATE = saved

    def test_the_stamp_keeps_sub_second_precision(self):
        # int() truncation would move the bound EARLIER and drop a stop record written in the same second
        with tempfile.TemporaryDirectory() as td:
            saved = jd.STATE
            try:
                jd.STATE = Path(td)
                sb.append_machine_cut(Path(td), SID, "restart", T0 + 10.75)
                self.assertEqual(km._last_machine_cut(SID)[0], float(T0) + 10.75)
            finally:
                jd.STATE = saved

    def test_the_marker_is_skipped_by_the_other_keyed_readers(self):
        # it shares states/<sid>.jsonl with the state and awaiting records — its own key keeps them apart
        with tempfile.TemporaryDirectory() as td:
            sb.append_state(Path(td), SID, "working", T0)
            sb.append_machine_cut(Path(td), SID, "restart", T0 + 1)
            self.assertEqual(sb.last_state_value(Path(td), SID), "working",
                             "the state reader must read past a machineCut record")


class MachineCutBeforeItsNoticeLands(_FeedHarness):
    """THE REGRESSION, end to end through the real parse and the real tick: a restart-cut session whose
    resume notice has not been written yet must not be blocked on the user, must not wear the badge, and
    must not have its focus card flipped out of Working."""

    def _cut_awaiting_its_notice(self, cause="restart"):
        """The window: the CLI wrote the stop record when the turn was cut; romp has queued the resume
        (stamped) but the resumed CLI has not written the notice to the transcript yet."""
        recs = [uline(T0, "wire the thing", "u1"),
                aline(T0 + 20, "digging in", "a1", "u1", "tool_use"),
                uline(T0 + 60, "[Request interrupted by user]", "u2", "a1")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        sb.append_machine_cut(jd.STATE, SID, cause, T0 + 62)

    def test_the_focus_card_is_not_blocked_on_the_user(self):
        self._cut_awaiting_its_notice()
        g = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g], "working",
                         "romp cut this turn and is resuming it — the user is owed nothing")

    def test_no_interrupt_block_row_is_filed_at_all(self):
        # the row itself is the damage the user sees repeatedly: an interrupt/block verdict in the card's
        # log claiming they stopped a session they never touched
        self._cut_awaiting_its_notice()
        g = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        log = jd.load_goals(SID)["nodes"][g].get("log") or []
        self.assertEqual([r for r in log if r.get("src") == "interrupt"], [],
                         "no interrupt verdict may be written for a cut romp itself caused")
        self.assertNotIn(jd.INTERRUPT_BLOCK_WHY, json.dumps(log))

    def test_the_card_stays_in_working(self):
        self._cut_awaiting_its_notice()
        self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        card = self._card()
        self.assertEqual(card["column"], "working")
        self.assertFalse(card.get("interrupted"), "and wears no 'you stopped this' badge")

    def test_a_crash_cut_awaiting_its_notice_is_also_continued(self):
        self._cut_awaiting_its_notice("crash")
        g = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g], "working")

    def test_auto_nudge_still_fires_so_a_real_re_stall_is_caught(self):
        # the false block's other half: a machine cut must not suppress the nudge (the 2026-07-14 rule)
        self._cut_awaiting_its_notice()
        turns = jd.parsed_session(SID, [str(self.tpath)], NOW)["turns"]
        self.assertFalse(km._interrupt_suppresses_nudge(turns, SID),
                         "romp caused the cut — the nudge must stay armed")

    def test_the_same_transcript_with_no_stamp_still_blocks(self):
        # the guard on the guard: without the backend's stamp this shape IS a genuine user stop, and must
        # still reach the user. If this ever goes green-by-default the mechanism has stopped discriminating.
        self._genuine_stop()
        g = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g], "blocked",
                         "a real Esc with no machine-cut stamp still needs the user")

    def test_a_user_stop_after_the_resumed_turn_still_blocks(self):
        # the restart's cut (+60) was stamped at +62 and resumed; the user then genuinely stopped the
        # resumed turn at +200. The stale stamp must not swallow that stop.
        recs = [uline(T0, "wire the thing", "u1"),
                aline(T0 + 20, "digging in", "a1", "u1", "tool_use"),
                uline(T0 + 60, "[Request interrupted by user]", "u2", "a1"),
                uline(T0 + 62, sb.BOOT_RESUME_NUDGE, "u3", "u2"),
                aline(T0 + 80, "picked it back up", "a2", "u3", "tool_use"),
                uline(T0 + 200, "[Request interrupted by user]", "u4", "a2")]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        sb.append_machine_cut(jd.STATE, SID, "restart", T0 + 62)
        g = self._goal()
        km._interrupt_block_tick(NOW, self.tmux)
        self.assertEqual(jd.load_goals(SID)["status"][g], "blocked",
                         "the user stopped the RESUMED turn — that one is theirs and belongs in needs-you")


if __name__ == "__main__":
    unittest.main()


class RetryingCutIsAMachineCut(unittest.TestCase):
    """A restart landing mid-API-retry (or mid-compaction) cuts the turn exactly as a working cut
    does (the user 2026-08-19, whose figure session sat blocked-on-you — "you stopped this session
    mid-turn" — after every restart that landed in its retry loop, with no machineCut stamp ever
    written and no resume nudge queued). The boot reconcile's cut discriminator now reads every
    MACHINE-ACTIVE last state; "permission"/"picker" stay excluded — those turns were already
    waiting on the user, so blocked-on-you is the truth there."""

    def test_machine_active_states_read_as_cut_and_user_wait_states_do_not(self):
        import inspect
        src = inspect.getsource(sb.SdkBackend)
        self.assertIn('last_state_value(self.state_dir, sid) in ("working", "retrying", "compacting")', src)
        self.assertNotIn('last_state_value(self.state_dir, sid) == "working"', src,
                         "the narrow test is gone — a retrying cut got no stamp and no resume")

    def test_last_state_value_reports_retrying(self):
        with tempfile.TemporaryDirectory() as td:
            sb.append_state(Path(td), SID, "working", T0)
            sb.append_state(Path(td), SID, "retrying", T0 + 5)
            self.assertEqual(sb.last_state_value(Path(td), SID), "retrying",
                             "the discriminator sees the retry loop the restart landed in")
