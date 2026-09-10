#!/usr/bin/env python3
"""Notice cards (the user 2026-07-06): informational transcript notices — a backgrounded agent's report, a
romp SYSTEM notice (kernel restart/resume, Retry), folded system-reminders — each get their own boxed card
with a type chip + collapse, distinct from the postal/teammate cards. The kernel side of that is the
`rompSystem` flag: a romp SYSTEM notice carries a `<!-- romp-system -->` marker so build_session can tell it
apart from a feed NUDGE (both author 'romp'), letting the chat render the two differently. Synthetic only."""
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
km = load_source("romp_kernel_notice", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781100000
SID = "11111111-2222-3333-4444-555555555555"
T0 = NOW - 3600


class MarkersCarryRompSystem(unittest.TestCase):
    """A romp SYSTEM notice carries the <!-- romp-system --> marker so it cards up instead of bubbling. The
    kernel-restart/resume notice is the concrete case; the auto-Retry stays a plain bubble (frequent + minimal,
    a card per retry would be noise), so it deliberately does NOT carry the marker."""

    def test_boot_resume_carries_the_marker(self):
        sdk = load_source("romp_sdk_backend_nc", os.path.join(BIN, "romp_sdk_backend.py"))
        self.assertIn("romp-system", sdk.BOOT_RESUME_NUDGE, "the restart/resume notice is a romp SYSTEM notice")
        self.assertIn("romp-injected", sdk.BOOT_RESUME_NUDGE, "still romp-injected → author 'romp'")
        with open(os.path.join(BIN, "romp-kernel")) as f:
            src = f.read()
        self.assertIn('if "<!-- romp-system -->" in text:', src,
                      "build_session flags a romp-system message — COMMENT FORM only (the user "
                      "2026-07-08: content merely mentioning romp-system must not flip the card kind)")
        self.assertIn('"retry\\n\\n<!-- romp-injected -->"', src, "Retry stays a plain nudge bubble (no marker)")

    def test_every_system_notice_template_carries_a_gist(self):
        # 2026-09-08: each [romp] mechanics notice names its one-line head in a <!-- romp-gist --> marker, APPENDED
        # (the rename ping is detected by its leading head, the interrupt causes by their leading sentences)
        sdk = load_source("romp_sdk_backend_nc2", os.path.join(BIN, "romp_sdk_backend.py"))
        for name in ("BOOT_RESUME_NUDGE", "ASK_DIED_NOTICE", "CRASH_RESUME_NUDGE"):
            text = getattr(sdk, name)
            self.assertRegex(text, r"<!-- romp-gist: [^>]+ -->$", name + " ends with its gist marker")
            self.assertTrue(text.startswith("<!-- romp-injected --><!-- romp-system -->[romp]"), name + " keeps its leading head")
        lost = sdk.task_death_notice([{"desc": "watch the suite"}])
        self.assertIn("<!-- romp-gist: 1 background task cut off when the process ended -->", lost)
        self.assertEqual(km._romp_system_gist(lost), "1 background task cut off when the process ended")
        for verdict, frag in (("merged", "merged"), ("closed", "closed without merging"), ("failed", "failed check"), ("ghfail", "dropped")):
            self.assertIn("<!-- romp-gist: ", km._pr_watch_notice(verdict, "notes-api", 12))
            self.assertIn(frag, km._romp_system_gist(km._pr_watch_notice(verdict, "notes-api", 12)))
        for kind in ("met", "timeout", "soft", "execfail"):
            self.assertIsNotNone(km._romp_system_gist(km._watch_notice(kind, {"note": "the suite is green", "id": "w1"})), kind)
        # the same lift the user-event build uses serves a QUEUED copy of the notice (T243)
        self.assertEqual(km._queued_romp_flags(sdk.BOOT_RESUME_NUDGE).get("gist"), "resumed after a romp restart cut its turn")


class BuildSessionRompSystemFlag(unittest.TestCase):
    """build_session sets ev['rompSystem'] on a romp message carrying the marker, and NOT on a plain nudge —
    so render.ts can draw a romp NOTICE CARD for the former and keep the gray nudge bubble for the latter."""

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
                      km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE = names, proj, td / "goals", td
        km.NAMES = names
        km._GLOBAL_CLAUDE_MD = td / "no-global.md"
        km._read_task_store = lambda fsid, fold=None: []
        km._tmux_sessions = lambda: {SID: {"state": "idle", "since": NOW - 100, "model": "",
                                           "effort": "", "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        km._parse_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.GOALDIR, jd.STATE, km.NAMES,
         km._tmux_sessions, km._read_task_store, km._GLOBAL_CLAUDE_MD) = self.saved
        km._parse_cache.clear()
        self.td.cleanup()

    def _iso(self, t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def _write(self, msgs):
        # msgs: list of (uuid, parent, promptSource, content)
        recs = []
        for u, p, ps, c in msgs:
            recs.append({"type": "user", "timestamp": self._iso(T0 + len(recs) * 10), "uuid": u,
                         "parentUuid": p, "promptSource": ps, "message": {"role": "user", "content": c}})
            recs.append({"type": "assistant", "timestamp": self._iso(T0 + len(recs) * 10), "uuid": u + "a",
                         "parentUuid": u, "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}})
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    def _user_events(self):
        return [e for e in km.build_session(SID, NOW)["events"] if e["kind"] == "user"]

    def test_system_notice_gets_rompsystem_flag(self):
        self._write([("u1", None, "typed", "start"),
                     ("u2", "u1a", "sdk",
                      "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted; resumed.")])
        sysev = next(e for e in self._user_events() if e.get("rompSystem"))
        self.assertTrue(sysev.get("romp"), "a romp SYSTEM notice is still authored romp")
        self.assertTrue(sysev.get("rompSystem"), "and flagged as a system notice → its own card")

    def test_a_system_notice_carries_its_user_facing_gist(self):
        # 2026-09-08 (the notice-vocabulary pass): the chat's head for a romp SYSTEM notice is the kernel's GIST,
        # never the agent-facing body's first sentence ("Re-read the tail… pick the work back up") — the emitter
        # writes <!-- romp-gist --> beside its other markers and build_session lifts it beside rompSystem
        self._write([("u1", None, "typed", "start"),
                     ("u2", "u1a", "sdk",
                      "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted; resumed. "
                      "Re-read the tail and carry on.<!-- romp-gist: resumed after a romp restart cut its turn -->")])
        sysev = next(e for e in self._user_events() if e.get("rompSystem"))
        self.assertEqual(sysev.get("gist"), "resumed after a romp restart cut its turn")

    def test_a_system_notice_without_a_gist_marker_has_no_gist_field(self):
        # a notice recorded before gists shipped: the field is absent (the chat falls back to the first line);
        # prose merely MENTIONING romp-gist is not a marker (comment form only, like every romp marker)
        self._write([("u1", None, "typed", "start"),
                     ("u2", "u1a", "sdk",
                      "<!-- romp-injected --><!-- romp-system -->[romp] The romp kernel restarted; resumed. "
                      "(nothing here says romp-gist: in a comment)")])
        sysev = next(e for e in self._user_events() if e.get("rompSystem"))
        self.assertNotIn("gist", sysev)

    def test_a_plain_nudge_is_not_flagged(self):
        self._write([("u1", None, "typed", "start"),
                     ("u2", "u1a", "sdk",
                      "<!-- romp-injected -->keep going on the plan\nromp-goal-id: %s:g1" % SID)])
        nudge = next(e for e in self._user_events() if e.get("romp"))
        self.assertFalse(nudge.get("rompSystem"), "a feed nudge is NOT a system notice → stays the nudge bubble")


if __name__ == "__main__":
    unittest.main()
