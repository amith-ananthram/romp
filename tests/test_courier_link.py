#!/usr/bin/env python3
"""The courier link survives planner-first placement (the user 2026-08-23): a peer-delegate segment
the planner placed before the courier saw it minted no courier goal, so the SENDER's handoff waited
on a completion event that could never fire (12 live handoffs, five ~240h old). Three fixes, all
executed here: (1) the courier's link-only repair attaches links[] to the placed goal's TOP —
never origin, which keeps its born-from meaning; (2) run_propagate completes the sender's tracking
node from links[] exactly as from origin; (3) a DORMANT sender's incomplete handoff converts to the
dead-wait procedural block. SYNTHETIC fixtures only."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

SENDER = "11111111-2222-3333-4444-555555555555"
RECIP = "66666666-7777-8888-9999-000000000000"
MID = "msg-aaaa-1111"
T = 1_787_500_000


def _seed_sender():
    st = jd.load_goals(SENDER)
    st["nodes"][SENDER + ":g1"] = jd.GuardedNode({
        "id": SENDER + ":g1", "text": "↪ delegated to api: build the exporter", "parentId": None,
        "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T, "mt": T,
        "handoff": {"peer": RECIP, "msgId": MID}, "log": []})
    jd.save_goals(SENDER, st)


def _seed_recipient(placed_under_existing=True):
    st = jd.load_goals(RECIP)
    st["nodes"][RECIP + ":g5"] = jd.GuardedNode({
        "id": RECIP + ":g5", "text": "Ship the data layer", "parentId": None,
        "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T, "mt": T, "log": []})
    if placed_under_existing:
        st["placements"]["seg-d1"] = RECIP + ":g5"
    jd.save_goals(RECIP, st)


class CourierLinkRepair(unittest.TestCase):
    def setUp(self):
        # the kernel's judge is one module object shared by every test module in the process, so a store saved at its import-bound GOALDIR outlives the module and reaches every later module's feed for the placeholder sid (T281/T282); a private root for the duration.
        self._td = tempfile.TemporaryDirectory()
        self._state = jd.STATE
        jd._rebind_state(Path(self._td.name))
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]
        _seed_sender()
        _seed_recipient()

    def tearDown(self):
        jd.discover = self._saved
        jd._rebind_state(self._state)
        self._td.cleanup()

    def test_the_store_this_module_saves_does_not_outlive_it(self):
        # The residue pin (T281/T282): a store saved through the shared judge lands under this test's root and
        # the run-wide root (what every later module's feed reads) is exactly as it was.
        shared = Path(self._state) / "goals" / (SENDER + ".json")
        before = (shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None)
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":t282"] = jd.GuardedNode({"id": SENDER + ":t282", "text": "a note", "parentId": None, "nodeComplete": False,
                                                        "blocked": False, "cleared": False, "trail": [], "t": 1, "mt": 1, "log": []})
        jd.save_goals(SENDER, st)
        self.assertTrue((Path(self._td.name) / "goals" / (SENDER + ".json")).exists(), "the store lives under this module's root")
        self.assertEqual((shared.exists(), shared.stat().st_mtime_ns if shared.exists() else None), before,
                         "the run-wide goals directory is untouched by this module")

    def test_link_attaches_to_the_placed_top_and_is_idempotent(self):
        st = jd.load_goals(RECIP)
        self.assertTrue(jd._attach_courier_link(st, "seg-d1", MID))
        st = jd.load_goals(RECIP)
        links = st["nodes"][RECIP + ":g5"].get("links") or []
        self.assertEqual(links, [{"peer": SENDER, "goalId": SENDER + ":g1", "msgId": MID}],
                         "the link rides links[], never origin — born-from stays truthful")
        self.assertFalse(jd._attach_courier_link(st, "seg-d1", MID), "idempotent by msgId")

    def test_no_sender_backref_means_no_link(self):
        st = jd.load_goals(RECIP)
        self.assertFalse(jd._attach_courier_link(st, "seg-d1", "msg-unknown-9999"))
        self.assertNotIn("links", dict(jd.load_goals(RECIP)["nodes"][RECIP + ":g5"]))

    def test_propagate_completes_the_sender_from_links(self):
        st = jd.load_goals(RECIP)
        jd._attach_courier_link(st, "seg-d1", MID)
        st = jd.load_goals(RECIP)
        jd.record_verdict(st, st["nodes"][RECIP + ":g5"], "closer", "done", T + 100, why="shipped")
        jd.rollup_status(st, True)
        jd.save_goals(RECIP, st)
        n = jd.run_propagate(now=T + 200)
        self.assertGreaterEqual(n, 1)
        snd = jd.load_goals(SENDER)["nodes"][SENDER + ":g1"]
        self.assertTrue(snd.get("nodeComplete"), "the handoff checks off from the repaired link")

    def test_a_refless_completed_node_never_kills_the_pass(self):
        # THE 2026-08-23 REGRESSION: the refs loop's body sat OUTSIDE the loop, so the first completed
        # node with NO origin/links hit an unbound name and the exception killed run_propagate — and
        # with it every stage after it in the triage pass (grouper, consolidator, distiller), which is
        # how "everything stuck on Distilling" happened. A plain completed node must be a no-op.
        st = jd.load_goals(RECIP)
        st["nodes"][RECIP + ":g0"] = jd.GuardedNode({
            "id": RECIP + ":g0", "text": "An ordinary finished goal", "parentId": None,
            "nodeComplete": True, "blocked": False, "cleared": False, "trail": [],
            "t": T - 100, "mt": T - 100, "log": []})
        jd.save_goals(RECIP, st)
        jd.run_propagate(now=T + 200)   # must not raise, and must not touch the sender

    def test_every_ref_completes_its_own_sender_node(self):
        # Multi-ref: origin AND a repaired link each complete their sender's tracking node — the
        # mis-indented loop processed only the LAST ref.
        st = jd.load_goals(SENDER)
        st["nodes"][SENDER + ":g2"] = jd.GuardedNode({
            "id": SENDER + ":g2", "text": "↪ delegated to api: second thread of the exporter",
            "parentId": None, "nodeComplete": False, "blocked": False, "cleared": False,
            "trail": [], "t": T, "mt": T, "handoff": {"peer": RECIP, "msgId": "msg-bbbb-2222"}, "log": []})
        jd.save_goals(SENDER, st)
        st = jd.load_goals(RECIP)
        nd = st["nodes"][RECIP + ":g5"]
        nd["origin"] = {"peer": SENDER, "goalId": SENDER + ":g1", "msgId": MID}
        nd["links"] = [{"peer": SENDER, "goalId": SENDER + ":g2", "msgId": "msg-bbbb-2222"}]
        jd.record_verdict(st, nd, "closer", "done", T + 100, why="shipped")
        jd.rollup_status(st, True)
        jd.save_goals(RECIP, st)
        jd.run_propagate(now=T + 200)
        snd = jd.load_goals(SENDER)
        self.assertTrue(snd["nodes"][SENDER + ":g1"].get("nodeComplete"), "the origin's sender node completes")
        self.assertTrue(snd["nodes"][SENDER + ":g2"].get("nodeComplete"), "the link's sender node completes too")

    def test_courier_scan_carries_the_repair_branch(self):
        src = open(os.path.join(BIN, "romp-judge")).read()
        # the scan asks the read-only view whether the link is missing and loads for the write only then (2026-09-09)
        self.assertIn("_courier_link_wanted(cstore, seg[\"id\"], pm0[1]) is not None", src)
        self.assertIn("_attach_courier_link(load_goals(fsid), seg[\"id\"], pm0[1])", src)
        self.assertIn('_seg_peer_kind(seg) == "delegate"', src)


class DormantHandoffConverts(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()      # a private root: see CourierLinkRepair.setUp
        self._state = jd.STATE
        self._shared_before = self._shared_sid_leftovers()   # the run-wide root's state, read before the rebind
        jd._rebind_state(Path(self._td.name))
        _seed_sender()
        d = jd.STATE / "states"
        d.mkdir(parents=True, exist_ok=True)
        (d / (SENDER + ".jsonl")).write_text(json.dumps({"state": "idle", "t": T + 50}) + "\n")
        # the names-registry launch record: what marks a reg-less sid as dead HISTORY (a session the
        # kernel once launched that no backend holds a record of) — without it the corroborator reads
        # the sid as transcript-derived and stands down. No SDK reg and no Codex record exist under
        # this private root, so the corroboration answers true and the sweep files the block.
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SENDER).write_text("web\t~/notes-api\t#3355aa\t#ffffff\n")
        # …and the SDK registry directory EXISTS and is readable: dead history is a registry that can be
        # read and lacks the sid; a missing sdk/ with names on record is blindness, on which the
        # corroborator stands down instead (tests/test_sdk_registry_blind.py)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        km._PREV_ALIVE = None
        self.nudged = {}
        self.addCleanup(self._assert_no_shared_sid_leftovers)   # runs AFTER tearDown: the run-wide root is as it was

    @staticmethod
    def _shared_sid_leftovers():
        # the two files the dead-wait block writes under the shared placeholder sid, as (exists, mtime_ns)
        # at whatever root the judge is bound to: the journal row for SENDER:g1 (append_block) and the
        # nudge record in auto-nudge.json. Either one left at the run-wide root reaches every later
        # goal-store test under that sid in this process: load_goals replays the row onto their fresh g1
        # (blocked) and the distiller takes the staller path instead of distilling. Sixteen test_judge.py
        # tests (the distiller and procedural-block classes) went red when xdist placed them after this one
        # in a worker; the two modules in one process, this one first, reproduced it.
        out = []
        for p in (jd._overrides_dir() / (SENDER + ".jsonl"), jd.STATE / "auto-nudge.json"):
            out.append((p.exists(), p.stat().st_mtime_ns if p.exists() else None))
        return tuple(out)

    def _assert_no_shared_sid_leftovers(self):
        # checked once tearDown has rebound the judge to the run-wide root: both files land under the
        # private root and go with the tempdir, so the run-wide root's pair is exactly as setUp read it
        self.assertEqual(self._shared_sid_leftovers(), self._shared_before,
                         "the sender's journal row or the nudge record reached the run-wide root")

    def tearDown(self):
        jd._rebind_state(self._state)                # the private root goes with the tempdir
        self._td.cleanup()

    def test_dormant_sender_handoff_blocks_with_the_dead_wait_why(self):
        km._dead_wait_sweep(set(), self.nudged, T + 900)
        nd = jd.load_goals(SENDER)["nodes"][SENDER + ":g1"]
        self.assertTrue(nd.get("blocked"), "an unpropagatable handoff on a dead sender needs the user")
        self.assertTrue(str(nd.get("blockWhy") or "").startswith(jd.DEAD_WAIT_WHY_PREFIX))
        self.assertIn("delegation to", nd.get("blockWhy") or "")
        km._dead_wait_sweep(set(), self.nudged, T + 990)
        km._PREV_ALIVE = None
        self.assertEqual(len([k for k in self.nudged if k == SENDER + ":g1"]), 1, "once per node")


if __name__ == "__main__":
    unittest.main()
