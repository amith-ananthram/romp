#!/usr/bin/env python3
"""A rolled-back reply must never ghost back into the chat (the user 2026-08-03).

A chat DELETE rolls the conversation back to just before the target message; once the CLI
relaunches with --resume-session-at, the next message forks the transcript graph past the deleted
exchange, and the abandoned branch drops off the kept path. But the SDK backend salvages a durable
orphanReply marker for nearly every settle (the settle check routinely beats the CLI's transcript
append), and the orphan dedup read only the KEPT atoms — so the moment the rollback was consumed,
the abandoned reply's marker stopped deduping and re-emitted the deleted exchange's reply as a
ghost bubble: the user's message vanished, the answer to it came back, forever, and the judges saw
it too. The parse_session leaf_override filter only covered the ARMED window (2026-08-01).

Fix under test: markers dedup against the uuids that landed WITH TEXT on ANY branch of the graph
(FileAdapter.landed_text_uuids) — a reply the disk kept is never a loss, wherever its branch went.
Both doors are pinned: event_model.synthesize_orphans (the parse; feeds chat AND judges) and
build_session's own marker interleave in the kernel. SYNTHETIC only: placeholder uuids, invented
notes-api prompts, temp dirs."""
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
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_em_ghost", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_ghost", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_ghost", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600

GHOST = "Not stuck — the answer to your runner question is just above."
LOST = "A genuinely lost reply: the runner streams results as TAP."


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def marker(t, uuid, text):
    return {"t": t, "orphanReply": {"uuid": uuid, "text": text}}


# The consumed-rollback shape: u2/a2 were deleted (u3 forks from a1, the CLI's --resume-session-at
# target), so the kept path is u1 → a1 → u3 → a3 and the u2/a2 branch is abandoned-but-on-disk.
def consumed_rollback_records():
    return [
        uline(T0, "how do the notes-api tests run?", "u1", None),
        aline(T0 + 40, "They run under the bundled runner.", "a1", "u1"),
        uline(T0 + 300, "hello, are you stuck?", "u2", "a1"),
        aline(T0 + 305, GHOST, "a2", "u2"),
        uline(T0 + 500, "great — and how are fixtures loaded?", "u3", "a1"),
        aline(T0 + 540, "Fixtures load from the tests directory.", "a3", "u3"),
    ]


# Every settle salvages a marker (the check routinely beats the CLI's append), so the markers
# mirror that: one per reply that landed, plus one reply the disk truly never kept.
def markers_rows():
    return [
        marker(T0 + 40, "a1", "They run under the bundled runner."),
        marker(T0 + 305, "a2", GHOST),
        marker(T0 + 420, "lost1", LOST),
        marker(T0 + 540, "a3", "Fixtures load from the tests directory."),
    ]


def parse(records, markers_list, leaf_override=None):
    td = Path(tempfile.mkdtemp())
    tpath = td / (SID + ".jsonl")
    tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return em.parse_session(str(tpath), states=markers_list, now=NOW,
                            leaf_override=leaf_override)


