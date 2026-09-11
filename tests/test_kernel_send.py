#!/usr/bin/env python3
"""POST /send body parsing — the human->agent input channel the Obsidian track-changes
plugin posts to. The kernel then hands the text to whichever backend drives the session
(Claude Code via the SDK, or Codex; Sessions.backend_for), the same delivery the chat
composer's WS sendMessage uses, so the plugin never touches a backend itself. Until the
terminal backend's removal (2026-09-11) that delivery was _tmux_send.
"""
import os
import unittest
from unittest import mock
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_send", os.path.join(BIN, "romp-kernel"))


class ParseSendBody(unittest.TestCase):
    def test_id_and_text(self):
        self.assertEqual(km._parse_send_body(b'{"id":"alpha","text":"hi"}'), {"who": "alpha", "text": "hi"})

    def test_name_is_accepted_as_who(self):
        self.assertEqual(km._parse_send_body(b'{"name":"beta","text":"yo"}'), {"who": "beta", "text": "yo"})

    def test_rejects_missing_or_empty(self):
        self.assertIsNone(km._parse_send_body(b'{"id":"alpha"}'))           # no text
        self.assertIsNone(km._parse_send_body(b'{"text":"hi"}'))            # no id/name
        self.assertIsNone(km._parse_send_body(b'{"id":"alpha","text":""}'))  # empty text
        self.assertIsNone(km._parse_send_body(b'{"id":"","text":"hi"}'))    # empty id

    def test_tag_appends_the_render_hint_marker(self):
        # scheduled/scripted senders (the user 2026-08-18, the nightly optimizer briefing):
        # the "tag" field makes the kernel append the SAME marker `romp send --tag` writes,
        # so the chat dresses the message machine-sent under that label
        self.assertEqual(km._parse_send_body(b'{"id":"alpha","text":"hi","tag":"nightly-optimizer"}'),
                         {"who": "alpha", "text": "hi\n\n<!-- romp-tag: nightly-optimizer -->"})

    def test_bad_tag_fails_the_whole_parse(self):
        # a malformed tag is a 400, never a silent plain delivery — delivering anyway would
        # misattribute the text (fail loudly, 2026-07-03)
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"two words"}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":""}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":123}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"-leading-dash"}'))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":"hi","tag":"' + b"x" * 25 + b'"}'))

    def test_rejects_bad_json_non_object_and_non_string_text(self):
        self.assertIsNone(km._parse_send_body(b'not json'))
        self.assertIsNone(km._parse_send_body(b'[1,2,3]'))
        self.assertIsNone(km._parse_send_body(b''))
        self.assertIsNone(km._parse_send_body(b'{"id":"a","text":123}'))


