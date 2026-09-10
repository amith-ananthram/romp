#!/usr/bin/env python3
"""_distill_session saves its store twice when a mirror top gets its one-shot title: once right after
_title_mirror_tops and again after the distills. The first publish popped the store's CAS base (_baseRev)
and nothing restored it, so the tail save took save_goals' unconditional branch and wrote over anything a
concurrent writer published during the distiller's model call (review 2026-09-06). Pins that the tail
save REBASES: the kernel-side block filed during the call and the distiller's own summary both survive.
SYNTHETIC fixtures only; a private synthetic sid."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_distill_twosave", os.path.join(BIN, "romp-judge"))
em = jd.em

NOW = 1_787_700_000
T0 = NOW - 3600
SID = "d1570001-1111-4222-8333-000000000001"    # private synthetic sid, never the shared placeholder
DONE, MIRROR, OPEN = (SID + ":g%d" % i for i in (1, 2, 3))
MIRROR_WHY = "declared in the agent's own to-do list"   # _title_mirror_tops' mirror-top predicate


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, ps="typed"):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": ps, "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


RECORDS = [uline(T0, "please ship the demo ask", "u1"),
           aline(T0 + 60, "Shipped: the demo ask is done, with a regression test.", "a1", "u1")]


def _node(nid, text, t=T0, **kw):
    nd = {"id": nid, "text": text, "parentId": None, "nodeComplete": False, "blocked": False,
          "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    nd.update(kw)
    return jd.GuardedNode(nd)


class TailSaveRebases(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self.td = tempfile.TemporaryDirectory()
        jd._rebind_state(Path(self.td.name))
        self.path = Path(self.td.name) / (SID + ".jsonl")
        self.path.write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
        s = em.parse_session(str(self.path), rompuuid=SID, candidate_files=[str(self.path)], now=NOW)
        self.segs = [sg["id"] for turn in s["turns"] for sg in em.segments(turn)]
        self._title, self._distill = jd.mirror_title_llm, jd.distill_llm
        jd.mirror_title_llm = lambda *a, **k: "Wire the notes web pane"
        jd.distill_llm = self._distill_with_a_concurrent_writer

    def tearDown(self):
        jd.mirror_title_llm, jd.distill_llm = self._title, self._distill
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        jd._rebind_state(self._saved)
        self.td.cleanup()

    def _distill_with_a_concurrent_writer(self, *a, **k):
        # the kernel's nudge tick runs during the distiller's model call: it blocks the open top and
        # publishes while the distiller holds its already-once-saved store across the call
        ks = jd.load_goals(SID)
        jd.record_verdict(ks, ks["nodes"][OPEN], "nudge", "block", T0 + 200, why="which pane layout?")
        jd.rollup_status(ks, False)
        jd.save_goals(SID, ks)
        self.assertEqual(jd.load_goals(SID)["status"].get(OPEN), "blocked", "premise: the block landed")
        return "BACKGROUND: b.\n\nTAKEAWAY: it shipped."

    def _world(self):
        st = {"rompUuid": SID, "seq": 3, "nodes": {}, "placements": {}, "status": {},
              "placementsV": jd.PLACEMENTS_V}
        st["nodes"][DONE] = _node(DONE, "the finished ask", trail=list(self.segs))
        st["nodes"][MIRROR] = _node(MIRROR, "web pane", why=MIRROR_WHY)      # untitled mirror top
        st["nodes"][OPEN] = _node(OPEN, "tests for the api")
        jd.record_verdict(st, st["nodes"][DONE], "closer", "done", T0 + 100, why="shipped with a test")
        jd.rollup_status(st, False)
        jd.save_goals(SID, st)

    def test_the_tail_save_rebases_onto_a_publish_made_during_the_model_call(self):
        self._world()
        n = jd._distill_session(SID, str(self.path), NOW)
        self.assertEqual(n, 1, "premise: the distiller ran for the done top")
        after = jd.load_goals(SID)
        self.assertEqual(after["nodes"][MIRROR].get("text"), "Wire the notes web pane",
                         "the early save's title landed")
        self.assertEqual(after["nodes"][DONE].get("summary"), "it shipped.", "the tail save's summary landed")
        self.assertTrue(any(e.get("kind") == "block" for e in after["nodes"][OPEN].get("log") or []),
                        "the block published during the model call survives the tail save")
        self.assertEqual(after["status"].get(OPEN), "blocked")


if __name__ == "__main__":
    unittest.main()
