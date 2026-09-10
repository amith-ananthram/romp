#!/usr/bin/env python3
"""A modal "Done" click is answered, always (the user 2026-07-23).

The modal now crosses the sub-goal off the instant you click and waits to hear back, so silence is the
one answer it cannot use. `_resolve_node` returning False previously did nothing and said nothing,
which left an optimistic tick with no state behind it and no way to know.

The two Falses are NOT the same and the ack has to tell them apart: a node that is already complete
means the state the click asked for already holds (agreement), while a node missing from the store is a
real failure the user needs to see.

Synthetic only — a hermetic goal store, placeholder uuids, no session transcript needed.
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model_ack", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_ack", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_ack", os.path.join(BIN, "romp-kernel"))
# The kernel's OWN judge module, not a second copy of it. Loading another instance here gives it a
# different jd.STATE, so a store this test wrote would be invisible to the handler under test — which
# passes when the file runs alone and fails inside the full suite, where module identity differs.
jd = km.jd

SID = "11111111-2222-3333-4444-555555555555"
TOP = SID + ":g1"
SUB = SID + ":g2"


class NodeOverrideAck(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._real_send = km._send_to_app
        km._send_to_app = lambda app, msg: self.sent.append((app, msg))
        # The store below is saved through the kernel's judge, which is one module object shared by every test
        # module in the process: at its import-bound GOALDIR the store outlived this module and became part of
        # every later module's feed for the placeholder sid (T281). Rebind to a private root for the duration;
        # the handler under test reads the same module, so it still sees the store.
        self._td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        shared = Path(self._state) / "goals" / (SID + ".json")     # the run-wide store, as it is BEFORE this test writes
        self._shared_before = (shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None)
        jd._rebind_state(Path(self._td.name))
        store = {"nodes": {
            TOP: {"id": TOP, "text": "ship the notes API", "parentId": None, "t": 1, "mt": 1},
            SUB: {"id": SUB, "text": "decide the auth story", "parentId": TOP, "t": 1, "mt": 1},
        }}
        jd.save_goals(SID, store)

    def tearDown(self):
        km._send_to_app = self._real_send
        jd._rebind_state(self._state)
        self._td.cleanup()

    def test_the_fixture_store_does_not_outlive_the_module(self):
        # The residue pin (T281): setUp's store lives under this test's root; the run-wide goals directory (what
        # every later module's feed reads) carries nothing this module wrote.
        self.assertTrue((Path(self._td.name) / "goals" / (SID + ".json")).exists())
        shared = Path(self._state) / "goals" / (SID + ".json")
        self.assertEqual((shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None), self._shared_before,
                         "the run-wide goals directory is exactly as it was before this module's save")

    def _resolve(self, node_id):
        km.Handler._dispatch_ws(None, {"type": "nodeOverride", "sid": SID, "nodeId": node_id,
                                       "op": "resolve"}, {"app": "feed", "active": None})
        acks = [m for a, m in self.sent if a == "feed" and m.get("type") == "nodeOverrideResult"]
        self.assertEqual(len(acks), 1, "exactly one ack per click, whatever the outcome")
        return acks[0]

    def test_a_resolve_that_applies_acks_ok(self):
        ack = self._resolve(SUB)
        self.assertEqual((ack["ok"], ack["nodeId"], ack["op"]), (True, SUB, "resolve"))
        self.assertEqual(ack["error"], "")
        self.assertTrue(jd.load_goals(SID)["nodes"][SUB].get("nodeComplete"), "and it really applied")

    def test_an_already_complete_node_is_AGREEMENT_not_a_failure(self):
        # _resolve_node returns False here too, but the state the click asked for already holds, so
        # reporting a failure would revert a tick that is actually correct.
        self._resolve(SUB)
        self.sent.clear()
        ack = self._resolve(SUB)
        self.assertTrue(ack["ok"], "a second click on a crossed-off sub-goal is not an error")
        self.assertEqual(ack["error"], "")

    def test_a_node_missing_from_the_store_acks_a_REASON(self):
        ack = self._resolve(SID + ":g404")
        self.assertFalse(ack["ok"])
        self.assertIn("no longer in this session", ack["error"],
                      "the client shows this verbatim, so it has to read as an explanation")

    def test_the_ack_never_depends_on_the_resolve_succeeding(self):
        # The whole point: the client is waiting. Every outcome sends exactly one ack naming the node.
        for nid in (SUB, SUB, SID + ":g404"):
            self.sent.clear()
            ack = self._resolve(nid)
            self.assertEqual(ack["nodeId"], nid)
            self.assertIn("ok", ack)
            self.assertIn("error", ack)

    def test_a_clear_op_is_untouched_by_the_resolve_ack(self):
        km.Handler._dispatch_ws(None, {"type": "nodeOverride", "sid": SID, "nodeId": SUB, "op": "clear"},
                                {"app": "feed", "active": None})
        kinds = [m.get("type") for a, m in self.sent if a == "feed"]
        self.assertNotIn("nodeOverrideResult", kinds, "the ack belongs to resolve only")


if __name__ == "__main__":
    unittest.main()