def atom_texts(parsed):
    out = []
    for turn in parsed["turns"]:
        for a in turn["atoms"]:
            c = (a.get("message") or {}).get("content")
            if isinstance(c, list):
                out += [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
            elif isinstance(c, str):
                out.append(c)
    return out


class ParseNeverResurrectsARolledBackReply(unittest.TestCase):
    def test_consumed_rollback_ghost_is_suppressed_and_real_losses_still_salvage(self):
        parsed = parse(consumed_rollback_records(), markers_rows())
        texts = atom_texts(parsed)
        self.assertNotIn(GHOST, texts, "the abandoned reply's marker must not resurrect it")
        self.assertNotIn("hello, are you stuck?", texts, "the deleted ask stays deleted")
        self.assertIn(LOST, texts, "a reply the disk never kept still salvages")
        self.assertEqual(texts.count("They run under the bundled runner."), 1,
                         "a kept reply renders once — its marker dedups as before")

    def test_landed_text_uuids_rides_the_parse_result(self):
        parsed = parse(consumed_rollback_records(), markers_rows())
        self.assertEqual(parsed.get("landedTextUuids"), ["a1", "a2", "a3"],
                         "the kernel's own marker interleave dedups against this")

    def test_the_armed_window_still_hides_the_about_to_be_cut_reply(self):
        # Before the CLI consumes the rollback the graph still ends at a2; the kernel renders the
        # cut via leaf_override. Covered by BOTH the 2026-08-01 time filter and the landed check.
        records = consumed_rollback_records()[:4]
        parsed = parse(records, markers_rows()[:2], leaf_override="a1")
        texts = atom_texts(parsed)
        self.assertNotIn(GHOST, texts)
        self.assertNotIn("hello, are you stuck?", texts)

    def test_a_textless_disk_twin_still_does_not_eat_the_salvage(self):
        # The fable+AskUserQuestion loss: the streamed reply persists as an EMPTY thinking record
        # under the same uuid, on the KEPT path. landed_text_uuids is text-bearing only, so the
        # marker still salvages the text.
        records = [
            uline(T0, "how do the notes-api tests run?", "u1", None),
            {"type": "assistant", "timestamp": iso(T0 + 40), "uuid": "tw1", "parentUuid": "u1",
             "message": {"role": "assistant",
                         "content": [{"type": "thinking", "thinking": ""}]}},
        ]
        parsed = parse(records, [marker(T0 + 41, "tw1", "the explanation before the picker")])
        self.assertIn("the explanation before the picker", atom_texts(parsed))


class BuildSessionNeverResurrectsARolledBackReply(unittest.TestCase):
    """The kernel door: build_session's own marker interleave (_orphan_replies) reads states/
    directly and dedup'd by text against the KEPT turns only — with the parse now suppressing the
    ghost atom, this second door would re-emit it without the landed-uuid guard. Fixture cribbed
    from test_chat_payload_clock_invariant; km.jd is a separate module object, so BOTH copies get
    the temp paths."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        self.tpath.write_text("\n".join(json.dumps(r) for r in consumed_rollback_records()) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        (td / "states").mkdir()
        (td / "states" / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(m) for m in markers_rows()) + "\n")
        self.saved = []
        for mod in (jd, km.jd):
            self.saved.append((mod, mod.NAMES, mod.PROJECTS, mod.CAPDIR, mod.ARCHDIR,
                               mod.GOALDIR, mod.STATE, mod.STATESDIR))
            mod.NAMES, mod.PROJECTS = names, proj
            mod.CAPDIR, mod.ARCHDIR, mod.GOALDIR = td / "captions", td / "archive", td / "goals"
            mod.STATE, mod.STATESDIR = td, td / "states"
        self.saved_km = (km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD)
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None,
                                           "color": None}}
        km._parse_cache.clear()

    def tearDown(self):
        for mod, *vals in self.saved:
            (mod.NAMES, mod.PROJECTS, mod.CAPDIR, mod.ARCHDIR,
             mod.GOALDIR, mod.STATE, mod.STATESDIR) = vals
        (km.NAMES, km._tmux_sessions, km._GLOBAL_CLAUDE_MD) = self.saved_km
        km._parse_cache.clear()
        self.td.cleanup()

    def test_the_chat_payload_has_no_ghost_but_keeps_the_real_salvage(self):
        m = km.build_session(SID, NOW)
        self.assertIsNotNone(m, "fixture session must build")
        mds = [e.get("md") or "" for e in m["events"]]
        self.assertFalse(any(GHOST in md for md in mds),
                         "the deleted exchange's reply must not reappear through EITHER door")
        self.assertFalse(any("hello, are you stuck?" in (e.get("prompt") or "") for e in m["events"]),
                         "the deleted ask stays deleted")
        self.assertTrue(any(LOST in md for md in mds),
                        "a genuinely lost reply still interleaves into the chat")


if __name__ == "__main__":
    unittest.main()
