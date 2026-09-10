#!/usr/bin/env python3
"""The tmux backend's OFFER is a kernel setting (T288, the user 2026-09-09): "Claude Code (tmux)" appears in the +
picker and the gear's Default backend list only while STATE/tmux-backend reads "on"; it is off by default. It rides
the judge-knob machinery: validated before it is written (only "on" / "off"), gesture-stamped, reported by /version
(top level and the cross-machine settings dict), applied and acked by /judge-settings, propagated to every linked
kernel from the socket op, and carried on the picker's sessionList reply as a boolean. The setting gates the OFFER
alone: nothing here touches how a tmux session runs.

Hermetic: STATE is a temp dir per test; the propagation thread runs inline; no sockets.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading as _real_threading
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_tmuxbackend", os.path.join(BIN, "romp-kernel"))
jd = km.jd
T_OLD, T_NEW = 1_700_000_000_000, 1_700_000_360_000


class _InlineThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target, self._args, self._kwargs = target, args, (kwargs or {})
    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd.STATE = Path(self._td)
        jd._state_cache.clear()
        self.propagated = []
        self._saved_threading = km.threading
        ns = {k: getattr(_real_threading, k) for k in dir(_real_threading) if not k.startswith("__")}
        ns["Thread"] = _InlineThread   # the fan-out runs inline, so a test sees every propagation call at once
        km.threading = types.SimpleNamespace(**ns)
        self._saved_prop = km._propagate_judge_settings
        km._propagate_judge_settings = lambda body: self.propagated.append(body)

    def tearDown(self):
        km._propagate_judge_settings = self._saved_prop
        km.threading = self._saved_threading
        jd.STATE = self._saved_state
        jd._state_cache.clear()
        shutil.rmtree(self._td, ignore_errors=True)

    def _ws(self, msg):
        sent = []
        client = {"send": lambda s: sent.append(json.loads(s)), "alive": True}
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(types.SimpleNamespace(), msg, client)
        return sent


class Default(_Base):
    def test_off_by_default_everywhere_it_is_read(self):
        self.assertEqual(jd._state_str("tmux-backend", "off"), "off")
        v = km._version_info()
        self.assertEqual(v["tmuxBackend"], "off", "/version top level")
        self.assertEqual(v["settings"]["tmuxBackend"], "off", "the cross-machine settings dict (the gear's mixed marks)")
        self.assertIn("tmux-backend", v["settingsGt"], "its stamp rides with the other stores'")
        self.assertEqual(km._apply_judge_settings({})["tmuxBackend"], "off", "the /judge-settings ack")
        self.assertIn("tmuxBackend", dict(km._JUDGE_SETTING_FIELDS))
        self.assertIn("tmux-backend", km._GT_STORES)
        # the picker's sessionList reply: a boolean, False until the setting is on
        reply = [m for m in self._ws({"type": "requestSessions"}) if m.get("type") == "sessionList"]
        self.assertEqual(len(reply), 1)
        self.assertIs(reply[0]["tmuxBackend"], False)


class SetAndClear(_Base):
    def test_only_on_and_off_are_stored(self):
        self.assertIsNotNone(km._set_tmux_backend("on"))
        self.assertEqual((jd.STATE / "tmux-backend").read_text(), "on")
        self.assertEqual(km._version_info()["tmuxBackend"], "on")
        for bad in ("yes", "true", "1", "", "ON"):
            self.assertIsNone(km._set_tmux_backend(bad), "%r is refused unwritten" % bad)
        self.assertEqual((jd.STATE / "tmux-backend").read_text(), "on", "the refused values wrote nothing")
        self.assertIsNotNone(km._set_tmux_backend("off"))
        self.assertEqual(km._version_info()["tmuxBackend"], "off")

    def test_the_settings_route_applies_and_acks(self):
        res = km._apply_judge_settings({"tmuxBackend": "on"})
        self.assertEqual(res["tmuxBackend"], "on")
        self.assertEqual(jd._state_str("tmux-backend", "off"), "on")
        res = km._apply_judge_settings({"tmuxBackend": "maybe"})
        self.assertEqual(res["tmuxBackend"], "on", "an invalid value is ignored and the ack shows what stands")
        res = km._apply_judge_settings({"tmuxBackend": "off"})
        self.assertEqual(res["tmuxBackend"], "off")

    def test_the_socket_op_stores_on_off_propagates_and_orders_by_gesture(self):
        # the gear's checkbox posts a boolean; the kernel stores on/off and fans the applied value out
        self.assertEqual(self._ws({"type": "setTmuxBackend", "enabled": True, "gt": T_NEW}), [])
        self.assertEqual(jd._state_str("tmux-backend", "off"), "on")
        self.assertEqual(self.propagated, [{"tmuxBackend": "on", "gt": T_NEW}])
        reply = [m for m in self._ws({"type": "requestSessions"}) if m.get("type") == "sessionList"]
        self.assertIs(reply[0]["tmuxBackend"], True, "the picker's reply now offers it")
        # an OLDER gesture stands down: nothing applied, nothing propagated, the delivering socket hears it
        sent = self._ws({"type": "setTmuxBackend", "enabled": False, "gt": T_OLD})
        self.assertEqual(jd._state_str("tmux-backend", "off"), "on")
        self.assertEqual(len(self.propagated), 1)
        self.assertEqual([m["setting"] for m in sent if m.get("type") == "settingStale"], ["tmux-backend"])
        # a newer one applies and turns it off again
        self._ws({"type": "setTmuxBackend", "enabled": False, "gt": T_NEW + 1})
        self.assertEqual(jd._state_str("tmux-backend", "off"), "off")
        self.assertEqual(self.propagated[-1], {"tmuxBackend": "off", "gt": T_NEW + 1})


if __name__ == "__main__":
    unittest.main()
