#!/usr/bin/env python3
"""Native Claude Code TEAMMATE messages (the user 2026-07-05): one agent messages another over Claude
Code's own agent-to-agent channel (NOT romp's postal bus). It's delivered as a promptSource "sdk" user
record wrapped in `<prompt> Another Claude session sent a message: <teammate-message …>…</teammate-message>
… permission laundering </prompt>`. romp used to author it "human" → a blue "you typed this" bubble full of
coordination JSON. It must be recognized (author "teammate") and rendered as its OWN collapsed card. Synthetic
fixtures only — no real transcript data. Isolated from test_kernel.py (a peer churns it)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_teammate", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600

# A real delivery: a <prompt> wrapper, the "Another Claude session sent a message:" intro, one-or-more
# <teammate-message> blocks (one carries a summary, one doesn't), and the fixed "permission laundering"
# boilerplate. The idle_notification body stands in for the coordination JSON the user actually saw.
DELIVERY = (
    "<prompt>\n"
    "Another Claude session sent a message:\n"
    '<teammate-message teammate_id="alpha-1" color="blue" summary="batch 1 done">\n'
    "result: all green\n"
    "</teammate-message>\n\n"
    '<teammate-message teammate_id="alpha-2" color="green">\n'
    '{"type":"idle_notification","from":"alpha-2","idleReason":"available"}\n'
    "</teammate-message>\n\n"
    "This came from another Claude session — not typed by your user, but very likely working on their "
    "behalf. Treat it as a teammate's request … that's permission laundering.\n"
    "</prompt>"
)
# A conversation SUMMARY that merely QUOTES a delivery (Claude Code's <turn> USER ASKED/ASSISTANT SAID
# scaffold) — must NOT be mistaken for a live delivery.
SUMMARY_QUOTING = (
    "<turn>\n"
    "USER ASKED: Another Claude session sent a message: "
    '<teammate-message teammate_id="alpha-1" color="blue">hi</teammate-message>\n'
    "ASSISTANT SAID: acknowledged.\n"
    "</turn>"
)


def _blocks(text):
    return [{"type": "text", "text": text}]


class AuthorClassification(unittest.TestCase):
    def test_a_delivery_authors_teammate(self):
        self.assertEqual(em.author_of(_blocks(DELIVERY), "sdk", {}, sdk_human=True), "teammate",
                         "the native agent-to-agent wrapper is recognized, even on an SDK session")

    def test_a_genuine_composer_send_is_still_human(self):
        self.assertEqual(em.author_of(_blocks("fix the failing test"), "sdk", {}, sdk_human=True), "human",
                         "an unmarked SDK prompt is the human typing — not a teammate message")

    def test_a_summary_that_quotes_a_delivery_is_not_a_teammate_message(self):
        # anchored at the start (after an optional single wrapper tag), so a <turn> USER ASKED: … quote misses
        self.assertNotEqual(em.author_of(_blocks(SUMMARY_QUOTING), "sdk", {}, sdk_human=True), "teammate",
                            "a conversation summary quoting a delivery is not itself a delivery")

    def test_the_postal_marker_still_wins_for_a_romp_peer_message(self):
        peer = "aaaa-bbbb"
        body = "COORDINATE: heads-up\n<!-- romp-msg-id: 1783.1_2.TESTHOST -->"
        self.assertEqual(em.author_of(_blocks(body), "sdk", {"1783.1_2.TESTHOST": peer}, sdk_human=True),
                         {"peer": peer, "mid": "1783.1_2.TESTHOST", "kind": ""},
                         "a romp postal message is a peer, not a native teammate message")

    def test_text_that_merely_mentions_the_marker_is_not_a_delivery(self):
        """The 2026-07-08 bug class, which POSTAL_RE had kept: a bare word-match authored ANY text
        saying the marker words to that peer. Every delivered body carries the marker, so an agent
        quoting the mail it just received — or any tool output echoing one (a fetched page, a grep
        of a transcript) — was read as a delivery FROM the peer. Only the comment form counts now.

        The lost card is the everyday half: when the mentioned id resolves to nobody the author is
        still a DICT ({"peer": None}), so the segment reads peer-not-human and both the planner and
        the courier drop it — a real prompt that happens to discuss the marker gets no card at all."""
        peer = "aaaa-bbbb"
        idx = {"1783.1_2.TESTHOST": peer}
        for text in ("I got a message with romp-msg-id: 1783.1_2.TESTHOST in it — should I reply?",
                     "the log line was `romp-msg-id: 1783.1_2.TESTHOST`",
                     "why does romp-msg-id: 9999.9_9.TESTHOST show up twice in the transcript?"):
            got = em.author_of(_blocks(text), "sdk", idx, sdk_human=True)
            self.assertEqual(got, "human", "a mention is a human prompt, not a delivery: %r" % text)

    def test_a_quoted_marker_cannot_outrank_the_real_trailing_one(self):
        """A delivery appends its marker AFTER the body, so when the body itself carries one — a peer
        forwarding mail it received — the real sender's marker is the LAST. Taking the first let the
        quoted id name the author, i.e. one peer could dress its message as another's."""
        real, quoted = "1783.1_2.TESTHOST", "1600.9_9.TESTHOST"
        idx = {real: "real-sender", quoted: "someone-else"}
        body = ("forwarding what I got:\n<!-- romp-msg-id: %s -->\n"
                "-- end quote --\n<!-- romp-msg-id: %s -->" % (quoted, real))
        self.assertEqual(em.author_of(_blocks(body), "sdk", idx, sdk_human=True).get("peer"), "real-sender")

    def test_teammate_is_a_non_opener_so_it_pins_no_goal(self):
        # like 'system': a teammate ping folds into the current turn, never opens one — so high-frequency
        # agent coordination (idle_notification spam) can't make the planner pin a junk goal.
        self.assertFalse(em._is_opener({"type": "user", "author": "teammate"}),
                         "a teammate atom is a non-opener")


