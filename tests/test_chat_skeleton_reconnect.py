#!/usr/bin/env python3
"""Reconnect skeletons — the kernel half (the user 2026-09-07, whose panes redialed after a long freeze and
pulled every session whole for the one tab on screen).

When a pane's socket died while its browser tab was away (a long freeze, a laptop sleep, a network change),
the redial used to be served as a client that holds nothing: a full {type:"session"} for EVERY tab — 17 frames,
~9 MB on the measured board — for one tab on screen. The page still holds every session it had; it only needs
the one it shows. Now a redial after the bundle's ready declares itself (?reconnect=1, test_pane_shim_return.py) and
the kernel sends THAT client the tab strip with a `skeleton` list (every listed tab but the active one, cheapest
transcript first), the active tab's full session, and a small status frame per skeleton tab so its chip stays
honest. A skeleton tab loads on the user's click (activeTab / needFull), on the client's idle prefetch
(needFull), or on any full the kernel sends for another reason; `ready` (a renderer that just evaluated, so it
holds nothing) clears the whole set. A client that declares no reconnect gets today's frames, byte for byte.

Drives the REAL _push / _push_session_now / _confirm_close_now / Handler._dispatch_ws / Handler._ws over fake
clients (the test_tab_meta_push.py pattern), with build_session stubbed to synthetic payloads and real temp
transcript files of distinct sizes (the size is the kernel's cost proxy). Synthetic only: the notes-api demo
world (web/api/tests/docs), placeholder UUIDs, TESTHOST.
"""
import contextlib
import inspect
import io
import json
import os
import re
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_skeleton", os.path.join(BIN, "romp-kernel"))

S1 = "11111111-2222-3333-4444-555555555551"   # web   — the tab the page is looking at (mid-size transcript)
S2 = "11111111-2222-3333-4444-555555555552"   # api   — the BIGGEST transcript
S3 = "11111111-2222-3333-4444-555555555553"   # tests — the smallest transcript
S4 = "11111111-2222-3333-4444-555555555554"   # docs  — just created: no transcript on disk yet
GONE = "11111111-2222-3333-4444-555555555559"  # an ended session, listed nowhere
NAMES = {S1: "web", S2: "api", S3: "tests", S4: "docs"}
TAB_ORDER = [S2, S1, S3, S4]                  # big, mid, small — the tab order is NOT the size order
SIZES = {S2: 3000, S1: 2000, S3: 1000}        # transcript bytes; S4 has none


def _sess(sid, n, state):
    """A synthetic build_session payload: n events (well under WIRE_TAIL, so a full send is the whole thing)."""
    return {"type": "session", "id": sid, "name": NAMES[sid],
            "events": [{"kind": "assistant", "uuid": "u%d" % i, "md": "m%d" % i} for i in range(n)],
            "status": {"state": state, "sinceEpoch": None}, "ledger": None}


class _Self:
    """The handler's `self` for _dispatch_ws: only _push_one is reached by the frames driven here. Records
    the call; runs `push_one` when given one (the real body is _push([client], connect=True))."""
    def __init__(self, push_one=None):
        self.calls = []
        self._po = push_one

    def _push_one(self, client):
        self.calls.append(client)
        if self._po:
            self._po(client)


def _fake_self(path):
    """A connect handler with a peer that closes at once (the test_view_deltas.py HandlerWiring shape)."""
    class FakeSelf:
        headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        rfile = io.BytesIO(); wfile = io.BytesIO()
        connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
        close_connection = False
        def send_response(self, *a): pass
        def send_header(self, *a): pass
        def end_headers(self): pass
    FakeSelf.path = path
    return FakeSelf()


class SkeletonReconnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid, n in SIZES.items():          # real files: os.path.getsize is the kernel's ranking, so it must stat
            p = os.path.join(self.tmp, sid + ".jsonl")
            with open(p, "w") as f:
                f.write("x" * n)              # content is irrelevant to the kernel here; the SIZE is the cost proxy
            self.paths[sid] = p
        self.paths[S4] = os.path.join(self.tmp, S4 + ".jsonl")   # never written: the transcript-less session
        self.SESS = {S1: _sess(S1, 5, "working"), S2: _sess(S2, 7, "working"),
                     S3: _sess(S3, 3, "waiting"), S4: _sess(S4, 0, "waiting")}
        self._saved = (km._chat_tab_sessions, km._tmux_sessions, km._cached_feed, km.build_session,
                       km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients))
        km._chat_tab_sessions = lambda now, tmux: [
            {"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in TAB_ORDER]
        km._tmux_sessions = lambda: {}
        km._cached_feed = lambda *a, **k: None          # no feed build — the chat frames are what is pinned
        self.built = []

        def build(sid, now, tmux=None, **kw):
            self.built.append(sid)
            return json.loads(json.dumps(self.SESS[sid]))   # a fresh copy per build, as the real builder returns
        km.build_session = build
        km._comments_frame = lambda sid, tmux: None
        km._push_subagents = lambda clients, now, tmux: None
        km.NAMES = Path(self.tmp) / "names"
        km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._built_chat.clear()
        km._prev_chat_events.clear()
        km._prev_chat_ledger.clear()
        del km._clients[:]
        km._pusher_wake.clear()

    def tearDown(self):
        (km._chat_tab_sessions, km._tmux_sessions, km._cached_feed, km.build_session,
         km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, clients) = self._saved
        del km._clients[:]
        km._clients.extend(clients)
        km._built_chat.clear()
        km._prev_chat_events.clear()
        km._prev_chat_ledger.clear()

    # ── helpers ──
    def _client(self, **kw):
        frames = []
        c = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)),
             "_frames": frames}
        c.update(kw)
        return c

    @staticmethod
    def _frames(c, typ=None):
        return [f for f in c["_frames"] if typ is None or f["type"] == typ]

    def _sessions(self, c):
        return [f["id"] for f in self._frames(c, "session")]

    def _statuses(self, c):
        return [(f["id"], f["status"]) for f in self._frames(c, "status")]

    def _tab_orders(self, c):
        return self._frames(c, "tabOrder")

    # ── §4.1 item 1 ──
    def test_00a_a_sid_already_held_whole_is_never_listed_when_the_set_resolves(self):
        # a full that won the race (a create's _push_session_now landing between the flag's pop and the set's write)
        # used to be re-listed as skeleton and then starved of tails (review find 2026-09-07): the resolve is atomic
        # under the client lock now, and a sid echat already holds is excluded outright
        import inspect
        c = self._client(active=S1, reconnect=True)
        c.setdefault("echat", {})[S3] = ("11111111-2222-3333-4444-555555555553", 0)
        km._push([c])
        self.assertEqual(self._tab_orders(c)[0]["skeleton"], [S2], "S3 is held whole → not a skeleton")
        self.assertEqual(c["skeleton"], {S2})
        self.assertTrue(c["skeleton"].isdisjoint(c["echat"]), "the two stores never both hold a sid")
        src = inspect.getsource(km._resolve_reconnect)
        self.assertLess(src.index("with _client_lock(c):"), src.index('c.pop("reconnect"'), "flag, stats and write: one locked step")

    def test_00b_a_skeleton_sid_whose_session_left_the_strip_leaves_the_set_and_its_status_slot(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        self.assertEqual(c["skeleton"], {S2, S3})
        orig = km._chat_tab_sessions
        km._chat_tab_sessions = lambda now, tmux: [s for s in orig(now, tmux) if s["sid"] != S3]   # S3 ended
        try:
            km._push([c])
        finally:
            km._chat_tab_sessions = orig
        self.assertEqual(c["skeleton"], {S2}, "a session the strip no longer lists is not a skeleton (review find 2026-09-07)")
        self.assertEqual(self._tab_orders(c)[-1]["skeleton"], [S2], "…and no strip names a sid its order lacks")
        self.assertNotIn(("status", S3), c["sent"], "its status slot went with it")

    def test_01_a_reconnecting_client_gets_the_strip_with_skeleton_and_one_full(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        self.assertEqual(self._sessions(c), [S1, S4],
                         "one full for the tab on screen — plus the transcript-less one, which is never a skeleton")
        to = self._tab_orders(c)
        self.assertEqual(len(to), 1)
        self.assertEqual(to[0]["order"], TAB_ORDER)
        self.assertEqual(to[0]["skeleton"], [S3, S2],
                         "ascending transcript size (small, big) — NOT tab order (big, small), and never the active")
        self.assertEqual(sorted(self._statuses(c)),
                         sorted([(S2, self.SESS[S2]["status"]), (S3, self.SESS[S3]["status"])]),
                         "one status frame per skeleton sid, carrying that session's status")
        self.assertEqual(c["skeleton"], {S2, S3})
        self.assertEqual(c["skeletonOrder"], [S3, S2])
        self.assertEqual(set(c["echat"]), {S1, S4}, "echat knows only the tabs that were sent whole")
        for sid in (S2, S3):
            self.assertNotIn(("chat", sid), c["sent"], "a skeleton sid never took the chat slot")
            self.assertIn(("status", sid), c["sent"], "…it rode its own status slot")
        self.assertIsNone(c.get("reconnect"), "one-shot: the first strip sender consumed it")
        types = [f["type"] for f in c["_frames"]]
        self.assertNotIn("chatTail", types)
        self.assertLess(types.index("tabOrder"), types.index("session"), "strip first")
        first_sess = self._frames(c, "session")[0]
        self.assertEqual(first_sess["id"], S1, "the active tab's full is the first session frame")
        self.assertLess(c["_frames"].index(first_sess), c["_frames"].index(self._frames(c, "status")[0]),
                        "the status frames trail the active full")

    # ── item 2 ──
    def test_02_the_next_cycle_sends_no_skeleton_sid_and_its_status_dedups(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        c["_frames"].clear()
        km._push([c])
        self.assertEqual([f for f in c["_frames"] if f["type"] in ("session", "chatTail") and f["id"] in (S2, S3)],
                         [], "no chat of any shape for a skeleton sid")
        self.assertEqual(self._statuses(c), [], "an unchanged status is deduped on its slot")
        for sid in (S2, S3):
            self.assertIsNone(km._built_chat[sid][2],
                              "the lazy full serialization was never materialized for a skeleton sid")
        self.assertIsNotNone(km._built_chat[S1][2], "…while the active tab's full send did materialize it")
        # a real transition: the status changes WITH the transcript, as it does live (the build cache keys on the
        # file stat) → exactly one status frame, for that sid, and still no full
        self.SESS[S2]["status"]["state"] = "waiting"
        with open(self.paths[S2], "a") as f:
            f.write("y")
        c["_frames"].clear()
        km._push([c])
        self.assertEqual(self._statuses(c), [(S2, {"state": "waiting", "sinceEpoch": None})])
        self.assertEqual(self._sessions(c), [], "still no full for a skeleton sid")
        self.assertEqual(c["skeleton"], {S2, S3}, "a status change releases nothing")

    # ── item 3 ──
    def test_03_activetab_releases_exactly_one_and_wakes_the_pusher(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        c["_frames"].clear()
        km._pusher_wake.clear()
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
        self.assertEqual(c["skeleton"], {S3}, "only the clicked tab left the set")
        self.assertEqual(c["active"], S2)
        self.assertTrue(km._pusher_wake.is_set(), "the click is the event the next cycle rides")
        self.assertNotIn(("status", S2), c["sent"], "its status slot went with it")
        km._push([c])
        self.assertEqual(self._sessions(c), [S2], "the released tab's full — and no S3")
        to = self._tab_orders(c)
        self.assertEqual(len(to), 1, "the strip is re-sent: its skeleton list changed, so its sig changed")
        self.assertEqual(to[0]["skeleton"], [S3])
        self.assertEqual(self._statuses(c), [], "S3's status is unchanged → deduped")

    def test_03b_activetab_naming_a_remote_or_unknown_id_releases_nothing(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": "gpu1:" + S2}, c)
        self.assertEqual(c["skeleton"], {S2, S3})
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": None}, c)
        self.assertEqual(c["skeleton"], {S2, S3})

    # ── item 4 ──
    def test_04_needfull_releases_exactly_one_and_repairs_now(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)   # S2 loads on its click…
        km._push([c])
        c["_frames"].clear()
        h = _Self()                                                             # …S3 on the idle prefetch's ask
        km.Handler._dispatch_ws(h, {"type": "needFull", "id": S3, "why": "prefetch"}, c)   # `why` is the client's diagnostic; ignored here
        self.assertEqual(c["skeleton"], set(), "the set emptied")
        self.assertNotIn(("status", S3), c["sent"], "_client_reset_chat_sid popped the status slot with the release")
        self.assertEqual(h.calls, [c], "the repair push ran on the handler thread, not on the next tick")
        km._push([c], connect=True)                                             # what that _push_one does
        self.assertEqual(self._sessions(c), [S3])
        to = self._tab_orders(c)
        self.assertEqual(len(to), 1, "the strip changed (no set) → re-sent")
        self.assertEqual(to[0].get("skeleton"), [], "an empty set is SAID as [] once a set has existed: the federated merge keeps a host's last list on an absent key")

    # ── item 5 ──
    def test_05_a_push_session_now_full_releases(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        c["_frames"].clear()
        km._clients.append(c)
        km._push_session_now(S3)                       # a create / handshake for a tab the page holds as skeleton
        self.assertEqual(self._sessions(c), [S3], "the full went (change_from 0 → always the full form)")
        self.assertEqual(c["skeleton"], {S2}, "…and released S3 through _send_chat_locked")
        self.assertIn(("chat", S3), c["sent"])
        self.assertNotIn(("status", S3), c["sent"])
        self.assertEqual(self._tab_orders(c), [], "its strip was identical to the one already held → deduped")
        c["_frames"].clear()
        km._push([c])
        self.assertEqual(self._tab_orders(c)[0]["skeleton"], [S2], "the next cycle's strip says so")
        self.assertEqual(self._sessions(c), [], "S3 is now an ordinary held tab: no re-send")

    # ── item 6 ──
    def test_06_a_fresh_client_is_unchanged(self):
        c = self._client(active=S1)
        km._push([c])
        self.assertEqual(sorted(self._sessions(c)), sorted(TAB_ORDER), "every full, as today")
        self.assertEqual(self._statuses(c), [], "no status frames for a client that declared no reconnect")
        to = self._tab_orders(c)
        self.assertEqual(len(to), 1)
        self.assertEqual(set(to[0]), {"type", "order", "tabs", "views", "live", "selfHost"},
                         "today's frame, key for key (T258's `live` rides every strip; `selfHost` names the viewing "
                         "kernel since 2026-09-06): no new key for a client that did not declare a reconnect")
        for sid in TAB_ORDER:
            self.assertIn(("chat", sid), c["sent"])
        self.assertNotIn("skeleton", c)
        self.assertNotIn("skeletonOrder", c)
        self.assertFalse([k for k in c["sent"] if k[0] == "status"])

    def test_06b_ready_pops_the_set_and_the_repush_is_every_full(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        self.assertEqual(c["skeleton"], {S2, S3})
        c["_frames"].clear()
        c["reconnect"] = True                          # whatever the flag's state, the reset pops it before its push
        h = _Self(lambda cl: km._push([cl], connect=True))   # the real _push_one body
        km.Handler._dispatch_ws(h, {"type": "ready"}, c)
        for k in ("skeleton", "skeletonOrder", "reconnect"):
            self.assertNotIn(k, c, k)
        self.assertFalse([k for k in c["sent"] if k[0] == "status"], "every status slot went with the set")
        self.assertEqual(h.calls, [c])
        self.assertEqual(sorted(self._sessions(c)), sorted(TAB_ORDER), "the following push sends every full")
        self.assertNotIn("skeleton", self._tab_orders(c)[0], "no set → no key")

    # ── item 7 ──
    def test_07_no_active_hint_means_no_skeleton(self):
        c = self._client(reconnect=True)
        km._push([c])
        self.assertEqual(sorted(self._sessions(c)), sorted(TAB_ORDER), "the kernel cannot know what the page shows → everything")
        self.assertNotIn("skeleton", self._tab_orders(c)[0])
        self.assertNotIn("skeleton", c)
        self.assertIsNone(c.get("reconnect"), "consumed all the same")

    # ── item 8 ──
    def test_08_dedup_slots_flip_from_status_to_chat_on_release(self):
        c = self._client(active=S1, reconnect=True)
        km._push([c])
        self.assertIn(("status", S2), c["sent"])
        self.assertNotIn(("chat", S2), c["sent"])
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
        self.assertNotIn(("status", S2), c["sent"], "the release drops the status slot at once")
        km._push([c])
        self.assertIn(("chat", S2), c["sent"])
        self.assertNotIn(("status", S2), c["sent"])

    # ── item 9 ──
    def test_09_the_handshake_records_the_flag_and_wakes_the_pusher(self):
        for path, expect in (("/ws?app=chat&delta=1&iid=page-9&active=%s&reconnect=1" % S1, True),
                             ("/ws?app=chat&delta=1&iid=page-9&active=%s" % S1, False)):
            got = []
            real_reg, real_recv = km._register_ws_client, km._ws_recv
            km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
            km._ws_recv = lambda rfile: (0x8, b"", True)              # the peer closes at once
            km._pusher_wake.clear()
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    km.Handler._ws(_fake_self(path))
            finally:
                km._register_ws_client, km._ws_recv = real_reg, real_recv
                for c in got:
                    if c in km._clients:
                        km._clients.remove(c)
            self.assertEqual(len(got), 1, path)
            c = got[0]
            self.assertEqual(c.get("active"), S1)
            self.assertEqual(c.get("iid"), "page-9")
            self.assertTrue(c.get("delta"))
            if expect:
                self.assertIs(c.get("reconnect"), True, path)
                self.assertTrue(km._pusher_wake.is_set(), "the reconnect is the event; no backstop wait")
            else:
                self.assertIsNone(c.get("reconnect"), path)
                self.assertFalse(km._pusher_wake.is_set(), "a fresh page wakes nothing new")

    # ── item 10 ──
    def test_10_source_pins_every_strip_sender_resolves_and_uses_the_one_builder(self):
        src = inspect.getsource(km)
        self.assertEqual(src.count('{"type": "tabOrder"'), 1,
                         "exactly one tabOrder literal in the kernel — inside _tab_order_frame, the builder that "
                         "carries the skeleton list; a hand-built frame would erase the set on the client")
        self.assertIn('{"type": "tabOrder"', inspect.getsource(km._tab_order_frame))
        for fn in (km._push, km._push_session_now, km._confirm_close_now):
            s = inspect.getsource(fn)
            self.assertIn("_resolve_reconnect(c, chat_list)", s, fn.__name__)
            self.assertIn("_send_tab_order(c, tab_order, tab_meta, tmux)", s, fn.__name__)
            self.assertLess(s.index("_resolve_reconnect(c, chat_list)"), s.index("_send_tab_order(c, tab_order, tab_meta, tmux)"),
                            fn.__name__ + ": resolve BEFORE the strip")
        i = src.find('msg.get("type") == "ready"')
        body = src[i:i + 2600]
        self.assertNotIn("_send_tab_order(client", body,
                         "the ready arm sends no strip of its own: its connect push (_push_one) resolves the flag and sends the strip")
        self.assertNotIn('client["send"](json.dumps({"type": "tabOrder"', src, "no strip bypasses the builder")
        i = src.find('msg.get("type") == "activeTab"')
        body = src[i:i + 700]
        self.assertIn('_release_skeleton(client, str(msg["id"]))', body)
        self.assertLess(body.index("_release_skeleton("), body.index("_pusher_wake.set()"),
                        "release, THEN wake — the woken cycle must see the released sid")
        s = inspect.getsource(km._send_chat_locked)
        self.assertIn("_release_skeleton_locked(c, sid)", s)
        self.assertLess(s.index("head_from = max(0, total - WIRE_TAIL)"), s.index("_release_skeleton_locked(c, sid)"),
                        "in the FULL branch — after the tail branch has returned")
        self.assertIn("_release_skeleton_locked(client, sid)", inspect.getsource(km._client_reset_chat_sid))
        s = inspect.getsource(km._client_reset_chat_base)
        for k in ('client.pop("skeleton", None)', 'client.pop("skeletonOrder", None)',
                  'client.pop("reconnect", None)', 'k[0] in ("chat", "status", "taborder")'):
            self.assertIn(k, s)
        s = inspect.getsource(km._push)
        self.assertIn("_send_chat_or_status(c, m, ms, change_from, led_changed)", s)
        self.assertNotIn("= _send_chat(c, m, ms, change_from, led_changed)", s,
                         "the pusher's per-client send goes through the skeleton-aware twin")
        self.assertIn('+((everConnected&&bundleReady&&!readyQueued)?"&reconnect=1":"")', km._shim("chat", 1),
                      "the shim declares the redial once its bundle's ready has left on a socket")
        s = inspect.getsource(km.Handler._ws)
        self.assertIn('reconnect = (q.get("reconnect") or [""])[0] == "1"', s)
        self.assertIn('client["reconnect"] = True', s)
        self.assertLess(s.index("_register_ws_client(client)"),
                        s.index('if client.get("reconnect"):\n            _pusher_wake.set()'),
                        "registered first, then woken — the pusher must find the client in _clients")

    def test_10b_a_close_confirmation_as_the_first_strip_already_carries_the_set(self):
        # the behavioral twin: the ≤1-cycle gap between the handshake and the pusher's first pass, filled by an
        # off-cycle strip — it must not paint the page's stale sessions as loaded tabs
        c = self._client(active=S1, reconnect=True)
        km._clients.append(c)
        self.assertTrue(km._confirm_close_now(GONE))
        to = self._tab_orders(c)
        self.assertEqual(len(to), 1)
        self.assertEqual(to[0]["skeleton"], [S3, S2])
        self.assertEqual(c["skeleton"], {S2, S3})
        self.assertIsNone(c.get("reconnect"), "consumed by the confirmation, so the pusher's pass resolves nothing new")
        c["_frames"].clear()
        km._push([c])
        self.assertEqual(self._tab_orders(c), [], "the pusher's identical strip dedups")
        self.assertEqual(self._sessions(c), [S1, S4], "…and its fulls are exactly the reconnect set's complement")

    def test_10c_skeleton_for_ranks_by_size_skips_the_active_and_the_transcript_less(self):
        rows = km._chat_tab_sessions(0, {})
        self.assertEqual(km._skeleton_for({}, S1, rows), [S3, S2])
        self.assertEqual(km._skeleton_for({}, S3, rows), [S1, S2])
        self.assertEqual(km._skeleton_for({}, "gpu1:" + S1, rows), [S3, S1, S2],
                         "a remote/viewer/closed hint matches nothing: every local transcript is skeleton")
        self.assertEqual(km._skeleton_for({}, S1, rows + [{"sid": GONE, "path": None}]), [S3, S2],
                         "a path-less row is skipped, not a crash")

    # ── item 11 ──
    def test_11_every_touch_of_the_set_is_under_the_clients_slot_lock(self):
        src = inspect.getsource(km)
        lines = src.splitlines()

        def owners(token):
            """The defs whose bodies mention `token` (their own def line excluded)."""
            cur, found = None, set()
            for ln in lines:
                m = re.match(r"^\s*def (\w+)\(", ln)
                if m:
                    cur = m.group(1)
                    continue
                if token in ln:
                    found.add(cur)
            return found

        self.assertEqual(owners('"skeleton"') | owners('"skeletonOrder"'),
                         {"_client_reset_chat_base", "_release_skeleton_locked", "_resolve_reconnect",
                          "_tab_order_frame", "_send_chat_or_status", "_send_tab_order"},
                         "the set is touched only in its helpers — a new site must join this list AND take the lock")
        for name in ("_client_reset_chat_base", "_resolve_reconnect", "_send_chat_or_status", "_send_tab_order"):
            s = inspect.getsource(getattr(km, name))
            self.assertLess(s.index("with _client_lock("), s.index('"skeleton"'), name + ": the lock comes first")
        # the two lock-free helpers are reached only from bodies that hold the lock
        self.assertEqual(owners("_release_skeleton_locked("),
                         {"_release_skeleton", "_send_chat_locked", "_client_reset_chat_sid"})
        self.assertEqual(owners("_tab_order_frame("), {"_send_tab_order"})
        for name in ("_release_skeleton", "_client_reset_chat_sid", "_send_tab_order"):
            s = inspect.getsource(getattr(km, name))
            self.assertLess(s.index("with _client_lock("), s.index("_release_skeleton_locked(" if name != "_send_tab_order" else "_tab_order_frame("), name)
        self.assertEqual(owners("_send_chat_locked("), {"_send_chat", "_send_chat_or_status"},
                         "_send_chat_locked has no lock-free caller")
        for name in ("_send_chat", "_send_chat_or_status"):
            s = inspect.getsource(getattr(km, name))
            self.assertLess(s.index("with _client_lock("), s.index("_send_chat_locked("), name)


if __name__ == "__main__":
    unittest.main()
