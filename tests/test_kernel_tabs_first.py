"""TABS-FIRST (the user 2026-06-26): the tabOrder push carries name+color per tab so the client can paint the
WHOLE strip as placeholders up front (no one-by-one pop-in). Every strip sender (_push, on its cycle and as
the connect push a `ready` triggers; _push_session_now; _confirm_close_now) hands a `tabs` list of {id, name,
color} alongside the sid `order` to _send_tab_order, the one frame builder's caller. The `ready` handler
sends no strip of its own, whichever app's renderer posted it.
"""
import inspect
import json
import os
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
KPATH = os.path.join(BIN, "romp-kernel")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel", KPATH)


class TabsFirst(unittest.TestCase):
    def test_push_taborder_carries_name_and_color_per_tab(self):
        src = inspect.getsource(km._push)
        self.assertIn('tab_meta = [{"id": s["sid"], "name": s.get("name", ""), "color": _name_color(s["sid"])}', src,
                      "the periodic push builds a name+color list per tab")
        # 2026-09-07: the frame itself moved into _tab_order_frame — the ONE builder (T258: it carries the
        # affirmed-live sids; and a reconnecting client's skeleton list) — so the pusher hands its order + meta
        # + liveness to _send_tab_order, which builds the frame per client
        self.assertIn('_send_tab_order(c, tab_order, tab_meta, live_map)', src,
                      "and ships it as the tabs field alongside the sid order, through the one strip builder")
        self.assertIn('fr = {"type": "tabOrder", "order": list(order), "tabs": tabs, "selfHost": _self_host(),\n'
                      '          **_views_payload(), "live": sorted({str(x) for x in live})}',
                      inspect.getsource(km._tab_order_frame), "the builder's frame keeps today's shape")

    def test_every_tab_order_frame_names_this_kernels_own_host(self):
        # the chat reads a postal card's sender host against the viewing kernel's own name (its
        # postalSenderHost); the session frame carries the name, but only a LOCAL session's frame teaches
        # it, so a dashboard whose kernel runs no sessions of its own never learned it until the + picker
        # opened, and a remote card stamped with this kernel's name stayed plain text (review find,
        # 2026-09-06). The tabOrder frame is the one every chat receives, first of all on connect.
        sid = "11111111-2222-3333-4444-555555555555"
        frame = km._tab_order_frame([sid], [{"id": sid, "name": "web", "color": None}], [sid])
        self.assertEqual(frame["type"], "tabOrder")
        self.assertEqual(frame["selfHost"], km._self_host())
        self.assertEqual(sorted(frame), ["live", "order", "selfHost", "tabs", "type", "views"])
        # the three senders share the one spelling: the pusher's tabs-first send (the connect push a `ready`
        # triggers included), the off-cycle session push and the close confirmation all hand their order + meta +
        # liveness to _send_tab_order, the builder's ONE caller (2026-09-07: it builds the frame per client,
        # so a reconnecting client's skeleton list can ride it); a fourth inline dict would drop the field again
        text = open(KPATH).read()
        self.assertEqual(text.count('_send_client(c, ("taborder",), _tab_order_frame(tab_order, tab_meta, live, c))'), 1)
        self.assertEqual(text.count("_tab_order_frame(tab_order, tab_meta, live, c)"), 1, "the builder's one caller: _send_tab_order")
        self.assertEqual(text.count("_send_tab_order(c, tab_order, tab_meta, live_map)"), 3)
        self.assertEqual(text.count('{"type": "tabOrder"'), 1, "the literal lives in _tab_order_frame alone")
        self.assertIn("_send_tab_order(c, tab_order, tab_meta, live_map)", inspect.getsource(km._push_session_now))
        self.assertIn("_send_tab_order(c, tab_order, tab_meta, live_map)", inspect.getsource(km._confirm_close_now))

    def _ready(self, app):
        """One `ready` from a renderer of `app`, the connect push stubbed as a marker: the types of the frames
        the handler put on the socket, in order, and the client's dedup slots afterwards. The slots read the
        same frames a second way: a strip sent through _send_client records its ("taborder",) key there."""
        # the liveness reads a strip built at ready would make: pinned, so should such a strip return, these
        # tests fail the same way whatever this machine runs
        saved = (km._live_map, km._alive_sessions)
        km._live_map = lambda: {}
        km._alive_sessions = lambda now, live_map: []
        try:
            sent = []
            h = object.__new__(km.Handler)
            h._push_one = lambda c: sent.append({"type": "_pushed"})   # the connect push, as a marker
            client = {"app": app, "wid": "w1", "alive": True, "send": lambda s: sent.append(json.loads(s))}
            km.Handler._dispatch_ws(h, {"type": "ready"}, client)
        finally:
            km._live_map, km._alive_sessions = saved
        return [m["type"] for m in sent], client.get("sent", {})

    def test_connect_ready_handler_sends_no_tab_order_of_its_own(self):
        # The strip a chat page gets at `ready` is the connect push's: _push lists living plus kept-open tabs
        # through the ("taborder",) slot. The ready arm used to send a second strip from a liveness read of its
        # own (living sessions only), and the client closes every tab a later frame omits without affirming it
        # live, so every read-only reopened tab the push had just listed went down at each ready.
        types, slots = self._ready("chat")
        self.assertEqual(types, ["_pushed", "caps"],
                         "the connect push, then the caps frame: no strip from the handler itself")
        self.assertNotIn(("taborder",), slots, "and none attempted through the strip's dedup slot")

    def test_a_feed_clients_ready_yields_no_tab_order_frame(self):
        # The strip the ready arm used to send went to every app's socket, not only a chat's. The feed page has
        # no tabOrder handler, but every pane's federation layer writes an inbound strip into the stored
        # arrangement (federation.ts absorbHostReport), so a strip for a feed client would prune the kept-open
        # tabs from that store again, and the chat case above would not notice.
        types, slots = self._ready("feed")
        self.assertEqual(types, ["_pushed", "caps"], "a feed client's ready: the connect push, then caps, no strip")
        self.assertNotIn(("taborder",), slots)

    def test_a_timeline_clients_ready_yields_no_tab_order_frame(self):
        types, slots = self._ready("timeline")
        self.assertEqual(types, ["_pushed", "caps"], "a timeline client's ready: the connect push, then caps, no strip")
        self.assertNotIn(("taborder",), slots)

    def test_no_living_only_ordered_reader_remains(self):
        # The ready arm's strip was the one reader of a living-only ordered list; chat tabs and timeline lanes
        # read _chat_tab_sessions and _timeline_sessions, each through _ordered. With that strip gone the
        # reader had no caller, so it goes too: a strip rebuilt from it would drop every kept-open tab again.
        self.assertFalse(hasattr(km, "_ordered_alive"), "no module-level living-only ordered reader")

    def test_name_color_shape_matches_the_client_color_type(self):
        # _name_color returns {bg,fg} or None — exactly the render.ts Color the placeholder applies.
        # A sid with no names entry → None (no color), which the client tolerates.
        self.assertIsNone(km._name_color("11111111-2222-3333-4444-555555555555"))


if __name__ == "__main__":
    unittest.main()
