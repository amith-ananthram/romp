#!/usr/bin/env python3
"""A drive op naming a session this kernel doesn't have must FAIL LOUDLY, never degrade into a no-op.

The user 2026-07-29: on a board merging two kernels, a reply addressed to the wrong one reached a kernel
that owns no such session. Sessions.backend_for() falls through to tmux for any unrecognized sid, and
TmuxBackend.send then types at a pane named after the sid — with no such pane, the keystrokes evaporate
with nothing raised and nothing logged, so typed messages simply ceased to exist. Per the repo's
fail-loudly rule, an op we cannot deliver has to say so: a modal in the pane that fired it, the text kept
verbatim on disk so nothing typed is lost, and a line in the kernel log.
"""
import json
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_foreign", os.path.join(BIN, "romp-kernel"))

OURS = "11111111-2222-3333-4444-555555555555"
THEIRS = "99999999-8888-7777-6666-555555555555"   # lives on another machine's kernel


class KernelKnows(unittest.TestCase):
    """The names registry is the authority, and it OUTLIVES the session — so 'we don't have it' means
    never created here, not merely 'not running'."""

    def setUp(self):
        self._name_of, self._sdk = km._name_of, km._sdk
        km._name_of = lambda sid: "web" if sid == OURS else None
        km._sdk = lambda: None

    def tearDown(self):
        km._name_of, km._sdk = self._name_of, self._sdk

    def test_a_named_session_is_ours_even_when_long_dead(self):
        # the registry entry survives the session, so a send that REVIVES a dormant tab still goes through
        self.assertTrue(km._kernel_knows(OURS))

    def test_a_foreign_sid_is_not_ours(self):
        self.assertFalse(km._kernel_knows(THEIRS))

    def test_empty_is_not_ours(self):
        self.assertFalse(km._kernel_knows(""))
        self.assertFalse(km._kernel_knows(None))

    def test_a_session_mid_launch_is_ours_via_the_sdk(self):
        # spawned but not yet written to the registry — never call that foreign
        km._name_of = lambda sid: None
        km._sdk = lambda: type("B", (), {"owns": staticmethod(lambda s: s == OURS)})()
        self.assertTrue(km._kernel_knows(OURS))
        self.assertFalse(km._kernel_knows(THEIRS))


class RefusesForeignDriveOps(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s))}
        self._name_of, self._sdk = km._name_of, km._sdk
        km._name_of = lambda sid: "web" if sid == OURS else None
        km._sdk = lambda: None
        self._backend_for = km.Sessions.backend_for
        self.reached = []
        km.Sessions.backend_for = staticmethod(
            lambda sid: type("B", (), {"send": lambda _s, s, t: self.reached.append((s, t))})())

    def tearDown(self):
        km._name_of, km._sdk = self._name_of, self._sdk
        km.Sessions.backend_for = staticmethod(self._backend_for)

    def test_a_send_to_a_foreign_session_never_reaches_a_backend(self):
        handled = km._drive({"type": "sendMessage", "id": THEIRS, "text": "did you get this?"}, self.client)
        self.assertTrue(handled, "the op is CONSUMED — refused, not passed on to be silently retried")
        self.assertEqual(self.reached, [], "nothing was handed to a backend")

    def test_the_refusal_reaches_the_pane_that_fired_it_as_an_err_not_a_warn(self):
        km._drive({"type": "sendMessage", "id": THEIRS, "text": "did you get this?"}, self.client)
        self.assertEqual(len(self.sent), 1)
        msg = self.sent[0]
        # `err` (a modal you must dismiss), NOT `warn` (a toast that fades in 12s and can be missed)
        self.assertEqual(msg["type"], "err")
        self.assertIn("not delivered", msg["title"])
        self.assertIn("Nothing was sent", msg["text"])
        self.assertIn(THEIRS, msg["text"], "names the session it could not find, so the cause is diagnosable")
        # the typed text rides back so the user can recover it — the composer cleared it on Enter
        self.assertEqual(msg["copy"], "did you get this?")
        # …and the sid rides along, so the shell's error-center entry says WHICH session it was meant for
        self.assertEqual(msg["sid"], THEIRS)
        # …and the REQUEST it answers (review find, 2026-09-08): the feed latches a button on the click (Retry →
        # "Retrying…", Continue → "Sent") and re-arms it on the kernel's reply for THAT post, never every latch
        # the session holds, and never on a clock, so the reply names the op and the card it was for
        self.assertEqual((msg["op"], msg["itemId"]), ("sendMessage", ""))

    def test_a_card_reply_is_refused_the_same_way(self):
        # the exact shape that lost real messages: askFollowUp derives its sid from the itemId
        km._drive({"type": "askFollowUp", "itemId": THEIRS + ":g4", "text": "and the fix?"}, self.client)
        self.assertEqual(self.reached, [])
        self.assertEqual(self.sent[0]["type"], "err")
        self.assertEqual(self.sent[0]["copy"], "and the fix?")
        self.assertIn("reply", self.sent[0]["title"])
        self.assertEqual((self.sent[0]["op"], self.sent[0]["itemId"]), ("askFollowUp", THEIRS + ":g4"),
                         "the card's own latch is the one this refusal releases")

    def test_our_own_session_is_untouched(self):
        km._drive({"type": "sendMessage", "id": OURS, "text": "hello"}, self.client)
        self.assertEqual(self.reached, [(OURS, "hello")], "a session we own still gets its message")
        self.assertEqual(self.sent, [], "and no error is raised for it")

    def test_a_non_drive_op_still_falls_through_untouched(self):
        # UI/nav ops carry no session to own — they must keep reaching _dispatch_ws
        self.assertFalse(km._drive({"type": "setColormap", "name": "viridis"}, self.client))
        self.assertEqual(self.sent, [])

    def test_the_text_is_kept_verbatim_on_disk_so_nothing_typed_is_lost(self):
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as d:
            saved = km.jd.STATE
            km.jd.STATE = pathlib.Path(d)
            try:
                km._drive({"type": "sendMessage", "id": THEIRS, "text": "a paragraph I do not want to retype"},
                          self.client)
                rows = [json.loads(x) for x in (pathlib.Path(d) / "undelivered.jsonl").read_text().splitlines()]
            finally:
                km.jd.STATE = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "a paragraph I do not want to retype")
        self.assertEqual(rows[0]["sid"], THEIRS)
        self.assertEqual(rows[0]["op"], "sendMessage")


if __name__ == "__main__":
    unittest.main()
