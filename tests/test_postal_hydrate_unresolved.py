#!/usr/bin/env python3
"""An unresolvable postal id must not cost the turn its deep-link (the user 2026-07-23, via romp_docs).

The timeline draws a message arc from the message log, and clicking it carries the MESSAGE ID, which the
chat matches against a turn's data-mid. _hydrate_postal is all-or-nothing on purpose: if any id in an
event fails to resolve, it emits no cards, because a half-rendered card run would be worse. But it also
dropped the ids, so the arc pointed at a turn that could never answer to it and the click died in
silence — while docs/index.md promises the views are "each linked to the others".

The turn now carries the ids either way, and an id that will not resolve is reported instead of passing
quietly (CLAUDE.md: fail loudly, don't degrade silently).

Synthetic only — an invented message log, placeholder ids, hostname TESTHOST.
"""
import io
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model_ph", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge_ph", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_ph", os.path.join(BIN, "romp-kernel"))

KNOWN = "1700000000.11111_22222.TESTHOST"
UNKNOWN = "1700000001.33333_44444.TESTHOST"


def rec(mid):
    return {"id": mid, "from": "web", "fromId": "11111111-2222-3333-4444-555555555555",
            "body": "the auth story needs a decision", "kind": "question", "t": 1700000000, "park": None}


def turn(*mids):
    body = "\n".join("<!-- romp-msg-id: %s -->" % m for m in mids)
    return {"kind": "user", "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "md": "You have mail:\n" + body, "ts": "2026-07-23T09:23:00.000Z"}


class HydrateUnresolved(unittest.TestCase):
    def setUp(self):
        km._POSTAL_UNRESOLVED_RESET()                   # T234: each test is a fresh kernel life

    def _run(self, events, index):
        err = io.StringIO()
        real, km.sys.stderr = km.sys.stderr, err
        try:
            return km._hydrate_postal(events, index), err.getvalue()
        finally:
            km.sys.stderr = real

    def test_a_fully_resolved_turn_still_becomes_a_card(self):
        out, warned = self._run([turn(KNOWN)], {KNOWN: rec(KNOWN)})
        self.assertEqual([e["kind"] for e in out], ["postal-service"])
        self.assertEqual(out[0]["mid"], KNOWN)
        self.assertEqual(warned, "", "the healthy path stays silent")

    def test_an_unresolved_id_keeps_the_turn_landable(self):
        out, warned = self._run([turn(UNKNOWN)], {})
        self.assertEqual([e["kind"] for e in out], ["user"], "no half-rendered card")
        self.assertEqual(out[0]["mid"], UNKNOWN, "...but the deep-link target survives")
        self.assertEqual(out[0]["mids"], [UNKNOWN])
        self.assertIn("unresolved", warned, "and it is reported, not swallowed")
        self.assertIn(UNKNOWN, warned, "naming the id, so the log says which one")

    def test_a_PARTIAL_turn_carries_every_id_it_mentioned(self):
        # One resolves, one does not. The turn stays raw, and BOTH arcs into it must still land — the
        # resolved one no longer has a card of its own to aim at.
        out, warned = self._run([turn(KNOWN, UNKNOWN)], {KNOWN: rec(KNOWN)})
        self.assertEqual([e["kind"] for e in out], ["user"])
        self.assertEqual(out[0]["mids"], [KNOWN, UNKNOWN])
        self.assertEqual(out[0]["mid"], KNOWN, "the first id is the single-attribute fallback")
        self.assertIn("1 of 2", warned)

    def test_a_turn_with_no_postal_ids_is_untouched(self):
        plain = {"kind": "user", "uuid": "u1", "md": "just a prompt"}
        out, warned = self._run([plain], {})
        self.assertEqual(out, [plain], "no mid keys invented on ordinary turns")
        self.assertEqual(warned, "")

    # ── T234 (the user 2026-09-03): the warning is a fact stated ONCE per (session, id) per kernel life ──
    # The same unresolved ids re-warned on EVERY build — 20k to 116k journal lines per hour for 30+ hours,
    # the same pairs every pusher cycle — burying real signal. Fail-loud stays: the FIRST sighting of a
    # pair logs; repeats are counted, not printed; a NEW pair logs again.
    def test_the_same_unresolved_id_warns_once_across_builds_and_a_new_id_warns_again(self):
        _, w1 = self._run([turn(UNKNOWN)], {})
        _, w2 = self._run([turn(UNKNOWN)], {})           # the next build, same unresolved id
        self.assertEqual(w1.count("unresolved"), 1, "the first sighting logs")
        self.assertEqual(w2.count("unresolved"), 0,
                         "the same (session, id) pair on the next build is a repeat, not news")
        other = "22222222-3333-4444-5555-666666666666"
        _, w3 = self._run([turn(other)], {})
        self.assertEqual(w3.count("unresolved"), 1, "a NEW unresolved id is new information — it logs")
        facts = km._POSTAL_UNRESOLVED
        self.assertEqual(facts["warned"], 2, "two distinct pairs warned")
        self.assertEqual(facts["suppressed"], 1, "one repeat suppressed — the counter is exact")

    def test_the_original_event_is_not_mutated_in_place(self):
        # build_session hands these dicts around; stamping the caller's object would leak the ids into
        # whatever else holds that reference.
        ev = turn(UNKNOWN)
        out, _ = self._run([ev], {})
        self.assertNotIn("mid", ev, "the input event is left alone")
        self.assertIn("mid", out[0])


if __name__ == "__main__":
    unittest.main()
