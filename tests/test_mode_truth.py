#!/usr/bin/env python3
"""Permission-mode truth (T139, 2026-08-28; the exp specimen: a session in CONFIRMED
bypassPermissions got a permission ask for a plain main-thread Bash and sat blocked eleven hours).

Two truth rules, both the T124 family:
  * THE DECLARED-INTENT GUARD: the SDK's contract says bypass auto-approves every call before
    can_use_tool is consulted (deny rules aside) — when the CLI consults anyway (the specimen:
    CLI 2.1.221, once, against its own contract), romp — the registered permission authority —
    re-imposes the declared intent: auto-allow + a problems ring, never a block. Other modes keep
    asking: a consult under default/plan/acceptEdits is the CLI honestly delegating what its own
    evaluation could not auto-decide.
  * A REFUSED LIVE SWITCH REVERTS: set_mode flips the displayed mode optimistically and fires
    set_permission_mode; a refusal used to only log, leaving the switcher asserting a mode the CLI
    never accepted. Every layer now reverts to the last CONFIRMED mode (snapshot, session, reg —
    the next connect must not re-apply the refused pick) and the problems ring names it.

Hermetic state; synthetic sids; the SDK client is stubbed."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
# the callback imports PermissionResult* lazily — a hermetic box has no real SDK, so a stub module
# stands in AT CALL TIME ONLY (installed per-test in _Harness.setUp, removed in tearDown): a
# module-scope sys.modules entry leaked into the whole pytest process and made every real-SDK-gated
# test elsewhere stop skipping and run against the stub (30 failures in the full suite).
_fake = types.ModuleType("claude_agent_sdk")
class _PRA:
    def __init__(self, behavior="allow", updated_input=None, updated_permissions=None):
        self.behavior, self.updated_input, self.updated_permissions = behavior, updated_input, updated_permissions
class _PRD:
    def __init__(self, behavior="deny", message="", interrupt=False):
        self.behavior, self.message, self.interrupt = behavior, message, interrupt
_fake.PermissionResultAllow = _PRA
_fake.PermissionResultDeny = _PRD
_fake.PermissionUpdate = lambda **kw: kw
sb = load_source("romp_sdk_backend_modetruth", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-00000000d139"


class _Harness(unittest.TestCase):
    def setUp(self):
        self._sdk_before = sys.modules.get("claude_agent_sdk")
        if self._sdk_before is None:
            sys.modules["claude_agent_sdk"] = _fake   # call-time only; removed in tearDown
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "spec", "cwd": "/tmp", "mode": "bypassPermissions"})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = self.s

    def tearDown(self):
        if self._sdk_before is None:
            sys.modules.pop("claude_agent_sdk", None)

    def _problems(self):
        return [p["text"] for p in self.be.problems(20)]


class DeclaredIntentGuard(_Harness):
    def test_a_consult_under_bypass_auto_allows_and_rings(self):
        # the specimen, replayed: perm_mode is CONFIRMED bypass; the CLI consults anyway
        self.s.perm_mode = "bypassPermissions"
        res = asyncio.run(self.s._can_use_tool("Bash", {"command": "mkdir -p x"}, object()))
        self.assertEqual(getattr(res, "behavior", None), "allow",
                         "romp re-imposes the declared intent — never a block under bypass")
        self.assertTrue(any("bypassPermissions" in t and "contract" in t for t in self._problems()),
                        "…and the CLI's contract breach is VISIBLE, never silent: %r" % self._problems())

    def test_other_modes_still_ask(self):
        # a consult under default is the CLI honestly delegating — the ask machinery must engage.
        # The ask blocks on user input, so run it with a pre-resolved answer future.
        self.s.perm_mode = "default"
        async def drive():
            loop = asyncio.get_running_loop()
            self.s.loop = loop
            task = asyncio.ensure_future(self.s._can_use_tool("Bash", {"command": "x"}, object()))
            await asyncio.sleep(0.05)          # let it park on the ask
            self.assertIsNotNone(self.s._cur_ask_fut, "the ask engaged (the session marked permission)")
            self.s.resolve_ask("answer", "1")  # the user allows
            return await task
        res = asyncio.run(drive())
        self.assertEqual(getattr(res, "behavior", None), "allow")
        self.assertFalse(any("contract" in t for t in self._problems()),
                         "an honest consult rings nothing")


class RefusedSwitchReverts(_Harness):
    class _RefusingClient:
        async def set_permission_mode(self, mode):
            raise RuntimeError("control request rejected")

    class _AckingClient:
        async def set_permission_mode(self, mode):
            return None

    def test_refusal_reverts_every_layer_and_rings(self):
        # the real refused-live fixture (probed on CLI 2.1.221): 'auto' is model-dependent —
        # 'Cannot set permission mode to auto: auto mode unavailable for this model'
        self.s.perm_mode = "acceptEdits"       # the last CONFIRMED mode
        self.s.client = self._RefusingClient()
        async def drive():
            self.s.loop = asyncio.get_running_loop()
            self.assertTrue(self.be.set_mode(SID, "auto"))
            self.assertEqual(self.s.perm_mode, "auto", "the optimistic flip shows at once")
            await asyncio.sleep(0.05)          # the control request refuses
        asyncio.run(drive())
        self.assertEqual(self.s.perm_mode, "acceptEdits", "the snapshot reverted to the last confirmed mode")
        self.assertEqual(self.s.mode, "acceptEdits", "…and the connect-time mode")
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("mode"), "acceptEdits",
                         "…and the registry — the next connect must not re-apply the refused pick")
        self.assertTrue(any("did NOT apply" in t and "reverted" in t for t in self._problems()),
                        "the failed switch is unmissable: %r" % self._problems())

    def test_a_bypass_pick_applies_via_reconnect_never_the_live_call(self):
        # probed on CLI 2.1.221 (T139): set_permission_mode INTO bypass is refused unless the
        # process was LAUNCHED with --dangerously-skip-permissions — so the pick reconnects
        # (effort's pattern; the relaunch carries the mode) instead of firing a doomed live call
        self.s.perm_mode = "acceptEdits"
        calls = []
        self.s.set_mode_live = lambda mode, prev="default": calls.append(("live", mode))
        self.s.request_reconnect = lambda: calls.append(("reconnect",))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(calls, [("reconnect",)], "bypass = reconnect, never the refused live call")
        self.assertEqual(self.s.perm_mode, "bypassPermissions")
        calls.clear()
        self.s.perm_mode = "bypassPermissions"
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(calls, [("live", "default")], "leaving bypass switches live (the flag only gates entry)")

    def test_an_acked_switch_stays(self):
        self.s.perm_mode = "acceptEdits"
        self.s.client = self._AckingClient()
        async def drive():
            self.s.loop = asyncio.get_running_loop()
            self.be.set_mode(SID, "plan")      # plan ACKs live (probed)
            await asyncio.sleep(0.05)
        asyncio.run(drive())
        self.assertEqual(self.s.perm_mode, "plan")
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("mode"), "plan")
        self.assertFalse(any("did NOT apply" in t for t in self._problems()))


if __name__ == "__main__":
    unittest.main()
