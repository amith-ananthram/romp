#!/usr/bin/env python3
"""A stale live echo sits where its send time belongs, never among later rows (T344, the user 2026-09-11, who
saw a 10:28 PM row between 7:05 AM rows). The kernel keeps its own copies of sent messages as LIVE echo atoms
(mirrored in the registry's echoes and reseeded at boot) until the transcript lands their text; the chat merge
(_merge_live_atoms) used to put every fresh echo into the chat's LAST turn, sorted by its own send time, so a
romp notice sent yesterday whose text never landed sat among today's rows stamped with yesterday's clock, and
the day walk read the step back as a day boundary (T339 closed the walk's side; this is the producer).

The rule pinned here: a fresh echo stamped at or after the last turn's start stays in the last turn (a pending
send is always newer than the transcript); an echo OLDER than that is placed by time: into the turn whose
window [t, end] holds it, or, when it falls in the gap between two turns (or before the first), into a closed
synthetic turn of its own at that place, one per gap, so the rows the chat reads are in time order and the day
walk just works. Live stream atoms (a reply in flight) and command feedback stay in the last turn as before.
SYNTHETIC fixtures only: a private synthetic sid, the notes-api demo world, invented notice text."""
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_stale_echo", os.path.join(BIN, "romp-kernel"))

SID = "7e7e7e7e-8f8f-4a9a-b0b0-c1c1c1c1c1c1"      # private synthetic sid
DAY = 86400
T_DAY1 = 1_800_000_000                            # the earlier day's rows
T_DAY2 = T_DAY1 + DAY                             # the later day's rows
NOTICE = "[romp] The condition you asked romp to watch now HOLDS: the notes-api search suite has its verdict."


def _user(uuid, t, text, author="human"):
    return {"type": "user", "uuid": uuid, "t": t, "author": author, "parentUuid": None, "session_id": SID,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def _asst(uuid, t, text):
    return {"type": "assistant", "uuid": uuid, "t": t, "author": "assistant", "parentUuid": None, "session_id": SID,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def _turn(tid, atoms, ended=True):
    return {"id": tid, "trigger": {"uuid": atoms[0]["uuid"]}, "t": atoms[0]["t"], "end": atoms[-1]["t"], "ended": ended,
            "atoms": list(atoms)}


def _echo(t, text=NOTICE, key="echo:" + "e" * 32, author="romp", **extra):
    a = {"type": "user", "uuid": key, "session_id": SID, "t": t, "parentUuid": None, "author": author,
         "_echo_text": text, "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    a.update(extra)
    return a


def _stream(t, uuid="live-reply-1"):
    return {"type": "assistant", "uuid": uuid, "t": t, "author": "assistant", "session_id": SID, "parentUuid": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "still working on it"}]}}


class _Backend:
    """The owning backend as the merge sees it: a live tail and a prune that retires nothing."""

    def __init__(self, live):
        self.live = list(live)
        self.pruned = []

    def live_atoms(self, sid):
        return sorted(self.live, key=lambda a: a.get("t", 0))

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        self.pruned.append((frozenset(tx_uuids), human_floor))


def _session(turns):
    return {"rompUuid": SID, "name": "web", "dir": "/tmp/notes-api", "color": "#1EA1EB", "turns": turns}


def _two_days():
    """Two turns: one late on the earlier day (22:00, 22:01), one early on the later day (07:05, 07:06)."""
    a = _turn("t1", [_user("u1", T_DAY1 + 22 * 3600, "please run the notes-api search suite"),
                     _asst("a1", T_DAY1 + 22 * 3600 + 60, "Running the search suite now.")])
    b = _turn("t2", [_user("u2", T_DAY2 + 7 * 3600 + 300, "and the docs suite after it"),
                     _asst("a2", T_DAY2 + 7 * 3600 + 360, "Both suites are green.")])
    return [a, b]


def _flat(session):
    return [a for turn in session["turns"] for a in turn["atoms"]]


