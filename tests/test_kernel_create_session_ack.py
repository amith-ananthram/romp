#!/usr/bin/env python3
"""Creating/opening a session is ACK-FAST (the user 2026-07-14, who asked why it took so long to open a new
SDK-backed session). The measured 7-10s was NOT the CLI boot (spawn = file writes ~0.2s; connect is
threaded, a booting claude process ~0.4s in; the raw SDK connect itself is ~1.5s) — it was the handler's
inline _push_all(): a new session invalidates the discover cache, so the build re-scans and re-serializes
the WHOLE fleet synchronously on the WS thread before the focus frame is sent, duplicating the rebuild the
pusher (already woken by spawn's poke) was doing in parallel. The contract pinned here: create/open paths
reveal FIRST (setActive holds the sid client-side, so the tab lands already-selected when the pusher's
build arrives) and wake the pusher via the dirty-mark — never a synchronous fleet build on the WS thread
(the push-architecture rule, 2026-07-05). Synthetic only — no real session data."""
import os
import tempfile
import unittest
from romp_load import load_source

os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # isolate: importing the kernel must not touch live state
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
km = load_source("romp_kernel_createack", os.path.join(BIN, "romp-kernel"))

KSRC = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()


class _FakeSdk:
    def __init__(self):
        self.calls = []

    # mirrors the real SdkBackend.spawn: the kernel now picks a fleet-aware identity colour and hands it
    # down (test_kernel_color_pick.py), so the fake must accept it exactly like the real one does
    def spawn(self, nm, cwd, bg="", fg="", sid=None, auth="", env=None):
        self.calls.append(("spawn", nm, cwd))
        return "11111111-2222-3333-4444-555555555555"

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return True

    def live_sessions(self):
        return {}                     # no live SDK sessions → the colour pick sees an empty fleet


class CreateSessionAckFast(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeSdk()
        self.events = []
        self._saved = {n: getattr(km, n) for n in
                       ("_sdk", "_reveal_chat_for", "_mark_views_dirty", "_push_all")}
        km._sdk = lambda: self.fake
        km._reveal_chat_for = lambda c, m: self.events.append(("reveal", c, m))
        km._mark_views_dirty = lambda: self.events.append(("dirty",))
        km._push_all = lambda: self.events.append(("PUSH_ALL",))

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)

    def test_create_reveals_first_and_never_builds_inline(self):
        asker = {"app": "chat", "wid": "win-A"}   # the dashboard whose picker asked — reveals are per-viewer
        sid, extra = km._create_sdk_session("newsesh", "/tmp", client=asker)   # (sid, applied-prefs echo) since 2026-08-16
        self.assertEqual(sid, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(extra, {}, "no prefs asked for -> nothing applied, nothing echoed")
        self.assertEqual(self.fake.calls[0][0], "spawn")
        self.assertEqual(self.fake.calls[1], ("connect", sid), "eager-connect still runs (model lists immediately)")
        kinds = [e[0] for e in self.events]
        self.assertNotIn("PUSH_ALL", kinds,
                         "a synchronous fleet build on the WS thread is the 7-10s open delay — never inline")
        self.assertIn("dirty", kinds, "the pusher is woken to ship the new tab")
        self.assertLess(kinds.index("reveal"), kinds.index("dirty"),
                        "focus is sent before the (async) build — the tab lands already-selected")
        _, who, reveal = next(e for e in self.events if e[0] == "reveal")
        self.assertIs(who, asker, "…selected on the ASKING window alone (the per-viewer rule)")
        self.assertEqual(reveal, {"type": "focus", "id": sid})

    def test_createsession_handler_has_no_inline_push(self):
        # source pin: the whole createSession dispatch block (both the already-running reopen and the
        # SDK-create branch) wakes the pusher instead of building synchronously
        block = KSRC.split('"createSession"')[1].split('"requestSessions"')[0]
        self.assertNotIn("_push_all()", block, "createSession paths must never build the fleet inline")
        self.assertIn("_create_sdk_session", block)
        self.assertIn("_mark_views_dirty()", block)


if __name__ == "__main__":
    unittest.main()


class TagsLandBeforeTheDirectPush(unittest.TestCase):
    """Tab groups on tags (#1007): a child created with a parent and named tags carries them BEFORE the
    direct push, so the first tabOrder frame already sections the new tab. Both real create paths are
    executed here — the SDK one and the Codex one — with the push stubbed to snapshot the child's tag
    names AT push time; moving the tag step after the push (or dropping it) fails these. Synthetic
    sids and tag names only."""
    PARENT = "66666666-7777-4888-8999-000000000000"
    CHILD_SDK = "11111111-2222-3333-4444-555555555555"
    CHILD_CODEX = "22222222-3333-4444-5555-666666666666"

    class _FakeCodex:
        def __init__(self, sid):
            self.sid = sid
        def spawn(self, nm, cwd, bg="", fg="", *a, **kw):
            return self.sid
        def live_sessions(self):
            return {}

    def setUp(self):
        self.at_push = {}
        self._saved = {n: getattr(km, n) for n in
                       ("_sdk", "_codex", "_reveal_chat_for", "_mark_views_dirty", "_push_all", "_push_session_now")}
        km._sdk = lambda: _FakeSdk()
        km._codex = lambda: self._FakeCodex(self.CHILD_CODEX)
        km._reveal_chat_for = lambda c, m: None
        km._mark_views_dirty = lambda: None
        km._push_all = lambda: None
        km._push_session_now = lambda sid: self.at_push.__setitem__(str(sid), self._tags_of(sid))
        # a clean tag store holding one tag with the parent in it
        km._set_timeline_views({"tags": [], "order": []} if "order" in km._timeline_views() else {"tags": []})
        km._edit_tag("alpha", add=[self.PARENT])

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)

    @staticmethod
    def _tags_of(sid):
        return sorted(t["name"] for t in km._timeline_views()["tags"]
                      if any(m["host"] == "" and m["sid"] == str(sid) for m in t["members"]))

    def test_an_sdk_child_is_in_its_parents_tag_and_the_named_one_when_its_tab_is_pushed(self):
        sid, extra = km._create_sdk_session("child", "/tmp", parent=self.PARENT, tags=("beta",))
        self.assertEqual(sid, self.CHILD_SDK)
        self.assertEqual(self.at_push.get(sid), ["alpha", "beta"],
                         "inherited + named tags are in the store BEFORE the direct push, not a cycle later")
        self.assertEqual(sorted(extra["tags"]), ["alpha", "beta"], "and the ack echoes the same names")

    def test_a_codex_child_is_in_its_parents_tag_and_the_named_one_when_its_tab_is_pushed(self):
        sid, extra = km._create_codex_session_inner("child2", "/tmp", parent=self.PARENT, tags=("beta",))
        self.assertEqual(sid, self.CHILD_CODEX)
        self.assertEqual(self.at_push.get(sid), ["alpha", "beta"])
        self.assertEqual(sorted(extra["tags"]), ["alpha", "beta"])
