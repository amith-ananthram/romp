#!/usr/bin/env python3
"""A goal is never DONE BY ASSOCIATION (the user 2026-08-24 audit): a delegation's completion may not
close an adjacent ask the completing report never affirmed — and explicitly declining the ask is
evidence AGAINST closing.

The audited shape: an ask filed under a "↪ delegated to <peer>" tracking node; the peer's completing
report explicitly declined it; the courier link-back completed the tracking node, rollup's roll-down
folded the open child into an eventless done-display cache (sealed out of every judge menu), and the
next kernel boot's migrate sweep — whose era check was truthiness, not the diary KEY — synthesized a
witnessed-looking src=judge done row from that cache, making the fold irreversible even by reopen.
Three guards under test:
  * _lift_handoff_children: a completing handoff node's OPEN children move up beside it BEFORE the
    fold — they stay open, visible, judgeable; done children stay under the delegation;
  * the migrate gate keys on diary-KEY ABSENCE ("log" not in nd): a diary-era node whose flags were
    set eventlessly (roll-down cache) never gains manufactured history; genuinely legacy nodes (no
    key) keep the synth so archives render unchanged;
  * the closer's evidence rules name the decline: covering work closes a goal only when it answers
    that goal's own ask affirmatively; a decline/defer leaves it open (or blocks) and says so.
All fixtures SYNTHETIC: placeholder UUIDs, invented text."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from inspect import getsource

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_dnc", os.path.join(BIN, "romp-judge"))

SENDER = "11111111-2222-3333-4444-555555555555"
RECIP = "66666666-7777-8888-9999-000000000000"
MID = "msg-aaaa-1111"
T = 1_787_500_000
U, H, X, Y = (SENDER + ":g1", SENDER + ":g2", SENDER + ":g3", SENDER + ":g4")


def _node(nid, text, parent, t=T, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return jd.GuardedNode(base)


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved_discover = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]
        jd._PEER_ASK_CACHE[:] = [None, ({}, {})]

    def tearDown(self):
        jd.discover = self._saved_discover
        jd._PEER_ASK_CACHE[:] = [None, ({}, {})]
        for f in jd.GOALDIR.glob("*"):
            f.unlink()
        if jd.MESSAGES.exists():
            jd.MESSAGES.unlink()

    def _seed_sender(self, peer=RECIP, **handoff):
        st = jd.load_goals(SENDER)
        st["nodes"][U] = _node(U, "Improve the notes app across surfaces", None)
        st["nodes"][H] = _node(H, "↪ delegated to api: rework the gear menu", U,
                               handoff=dict({"peer": peer, "msgId": MID}, **handoff))
        # the adjacent ask the report will DECLINE — open, nothing ever filed under it
        st["nodes"][X] = _node(X, "Add tag editing to the tab dropdown", H, t=T + 10)
        # a step that earned its own verdict — it belongs to the delegation's history and stays
        st["nodes"][Y] = _node(Y, "Send the design spec over", H, t=T + 5)
        jd.record_verdict(st, st["nodes"][Y], "closer", "done", T + 6)
        jd.save_goals(SENDER, st)

    def _seed_recipient_done(self):
        st = jd.load_goals(RECIP)
        st["nodes"][RECIP + ":g5"] = _node(RECIP + ":g5", "Rework the gear menu", None,
                                           origin={"peer": SENDER, "goalId": H, "msgId": MID})
        jd.record_verdict(st, st["nodes"][RECIP + ":g5"], "closer", "done", T + 50)
        jd.save_goals(RECIP, st)


class LinkBackLift(_Base):
    """The specimen shape, synthetically: delegation ships -> the open adjacent ask STAYS OPEN."""

    def test_open_child_moves_up_and_stays_open(self):
        self._seed_sender()
        self._seed_recipient_done()
        self.assertEqual(jd.run_propagate(now=T + 200), 1, "the link-back fired")
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][H].get("nodeComplete"), "the delegation itself completed")
        x = st["nodes"][X]
        self.assertEqual(x.get("parentId"), U, "the un-ruled ask moved up beside the delegation")
        self.assertFalse(x.get("nodeComplete"), "…and STAYS OPEN — done-by-association never lands")
        self.assertFalse(x.get("rolledUp"), "the roll-down never saw it")
        self.assertFalse(any(e.get("kind") == "done" for e in x.get("log") or []),
                         "no manufactured history either")

    def test_a_child_with_its_own_verdict_stays_under_the_delegation(self):
        self._seed_sender()
        self._seed_recipient_done()
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(SENDER)
        self.assertEqual(st["nodes"][Y].get("parentId"), H,
                         "a step the judges ruled belongs to the delegation's history")

    def test_a_closer_completion_of_the_tracking_node_lifts_too(self):
        # the guard lives in rollup_status's pre-pass, so it covers EVERY completer — not only the
        # courier link-back (review 2026-08-24: a status-report reply can legitimately let the closer
        # rule the tracking node done before the courier's pass reaches it)
        self._seed_sender()
        st = jd.load_goals(SENDER)
        jd.record_verdict(st, st["nodes"][H], "closer", "done", T + 100, why="the peer reported it shipped")
        jd.rollup_status(st, False)
        self.assertEqual(st["nodes"][X].get("parentId"), U)
        self.assertFalse(st["nodes"][X].get("nodeComplete"))

    def test_a_stale_republish_heals_on_the_next_rollup(self):
        # the save-rebase race (review 2026-08-24): a concurrent pass republishes the child's OLD
        # parentId back under the already-completed tracking node — the rebase folds diary rows, not
        # parents. The guard re-runs in every writer's rollup, so the very next pass lifts it again
        # instead of the roll-down sealing it forever.
        self._seed_sender()
        st = jd.load_goals(SENDER)
        jd.record_verdict(st, st["nodes"][H], "courier", "done", T + 100)   # H complete...
        self.assertEqual(st["nodes"][X].get("parentId"), H, "...and X (stale) still under it")
        jd.rollup_status(st, False)                                         # any writer's rollup
        self.assertEqual(st["nodes"][X].get("parentId"), U, "the pre-pass lifted it")
        self.assertFalse(st["nodes"][X].get("nodeComplete"), "no fold, no done-by-association")
        self.assertFalse(st["nodes"][X].get("rolledUp"))

    def test_an_umbrella_ruling_still_absorbs_the_whole_subtree(self):
        # the guard is scoped to the HANDOFF node's own completion: when the judges rule the umbrella
        # TOP done, the designed roll-down absorb applies to everything under it, handoff children
        # included — that completion DID consider the tree (goal history rides the closer menu)
        self._seed_sender()
        st = jd.load_goals(SENDER)
        jd.record_verdict(st, st["nodes"][U], "closer", "done", T + 100, why="the whole effort shipped")
        jd.rollup_status(st, True)
        self.assertEqual(st["nodes"][X].get("parentId"), H, "no lift — the ancestor's ruling absorbs")
        self.assertTrue(st["nodes"][X].get("rolledUp"), "the designed umbrella ending, unchanged")

    def test_cross_host_reply_lifts_the_same_way(self):
        self._seed_sender(peer="boxa:worker_two")
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.MESSAGES.write_text(json.dumps({"id": "m1", "ev": "sent", "from": "worker_two",
                                           "from_id": "peer:boxa:worker_two", "to_id": SENDER,
                                           "t": T + 60, "kind": "coordinate", "body": "x"}) + "\n")
        jd._PEER_ASK_CACHE[:] = [None, ({}, {})]
        self.assertEqual(jd.run_propagate(now=T + 200), 1)
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][H].get("nodeComplete"))
        self.assertEqual(st["nodes"][X].get("parentId"), U)
        self.assertFalse(st["nodes"][X].get("nodeComplete"))


class CrossHostReplyKeyedByWearerAtSendTime(_Base):
    """The remote arm resolves the handoff's peer NAME to a sid AT the handoff's send time (2026-09-08,
    jd._alias_at): the name→sid alias used to be last-write-wins over the whole log, so a name a NEW
    session reused re-pointed every OLD handoff at the new wearer — the real recipient's report-back was
    missed (its reply sits under the old sid), and the stranger's first mail read as the report-back.
    That alias read is the LEGACY path (a tracker planted from a row without to_sid, the first two
    tests). A tracker planted from a row that carries to_sid keys on that sid alone (handoff.toSid,
    review find 2026-09-08, the last two): the same key the kernel's wait maps use for the row, so the
    two readers of one log cannot disagree about one handoff."""

    WX = "77777777-8888-9999-aaaa-000000000001"    # wore "worker_two" on boxa when the handoff was sent
    WY = "77777777-8888-9999-aaaa-000000000002"    # reused the name later
    ELSE = "88888888-9999-aaaa-bbbb-cccccccccccc"  # some other local session WY mailed

    def _log(self, rows):
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.MESSAGES.write_text("".join(json.dumps(r) + "\n" for r in rows))
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def _row(self, i, sid, to, t):
        return {"id": "m%d" % i, "ev": "sent", "from": "worker_two", "from_id": sid, "from_host": "boxa",
                "to_id": to, "t": t, "kind": "coordinate", "body": "x"}

    def test_the_wearer_at_send_time_reports_back_even_after_the_name_is_reused(self):
        self._seed_sender(peer="boxa:worker_two")               # handoff sent at T
        self._log([self._row(0, self.WX, SENDER, T - 100),      # WX wore the name before the handoff
                   self._row(1, self.WX, SENDER, T + 60),       # …and is the one who reported back
                   self._row(2, self.WY, self.ELSE, T + 500)])  # WY took the name later (never mailed SENDER)
        self.assertEqual(jd.run_propagate(now=T + 1000), 1,
                         "keyed to WX, the wearer at T: its reply completes the handoff (last-write-wins: "
                         "keyed to WY, no reply, the handoff stays open)")
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][H].get("nodeComplete"))
        done = [e for e in st["nodes"][H]["log"] if e.get("kind") == "done" and e.get("src") == "courier"]
        self.assertEqual([e.get("ev_t") for e in done], [T + 60], "the courier's verdict rides WX's reply")

    def test_a_later_wearers_mail_is_not_the_report_back(self):
        self._seed_sender(peer="boxa:worker_two")
        self._log([self._row(0, self.WX, SENDER, T - 100),      # WX wore the name at T, never replied
                   self._row(2, self.WY, SENDER, T + 500)])     # WY, wearing it later, mails SENDER
        self.assertEqual(jd.run_propagate(now=T + 1000), 0,
                         "a stranger's mail cannot complete WX's handoff (last-write-wins: done)")
        self.assertFalse(jd.load_goals(SENDER)["nodes"][H].get("nodeComplete"))

    def test_a_tracker_carrying_the_rows_to_sid_completes_on_that_sid_alone(self):
        # review find, 2026-09-08: WY was recreated under WX's name and its FIRST mail to this host is
        # its report-back, the shape no name alias can read (at T the name meant WX). The row named WY
        # by id; the kernel's wait maps read that and called the row answered, while the arm, anchoring
        # the name, keyed to WX and left the tracker open forever (main's last-write-wins completed it).
        self._seed_sender(peer="boxa:worker_two", toSid=self.WY)
        self._log([self._row(0, self.WX, SENDER, T - 100),      # WX wore the name before the handoff
                   self._row(1, self.WY, SENDER, T + 60)])      # WY's first sighting IS the report-back
        self.assertEqual(jd.run_propagate(now=T + 1000), 1,
                         "keyed to the row's own sid: WY's reply completes it (anchored alias: WX, open)")
        st = jd.load_goals(SENDER)
        self.assertTrue(st["nodes"][H].get("nodeComplete"))
        done = [e for e in st["nodes"][H]["log"] if e.get("kind") == "done" and e.get("src") == "courier"]
        self.assertEqual([e.get("ev_t") for e in done], [T + 60], "the courier's verdict rides WY's reply")

    def test_with_to_sid_the_names_earlier_wearer_is_a_stranger(self):
        # the converse: the sid rules. WX, whom the send-time alias would pick, mails the sender; the
        # row named WY, so that is not the report-back (anchored alias: done, on the wrong evidence)
        self._seed_sender(peer="boxa:worker_two", toSid=self.WY)
        self._log([self._row(0, self.WX, SENDER, T - 100),
                   self._row(1, self.WX, SENDER, T + 60)])
        self.assertEqual(jd.run_propagate(now=T + 1000), 0)
        self.assertFalse(jd.load_goals(SENDER)["nodes"][H].get("nodeComplete"))


class MigrateGateKeyPresence(_Base):
    """The era marker is the diary KEY: eventless flags on a diary-era node never become history."""

    def test_rolldown_cache_is_never_synthesized(self):
        nd = _node(X, "an ask the roll-down folded", H, nodeComplete=True, rolledUp=True)
        self.assertEqual(nd["log"], [], "precondition: diary-era node (empty diary at birth)")
        jd._migrate_node(nd)
        self.assertEqual(nd["log"], [],
                         "flags without rows on a diary-era node are display cache, not verdicts — "
                         "no src=judge done row is manufactured")

    def test_genuinely_legacy_node_still_synthesizes(self):
        nd = dict(_node(Y, "a pre-diary done item", None, nodeComplete=True))
        del nd["log"]                                  # NO key: the true legacy shape
        jd._migrate_node(nd)
        rows = [e for e in nd["log"] if e.get("kind") == "done"]
        self.assertEqual(len(rows), 1, "archives keep rendering: legacy flags are their only history")
        self.assertTrue(rows[0].get("synth"), "…and the reconstruction is marked as such")

    def test_legacy_rolledup_node_keeps_the_synth_too(self):
        nd = dict(_node(X, "a pre-diary rolled item", H, nodeComplete=True, rolledUp=True))
        del nd["log"]
        jd._migrate_node(nd)
        self.assertTrue(any(e.get("kind") == "done" for e in nd["log"]),
                        "pre-diary rolledUp flags are the only record there is — unchanged behavior")


class CloserDeclineRules(_Base):
    """The evidence rules SAY the decline never closes — pinned where the closer reads them."""

    def test_no_work_filed_rule_requires_affirmative_coverage(self):
        i = jd.CLOSER_SYS.index("No-work-filed rule:")
        j = jd.CLOSER_SYS.index("- awaiting:")
        blk = jd.CLOSER_SYS[i:j]
        self.assertIn("answers this goal's own ask affirmatively", blk)
        self.assertIn("declines or defers the ask is", blk)
        self.assertIn("evidence AGAINST closing", blk)
        self.assertIn("say in the why that the report declined it", blk)

    def test_status_report_note_names_the_decline_case(self):
        src = getsource(jd._close_turn)
        self.assertIn("declines or ", src)
        self.assertIn("defers a goal's ask is not a completion", src)
        self.assertIn("say the reply declined it", src)


if __name__ == "__main__":
    unittest.main()
