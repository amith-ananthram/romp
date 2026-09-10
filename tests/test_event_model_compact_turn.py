#!/usr/bin/env python3
"""A compact_boundary opens its OWN turn (the user 2026-07-13). As a non-opener it used to absorb into
the previous turn, whose end = max(atom ends) then stretched to the boundary's timestamp — so the
timeline drew a phantom work bar spanning the whole idle gap "leading up to the moment of compaction",
growing live while the compact ran. Now the boundary anchors a fresh turn: the prior turn's bar ends at
its real last activity, the post-compact continuation files under the boundary turn (its bar starts AT
the compaction), and a boundary-only tail reads ended (a completed event, not in-flight work) so it can
never fake an open bar / WORKING. Synthetic transcript only — placeholder UUIDs, no real data."""
import datetime
import json
import os
import tempfile
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_compact", os.path.join(BIN, "romp-event-model"))

SID = "11111111-2222-3333-4444-555555555555"


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _turns(recs):
    td = tempfile.mkdtemp()
    p = os.path.join(td, SID + ".jsonl")
    open(p, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
    sess = em.parse_session(p, rompuuid=SID, name="impl", dir="/TESTDIR",
                            candidate_files=[p], states=None, postal_log=[], now=int(time.time()))
    return sess["turns"]


def _recs(now, with_continuation):
    t_old, t_done = now - 3600, now - 60
    recs = [
        {"type": "user", "timestamp": _iso(t_old), "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "please do the thing"}},
        {"type": "assistant", "timestamp": _iso(t_old + 30), "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done."}],
                     "stop_reason": "end_turn"}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": _iso(t_done), "uuid": "cb1",
         "parentUuid": None, "logicalParentUuid": "a1",
         "compactMetadata": {"trigger": "manual", "preTokens": 100000}},
        {"type": "user", "timestamp": _iso(t_done), "uuid": "cs1", "parentUuid": "cb1",
         "isCompactSummary": True,
         "message": {"role": "user", "content": "This session is being continued from a previous one..."}},
    ]
    if with_continuation:
        recs.append({"type": "assistant", "timestamp": _iso(t_done + 20), "uuid": "a2", "parentUuid": "cs1",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "resuming"}],
                                 "stop_reason": "end_turn"}})
    return recs


class CompactBoundaryTurn(unittest.TestCase):
    def test_boundary_never_stretches_the_previous_turn(self):
        now = int(time.time())
        turns = _turns(_recs(now, with_continuation=True))
        self.assertEqual(len(turns), 2, "the compaction is its own turn, not an absorbed tail")
        old, comp = turns
        self.assertEqual(old["end"], now - 3600 + 30,
                         "the prior turn's bar ends at its real last activity — an hour-long phantom "
                         "work period 'leading up to the compaction' was the bug")
        self.assertTrue(old["ended"])
        self.assertEqual(comp["t"], now - 60, "the compaction turn starts AT the boundary, never before")
        self.assertIn("cb1", [a.get("uuid") for a in comp["atoms"]])

    def test_continuation_work_files_under_the_boundary_turn(self):
        now = int(time.time())
        comp = _turns(_recs(now, with_continuation=True))[1]
        self.assertIn("a2", [a.get("uuid") for a in comp["atoms"]],
                      "the CLI's post-compact continuation rides the compaction turn")
        self.assertTrue(comp["ended"], "the continuation's end_turn settles it")

    def test_a_boundary_only_tail_is_self_contained(self):
        # mid-compaction parse: the boundary landed, the continuation hasn't — the turn must not read
        # open (a phantom growing bar / WORKING on an idle session)
        now = int(time.time())
        comp = _turns(_recs(now, with_continuation=False))[-1]
        self.assertTrue(comp["ended"], "a boundary with no assistant work yet is a completed event")
        self.assertEqual(comp["end"], comp["t"], "zero span — the marker, not a bar")


PID = "aaaaaaaa-1111-2222-3333-444444444444"


