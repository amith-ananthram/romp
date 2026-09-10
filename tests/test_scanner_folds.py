#!/usr/bin/env python3
"""The kernel's per-push transcript/states readers fold appends instead of re-reading whole files
(issue 903): _pending_queued and _undelivered_wake_tail (queue ledgers over the transcript),
_last_machine_cut and the five durable-note readers (states/<sid>.jsonl), all through _fold_records
over em._read_jsonl_incremental's cached record list.

The contract: the folded answer equals a from-scratch fold at EVERY appended record (the reference
clears the fold caches first; the record list itself comes from the same incremental reader either
way), a rewrite re-folds from record 0, an unchanged file is served without stepping a record, and the
wake tail's positions stay stable across folds. Synthetic only: placeholder ids, invented text."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_scanfold", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID = "11111111-2222-3333-4444-555555555555"
TASK = "<task-notification>\n<task-id>bash-1</task-id>\n<status>completed</status>\n</task-notification>"
AGENT = "<task-notification>\n<task-id>a0123456789abcdef</task-id>\n<status>completed</status>\n</task-notification>"


def qop(op, content=None, ts="2026-06-10T14:00:00.000Z"):
    o = {"type": "queue-operation", "operation": op, "sessionId": SID, "timestamp": ts}
    if content is not None:
        o["content"] = content
    return o


def user(text, meta=False):
    o = {"type": "user", "uuid": "u-%d" % abs(hash(text)) , "message": {"role": "user", "content": text}}
    if meta:
        o["isMeta"] = True
    return o


def asst(text):
    return {"type": "assistant", "uuid": "a-%d" % abs(hash(text)),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def _clear_folds():
    for c in (km._queued_parse_cache, km._wake_tail_cache, km._machine_cut_cache, km._states_notes_cache):
        c.clear()


class _Fold(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "states").mkdir()
        self.states = jd.STATE / "states" / (SID + ".jsonl")
        self.tpath = Path(self.td.name) / (SID + ".jsonl")
        self.tpath.write_text("")
        self.states.write_text("")
        _clear_folds()

    def tearDown(self):
        jd.STATE = self.saved
        _clear_folds()
        self.td.cleanup()

    def append(self, path, recs):
        with open(path, "a") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        os.utime(path, None)

    def rewrite(self, path, recs):
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        os.utime(path, None)

    def differential(self, path, recs, folded, label):
        """Append `recs` one at a time; after each, the folded answer must equal a fresh fold."""
        for i, r in enumerate(recs):
            self.append(path, [r])
            inc = folded()
            _clear_folds()
            ref = folded()
            self.assertEqual(json.dumps(inc, sort_keys=True), json.dumps(ref, sort_keys=True),
                             "%s diverged at record %d (%s)" % (label, i + 1, r.get("operation") or r.get("type")))
            folded()                                       # warm the fold cache again for the next step


class PendingQueued(_Fold):
    SEQ = [user("hello"), qop("enqueue", "first"), qop("enqueue", "second"), asst("working"),
           qop("dequeue"), qop("enqueue", "third"), qop("remove"), qop("remove"), qop("enqueue", "fourth"),
           user("[Request interrupted by user]"), qop("enqueue", "fifth"), qop("dequeue")]

    def test_folded_equals_whole_file_at_every_append(self):
        self.differential(self.tpath, self.SEQ, lambda: km._pending_queued(str(self.tpath)), "_pending_queued")
        self.assertEqual(km._pending_queued(str(self.tpath)), ["fifth"])

    def test_content_addressed_remove_and_popAll(self):
        recs = [qop("enqueue", "a"), qop("enqueue", "b"), qop("enqueue", "c"), qop("remove", "b"),
                qop("enqueue", "d"), qop("popAll"), qop("enqueue", "e"), qop("remove", "zzz")]
        self.differential(self.tpath, recs, lambda: km._pending_queued(str(self.tpath)), "remove/popAll")
        self.assertEqual(km._pending_queued(str(self.tpath)), [], "a remove naming nothing pending takes the oldest")
        self.append(self.tpath, [qop("enqueue", "f"), qop("enqueue", "g"), qop("remove", "g")])
        self.assertEqual(km._pending_queued(str(self.tpath)), ["f"], "a content-addressed remove takes that entry only")

    def test_a_rewrite_refolds_from_zero(self):
        self.append(self.tpath, self.SEQ)
        self.assertEqual(km._pending_queued(str(self.tpath)), ["fifth"])
        self.rewrite(self.tpath, self.SEQ[:3])            # a rewind: the file is a different prefix now
        self.assertEqual(km._pending_queued(str(self.tpath)), ["first", "second"])
        _clear_folds()
        self.assertEqual(km._pending_queued(str(self.tpath)), ["first", "second"])

    def test_missing_file_is_empty(self):
        self.assertEqual(km._pending_queued(str(self.tpath) + ".nope"), [])


class WakeTail(_Fold):
    SEQ = [user("hi"), qop("enqueue", TASK, ts="2026-06-10T14:00:00.500Z"), qop("popAll"),   # recalled whole
           qop("enqueue", TASK, ts="2026-06-10T14:00:01.000Z"), asst("busy"),
           qop("enqueue", "plain text from a peer", ts="2026-06-10T14:00:02.000Z"),
           qop("enqueue", AGENT, ts="2026-06-10T14:00:03.000Z"),
           qop("dequeue"),                                  # clears NOTHING here
           qop("remove", "plain text from a peer"),         # content-addressed: that entry only
           user("<system-reminder>x</system-reminder>", meta=True),   # isMeta: no evidence
           user("Delivered: " + TASK),                      # carries the wrapper → resolves it
           qop("enqueue", TASK, ts="2026-06-10T14:00:09.000Z"),
           qop("enqueue", TASK, ts="2026-06-10T14:00:10.000Z"),
           user(TASK)]                                      # one copy carried → clears exactly one

    def test_folded_equals_whole_file_at_every_append(self):
        self.differential(self.tpath, self.SEQ, lambda: km._undelivered_wake_tail(str(self.tpath)), "_undelivered_wake_tail")
        tail, mark = km._undelivered_wake_tail(str(self.tpath))
        self.assertEqual([e["text"] for e in tail], [AGENT, TASK])
        self.assertEqual([e["wrapper"] for e in tail], [False, True], "the agent notice is the CLI's own")
        self.assertEqual(mark, (tail[-1]["pos"], tail[-1]["ts"]))

    def test_positions_are_stable_across_folds(self):
        self.append(self.tpath, self.SEQ[:7])
        tail0, _ = km._undelivered_wake_tail(str(self.tpath))
        pos_agent = next(e["pos"] for e in tail0 if e["text"] == AGENT)
        self.append(self.tpath, self.SEQ[7:])
        tail1, _ = km._undelivered_wake_tail(str(self.tpath))
        self.assertEqual(next(e["pos"] for e in tail1 if e["text"] == AGENT), pos_agent,
                         "an entry keeps its position while the file only grows")

    def test_callers_cannot_mutate_the_carried_state(self):
        self.append(self.tpath, self.SEQ[:4])
        tail, _ = km._undelivered_wake_tail(str(self.tpath))
        tail[0]["text"] = "clobbered"
        tail2, _ = km._undelivered_wake_tail(str(self.tpath))
        self.assertEqual(tail2[0]["text"], TASK)


class MachineCut(_Fold):
    SEQ = [{"t": 100, "state": "working"}, {"t": 110, "machineCut": "restart"}, {"t": 120, "state": "idle"},
           {"t": 130, "machineCut": "crash"}, {"t": 140, "retriesRecovered": 2}]

    def test_folded_equals_whole_file_and_newest_wins(self):
        self.differential(self.states, self.SEQ, lambda: km._last_machine_cut(SID), "_last_machine_cut")
        self.assertEqual(km._last_machine_cut(SID), (130.0, "crash"))

    def test_no_marker_is_the_zero_answer(self):
        self.append(self.states, self.SEQ[:1])
        self.assertEqual(km._last_machine_cut(SID), (0.0, ""))
        self.assertEqual(km._last_machine_cut("no-such-sid"), (0.0, ""))


class Notes(_Fold):
    SEQ = [{"t": 100, "state": "working"}, {"t": 105, "retriesRecovered": 3}, {"t": 110, "cmdGesture": "/effort high"},
           {"t": 111, "effortApplied": "high"}, {"t": 120, "state": "idle"},
           {"t": 130, "orphanReply": {"uuid": "o-1", "text": "the reply the disk lost"}},
           {"t": 140, "retriesGaveUp": 5, "errorKind": "overloaded"}, {"t": 150, "retriesRecovered": 0},
           {"no_t": True, "cmdGesture": "/model x"}, {"t": 160, "cmdGesture": "/model sonnet"}]

    def _all(self):
        return {"rec": km._retry_recoveries(SID), "gave": km._retry_gaveups(SID), "orph": km._orphan_replies(SID),
                "eff": km._effort_changes(SID), "gest": km._cmd_gestures(SID)}

    def test_folded_equals_whole_file_at_every_append(self):
        self.differential(self.states, self.SEQ, self._all, "states notes")
        a = self._all()
        self.assertEqual(a["rec"], [{"t": 105, "retries": 3}])
        self.assertEqual(a["gave"], [{"t": 140, "retries": 5, "errorKind": "overloaded"}])
        self.assertEqual(a["orph"], [{"t": 130, "uuid": "o-1", "text": "the reply the disk lost"}])
        self.assertEqual(a["eff"], [{"t": 111, "effort": "high"}])
        self.assertEqual(a["gest"], [{"t": 110, "cmd": "/effort high"}, {"t": 160, "cmd": "/model sonnet"}])

    def test_an_unchanged_file_is_served_without_refolding(self):
        self.append(self.states, self.SEQ)
        first = km._states_notes(SID)
        self.assertIs(km._states_notes(SID), first, "same records → the cached state itself")
        self.append(self.states, [{"t": 170, "effortApplied": "low"}])
        second = km._states_notes(SID)
        self.assertIsNot(second, first)
        self.assertEqual(first["efforts"], [{"t": 111, "effort": "high"}], "the state a caller holds never changes under it")
        self.assertEqual(second["efforts"], [{"t": 111, "effort": "high"}, {"t": 170, "effort": "low"}])


class FoldRecords(_Fold):
    def test_only_appended_records_are_stepped_and_a_rewrite_refolds(self):
        cache, seen = {}, []

        def step(state, r):
            seen.append(r["n"])
            return state + [r["n"]]
        self.append(self.tpath, [{"n": i} for i in range(3)])
        self.assertEqual(km._fold_records(cache, self.tpath, list, step), [0, 1, 2])
        self.assertEqual(seen, [0, 1, 2])
        self.append(self.tpath, [{"n": 3}, {"n": 4}])
        self.assertEqual(km._fold_records(cache, self.tpath, list, step), [0, 1, 2, 3, 4])
        self.assertEqual(seen, [0, 1, 2, 3, 4], "an append steps only the new records")
        self.assertEqual(km._fold_records(cache, self.tpath, list, step), [0, 1, 2, 3, 4])
        self.assertEqual(seen, [0, 1, 2, 3, 4], "an unchanged file steps nothing")
        self.rewrite(self.tpath, [{"n": 7}, {"n": 8}])
        self.assertEqual(km._fold_records(cache, self.tpath, list, step), [7, 8])
        self.assertEqual(seen[-2:], [7, 8], "a rewrite folds from record 0")


if __name__ == "__main__":
    unittest.main()
