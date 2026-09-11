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


def _idle(t, end):
    """The idle atom event_model.synthesize_idle appends to a finished turn: from the Stop state row to the
    NEXT state row, so a finished turn's `end` reaches the next turn's start (the production shape)."""
    return {"type": "idle", "uuid": None, "session_id": SID, "t": t, "end": end, "_seq": 10 ** 12 + t}


def _two_days_idle():
    """The production shape: the earlier day's turn carries a trailing idle atom stretched to the later day's
    turn start, so its parsed `end` is 07:05 the next day although its work ended at 22:01."""
    a, b = _two_days()
    a["atoms"].append(_idle(a["end"], b["t"]))
    a["end"] = b["t"]
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

    def test_a_finished_turns_idle_stretch_is_not_its_window(self):
        # the manager's review of the first cut: every finished SDK turn's `end` is the NEXT turn's start (the
        # synthesized idle atom), so a window read off `end` swallowed every notice sent while the session sat
        # idle; the window ends at the turn's last recorded non-idle activity
        turns = _two_days_idle()
        self.assertEqual(turns[0]["end"], turns[1]["t"], "the fixture has the production shape")
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60)                     # 27 minutes after the work ended
        merged, _ = self._merge(turns, [stale])
        self.assertEqual([t["id"] for t in merged["turns"]][::2], ["t1", "t2"], "a synthetic turn in the gap")
        self.assertTrue(merged["turns"][1].get("echoTurn"))
        self.assertNotIn(stale["uuid"], [a["uuid"] for a in merged["turns"][0]["atoms"]],
                         "the echo never joins a turn that ended before it was sent")
        self.assertEqual(km._turn_activity_end(turns[0]), T_DAY1 + 22 * 3600 + 60, "the last reply, not the idle atom's end")
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32)  # between the prompt and the reply
        merged2, _ = self._merge(_two_days_idle(), [inside])
        self.assertIn(inside["uuid"], [a["uuid"] for a in merged2["turns"][0]["atoms"]], "inside the activity: joins")
        self.assertEqual(len(merged2["turns"]), 2)

    def test_the_placer_returns_the_destinations_with_the_turns(self):
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60, dropped=True)
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32)
        turns, placed = km._place_stale_echoes(_two_days(), [stale, inside])
        self.assertEqual([t["id"] for t in turns][::2], ["t1", "t2"])
        self.assertEqual(placed, ((0, inside["uuid"], False), (1, stale["uuid"], True)),
                         "(destination index, uuid, dropped) per echo, indexes after the insertions")

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

    def test_a_stale_echo_and_a_fresh_one_in_the_same_build_each_take_their_place(self):
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60, key="echo:" + "beef" * 8)
        fresh = _echo(T_DAY2 + 7 * 3600 + 400, key="echo:" + "cafe" * 8, author="human", _echo_text="one more thing")
        fresh["message"]["content"][0]["text"] = "one more thing"
        merged, _ = self._merge(_two_days(), [stale, fresh])
        self.assertEqual([t["id"] for t in merged["turns"]][::2], ["t1", "t2"])
        self.assertEqual(merged["turns"][1]["atoms"][0]["uuid"], stale["uuid"], "the stale one in the gap")
        self.assertEqual(merged["turns"][-1]["atoms"][-1]["uuid"], fresh["uuid"], "the fresh one in the tail")
        self.assertEqual(merged["turns"][-1]["end"], fresh["t"], "the tail still extends the last turn's window")

    def test_boundaries_an_echo_at_a_turns_end_joins_it_and_one_at_the_last_turns_start_takes_the_tail(self):
        turns = _two_days()
        at_end = _echo(turns[0]["end"], key="echo:" + "dead" * 8)                 # stamped exactly at t1's end
        at_last_start = _echo(turns[1]["t"], key="echo:" + "face" * 8)          # stamped exactly at t2's start
        merged, _ = self._merge(turns, [at_end, at_last_start])
        self.assertEqual(len(merged["turns"]), 2, "neither needs a synthetic turn")
        self.assertIn(at_end["uuid"], [a["uuid"] for a in merged["turns"][0]["atoms"]])
        self.assertIn(at_last_start["uuid"], [a["uuid"] for a in merged["turns"][1]["atoms"]])

    def test_a_dropped_echo_keeps_its_flag_and_the_placement_is_reported_for_the_fold(self):
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60, dropped=True)
        merged, _ = self._merge(_two_days(), [stale])
        a = next(x for t in merged["turns"] for x in t["atoms"] if x["uuid"] == stale["uuid"])
        self.assertTrue(a.get("dropped"), "placement never touches the atom")
        self.assertEqual(merged["_placed"], ((1, stale["uuid"], True),),
                         "the chat fold keys its sealed prefix on (turn index, uuid, dropped) of every placed echo")
        fresh_only, _ = self._merge(_two_days(), [_echo(T_DAY2 + 7 * 3600 + 400, key="echo:" + "a" * 32)])
        self.assertEqual(fresh_only["_placed"], (), "a tail echo is not placed and not reported")

    def test_an_older_stream_atom_is_still_the_last_turns_live_work(self):
        # a reply in flight is never placed by time: only echoes are
        turns = _two_days()
        turns[-1]["ended"] = False
        merged, _ = self._merge(turns, [_stream(T_DAY1 + 23 * 3600)])
        self.assertEqual(len(merged["turns"]), 2)
        self.assertIn("live-reply-1", [a["uuid"] for a in merged["turns"][-1]["atoms"]])
        self.assertFalse(merged["turns"][-1]["ended"])

    def test_a_send_the_cli_still_owes_keeps_the_tail_however_old_its_stamp(self):
        # a held copy (T306) keeps its send stamp while the queue behind it feeds: on release it is older than
        # the last turn's start but still pending, listed by the CLI's queue ledger, and rides the tail
        saved = km._pending_ledger
        km._path_of = lambda sid, now=None: "/nonexistent/notes-api/%s.jsonl" % SID
        km._pending_ledger = lambda path: ["one more thing"]
        try:
            owed = _echo(T_DAY1 + 22 * 3600 + 28 * 60, key="echo:" + "ab" * 16, author="human", _echo_text="one more thing")
            owed["message"]["content"][0]["text"] = "one more thing"
            merged, _ = self._merge(_two_days(), [owed])
        finally:
            km._pending_ledger = saved
        self.assertEqual(len(merged["turns"]), 2, "no placement for an owed send")
        self.assertEqual(merged["turns"][-1]["atoms"][-1]["uuid"], owed["uuid"],
                         "it rides the last turn's tail: an in-flight echo sorts after every atom the turn holds, whatever "
                         "its send stamp (the model reads a mid-turn send at its next tool boundary; main's rule, 2026-09-11)")
        self.assertEqual(merged["_placed"], ())

    def test_the_synthetic_turn_is_marked_and_names_its_echoes(self):
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60)
        merged, _ = self._merge(_two_days(), [stale])
        gap = merged["turns"][1]
        self.assertTrue(gap.get("echoTurn"))
        self.assertEqual(gap.get("placedEchoes"), [stale["uuid"]])
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32)
        merged2, _ = self._merge(_two_days(), [inside])
        self.assertEqual(merged2["turns"][0].get("placedEchoes"), [inside["uuid"]], "a window placement is named on the turn")
        self.assertFalse(merged2["turns"][0].get("echoTurn"), "…which is a real turn")

    def test_a_placed_echo_never_splits_a_judged_turns_segments(self):
        # a judged turn's segment ids mirror the judge's parse, which never sees an echo: the segmenters
        # read the turn without its placed echoes, so the bar and its captions keep their keys
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32)
        turns = _two_days()
        merged, _ = self._merge(turns, [inside])
        before = [seg["id"] for seg in km.em.segments(turns[0])]
        after = [seg["id"] for seg in km.em.segments(km._turn_sans_placed_echoes(merged["turns"][0]))]
        self.assertEqual(after, before)
        self.assertNotEqual([seg["id"] for seg in km.em.segments(merged["turns"][0])], before,
                            "(the raw placed turn WOULD split: that is what the helper prevents)")
        self.assertEqual([seg["id"] for seg in km._segs_seam(merged["turns"][0], {})], before, "the seam-aware segmenter reads it too")
        # the anchors too (the dot's prompt atom, the bar's first work atom, the reply): byte-identical with and
        # without the placed echo, for the chat build's maps and the lanes' bars alike
        anchors = lambda t: [km._seg_anchors(seg["atoms"]) for seg in km._segs_seam(t, {})]
        self.assertEqual(anchors(km._turn_sans_placed_echoes(merged["turns"][0])), anchors(turns[0]))
        untouched = merged["turns"][-1]
        self.assertIs(km._turn_sans_placed_echoes(untouched), untouched, "a turn with nothing placed is handed back as is")

    def test_the_feeds_plain_prompt_reader_never_reads_an_echo_as_a_prompt(self):
        # a stale HUMAN echo (a swallowed send) is not the user talking on the thread: the re-judging latch
        # must never arm off it (the code's own rule: the parse's plain-reply turn, never the echo)
        stale = _echo(T_DAY1 + 22 * 3600 + 28 * 60, author="human", _echo_text="are we done here?")
        stale["message"]["content"][0]["text"] = "are we done here?"
        inside = _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "f" * 32, author="human", _echo_text="and this?")
        inside["message"]["content"][0]["text"] = "and this?"
        merged, _ = self._merge(_two_days(), [stale, inside])
        self.assertEqual(km._last_plain_user_turn_t(merged["turns"]), T_DAY2 + 7 * 3600 + 300, "the later day's real prompt, not an echo")
        self.assertEqual(km._last_plain_user_turn_t([merged["turns"][1]]), 0, "an echo's own turn is no prompt turn")

    def test_two_echoes_into_one_turns_window_land_in_one_copy_and_the_placement_returns(self):
        # the live regression of 2026-09-11 (every feed build failed on a session with two such echoes): the
        # second echo into the same turn re-copied it, the destinations kept the first copy, and the index
        # lookup at the end raised KeyError. Each turn is copied once; both echoes sit in that one copy.
        turns = _two_days()
        e1 = _echo(T_DAY1 + 22 * 3600 + 20, key="echo:" + "aa" * 16)
        e2 = _echo(T_DAY1 + 22 * 3600 + 40, key="echo:" + "bb" * 16, dropped=True)
        placed_turns, placed = km._place_stale_echoes(turns, [e1, e2])
        self.assertEqual([a["uuid"] for a in placed_turns[0]["atoms"]], ["u1", e1["uuid"], e2["uuid"], "a1"])
        self.assertEqual(placed, ((0, e1["uuid"], False), (0, e2["uuid"], True)))
        self.assertEqual(placed_turns[0].get("placedEchoes"), [e1["uuid"], e2["uuid"]])
        self.assertIsNot(placed_turns[0], turns[0], "…in a copy, the parse's turn untouched")
        self.assertEqual([a["uuid"] for a in turns[0]["atoms"]], ["u1", "a1"])
        # the whole merge, with a third echo in the gap after that turn: every destination resolves
        gap = _echo(T_DAY1 + 22 * 3600 + 28 * 60, key="echo:" + "cc" * 16)
        merged, _ = self._merge(_two_days(), [e1, e2, gap])
        self.assertEqual([t["id"] for t in merged["turns"]][::2], ["t1", "t2"])
        self.assertEqual(merged["_placed"], ((0, e1["uuid"], False), (0, e2["uuid"], True), (1, gap["uuid"], False)))

    def test_the_parse_object_is_not_mutated(self):
        turns = _two_days()
        before = [(t["id"], len(t["atoms"])) for t in turns]
        atom_lists = [t["atoms"] for t in turns]
        self._merge(turns, [_echo(T_DAY1 + 22 * 3600 + 28 * 60), _echo(T_DAY1 + 22 * 3600 + 30, key="echo:" + "b" * 32)])
        self.assertEqual([(t["id"], len(t["atoms"])) for t in turns], before)
        self.assertTrue(all(t["atoms"] is l for t, l in zip(turns, atom_lists)), "the parse's atom lists are the same objects")


if __name__ == "__main__":
    unittest.main()
