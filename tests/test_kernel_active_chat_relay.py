#!/usr/bin/env python3
"""The active-chat relay (T347, the user 2026-09-11, who wanted the focused session's cards on top of the feed).

The feed's focused-session section is a view of the chat pane's active tab. The chat pane already posts
{type: "activeTab", id} on every tab switch; the kernel now files that sid under the poster's dashboard-window id
(wid — one window's panes share it) in _ACTIVE_CHAT_BY_WID and sends the same window's feed clients
{type: "activeChat", id: sid|null} on the ("activeChat",) dedup slot; a feed whose bundle says ready is sent the
current value ahead of its connect push, so a reloaded feed learns the focus without waiting for a tab switch.
Nothing else in the push cycle changes, and no card moves: the section is a view.

Drives the REAL Handler._dispatch_ws over fake clients (the test_chat_skeleton_reconnect.py shape). Synthetic
only: placeholder session ids, dashboard ids W1/W2/W3.
"""
import inspect
import json
import os
import tempfile
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_active_chat_relay", os.path.join(BIN, "romp-kernel"))

WEB = "aaaaaaaa-1111-4111-8111-111111111111"    # the notes-api demo world's `web` session
API = "aaaaaaaa-2222-4222-8222-222222222222"    # …and its `api` session


class _Self:
    """The handler's `self` for _dispatch_ws: the ready arm's connect push is the one method reached here."""
    def __init__(self):
        self.calls = []

    def _push_one(self, client):
        self.calls.append(client)


def _dispatch(msg, client):
    km.Handler._dispatch_ws(_Self(), msg, client)


