#!/usr/bin/env python3
"""Dismiss-dialog action (the user 2026-07-16): when a tmux session hits its monthly spend cap MID-TURN,
the CLI drops into an interactive menu ("What do you want to do? / Adjust monthly spend limit / …") that
eats every keystroke as navigation — so an injected "retry" vanishes into the menu and Retry can never
unblock the session. The real unblock is Esc (cancel — no billing change). The chat's spend-cap card now
offers "Dismiss dialog" on tmux, wired to a dismissDialog drive op → TmuxBackend.dismiss_dialog, which
VERIFIES the menu is up (so a stale click can't Esc a normal prompt) then sends Escape, never Enter.

XDG_STATE_HOME is redirected before the kernel loads so no test state leaks into the live store."""
import os
import re
import tempfile
import unittest
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_dismiss", os.path.join(BIN, "romp-kernel"))

# a faithful capture of the spend-cap modal the CLI shows (invented balance/reset — no real data)
MODAL = (
    "  DELETE /notes/<id> returns 204 No Content\n"
    "▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔\n"
    "   What do you want to do?                       Usage credit balance: $12.34\n"
    "   ❯ Adjust monthly spend limit: Unlimited              ← or → to set a limit\n"
    "     Wait for limit to reset              Resets 1:30pm (America/Los_Angeles)\n"
    "     Upgrade to Max for higher session limits every month\n"
    "   Enter to confirm · Esc to cancel\n"
)
# a normal prompt that merely MENTIONS the phrase (a message quoting the earlier notice) — NOT the menu
PLAIN = ("❯ I hit my monthly spend limit earlier, raise it at claude.ai/settings/usage\n"
         "━━━ web ━━━\n❯ \n")


class SpendDialogDetection(unittest.TestCase):
    def test_detects_the_live_modal(self):
        self.assertTrue(km._spend_dialog_showing(MODAL))

    def test_ignores_conversation_that_only_mentions_the_phrase(self):
        # "spend limit" in prose must not read as a live menu — requires the title + menu chrome
        self.assertFalse(km._spend_dialog_showing(PLAIN))

    def test_ignores_empty_capture(self):
        self.assertFalse(km._spend_dialog_showing(""))


class _FakeTmux(km.TmuxBackend):
    """A TmuxBackend with the two raw-tmux primitives stubbed, so dismiss_dialog can be exercised
    without a real tmux server."""
    def __init__(self, pane):
        self._pane = pane
        self.keys_sent = []

    def capture(self, name, join=False, colour=False, t=2.5):
        return self._pane

    def send_keys(self, name, *keys, t=3):
        self.keys_sent.append(keys)


class DismissDialog(unittest.TestCase):
    def setUp(self):
        # _name_of maps sid→tmux name; stub it so dismiss_dialog resolves without a live session
        self._orig = km._name_of
        km._name_of = lambda sid: "web"

    def tearDown(self):
        km._name_of = self._orig

    def test_sends_escape_when_the_modal_is_up(self):
        be = _FakeTmux(MODAL)
        ok, err = be.dismiss_dialog("sid-web")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(be.keys_sent, [("Escape",)])   # Esc (cancel), never Enter (a billing change)

    def test_refuses_when_no_dialog_is_showing(self):
        be = _FakeTmux(PLAIN)
        ok, err = be.dismiss_dialog("sid-web")
        self.assertFalse(ok)
        self.assertIn("no spend-limit dialog", err)
        self.assertEqual(be.keys_sent, [])              # nothing fired into a normal prompt


class DriveOpArm(unittest.TestCase):
    def test_dismiss_dialog_is_a_drive_op(self):
        import inspect
        src = inspect.getsource(km._drive)
        self.assertIn('"dismissDialog"', src)   # in ID_OPS → routed by session id
        self.assertIn('elif t == "dismissDialog":', src)
        # gated on the backend actually having the method (tmux-only, like unqueue); refusal warn-toasts
        self.assertIn('if hasattr(be, "dismiss_dialog")', src)
        self.assertIn('client["send"](json.dumps({"type": "warn", "text": derr}))', src)


if __name__ == "__main__":
    unittest.main()
