#!/usr/bin/env python3
"""The closer's DONE-ANCHOR appends the recap segment only to goals the turn actually worked on
(the user 2026-08-14). A rider ruled from goal history — steps-finished, starved, status-report,
lift — keeps its organic trail, whose tail is where its work truly happened. Before this, every
resolved rider's trail gained the RULING turn's tail segment, so the read side (work anchor =
trail[-1]; the completed card's summary pin = newest subtree tail) deep-linked the card to the
ruling turn's unrelated prose: a summary click landed on later chatter minutes below the answer
the card was about.

All fixtures synthetic (placeholder UUIDs, invented text).
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_da", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_da", os.path.join(BIN, "romp-judge"))

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": text}, "promptSource": "typed"}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def _turn():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / (SID + ".jsonl")
        recs = [uline(T0, "wire the widget end to end", "u1"),
                aline(T0 + 20, "Shipped: the widget wiring merged and deployed.", "a1", "u1")]
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=NOW)["turns"][0]


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _row(kind, ev, src="closer", why=None, at=None):
    return {"ev_t": ev, "src": src, "kind": kind, **({"why": why} if why else {}),
            "at": at if at is not None else ev}


def _node(s, g, text, parent=None, log=None, **extra):
    nd = {"id": SID + ":" + g, "text": text,
          "parentId": (SID + ":" + parent) if parent else None,
          "nodeComplete": False, "blocked": False, "cleared": False, "trail": [], "t": T0,
          "log": log or []}
    nd.update(extra)
    s["nodes"][nd["id"]] = nd
    return nd


# an OLD segment id, the shape _segs mints (<sid>:<t>:<hash>): the rider's real work, filed long
# before the audited turn — the anchor the read side must keep pointing at
WORK_SEG = SID + ":%d:aaaa1111" % (T0 - 500)


class DoneAnchorScope(unittest.TestCase):
    def setUp(self):
        self._llm = jd.closer_llm
        self.turn = _turn()

    def tearDown(self):
        jd.closer_llm = self._llm

    def test_a_turn_menu_node_gains_the_recap(self):
        # the 2026-06-17 design case, unchanged: the turn's own goal deep-links to its recap tail
        s = _store()
        nd = _node(s, "g1", "wire the widget end to end", trail=[WORK_SEG])
        seg = jd._segs(self.turn, s)[-1]
        s["placements"][seg["id"]] = nd["id"]          # this turn's work IS g1's work
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [{"goal": 1, "why": "it shipped"}], "block": []}'
        newly = jd._close_turn(s, self.turn)
        self.assertEqual(newly, [nd["id"]])
        self.assertEqual(nd["trail"][-1], seg["id"],
                         "a goal resolved from its own turn anchors to that turn's recap")

    def test_a_steps_finished_rider_keeps_its_organic_trail(self):
        # the observed bug's shape: the goal rides the menu on goal history (here the steps-finished
        # channel), the closer rules it done on an unrelated turn — its trail must NOT gain that
        # turn's tail, or the card's work anchor and summary pin re-aim at the ruling turn's prose
        s = _store()
        nd = _node(s, "g1", "explain the widget design decisions", trail=[WORK_SEG])
        _node(s, "g2", "answered all four design questions", parent="g1",
              log=[_row("done", T0 + 50)], nodeComplete=True, trail=[WORK_SEG])   # filed after the
        #                                       parent's mint → nominates (the look-stamp default is t)
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [{"goal": 1, "why": "history shows it delivered"}], "block": []}'
        newly = jd._close_turn(s, self.turn)
        self.assertEqual(newly, [nd["id"]], "the rider's done still lands")
        self.assertEqual(nd["trail"], [WORK_SEG],
                         "a history-ruled rider keeps its organic trail — the ruling turn's recap "
                         "holds none of its work")

    def test_a_lift_rider_keeps_its_organic_trail(self):
        # same guarantee on the lift channel: unblocker ruled the wait over, the closer's rider
        # ruling must not stamp the audited turn's tail onto the goal
        s = _store()
        nd = _node(s, "g1", "explain the widget design decisions", trail=[WORK_SEG],
                   log=[_row("block", T0 + 100, src="planner", why="add the caveat too?"),
                        _row("unblock", T0 + 150, src="unblocker",
                             why="answered in passing — the full explanation landed")])
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [{"goal": 1, "why": "the history shows the answer shipped"}], "block": []}'
        newly = jd._close_turn(s, self.turn)
        self.assertEqual(newly, [nd["id"]], "the lift-ridden done still lands")
        self.assertEqual(nd["trail"], [WORK_SEG],
                         "a lift-ridden rider keeps its organic trail")

    def test_a_blocked_turn_menu_node_gains_the_recap_too(self):
        # block shares the resolved set: the turn's own goal blocking at turn-end anchors its brief
        # to the recap, as ever
        s = _store()
        nd = _node(s, "g1", "wire the widget end to end", trail=[WORK_SEG])
        seg = jd._segs(self.turn, s)[-1]
        s["placements"][seg["id"]] = nd["id"]
        jd.closer_llm = lambda tt, mt, *_a: '{"done": [], "block": [{"goal": 1, "why": "which env?"}]}'
        jd._close_turn(s, self.turn)
        self.assertEqual(nd["trail"][-1], seg["id"],
                         "a goal blocked from its own turn anchors to that turn's recap")


if __name__ == "__main__":
    unittest.main()