class StaleEchoPlacement(unittest.TestCase):
    def setUp(self):
        self.saved = (km.Sessions.backend_for, km._path_of)
        km._merge_sets_memo.clear()
        km._path_of = lambda sid, now=None: None

    def tearDown(self):
        km.Sessions.backend_for, km._path_of = self.saved
        km._merge_sets_memo.clear()

    def _merge(self, turns, live):
        be = _Backend(live)
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        return km._merge_live_atoms(_session(turns), SID), be

    def test_an_echo_sent_between_two_days_turns_sits_between_them_not_among_the_later_rows(self):
        # the user's case: a notice sent at 22:28 on the earlier day, never landed, read the next morning
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60)
        merged, _ = self._merge(_two_days(), [stale])
        ts = [a["t"] for a in _flat(merged)]
        self.assertEqual(ts, sorted(ts), "the rows the chat reads are in time order: %r" % ts)
        self.assertNotIn(stale["uuid"], [a["uuid"] for a in merged["turns"][-1]["atoms"]],
                         "the stale echo is not in the LAST turn (that put a 10:28 PM row between 7:05 AM rows)")
        holder = next(t for t in merged["turns"] if any(a["uuid"] == stale["uuid"] for a in t["atoms"]))
        self.assertEqual((holder["t"], holder["end"], holder["ended"]), (stale["t"], stale["t"], True),
                         "in the gap between the turns it gets a closed turn of its own at its send time: %r" % holder)
        self.assertIsNone(holder["trigger"])
        self.assertEqual([t["id"] for t in merged["turns"]], ["t1", holder["id"], "t2"], "…placed before the later day's turn")
        # the last turn keeps its own window and ended state: a stale echo is not live work
        self.assertEqual((merged["turns"][-1]["end"], merged["turns"][-1]["ended"]), (T_DAY2 + 7 * 3600 + 360, True))

    def test_an_echo_inside_an_earlier_turns_window_joins_that_turn(self):
        turns = _two_days()
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32)   # between u1 and a1
        merged, _ = self._merge(turns, [inside])
        self.assertEqual([a["uuid"] for a in merged["turns"][0]["atoms"]], ["u1", inside["uuid"], "a1"])
        self.assertEqual(len(merged["turns"]), 2, "no synthetic turn when a window holds the echo")
        self.assertEqual(merged["turns"][0]["end"], turns[0]["end"], "the window already held it: unchanged")

    def test_an_echo_older_than_the_first_turn_leads_the_transcript(self):
        oldest = _echo(T_DAY1 + 9 * 3600, key="echo:" + "d" * 32)
        merged, _ = self._merge(_two_days(), [oldest])
        self.assertEqual(merged["turns"][0]["atoms"][0]["uuid"], oldest["uuid"])
        self.assertEqual([t["id"] for t in merged["turns"]][1:], ["t1", "t2"])

    def test_two_echoes_in_one_gap_share_one_synthetic_turn_in_time_order(self):
        e1 = _echo(T_DAY1 + 23 * 3600, key="echo:" + "c" * 32)
        e2 = _echo(T_DAY1 + 22 * 3600 + 28 * 60, key="echo:" + "b" * 32)
        merged, _ = self._merge(_two_days(), [e1, e2])
        self.assertEqual(len(merged["turns"]), 3)
        gap = merged["turns"][1]
        self.assertEqual([a["uuid"] for a in gap["atoms"]], [e2["uuid"], e1["uuid"]])
        self.assertEqual((gap["t"], gap["end"]), (e2["t"], e1["t"]), "the synthetic turn spans its members")

    def test_a_fresh_echo_stays_in_the_last_turn_as_before(self):
        # a pending send: stamped after the last turn's start; the last turn's window extends over it
        fresh = _echo(T_DAY2 + 7 * 3600 + 400, key="echo:" + "a" * 32, author="human", _echo_text="one more thing")
        fresh["message"]["content"][0]["text"] = "one more thing"
        merged, _ = self._merge(_two_days(), [fresh])
        self.assertEqual(len(merged["turns"]), 2)
        self.assertEqual(merged["turns"][-1]["atoms"][-1]["uuid"], fresh["uuid"])
        self.assertEqual(merged["turns"][-1]["end"], fresh["t"])
        self.assertTrue(merged["turns"][-1]["ended"], "an echo never reopens the turn")

    def test_a_streaming_reply_is_live_work_in_the_last_turn_whatever_its_stamp(self):
        turns = _two_days()
        turns[-1]["ended"] = False
        merged, _ = self._merge(turns, [_stream(T_DAY2 + 7 * 3600 + 361)])
        self.assertEqual(len(merged["turns"]), 2)
        self.assertEqual(merged["turns"][-1]["atoms"][-1]["uuid"], "live-reply-1")
        self.assertFalse(merged["turns"][-1]["ended"])

    def test_the_synthetic_turns_id_is_stable_across_builds(self):
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60)
        a, _ = self._merge(_two_days(), [stale])
        km._merge_sets_memo.clear()
        b, _ = self._merge(_two_days(), [stale])
        self.assertEqual(a["turns"][1]["id"], b["turns"][1]["id"], "the same echo yields the same turn id build after build")
        self.assertNotIn(a["turns"][1]["id"], ("t1", "t2", "live"))

    def test_the_parse_object_is_not_mutated(self):
        turns = _two_days()
        before = [(t["id"], len(t["atoms"])) for t in turns]
        self._merge(turns, [_echo(T_DAY1 + 22 * 3600 + 28 * 60)])
        self.assertEqual([(t["id"], len(t["atoms"])) for t in turns], before)


if __name__ == "__main__":
    unittest.main()
