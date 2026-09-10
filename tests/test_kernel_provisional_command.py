#!/usr/bin/env python3
"""Which triggers get a provisional 'Analyzing…' placeholder — the "a WORKING session always shows a card"
invariant, and its two boundaries.

A BARE slash-command turn must NOT spawn one (the user 2026-06-29): the planner SKIPS such segments (they
never become goals), so they never get a `placement` — that placeholder would hang forever (the JLD `/usage`
case). NARROWED 2026-07-22: a command that put the MODEL to work (a skill / custom command carrying the real
ask in its args, `/jld <request>`) IS planned now, so it DOES get a placeholder and that placeholder drops on
schedule — the old blanket rule left such a session with no card at all. The discriminator is exact: a
built-in's <local-command-stdout> becomes a SYNTHETIC assistant atom flagged `command`, while model-side atoms
(a skill's skillMd payload, ordinary reply/tool-use) carry no such flag — see jd._seg_command_worked.
But a kernel RESUME turn MUST get one (the user 2026-07-09): a romp-system restart/resume
notice reopens the session's OWN work after a `romp --refresh` or a crash heal, and leaving that actively-
working session cardless is the bug that prompted this — it read WORKING with nothing to click. Safe because,
unlike a command, a system segment IS placed when it ends (plan_units' housekeeping 'work' unit, placed even
on a skip → the placeholder drops). Synthetic transcript only — placeholder UUIDs, hostname TESTHOST, no real
data.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_provcmd", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


def _iso(ep):
    import datetime
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ProvisionalCommand(unittest.TestCase):
    def _session(self, recs):
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, ignore_errors=True)
        p = os.path.join(td, SID + ".jsonl")
        open(p, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        return {"path": p, "sid": SID, "name": "JLD"}

    def test_command_turn_gets_no_provisional_placeholder(self):
        now = int(time.time())
        # a bare /usage command (no output, no model work) — exactly JLD's stuck case
        s = self._session([{"type": "user", "timestamp": _iso(now - 5), "uuid": "c1", "parentUuid": None,
                            "message": {"role": "user", "content": "<command-name>/usage</command-name>"}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNone(card, "a slash-command turn never warrants a provisional placeholder")

    def test_a_worked_command_turn_DOES_get_a_placeholder(self):
        # `/jld <the ask>` — a SKILL / custom command carrying a real request in its args, with the model now
        # working on it. Unlike a bare built-in it IS placed by the planner (_seg_command_worked), so its
        # placeholder drops on schedule instead of hanging. Without this the JLD session ran with NO card at
        # all, not even a provisional one (the user 2026-07-22).
        now = int(time.time())
        s = self._session([
            {"type": "user", "timestamp": _iso(now - 5), "uuid": "c1", "parentUuid": None,
             "message": {"role": "user",
                         "content": "<command-name>/jld</command-name>\n"
                                    "<command-args>design a speech pathology curriculum</command-args>"}},
            {"type": "assistant", "timestamp": _iso(now - 3), "uuid": "a1", "parentUuid": "c1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Working on it…"}],
                         "stop_reason": None}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNotNone(card, "a command that put the model to work warrants a placeholder")
        self.assertEqual(card["column"], "working")
        self.assertTrue(card["provisional"])

    def test_a_real_prompt_still_gets_a_placeholder(self):
        now = int(time.time())
        # a genuine human prompt the planner hasn't placed yet → the placeholder SHOULD appear (control)
        s = self._session([{"type": "user", "timestamp": _iso(now - 5), "uuid": "u1", "parentUuid": None,
                            "promptSource": "typed",
                            "message": {"role": "user", "content": "Please refactor the auth module"}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNotNone(card, "a real unplaced human prompt still surfaces a placeholder")

    def test_kernel_resume_turn_gets_a_placeholder(self):
        # A romp-system RESUME notice (a `romp --refresh` restart or a crash heal) reopens the session's own
        # in-flight work. Its trigger authors 'romp' (not 'human') and carries no romp-goal-id, so the old gate
        # dropped it → the resumed, actively-working session showed WORKING with no card (nimbus, 2026-07-09).
        # Now _seg_system lets it through; it's safe because the segment gets PLACED when it ends.
        now = int(time.time())
        body = ("<!-- romp-injected --><!-- romp-system -->[romp] The kernel restarted and resumed this "
                "session; re-read the tail and pick the work back up where it stopped.")
        s = self._session([{"type": "user", "timestamp": _iso(now - 5), "uuid": "r1", "parentUuid": None,
                            "message": {"role": "user", "content": body}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNotNone(card, "a kernel-resume turn is continued user work → it warrants a placeholder")
        self.assertEqual(card["column"], "working")
        self.assertTrue(card["provisional"])
        # ...but the notice BODY is romp plumbing, never a headline (the user 2026-07-13: the raw nudge,
        # comment markers and all, showed as the Working card's text). The card speaks about the state.
        self.assertEqual(card["text"], "Resuming work after a restart")
        self.assertNotIn("<!--", card["text"])
        self.assertNotIn("[romp]", card["text"])

    def test_marker_comments_never_render_in_the_headline(self):
        # any comment marker riding a real prompt (pasted HTML, a stray romp marker mention) is stripped
        # from the DISPLAY text — markers are plumbing, the headline is for the user (the user 2026-07-13)
        now = int(time.time())
        s = self._session([{"type": "user", "timestamp": _iso(now - 5), "uuid": "u1", "parentUuid": None,
                            "promptSource": "typed",
                            "message": {"role": "user", "content":
                                        "please fix the <!-- some comment --> rendering in the header"}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNotNone(card)
        self.assertNotIn("<!--", card["text"])
        self.assertIn("please fix the", card["text"])

    def test_a_followup_gets_no_provisional_placeholder(self):
        # a follow-up (carries the romp-goal-id marker) files UNDER its already-reopened target goal, so a
        # separate provisional card would just FLASH then vanish. No placeholder (the user 2026-07-01).
        now = int(time.time())
        body = "does the context look right?\n\n<!-- romp-goal-id: %s:g7 -->" % SID
        s = self._session([{"type": "user", "timestamp": _iso(now - 5), "uuid": "u1", "parentUuid": None,
                            "promptSource": "typed",
                            "message": {"role": "user", "content": body}}])
        card = km._provisional_card(s, "JLD", {"bg": "#fff", "fg": "#000"}, SID, True, now, store={})
        self.assertIsNone(card, "a follow-up reopens its target goal — no separate provisional flash")


if __name__ == "__main__":
    unittest.main()
