"""The /clear chat UX (the user 2026-07-27): a cleared conversation must not render as a dead gap.

Before this, the chat pane was the one surface with zero episode awareness (plans/clear-episodes.md):
a /clear wiped the DOM to "No messages yet.", the pre-clear conversation was unreachable, and the
stretch while the CLI minted the fresh transcript had no observable state at all. Now:
  - build_session opens a post-clear episode with a {kind:"clear"} boundary card (above the system
    card, both collapsed client-side), so a cleared session is never events-empty;
  - build_episode serves the PRE-CLEAR conversation for the card's lazy expand, through
    build_session's read-only path_override mode, capped with an honest truncated count, and FAILS
    LOUDLY (an error string, never a silent empty) when the old transcript is missing;
  - the SDK backend brackets the in-flight /clear (SdkSession._clearing) so the chip and the chat
    show "clearing" while it runs (_is_clear_cmd; the tmux TUI /clear keeps its known fork-lane gap).
Synthetic data only.
"""
import inspect
import json
import os
import shutil
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
km = load_source("romp_kernel_clearchat", os.path.join(BIN, "romp-kernel"))
jd = km.jd
sb = load_source("romp_sdk_backend_clearchat",
                      os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
NEWFSID = "66666666-7777-8888-9999-000000000000"
NOW = 1750000000
CLEAR_T = NOW - 60


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(t, uuid, text, parent=None, fsid=SID):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t),
            "sessionId": fsid, "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main",
            "message": {"role": "user", "content": text}}


def _arec(t, uuid, text, parent, fsid=SID):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": iso(t),
            "sessionId": fsid, "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main",
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "text", "text": text}]}}


def _write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))