class SessionList(unittest.TestCase):
    """GET /sessions — the UNIFIED romp session list across every backend (Claude Code via the SDK, and
    Codex) that external tools read (the Obsidian Cmd+M picker + diff chips, the postal bus) instead of
    asking a backend themselves; it replaced the terminal-backend-only listing those tools once shelled tmux
    for. _session_rows assembles each LIVE session from Sessions.live() (the backend query) + the names
    registry + working-notes."""

    def _stub(self, live, notes, names):
        saved = (km.Sessions.live, km._working_notes, km._name_of, km._cwd_of, km._identity_of)
        km.Sessions.live = staticmethod(lambda: live)
        km._working_notes = lambda: notes
        km._name_of = lambda sid: names.get(sid, (sid[:8],))[0]
        km._cwd_of = lambda sid: names[sid][1]
        km._identity_of = lambda sid: names[sid][2:4]
        self.addCleanup(lambda: setattr(km.Sessions, "live", saved[0]))
        self.addCleanup(lambda: (setattr(km, "_working_notes", saved[1]), setattr(km, "_name_of", saved[2]),
                                 setattr(km, "_cwd_of", saved[3]), setattr(km, "_identity_of", saved[4])))

    def test_session_rows_assembles_both_backends(self):
        self._stub(
            live={"sid-c": {"state": "working", "backend": "codex"},
                  "sid-s": {"state": "waiting", "backend": "sdk"}},
            notes={"sid-c": "owns feed.ts"},           # SDK has no working-note yet (P3) → ''
            names={"sid-c": ("alpha", "/work/a", "#112233", "#ffffff"),
                   "sid-s": ("beta", "/work/b", "blue", "white")})
        rows = {r["id"]: r for r in km._session_rows()}
        self.assertEqual(set(rows), {"sid-c", "sid-s"})
        # lastSid = the session's CURRENT transcript fsid (self-identity join, the user 2026-07-27);
        # with no diverged SDK registry it is the sid itself.
        self.assertEqual(rows["sid-c"], {"id": "sid-c", "name": "alpha", "state": "working", "dir": "/work/a",
                                         "bg": "#112233", "fg": "#ffffff", "lastSid": "sid-c",
                                         "compacting": False,          # romp compact --wait polls this
                                         "working": "owns feed.ts", "backend": "codex"})
        self.assertEqual(rows["sid-s"], {"id": "sid-s", "name": "beta", "state": "waiting", "dir": "/work/b",
                                         "bg": "blue", "fg": "white", "lastSid": "sid-s",
                                         "compacting": False,
                                         "working": "", "backend": "sdk"})

    def test_one_rows_helper_exception_never_hides_the_session(self):
        # the per-row guard (2026-08-31, the listing-blink source class): absence from GET /sessions
        # reads as death downstream, so one sid's helper blowing up keeps the session listed as a
        # minimal honest row — and never takes the WHOLE listing down with it
        self._stub(
            live={"sid-c": {"state": "working", "backend": "codex"},
                  "sid-s": {"state": "waiting", "backend": "sdk"}},
            notes={}, names={"sid-s": ("beta", "/work/b", "blue", "white")})
        saved = km._identity_of
        km._identity_of = lambda sid: (_ for _ in ()).throw(RuntimeError("mid-cycle")) \
            if sid == "sid-c" else saved(sid)
        try:
            rows = {r["id"]: r for r in km._session_rows()}
        finally:
            km._identity_of = saved
        self.assertEqual(set(rows), {"sid-c", "sid-s"}, "the failing row stays PRESENT")
        self.assertEqual((rows["sid-c"]["state"], rows["sid-c"]["backend"]), ("working", "codex"),
                         "the minimal row keeps what the live() meta already knew")
        self.assertEqual(rows["sid-s"]["name"], "beta", "the healthy sibling is untouched")

    def test_listing_pays_one_transcript_sweep_not_one_per_row(self):
        # the /sessions latency root (py-spy 2026-08-31): _path_of per row re-ran discover()'s
        # fingerprint validity stats for EVERY live session — ~71% of the route's handler time on a
        # loaded kernel (the p90-3.3s complaint). The listing resolves transcript paths through ONE
        # _sessions() sweep and hands each row its path; a regression to per-row resolution makes
        # the sweep count scale with the row count, which this pins at exactly 1.
        live = {"sid-%d" % i: {"state": "waiting", "backend": "sdk"} for i in range(12)}
        names = {sid: (sid, "/w", "blue", "white") for sid in live}
        self._stub(live=live, notes={}, names=names)
        calls = []
        saved = km._sessions
        km._sessions = lambda now, window=None, forks=True: (calls.append(1), [])[1]
        try:
            rows = km._session_rows()
        finally:
            km._sessions = saved
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(calls), 1, "one transcript-path sweep for the whole listing, not one per row")

    def test_sweep_failure_never_takes_the_listing_down(self):
        # the hoisted path sweep runs OUTSIDE the per-row guard, so its failure needs its own
        # containment (review find, 2026-08-31): a discover raise (names-dir permission fault,
        # remove race) degrades to pathless FULL rows — compacting reads False that build — and
        # never turns GET /sessions into a 500 (absence reads as death downstream).
        self._stub(live={"sid-c": {"state": "working", "backend": "codex"}},
                   notes={}, names={"sid-c": ("alpha", "/w", "#112233", "#ffffff")})
        saved = km._sessions
        km._sessions = lambda now, window=None, forks=True: (_ for _ in ()).throw(OSError("names dir EACCES"))
        try:
            rows = km._session_rows()
        finally:
            km._sessions = saved
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "alpha", "a FULL row, not the minimal fallback")
        self.assertFalse(rows[0]["compacting"])

    def test_empty_when_no_live_sessions(self):
        self._stub(live={}, notes={}, names={})
        self.assertEqual(km._session_rows(), [])


class WorkingNoteStore(unittest.TestCase):
    """The backend-agnostic working-note store (working/<sid> files): the postal bus's set_working goes
    through the kernel (Sessions.set_working_note, served at POST /working), works for ANY sid whichever
    backend drives it (Claude Code via the SDK, or Codex), and the note surfaces in _working_notes (→ GET
    /sessions). It replaced the terminal backend's @romp-working tmux variable, gone with that backend
    (2026-09-11)."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._saved = km.WORKING_DIR
        km.WORKING_DIR = Path(tempfile.mkdtemp()) / "working"

    def tearDown(self):
        km.WORKING_DIR = self._saved

    def test_set_read_and_clear_round_trip(self):
        km.Sessions.set_working_note("sid-x", "owns feed.ts")
        self.assertEqual(km.Sessions.working_note("sid-x"), "owns feed.ts")
        self.assertEqual(km._working_notes(), {"sid-x": "owns feed.ts"})
        km.Sessions.set_working_note("sid-x", "")          # clear → the claim is lifted
        self.assertEqual(km.Sessions.working_note("sid-x"), "")
        self.assertEqual(km._working_notes(), {})

    def test_any_backend_sid_can_publish(self):
        # no backend gate: an SDK session's sid stores + reads the same way a Codex one does
        km.Sessions.set_working_note("sdk-sid", "drafting api")
        km.Sessions.set_working_note("codex-sid", "triaging tests")
        self.assertEqual(km._working_notes().get("sdk-sid"), "drafting api")
        self.assertEqual(km._working_notes().get("codex-sid"), "triaging tests")

    def test_rejects_path_traversal_sid(self):
        km.Sessions.set_working_note("../evil", "x")        # sid is a path component → must not escape the store
        self.assertEqual(km._working_notes(), {})
        self.assertEqual(km.Sessions.working_note("../evil"), "")

    def test_post_working_endpoint_is_wired(self):
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn('u.path == "/working"', src, "POST /working routes set_working through the kernel")
        self.assertIn("Sessions.set_working_note(sid, str(body.get(\"text\") or \"\"))", src)


if __name__ == "__main__":
    unittest.main()
