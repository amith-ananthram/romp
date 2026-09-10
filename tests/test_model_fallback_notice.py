#!/usr/bin/env python3
"""A mid-turn safeguards model swap must be visible in the chat (the user 2026-08-03).

When the model's safeguards flag a prompt, the CLI retries the turn on a fallback model and writes a
system/model_refusal_fallback record — but every system subtype except compact_boundary was dropped at
the parse, so the swap was invisible: the user watched a turn silently change models (observed live:
fable → opus, twice in one session). Conversation state the transcript records must be apparent to the
user. The record now becomes a system atom (event_model) and a {kind:"modelFallback"} chat event
(build_session), placed at the record's own timestamp — the retry start — so the notice reads BEFORE
the fallback model's reply. SYNTHETIC only: placeholder uuids, invented notes-api prompts, temp dirs."""
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
em = load_source("romp_em_mswap", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_mswap", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_mswap", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600

NOTICE = ("The model's safeguards flagged this message. Switched to a fallback model. "
          "Send feedback with /feedback.")
CATEGORY = "synthetic-category"
EXPLANATION = "a synthetic explanation of the refusal"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


# The refusal-turn shape as the CLI writes it: the refused call leaves an assistant record whose only
# content is a {"type":"fallback"} block, the fallback model then replies, and the system record —
# stamped with the RETRY START time, parented onto the reply — carries the user-facing explanation.
def refusal_turn_records():
    return [
        {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None,
         "promptSource": "typed",
         "message": {"role": "user", "content": "summarize the notes-api README"}},
        {"type": "assistant", "timestamp": iso(T0 + 5), "uuid": "afb", "parentUuid": "u1",
         "message": {"role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "fallback", "from": {"model": "claude-fable-5"},
                                  "to": {"model": "claude-opus-5"}}]}},
        {"type": "assistant", "timestamp": iso(T0 + 45), "uuid": "a1", "parentUuid": "afb",
         "message": {"role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "The README covers install and usage."}]}},
        {"type": "system", "subtype": "model_refusal_fallback", "timestamp": iso(T0 + 5),
         "uuid": "sfb", "parentUuid": "a1", "direction": "retry", "trigger": "refusal",
         "level": "warning", "content": NOTICE,
         "originalModel": "claude-fable-5", "fallbackModel": "claude-opus-5",
         # T279: the refusal's category (an open string; null when neither lane carried one) and the
         # API's explanation (display-only prose; null on server-lane banners), plus the scope
         "scope": "session", "apiRefusalCategory": CATEGORY, "apiRefusalExplanation": EXPLANATION},
    ]


class ParseEmitsTheFallbackAtom(unittest.TestCase):
    def _parse(self):
        td = Path(tempfile.mkdtemp())
        tpath = td / (SID + ".jsonl")
        tpath.write_text("\n".join(json.dumps(r) for r in refusal_turn_records()) + "\n")
        return em.parse_session(str(tpath), now=NOW)

    def test_the_record_becomes_a_system_atom_with_the_swap_facts(self):
        parsed = self._parse()
        atoms = [a for t in parsed["turns"] for a in t["atoms"]]
        fb = [a for a in atoms if a.get("subtype") == "model_refusal_fallback"]
        self.assertEqual(len(fb), 1, "the swap must survive the parse — it was silently dropped before")
        self.assertEqual(fb[0]["fallback_from"], "claude-fable-5")
        self.assertEqual(fb[0]["fallback_to"], "claude-opus-5")
        self.assertEqual(fb[0]["content"], NOTICE)

    def test_the_atom_carries_the_refusal_category_explanation_and_scope(self):
        # T279: the head and fold of the chat notice, and the card's cause — dropped at the parse before
        parsed = self._parse()
        atoms = [a for t in parsed["turns"] for a in t["atoms"]]
        fb = [a for a in atoms if a.get("subtype") == "model_refusal_fallback"][0]
        self.assertEqual(fb["refusal_category"], CATEGORY)
        self.assertEqual(fb["refusal_explanation"], EXPLANATION)
        self.assertEqual(fb["scope"], "session")

    def test_the_notice_sorts_before_the_fallback_models_reply(self):
        # its timestamp is the retry START, so the atom order reads: flagged -> switched -> the reply
        parsed = self._parse()
        atoms = [a for t in parsed["turns"] for a in t["atoms"]]
        i_fb = next(i for i, a in enumerate(atoms) if a.get("subtype") == "model_refusal_fallback")
        i_reply = next(i for i, a in enumerate(atoms) if a.get("uuid") == "a1")
        self.assertLess(i_fb, i_reply)

    def test_it_folds_into_the_running_turn_and_never_opens_one(self):
        parsed = self._parse()
        self.assertEqual(len(parsed["turns"]), 1, "a system atom is not an opener")


class BuildSessionEmitsTheNoticeEvent(unittest.TestCase):
    """Fixture cribbed from test_rewind_ghost_reply; km.jd is a separate module object, so BOTH
    copies get the temp paths."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        (pdir / (SID + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in refusal_turn_records()) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
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

    def test_the_chat_carries_a_modelFallback_event_before_the_reply(self):
        m = km.build_session(SID, NOW)
        self.assertIsNotNone(m, "fixture session must build")
        kinds = [e.get("kind") for e in m["events"]]
        self.assertIn("modelFallback", kinds, "the swap must reach the chat payload")
        ev = m["events"][kinds.index("modelFallback")]
        self.assertEqual((ev["from"], ev["to"]), ("claude-fable-5", "claude-opus-5"))
        self.assertEqual(ev["md"], NOTICE)
        i_reply = next(i for i, e in enumerate(m["events"])
                       if "README covers install" in (e.get("md") or ""))
        self.assertLess(kinds.index("modelFallback"), i_reply,
                        "the notice reads before the fallback model's reply")

    def test_the_event_carries_the_category_explanation_and_scope(self):
        # T279: the client renders the category in the head and the explanation in the fold
        m = km.build_session(SID, NOW)
        ev = next(e for e in m["events"] if e.get("kind") == "modelFallback")
        self.assertEqual(ev["category"], CATEGORY)
        self.assertEqual(ev["explanation"], EXPLANATION)
        self.assertEqual(ev["scope"], "session")

    def test_the_notice_is_never_the_users_bubble(self):
        # a STANDING guard on the routing, green before T279 too (the system atom is never a user
        # event): the record's user-facing text belongs to the notice, never to a user bubble
        m = km.build_session(SID, NOW)
        for e in m["events"]:
            if e.get("kind") == "user":
                self.assertNotIn(NOTICE, e.get("md") or "")
                self.assertNotIn(EXPLANATION, e.get("md") or "")
        self.assertEqual([e.get("kind") for e in m["events"]].count("modelFallback"), 1)


if __name__ == "__main__":
    unittest.main()