class ClearChatViewTest(unittest.TestCase):
    def setUp(self):
        # both restored in tearDown: the judge is one module shared by every test module in the process, and
        # it was left aimed at this tmp after rmtree (T282)
        self._saved_state, self._saved_projects = jd.STATE, jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        # the launch cwd must round-trip _proj_dir's realpath+encode (a literal "/tmp/…" string does
        # not on macOS, where /tmp realpaths to /private/tmp) — derive the project dir via jd itself
        self.cwd = os.path.realpath(os.path.join(self._td, "notes-api"))
        os.makedirs(self.cwd, exist_ok=True)
        self.proj = jd._proj_dir(self.cwd)
        self.proj.mkdir(parents=True)
        # discover iterates the names registry: name + launch cwd (mapped to the project dir)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / SID).write_text("web\t" + self.cwd)
        # episode 1 (the anchor file): a small finished conversation, later cleared
        _write_jsonl(self.proj / (SID + ".jsonl"), [
            _urec(NOW - 3600, "u1", "set up the api server"),
            _arec(NOW - 3590, "a1", "done - the notes-api server runs on port 8080", "u1"),
        ])
        # episode 2 (the post-/clear fork): a fresh null-rooted head in a NEW file
        _write_jsonl(self.proj / (NEWFSID + ".jsonl"), [
            _urec(NOW - 50, "n1", "hello again", fsid=NEWFSID),
            _arec(NOW - 40, "n2", "fresh start - what next?", "n1", fsid=NEWFSID),
        ])
        # the SDK registry's lastSid points discover at the new file (the /clear re-point)
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps(
            {"sid": SID, "name": "web", "alive": True, "lastSid": NEWFSID}))
        # the episodes log records both heads; row -1 is the boundary the chat renders
        _write_jsonl(jd.STATE / "episodes" / (SID + ".jsonl"), [
            {"head": "u1", "fsid": SID, "t": NOW - 3600},
            {"head": "n1", "fsid": NEWFSID, "t": CLEAR_T},
        ])

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        jd.PROJECTS = self._saved_projects
        shutil.rmtree(self._td, ignore_errors=True)

    def _events(self):
        m = km.build_session(SID, NOW)
        self.assertIsNotNone(m, "the session must be discoverable")
        return m["events"]

    # ── the boundary card ────────────────────────────────────────────────────────────────────────

    def test_post_clear_chat_opens_with_the_clear_boundary_then_the_system_card(self):
        evs = self._events()
        kinds = [e.get("kind") for e in evs]
        self.assertEqual(kinds[0], "clear", "the boundary card leads (the cleared history predates the frame)")
        self.assertEqual(kinds[1], "system", "the system card follows, so both folds sit at the top")
        clear = evs[0]
        self.assertEqual(clear["uuid"], "clear:n1", "keyed on the episode head - stable across pushes")
        self.assertEqual(clear["clearedAt"], CLEAR_T)
        self.assertEqual(clear["episodes"], 2)
        self.assertIsNone(clear["dropped"], "a boundary that settled nothing names nothing")

    def test_boundary_card_names_the_dropped_cards(self):
        # the settle annotation rides the episodes log keyed to the boundary head (the user
        # 2026-07-27) -> the chat boundary card counts + names what the clear took, so the drop is
        # visible in the chat, not only the bell
        _write_jsonl(jd.STATE / "episodes" / (SID + ".jsonl"), [
            {"head": "u1", "fsid": SID, "t": NOW - 3600},
            {"head": "n1", "fsid": NEWFSID, "t": CLEAR_T},
            {"settleFor": "n1", "t": CLEAR_T,
             "settled": [{"id": SID + ":g1", "text": "Ship the deployment guide"},
                         {"id": SID + ":g2", "text": "Tune the api rate limits"}]},
        ])
        clear = self._events()[0]
        self.assertEqual(clear["kind"], "clear")
        self.assertEqual(clear["dropped"],
                         ["Ship the deployment guide", "Tune the api rate limits"])

    def test_a_cleared_session_is_never_events_empty(self):
        # the exact "No messages yet." lie: the fresh transcript has NOTHING renderable yet
        _write_jsonl(self.proj / (NEWFSID + ".jsonl"), [])
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        evs = self._events()
        self.assertTrue(evs, "a just-cleared session shows the boundary card, not an empty placeholder")
        self.assertIn("clear", [e.get("kind") for e in evs])

    def test_a_single_episode_session_gets_no_boundary_card(self):
        _write_jsonl(jd.STATE / "episodes" / (SID + ".jsonl"),
                     [{"head": "u1", "fsid": SID, "t": NOW - 3600}])
        self.assertNotIn("clear", [e.get("kind") for e in self._events()],
                         "no /clear ever happened - nothing to mark")

    # ── the lazy pre-clear expand ────────────────────────────────────────────────────────────────

    def test_build_episode_serves_the_pre_clear_conversation(self):
        ep = km.build_episode(SID, NOW)
        self.assertEqual(ep["type"], "chatEpisode")
        self.assertFalse(ep.get("error"))
        self.assertEqual(ep.get("truncated"), 0)
        self.assertEqual(ep.get("clearedAt"), CLEAR_T)
        texts = json.dumps(ep["events"])
        self.assertIn("notes-api server runs", texts, "the old episode's reply is in the fold")
        self.assertNotIn("fresh start", texts, "the NEW episode's turns never leak into the old fold")
        kinds = [e.get("kind") for e in ep["events"]]
        self.assertNotIn("clear", kinds, "no boundary/system inserts inside the historical render")
        self.assertNotIn("system", kinds)

    def test_build_episode_missing_transcript_fails_loudly(self):
        (self.proj / (SID + ".jsonl")).unlink()   # the pre-clear file is gone
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        ep = km.build_episode(SID, NOW)
        self.assertIn("missing", ep.get("error", ""), "an unreadable source surfaces an error, never a silent empty")
        self.assertEqual(ep["events"], [])

    def test_build_episode_caps_and_counts_the_cut(self):
        rows = [_urec(NOW - 3600, "u1", "set up the api server")]
        parent = "u1"
        for i in range(km.EPISODE_EVENT_CAP + 40):
            u = "x%d" % i
            rows.append(_arec(NOW - 3599 + i, u, "step %d" % i, parent))
            parent = u
        _write_jsonl(self.proj / (SID + ".jsonl"), rows)
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        ep = km.build_episode(SID, NOW)
        self.assertEqual(len(ep["events"]), km.EPISODE_EVENT_CAP)
        self.assertGreater(ep["truncated"], 0, "the cut is counted, never silent")

    # ── the in-flight bracket ────────────────────────────────────────────────────────────────────

    def test_is_clear_cmd_truth_table(self):
        self.assertTrue(sb._is_clear_cmd("/clear"))
        self.assertTrue(sb._is_clear_cmd("  /clear  "))
        self.assertTrue(sb._is_clear_cmd("/clear now"))
        self.assertFalse(sb._is_clear_cmd("/clearx"))
        self.assertFalse(sb._is_clear_cmd("please /clear"))
        self.assertFalse(sb._is_clear_cmd("/compact"))
        self.assertFalse(sb._is_clear_cmd(""))

    def test_clearing_bracket_sites(self):
        src = inspect.getsource(sb)
        # set on delivery + on a restored queue; cleared by the lastSid flip AND the turn's settle
        self.assertIn("if _is_clear_cmd(text):", src)
        self.assertIn("if any(_is_clear_cmd(t) for t in self._pending):", src)
        flip = src.index("self.backend._update_reg(self.sid, lastSid=fsid)")
        self.assertIn("self._clearing = False", src[flip:flip + 400],
                      "the fork landing ends the bracket (event-based)")

    def test_chip_says_clearing_first(self):
        src = inspect.getsource(km._session_chip)
        self.assertIn('"clearing" if _clearing_now(sid)', src)

    def test_live_clearing_indicator_rides_build_session(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('events.append({"kind": "clearing"})', src)
        # a queued "/clear" folds while the live element represents it (mirror of the /compact fold)
        self.assertIn('== "/clear"', src)


if __name__ == "__main__":
    unittest.main()


class PreClearNotesStayInTheirEpisode(unittest.TestCase):
    """T131 (the user 2026-08-27, screenshot): seventeen pre-clear "Recovered after N retries"
    rows stood in a freshly /clear-ed thread. The side-store notes (recoveries, gave-ups, command
    gestures, effort notes, orphan replies) are time-anchored against transcript atoms, and the
    /clear re-points the transcript to a fresh file — so the first new atom's flush dumped every
    note older than the clear into the new conversation. The floor: the live render drops notes
    at/before the last episode boundary; the EPISODE render keeps them (they live in the boundary
    card's fold, with the rest of the pre-clear conversation). Synthetic data only."""

    def setUp(self):
        # both restored in tearDown: the judge is one module shared by every test module in the process, and
        # it was left aimed at this tmp after rmtree (T282)
        self._saved_state, self._saved_projects = jd.STATE, jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        self.cwd = os.path.realpath(os.path.join(self._td, "notes-api"))
        os.makedirs(self.cwd, exist_ok=True)
        self.proj = jd._proj_dir(self.cwd)
        self.proj.mkdir(parents=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / SID).write_text("web\t" + self.cwd)
        _write_jsonl(self.proj / (SID + ".jsonl"), [
            _urec(NOW - 3600, "u1", "set up the api server"),
            _arec(NOW - 3590, "a1", "done - the notes-api server runs on port 8080", "u1"),
        ])
        _write_jsonl(self.proj / (NEWFSID + ".jsonl"), [
            _urec(NOW - 50, "n1", "hello again", fsid=NEWFSID),
            _arec(NOW - 40, "n2", "fresh start - what next?", "n1", fsid=NEWFSID),
        ])
        (jd.STATE / "sdk").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps(
            {"sid": SID, "name": "web", "alive": True, "lastSid": NEWFSID}))
        _write_jsonl(jd.STATE / "episodes" / (SID + ".jsonl"), [
            {"head": "u1", "fsid": SID, "t": NOW - 3600},
            {"head": "n1", "fsid": NEWFSID, "t": CLEAR_T},
        ])
        # the side-store: a pre-clear recovery storm + a pre-clear /auth gesture (the filmed pair),
        # and one POST-clear recovery that must keep rendering live
        _write_jsonl(jd.STATE / "states" / (SID + ".jsonl"), [
            {"t": NOW - 3000, "retriesRecovered": 1},
            {"t": NOW - 2900, "retriesRecovered": 3},
            {"t": NOW - 2800, "cmdGesture": "/auth login"},
            {"t": NOW - 30, "retriesRecovered": 2},
        ])

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        jd.PROJECTS = self._saved_projects
        shutil.rmtree(self._td, ignore_errors=True)

    def test_live_render_shows_only_the_current_episodes_notes(self):
        events = km.build_session(SID, NOW)["events"]
        retried = [e for e in events if e.get("kind") == "retried"]
        self.assertEqual([e["retries"] for e in retried], [2],
                         "pre-clear recoveries belong to the previous episode's render, not the fresh thread")
        self.assertEqual([e for e in events if e.get("kind") == "cmdGesture"], [],
                         "the pre-clear /auth gesture chip goes with them")

    def test_the_episode_render_keeps_its_own_eras_notes(self):
        got = km.build_episode(SID, NOW)
        self.assertNotIn("error", got or {}, "the pre-clear transcript must resolve")
        kinds = [(e.get("kind"), e.get("retries")) for e in got["events"] if e.get("kind") in ("retried", "cmdGesture")]
        self.assertIn(("retried", 1), kinds, "the boundary card's fold is where the pre-clear notes live")
        self.assertIn(("retried", 3), kinds)
        self.assertIn(("cmdGesture", None), kinds)

    def test_a_single_episode_session_keeps_every_note(self):
        _write_jsonl(jd.STATE / "episodes" / (SID + ".jsonl"),
                     [{"head": "u1", "fsid": SID, "t": NOW - 3600}])
        (jd.STATE / "sdk" / (SID + ".json")).write_text(json.dumps(
            {"sid": SID, "name": "web", "alive": True, "lastSid": SID}))
        events = km.build_session(SID, NOW)["events"]
        self.assertEqual([e["retries"] for e in events if e.get("kind") == "retried"], [1, 3, 2],
                         "no boundary, no floor — the whole history is one episode")
