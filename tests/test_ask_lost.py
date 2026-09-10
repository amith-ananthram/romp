#!/usr/bin/env python3
"""T214 (verified live 2026-09-01): an answer flushed across a kernel restart found no waiting ask
and was silently swallowed while the board flipped to Working. The truth now flows from where it
lives: resolve_ask returns whether a live future was actually waiting, on_ask forwards it, the
kernel's answer sites honor it (tests/test_kernel_card_predict.py holds that half), and the ask's
existence is DURABLE — a pendingAsk reg flag written at present, cleared at resolve, read by the
boot reconcile to ask the resumed session to raise its killed question again. Synthetic only."""
import os
import re
import tempfile
import types
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
sb = load_source("romp_sdk_backend_t214", os.path.join(BIN, "..", "kernel", "sdk_backend.py"))

SID = "aaaa2141-0000-0000-0000-000000000001"


class _Fut:
    def __init__(self, done):
        self._done = done
        self.result = None
    def done(self):
        return self._done
    def set_result(self, v):
        self.result = v


class ResolveTruth(unittest.TestCase):
    """resolve_ask answers 'was anything actually waiting?' — the swallow's root."""

    def _sess(self, fut):
        s = object.__new__(sb.SdkSession)
        s.loop = types.SimpleNamespace(call_soon_threadsafe=lambda f: f())   # run inline for the test
        s._cur_ask_fut = fut
        return s

    def test_no_loop_is_false(self):
        s = object.__new__(sb.SdkSession); s.loop = None
        self.assertFalse(s.resolve_ask("answer", 0))

    def test_no_waiting_future_is_false(self):
        self.assertFalse(self._sess(None).resolve_ask("answer", 0),
                         "the ask died with the old process — nothing was waiting")

    def test_already_resolved_future_is_false(self):
        self.assertFalse(self._sess(_Fut(done=True)).resolve_ask("answer", 0))

    def test_live_future_is_true_and_delivered(self):
        f = _Fut(done=False)
        self.assertTrue(self._sess(f).resolve_ask("answer", 2))
        self.assertEqual(f.result, ("answer", 2))

    def test_on_ask_forwards_the_delivery_outcome(self):
        be = object.__new__(sb.SdkBackend)
        s = self._sess(None)
        be.sessions = {SID: s}
        self.assertFalse(be.on_ask(SID, "answer", 0), "routing is not delivery — the outcome forwards")
        s2 = self._sess(_Fut(done=False))
        be.sessions = {SID: s2}
        self.assertTrue(be.on_ask(SID, "answer", 0))
        self.assertTrue(be.on_ask(SID, "focus", 1), "focus never resolves and never fails")


class DurableAskMarker(unittest.TestCase):
    """The ask's existence survives the process: pendingAsk rides the reg."""

    def _backend(self):
        be = object.__new__(sb.SdkBackend)
        be._pending_ask = {}
        be.regged = []
        be._update_reg = lambda sid, **f: be.regged.append((sid, f))
        be._notify = lambda *a, **k: None
        be._poke = lambda: None
        return be

    def test_present_writes_and_resolve_clears(self):
        be = self._backend()
        sess = types.SimpleNamespace(sid=SID)
        be._emit_ask(sess, {"q": "which port?"})
        self.assertIn((SID, {"pendingAsk": True}), be.regged, "the marker outlives the process")
        be._clear_ask(sess)
        self.assertIn((SID, {"pendingAsk": False}), be.regged, "resolved in this life → nothing owed at boot")

    def test_boot_reconcile_re_presents_and_clears_once(self):
        src = Path(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read_text()
        self.assertIn('ask_died = bool(r.get("pendingAsk"))', src)
        self.assertIn("([ASK_DIED_NOTICE] if ask_died else [])", src, "the rider queues after the resume nudge")
        self.assertIn('reg["pendingAsk"] = False   # asked once per death', src, "one re-raise per death, never a nag loop")
        self.assertIn("if not (cut or queued or dead_tasks or ask_died):", src,
                      "a killed ask alone is reason enough to wake the resume path")

    def test_the_notice_wears_the_sanctioned_mechanics_voice(self):
        body = sb.ASK_DIED_NOTICE
        line = re.sub(r"<!--.*?-->", "", body).strip()
        self.assertTrue(line.startswith("[romp] "), "the sanctioned mechanics prefix — the restart-notice family")
        for word in ("card", "board", "goal", "column", "nudge"):
            self.assertNotIn(word, line.lower(), "no romp nouns beyond the sanctioned name")


if __name__ == "__main__":
    unittest.main()