def _twin_recs(now, bare_text="/usage", with_wrapper=True, name="/usage"):
    """The CLI 2.1.215+ dual shape: a typed slash command lands TWICE — a raw-text user record (the
    submitted prompt verbatim, carrying promptId) and the <command-name> wrapper (same promptId),
    parent-chained straight through. Synthetic."""
    t0, tc = now - 3600, now - 120
    recs = [
        {"type": "user", "timestamp": _iso(t0), "uuid": "u1", "parentUuid": None,
         "message": {"role": "user", "content": "please do the thing"}},
        {"type": "assistant", "timestamp": _iso(t0 + 30), "uuid": "a1", "parentUuid": "u1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done."}],
                     "stop_reason": "end_turn"}},
        {"type": "user", "timestamp": _iso(tc), "uuid": "u2", "parentUuid": "a1", "promptId": PID,
         "message": {"role": "user", "content": bare_text}},
    ]
    if with_wrapper:
        recs += [
            {"type": "user", "timestamp": _iso(tc), "uuid": "u3", "parentUuid": "u2", "promptId": PID,
             "message": {"role": "user", "content": "<command-name>%s</command-name>\n"
                         "<command-message>%s</command-message>\n<command-args></command-args>"
                         % (name, name.lstrip("/"))}},
            {"type": "user", "timestamp": _iso(tc + 1), "uuid": "u4", "parentUuid": "u3",
             "message": {"role": "user", "content": "<local-command-stdout>ok</local-command-stdout>"}},
        ]
    return recs


def _atxt(a):
    return " ".join(b.get("text", "") for b in (a.get("message") or {}).get("content", [])
                    if isinstance(b, dict))


class BareInvocationTwin(unittest.TestCase):
    """CLI 2.1.215+ writes a typed slash command twice (see _twin_recs). The raw twin carries no
    wrapper, no isMeta, no isCompactSummary — left in, it walks the whole user-atom path and becomes a
    genuine HUMAN atom: a duplicate bubble in the chat, and a plannable human trigger the judges mint
    from (the rescue thread's 'Compact conversation context' card, 2026-07-20 — its quote was literally
    "/compact"). The wrapper is the one tracked command atom; its raw twin is invocation echo."""

    def _atoms(self, recs):
        return [a for t in _turns(recs) for a in t["atoms"]]

    def test_the_bare_twin_never_becomes_a_human_atom(self):
        atoms = self._atoms(_twin_recs(int(time.time())))
        plain = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                 and _atxt(a).strip() == "/usage"]
        self.assertEqual(plain, [], "the raw '/usage' twin surfaced as a human atom — a duplicate "
                                    "bubble the judges also treat as a plannable prompt")

    def test_the_wrapper_still_lands_as_the_one_command_atom(self):
        atoms = self._atoms(_twin_recs(int(time.time())))
        self.assertEqual(len([a for a in atoms if a.get("command") == "/usage"]), 1,
                         "exactly one tracked command atom — the wrapper's")

    def test_a_lone_slash_message_without_a_wrapper_stays_human(self):
        # no wrapper anywhere on this promptId → the text is a genuine message, whatever it looks like
        atoms = self._atoms(_twin_recs(int(time.time()), with_wrapper=False))
        self.assertEqual(len([a for a in atoms if a.get("type") == "user" and not a.get("command")
                              and _atxt(a).strip() == "/usage"]), 1,
                         "without a <command-name> twin the record is the user's own words — keep it")

    def test_a_twin_only_matches_its_own_invocation_text(self):
        # same promptId as a wrapper, but the text is NOT the invocation → it stays a human atom
        atoms = self._atoms(_twin_recs(int(time.time()), bare_text="see /usage docs for details"))
        kept = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                and "see /usage docs" in _atxt(a)]
        self.assertEqual(len(kept), 1, "only the invocation echo is dropped, never other text")

    def test_args_ride_the_twin_match(self):
        atoms = self._atoms(_twin_recs(int(time.time()), bare_text="/model opus", name="/model"))
        plain = [a for a in atoms if a.get("type") == "user" and not a.get("command")
                 and _atxt(a).strip() == "/model opus"]
        self.assertEqual(plain, [], "an args-bearing invocation echo drops too")


if __name__ == "__main__":
    unittest.main()
