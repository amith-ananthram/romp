#!/usr/bin/env python3
"""Sender 'still undelivered' backstop (the user 2026-06-29): the orphan sweep only bounces mail to a DEAD
recipient. Mail can also strand UNREAD in a LIVE-but-idle recipient's box (the stale-bus bug). _warn_stuck_mail
warns the live SENDER once, after STUCK_GRACE, and LEAVES the message for eventual delivery — gated on the
recipient being idle/waiting so a mid-turn recipient never trips a false alarm.

Synthetic only — placeholder UUIDs, no real session data.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path

from tests.conftest import restore_env

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
pm = load_source("romp_postal_undelivered", os.path.join(BIN, "romp-postal-service"))

SENDER = "11111111-1111-1111-1111-111111111111"
RECIP = "22222222-2222-2222-2222-222222222222"


class StuckMailWarning(unittest.TestCase):
    def setUp(self):
        self._seamfile = os.path.join(tempfile.mkdtemp(), "sessions.json")
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = self._seamfile      # local_agents() reads this instead of a live kernel
        for d in (pm.MAILROOT, pm.WARNED, pm.MAILPENDING):     # isolate each test
            shutil.rmtree(d, ignore_errors=True)

    def tearDown(self):
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def _set_recip_state(self, state):
        Path(self._seamfile).write_text(json.dumps(
            [{"id": SENDER, "name": "alice", "state": "idle"},
             {"id": RECIP, "name": "bob", "state": state}]))

    def _age_recip_mail(self, secs):
        old = time.time() - secs
        for f in (pm.MAILROOT / RECIP / "new").iterdir():
            os.utime(f, (old, old))

    def _sender_box(self):
        return pm.read_box(SENDER, consume=False)

    def test_idle_recipient_stuck_past_grace_warns_sender_once_and_keeps_the_mail(self):
        self._set_recip_state("idle")
        mid = pm.deliver(RECIP, "alice", SENDER, "please review my PR")
        self._age_recip_mail(pm.STUCK_GRACE + 60)
        pm._warn_stuck_mail()
        sb = self._sender_box()
        self.assertEqual(len(sb), 1, "the live sender is warned exactly once")
        self.assertIn("STILL UNDELIVERED", sb[0]["body"])
        self.assertIn("bob", sb[0]["body"], "the warning names the unreachable recipient")
        self.assertTrue((pm.MAILROOT / RECIP / "new" / mid).exists(),
                        "a live recipient's message is LEFT in new/ — it may still deliver (not bounced)")
        pm._warn_stuck_mail()
        self.assertEqual(len(self._sender_box()), 1, "the one-time marker prevents a duplicate warning")

    def test_working_recipient_is_not_warned(self):
        self._set_recip_state("working")
        pm.deliver(RECIP, "alice", SENDER, "ping while you work")
        self._age_recip_mail(pm.STUCK_GRACE + 60)
        pm._warn_stuck_mail()
        self.assertEqual(self._sender_box(), [],
                         "a mid-turn recipient legitimately waits for its next turn — no false alarm")

    def test_fresh_mail_within_grace_is_not_warned(self):
        self._set_recip_state("idle")
        pm.deliver(RECIP, "alice", SENDER, "just sent")     # mtime ~ now, within STUCK_GRACE
        pm._warn_stuck_mail()
        self.assertEqual(self._sender_box(), [], "within the grace the normal delivery path still owns it")

    def test_marker_is_pruned_once_the_message_delivers(self):
        self._set_recip_state("idle")
        mid = pm.deliver(RECIP, "alice", SENDER, "warn then deliver")
        self._age_recip_mail(pm.STUCK_GRACE + 60)
        pm._warn_stuck_mail()
        self.assertTrue((pm.WARNED / mid).exists(), "warned once → marker written")
        pm.read_box(RECIP, consume=True)                    # the recipient finally drains it (new/ -> cur/)
        pm._warn_stuck_mail()
        self.assertFalse((pm.WARNED / mid).exists(),
                         "the marker is pruned once the message left new/ so WARNED stays bounded")


class RefusedNotesKeepTheMail(unittest.TestCase):
    """deliver() can REFUSE now (2026-09-08: its sent row could not land). The orphan sweep and the
    stuck-mail warning used to catch every deliver error and carry on — destroy the orphan, touch the
    one-time marker — so a refused note lost the mail with no notice and no row, and the warning was
    never retried. A refusal keeps the file and the marker untouched, is said once per episode, and
    the next pass retries once the log writes again. Mutants killed: the generic `except Exception`
    arm swallowing the refusal (the orphan is destroyed / the marker touched); the sweep's destroy
    row written best-effort AFTER the unlink (the file goes with no row)."""

    def setUp(self):
        self._seamfile = os.path.join(tempfile.mkdtemp(), "sessions.json")
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = self._seamfile
        for d in (pm.MAILROOT, pm.WARNED, pm.MAILPENDING):
            shutil.rmtree(d, ignore_errors=True)
        self._tl, self._log = pm.TLDIR, pm._log
        self.logged = []
        pm._log = lambda m: self.logged.append(m)
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass
        pm._TL_FAULT[0] = False
        pm._REFUSAL_SAID.clear()

    def tearDown(self):
        pm.TLDIR, pm._log = self._tl, self._log
        pm._TL_FAULT[0] = False
        pm._REFUSAL_SAID.clear()
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    def _live(self, rows):
        Path(self._seamfile).write_text(json.dumps(rows))

    def _break_the_log(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(lambda: os.unlink(path))
        pm.TLDIR = Path(path) / "timeline"          # under a regular file: the REAL append fails (ENOTDIR)

    def _age(self, secs):
        old = time.time() - secs
        for f in (pm.MAILROOT / RECIP / "new").iterdir():
            os.utime(f, (old, old))

    def _rows(self):
        p = self._tl / "messages.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l] if p.exists() else []

    def _said(self):
        return len([m for m in self.logged if "kept for the next pass" in m])

    def test_the_sweep_keeps_an_orphan_whose_bounce_note_was_refused(self):
        self._live([{"id": SENDER, "name": "alice", "state": "idle"}])          # bob is dead → an orphan
        mid = pm.deliver(RECIP, "alice", SENDER, "please review my PR")
        self._age(pm.ORPHAN_GRACE + 60)
        self._break_the_log()
        pm._sweep_orphans()
        self.assertTrue((pm.MAILROOT / RECIP / "new" / mid).exists(),
                        "a refused bounce note destroys nothing: the orphan waits for the next sweep")
        self.assertEqual(pm.read_box(SENDER, consume=False), [], "no note was published without its row")
        pm._sweep_orphans()
        self.assertTrue((pm.MAILROOT / RECIP / "new" / mid).exists())
        self.assertEqual(self._said(), 1, "said once per episode, not per pass")
        pm.TLDIR = self._tl                                                    # the log writes again
        pm._sweep_orphans()
        self.assertFalse((pm.MAILROOT / RECIP / "new" / mid).exists(), "the next pass completes the bounce")
        notes = pm.read_box(SENDER, consume=False)
        self.assertEqual(len(notes), 1)
        self.assertIn("UNDELIVERED", notes[0]["body"])
        self.assertEqual([r["ev"] for r in self._rows() if r.get("id") == mid], ["sent", "bounced"],
                         "the destroy landed on the ledger")

    def test_the_sweep_records_the_destroy_before_it_destroys(self):
        # no live sender to bounce to (the sweep still runs: someone is live) → straight to the destroy
        self._live([{"id": "33333333-4444-5555-6666-777777777777", "name": "carol", "state": "idle"}])
        mid = pm.deliver(RECIP, "alice", SENDER, "please review my PR")
        self._age(pm.ORPHAN_GRACE + 60)
        self._break_the_log()
        pm._sweep_orphans()
        self.assertTrue((pm.MAILROOT / RECIP / "new" / mid).exists(),
                        "a destroy that cannot be recorded does not happen")
        pm.TLDIR = self._tl
        pm._sweep_orphans()
        self.assertFalse((pm.MAILROOT / RECIP / "new" / mid).exists())
        self.assertEqual([r["ev"] for r in self._rows() if r.get("id") == mid], ["sent", "bounced"])

    def test_the_stuck_warning_is_retried_once_its_note_lands(self):
        self._live([{"id": SENDER, "name": "alice", "state": "idle"},
                    {"id": RECIP, "name": "bob", "state": "idle"}])
        mid = pm.deliver(RECIP, "alice", SENDER, "please review my PR")
        self._age(pm.STUCK_GRACE + 60)
        self._break_the_log()
        pm._warn_stuck_mail()
        self.assertFalse((pm.WARNED / mid).exists(), "a refused warning leaves the one-time marker untouched")
        self.assertEqual(pm.read_box(SENDER, consume=False), [])
        pm._warn_stuck_mail()
        self.assertFalse((pm.WARNED / mid).exists())
        self.assertEqual(self._said(), 1, "said once per episode")
        pm.TLDIR = self._tl
        pm._warn_stuck_mail()
        warns = pm.read_box(SENDER, consume=False)
        self.assertEqual(len(warns), 1, "the warning fires on the first pass whose note lands")
        self.assertIn("STILL UNDELIVERED", warns[0]["body"])
        self.assertTrue((pm.WARNED / mid).exists(), "…and only then is it marked one-time")
        pm._warn_stuck_mail()
        self.assertEqual(len(pm.read_box(SENDER, consume=False)), 1, "one-time still holds")
        self.assertTrue((pm.MAILROOT / RECIP / "new" / mid).exists(), "the stuck message itself is left for delivery")


class TheSweepMovesAnUnreadableFileAside(unittest.TestCase):
    """A DEAD recipient's box has no drain to meet an unreadable file, so the orphan sweep moves it
    aside the way read_box does (review find, 2026-09-08): before, the sweep skipped it on every pass
    and the pending marker stayed latched for a session that will never read. The sidecar is
    evidence: the tidy that removes an emptied box leaves a box holding one. Mutants: the sweep's
    catch-all `except Exception: continue` kept for OSError (the file stays); the tidy guard removed
    (the sidecar goes with the box)."""

    def setUp(self):
        self._seamfile = os.path.join(tempfile.mkdtemp(), "sessions.json")
        self._prior_seam = os.environ.get("ROMP_SESSIONS_FILE")
        os.environ["ROMP_SESSIONS_FILE"] = self._seamfile
        for d in (pm.MAILROOT, pm.WARNED, pm.MAILPENDING):
            shutil.rmtree(d, ignore_errors=True)
        self._saved = (pm.TLDIR, pm._log, pm._kernel_post)
        self.logged, self.told = [], []
        pm._log = lambda m: self.logged.append(m)
        pm._kernel_post = lambda path, body, timeout=2: self.told.append((path, body)) or {"ok": True}
        try:
            (pm.TLDIR / "messages.jsonl").unlink()
        except OSError:
            pass
        pm._DASHBOARD_MISSED[0] = False

    def tearDown(self):
        pm.TLDIR, pm._log, pm._kernel_post = self._saved
        pm._DASHBOARD_MISSED[0] = False
        restore_env("ROMP_SESSIONS_FILE", self._prior_seam)

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode-0 file; the fault cannot be staged")
    def test_the_file_is_moved_aside_the_marker_drops_and_the_box_keeps_its_evidence(self):
        Path(self._seamfile).write_text(json.dumps([{"id": SENDER, "name": "alice", "state": "idle"}]))   # bob is dead
        mid = pm.deliver(RECIP, "alice", SENDER, "please review my PR", kind="question")
        f = pm.MAILROOT / RECIP / "new" / mid
        os.chmod(f, 0)
        self.assertTrue((pm.MAILPENDING / RECIP).exists())
        pm._sweep_orphans()
        self.assertFalse(f.exists(), "the unreadable file leaves new/")
        aside = [p.name for p in (pm.MAILROOT / RECIP).iterdir() if p.name.startswith(mid + ".corrupt-")]
        self.assertEqual(len(aside), 1, "moved aside beside new/, never deleted")
        self.assertFalse((pm.MAILPENDING / RECIP).exists(), "the marker drops: nothing readable is pending")
        rows = [json.loads(l) for l in (pm.TLDIR / "messages.jsonl").read_text().splitlines() if l]
        self.assertEqual([r["ev"] for r in rows if r.get("id") == mid], ["sent", "bounced"])
        self.assertTrue([r for r in rows if r.get("id") == mid][-1]["why"].startswith(pm.WHY_INBOX_UNREADABLE))
        self.assertEqual(len([p for p, b in self.told if p == "/postal-notice"]), 1, "one bell row")
        self.assertEqual(pm.read_box(SENDER, consume=False), [], "no bounce note: the sender's receipt carries it")
        pm._sweep_orphans()
        self.assertTrue((pm.MAILROOT / RECIP).is_dir(), "the emptied box is not tidied away while it holds evidence")
        self.assertEqual(len([p for p, b in self.told if p == "/postal-notice"]), 1, "said once")


if __name__ == "__main__":
    unittest.main()