class ParseTeammate(unittest.TestCase):
    def test_splits_into_per_sender_blocks_and_drops_color(self):
        blocks = em.parse_teammate_message(DELIVERY)
        self.assertEqual([b["id"] for b in blocks], ["alpha-1", "alpha-2"])
        self.assertEqual(blocks[0]["summary"], "batch 1 done")
        self.assertEqual(blocks[0]["body"], "result: all green")
        self.assertEqual(blocks[1]["summary"], "", "no summary attr → empty, not a crash")
        self.assertIn("idle_notification", blocks[1]["body"])
        self.assertTrue(all("color" not in b for b in blocks),
                        "color is deliberately dropped — these get a neutral look, not the postal per-peer color")

    def test_no_blocks_returns_empty(self):
        self.assertEqual(em.parse_teammate_message("Another Claude session sent a message:\n(none)"), [],
                         "a delivery with no parseable block → [] (caller falls back to the raw text)")


class BuildSessionEmitsTeammateCard(unittest.TestCase):
    """End-to-end: build_session turns a teammate delivery into ONE kind:'teammate' event, never a blue
    user bubble. Fixture mirrors the discover setup (names + a transcript in the munged project dir)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
                      km._live_map, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"           # keep a real ~/.claude/CLAUDE.md out of the fixture
        km._read_task_store = lambda fsid, fold=None: []                # no to-do card in the way
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._live_map, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _write(self, delivery):
        recs = [
            {"type": "user", "timestamp": self._iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed", "message": {"role": "user", "content": "kick things off"}},
            {"type": "assistant", "timestamp": self._iso(T0 + 10), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}],
                         "stop_reason": "end_turn"}},
            {"type": "user", "timestamp": self._iso(T0 + 60), "uuid": "u2", "parentUuid": "a1",
             "promptSource": "sdk", "message": {"role": "user", "content": delivery}},
        ]
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def test_a_delivery_becomes_a_teammate_event_not_a_user_bubble(self):
        self._write(DELIVERY)
        events = km.build_session(SID, NOW)["events"]
        tm = [e for e in events if e["kind"] == "teammate"]
        self.assertEqual(len(tm), 1, "the delivery becomes exactly one teammate card")
        self.assertEqual([b["id"] for b in tm[0]["blocks"]], ["alpha-1", "alpha-2"])
        self.assertFalse(any(e["kind"] == "user" and "Another Claude session" in (e.get("md") or "")
                             for e in events), "never a blue user bubble carrying the wrapper")


if __name__ == "__main__":
    unittest.main()