class ActiveChatRelay(unittest.TestCase):
    def setUp(self):
        self._clients = list(km._clients)
        self._record = dict(km._ACTIVE_CHAT_BY_WID)
        del km._clients[:]
        km._ACTIVE_CHAT_BY_WID.clear()
        km._pusher_wake.clear()

    def tearDown(self):
        del km._clients[:]
        km._clients.extend(self._clients)
        km._ACTIVE_CHAT_BY_WID.clear()
        km._ACTIVE_CHAT_BY_WID.update(self._record)

    def _client(self, app, wid=None, register=True, send=None):
        frames = []
        c = {"app": app, "alive": True, "sent": {}, "_frames": frames,
             "send": send or (lambda s: frames.append(json.loads(s)))}
        if wid is not None:
            c["wid"] = wid
        if register:
            km._clients.append(c)
        return c

    @staticmethod
    def _relayed(c):
        return [f for f in c["_frames"] if f["type"] == "activeChat"]

    def test_01_a_tab_switch_reaches_the_feed_of_the_same_window_only(self):
        chat = self._client("chat", "W1")
        feed1 = self._client("feed", "W1")
        feed2 = self._client("feed", "W2")
        timeline = self._client("timeline", "W1")
        chat2 = self._client("chat", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual(self._relayed(feed1), [{"type": "activeChat", "id": WEB}])
        for c, what in ((feed2, "another window's feed"), (timeline, "the window's timeline"),
                        (chat, "the poster"), (chat2, "the window's other chat column")):
            self.assertEqual(c["_frames"], [], what + " is sent nothing")
        self.assertEqual(km._ACTIVE_CHAT_BY_WID, {"W1": WEB})
        # the arm's own work is untouched: the active tab is recorded and the pusher is woken
        self.assertEqual(chat["active"], WEB)
        self.assertTrue(km._pusher_wake.is_set())
        self.assertIn(("activeChat",), feed1["sent"], "the frame rides its own dedup slot")

    def test_02_the_same_tab_again_is_deduped_and_a_different_one_relays(self):
        chat = self._client("chat", "W1")
        feed = self._client("feed", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB], "an unchanged value is not re-sent")
        _dispatch({"type": "activeTab", "id": API}, chat)
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB, API, WEB])

    def test_03_no_tab_relays_null(self):
        chat = self._client("chat", "W1")
        feed = self._client("feed", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        _dispatch({"type": "activeTab", "id": None}, chat)          # the chat says no tab has focus
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB, None])
        self.assertEqual(km._ACTIVE_CHAT_BY_WID, {"W1": None}, "recorded as None, and the key stays")
        _dispatch({"type": "activeTab", "id": API}, chat)
        _dispatch({"type": "activeTab"}, chat)                      # …or omits the id altogether
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB, None, API, None])
        _dispatch({"type": "activeTab", "id": ""}, chat)            # an empty id is no tab, and no new value
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB, None, API, None])

    def test_04_a_feed_that_says_ready_learns_the_current_focus_once(self):
        chat = self._client("chat", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)           # recorded with no feed connected yet
        feed = self._client("feed", "W1")                             # …then the window's feed (re)loads
        h = _Self()
        km.Handler._dispatch_ws(h, {"type": "ready"}, feed)
        self.assertEqual(h.calls, [feed], "the connect push still runs")
        self.assertEqual(self._relayed(feed), [{"type": "activeChat", "id": WEB}])
        types = [f["type"] for f in feed["_frames"]]
        self.assertIn("caps", types, "the ready arm's own closing frame")
        self.assertLess(types.index("activeChat"), types.index("caps"),
                        "ahead of the connect push, never after the caps frame the shim's redial gate reads")
        km.Handler._dispatch_ws(_Self(), {"type": "ready"}, feed)
        self.assertEqual(len(self._relayed(feed)), 2, "a second ready re-sends: a renderer that just evaluated holds nothing, "
                         "so the reset forgets the slot (a dedup here left a reloaded feed without its focus)")
        # a window whose chat has never reported gets no frame at all — not even a null
        other = self._client("feed", "W3")
        km.Handler._dispatch_ws(_Self(), {"type": "ready"}, other)
        self.assertEqual(self._relayed(other), [])
        self.assertNotIn(("activeChat",), other["sent"])
        # and a chat, timeline or shell that says ready is never sent one, whatever its window holds
        for app in ("chat", "timeline", "shell"):
            c = self._client(app, "W1")
            km.Handler._dispatch_ws(_Self(), {"type": "ready"}, c)
            self.assertEqual(self._relayed(c), [], app)

    def test_04b_a_relay_before_ready_never_starves_the_ready_arms_send(self):
        # the review's case: the feed page reloads and registers while its bundle still evaluates; a tab switch
        # in the window relays a frame into that listener-less document (it vanishes) and writes the slot; the
        # ready arm's send was then deduped for _DEDUP_REPOST_S and the section read "no session is focused"
        chat = self._client("chat", "W1")
        feed = self._client("feed", "W1")                             # registered, bundle not yet evaluated
        _dispatch({"type": "activeTab", "id": WEB}, chat)             # relayed into the void, slot written
        self.assertEqual(self._relayed(feed), [{"type": "activeChat", "id": WEB}])
        self.assertIn(("activeChat",), feed["sent"])
        km.Handler._dispatch_ws(_Self(), {"type": "ready"}, feed)     # the bundle evaluated: the reset forgets the slot
        self.assertEqual(self._relayed(feed), [{"type": "activeChat", "id": WEB}] * 2, "ready re-sends the focus")

    def test_08_the_record_leaves_with_the_windows_last_pane(self):
        chat = self._client("chat", "W1")
        feed = self._client("feed", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual(km._ACTIVE_CHAT_BY_WID.get("W1"), WEB)
        km._clients.remove(chat); km._forget_active_chat_if_last(chat)
        self.assertEqual(km._ACTIVE_CHAT_BY_WID.get("W1"), WEB, "the window's feed is still connected: the record stays for its reload")
        km._clients.remove(feed); km._forget_active_chat_if_last(feed)
        self.assertNotIn("W1", km._ACTIVE_CHAT_BY_WID, "the last pane of the window left: the record goes")
        other = self._client("feed", "W2"); km._clients.remove(other); km._forget_active_chat_if_last(other)   # a wid with no record: a no-op

    def test_05_the_record_is_keyed_by_the_wid_string_and_a_missing_wid_is_the_empty_key(self):
        chat = self._client("chat")                 # no wid at all: a page opened outside a dashboard
        feed = self._client("feed")
        feed_blank = self._client("feed", "")
        feed_w1 = self._client("feed", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual(km._ACTIVE_CHAT_BY_WID, {"": WEB})
        self.assertEqual([f["id"] for f in self._relayed(feed)], [WEB])
        self.assertEqual([f["id"] for f in self._relayed(feed_blank)], [WEB], "an empty wid is the same key")
        self.assertEqual(self._relayed(feed_w1), [])
        chat_n = self._client("chat", 7)            # a non-string wid files under its string form
        feed_n = self._client("feed", "7")
        _dispatch({"type": "activeTab", "id": API}, chat_n)
        self.assertEqual(km._ACTIVE_CHAT_BY_WID, {"": WEB, "7": API})
        self.assertEqual([f["id"] for f in self._relayed(feed_n)], [API])
        self.assertTrue(all(isinstance(k, str) for k in km._ACTIVE_CHAT_BY_WID))

    def test_06_a_dead_feed_does_not_stop_the_relay_to_the_window_s_other_feeds(self):
        chat = self._client("chat", "W1")

        def boom(s):
            raise BrokenPipeError("gone")
        dead = self._client("feed", "W1", send=boom)
        live = self._client("feed", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, chat)
        self.assertEqual([f["id"] for f in self._relayed(live)], [WEB])
        self.assertFalse(dead["alive"], "the failing socket is marked dead, as every sender marks it")
        # a client already marked dead is skipped outright
        gone = self._client("feed", "W1")
        gone["alive"] = False
        _dispatch({"type": "activeTab", "id": API}, chat)
        self.assertEqual(gone["_frames"], [])
        self.assertEqual([f["id"] for f in self._relayed(live)], [WEB, API])

    def test_07_only_a_chat_client_s_tab_switch_is_relayed(self):
        # the record is the CHAT pane's active tab; another pane posting activeTab (none does today) files nothing
        feed = self._client("feed", "W1")
        tl = self._client("timeline", "W1")
        _dispatch({"type": "activeTab", "id": WEB}, tl)
        self.assertEqual(km._ACTIVE_CHAT_BY_WID, {})
        self.assertEqual(feed["_frames"], [])
        self.assertEqual(tl["active"], WEB, "the arm's own bookkeeping still applies to it")


class Wiring(unittest.TestCase):
    """Source pins on the two arms (the repo's convention for handler wiring): the relay call sits in the activeTab
    arm after the wake, and the ready arm sends the recorded value after its reset and before its connect push."""
    def test_the_activetab_arm_relays_after_the_wake(self):
        src = inspect.getsource(km.Handler._dispatch_ws)
        i = src.index('msg.get("type") == "activeTab"')
        body = src[i:src.index('msg.get("type") == "needSlot"', i)]
        self.assertIn('_relay_active_chat(client, msg.get("id"))', body)
        self.assertLess(body.index("_pusher_wake.set()"), body.index("_relay_active_chat("),
                        "release, wake, THEN relay: the older pins on the arm's first 400 chars hold")

    def test_the_ready_arm_sends_the_record_between_the_reset_and_the_push(self):
        src = inspect.getsource(km.Handler._dispatch_ws)
        i = src.index('msg.get("type") == "ready"')
        body = src[i:src.index("_send_caps(client, views_seq=views_seq)", i)]
        self.assertIn("_send_active_chat(client)", body)
        self.assertLess(body.index("_client_reset_chat_base(client)"), body.index("_send_active_chat(client)"))
        self.assertLess(body.index("_send_active_chat(client)"), body.index("self._push_one(client)"))


if __name__ == "__main__":
    unittest.main()
