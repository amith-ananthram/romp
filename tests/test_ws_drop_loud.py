#!/usr/bin/env python3
"""A client the kernel drops for falling behind is LOUD — one stderr line and one bell row per drop.

_mk_ws_send raises when a client is WS_QUEUE_BYTES behind (tests/test_ws_send_bounded.py owns that
guarantee); every caller caught it with a bare `c["alive"] = False`. _drop_dead_ws_client writes a line for
the ping-timeout and supersession drops, but the budget drop left NO trace in the kernel log, the shim
logged nothing on its side, and a dashboard dropped and reconnected every few minutes read as a flaky
network. Pinned: one line per drop naming the pane, dashboard id, backlog and — for a push-path drop — the
slot whose frame tipped the budget (the raise site has no key of its own; _client_send leaves the slot in
flight on the client for the length of its call, and a one-shot reply reads `slot=-`, honestly); a row for
the bell via the kernel-problems channel (_sdk_problem_rows → feed sdkNotices); the log lives at the RAISE
site (_mk_ws_send → _note_ws_drop), so the direct one-shot `client["send"]` replies are covered too, not
only the push paths that go through _client_send; and the bell shows at most a few RECENT drop rows,
merged by time with the backend's problems — twenty drops appended last and sliced positionally would hide
every later backend problem for the rest of the kernel's life.

Synthetic only: no session data.
"""
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_wsdroploud", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
PREFIX = "ws: dropping"   # the prefix _drop_dead_ws_client already uses: one grep finds every kind of drop


def _drop_lines(err):
    return [l for l in err.getvalue().splitlines() if l.startswith(PREFIX)]


class _Sock:
    def shutdown(self, how):
        pass


def _budgeted(app, wid, over=10):
    """A client with the kernel's OWN send (_mk_ws_send), already `over` bytes PAST the drop budget with
    nothing in flight (T278: "behind" is the backlog behind the head frame, and a backlog past the budget
    drops the client on its next frame, whatever that frame's size)."""
    client = {"app": app, "wid": wid, "alive": True, "qbytes": km.WS_QUEUE_BYTES + over,
              "qlock": threading.Lock()}
    client["send"] = km._mk_ws_send(queue.Queue(), _Sock(), client)
    return client


class DroppedClientsAreLoud(unittest.TestCase):
    def _dropping(self):
        def boom(_s):
            raise OSError("ws client feed is 17000000 bytes behind — dropping")
        return {"app": "feed", "alive": True, "wid": "w9", "qbytes": 17000000, "send": boom}

    def test_one_stderr_line_per_drop_naming_pane_dashboard_and_backlog(self):
        c = self._dropping()
        err, old = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            km._send_client(c, ("feed",), {"type": "feed", "asks": []})
            km._send_client(c, ("working",), {"type": "working"})   # the same cycle's next frame fails too
        finally:
            sys.stderr = old
        lines = _drop_lines(err)
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("dropping feed client", lines[0]); self.assertIn("wid=w9", lines[0])
        self.assertIn("queued=17000000B", lines[0]); self.assertIn("17000000 bytes behind", lines[0])
        self.assertFalse(c["alive"])

    def test_the_drop_reaches_the_bell_through_the_kernel_problem_rows(self):
        n0 = len(km._WS_DROPS)
        old = sys.stderr; sys.stderr = io.StringIO()
        try:
            km._send_client(self._dropping(), ("feed",), {"type": "feed"})
        finally:
            sys.stderr = old
        self.assertEqual(len(km._WS_DROPS), n0 + 1)
        row = km._WS_DROPS[-1]
        self.assertIn("The Feed pane's live connection was dropped", row["text"]); self.assertIn("17.0 MB", row["text"])
        self.assertTrue(any(r["text"] == row["text"] and r["sig"].startswith("sdk|") and "|ws|" in r["sig"]
                            for r in km._sdk_problem_rows()), "rides the feed's sdkNotices → the bell")

    def test_a_drop_through_a_direct_send_is_loud_too(self):
        # the RAISE site logs, so whichever caller's frame tips the budget — a one-shot reply through a
        # bare client["send"], with no helper in sight — leaves the line and the bell row
        client = _budgeted("chat", "w3")
        n0 = len(km._WS_DROPS)
        err, old = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            with self.assertRaises(OSError):
                client["send"]('{"type":"renamed"}' + "x" * 100)
            # the line is on stderr the moment the raise site fires — BEFORE any helper has run on this client
            lines = _drop_lines(err)
            self.assertEqual(len(lines), 1, err.getvalue())
            self.assertIn("slot=-", lines[0], "a one-shot reply has no push slot, and the line says so")
            self.assertEqual(len(km._WS_DROPS), n0 + 1)
            km._client_send(client, "a later frame", ("chat", "x"))   # the helper on the same dead client: latched
        finally:
            sys.stderr = old
        lines = _drop_lines(err)
        self.assertEqual(len(lines), 1, "no second line for the same drop: " + err.getvalue())
        self.assertIn("dropping chat client", lines[0]); self.assertIn("wid=w3", lines[0]); self.assertIn("bytes behind", lines[0])
        self.assertFalse(client["alive"])
        self.assertEqual(len(km._WS_DROPS), n0 + 1)
        self.assertIn("The Chat pane's", km._WS_DROPS[-1]["text"])

    def test_a_push_path_drop_names_the_slot_whose_frame_tipped_the_budget(self):
        # the raise site has no key of its own; _client_send leaves the slot in flight on the client for
        # the length of the call, so the line names the frame — feed, a chat sid, the tab order — that did it
        for key, named in ((("feed",), "slot=feed "), (("chat", SID), "slot=chat:11111111 "), (("taborder",), "slot=taborder ")):
            client = _budgeted("feed", "w9")
            err, old = io.StringIO(), sys.stderr
            sys.stderr = err
            try:
                self.assertFalse(km._client_send(client, "x" * 100, key))
            finally:
                sys.stderr = old
            lines = _drop_lines(err)
            self.assertEqual(len(lines), 1, err.getvalue())
            self.assertIn(named, lines[0], key)
            self.assertNotIn("slot=-", lines[0])
            self.assertIn("frame=100B", lines[0])
            self.assertNotIn("curSlot", client, "the slot in flight is cleared after the call: a later one-shot reads `-`")
        # a client whose push-path drop was logged: a later direct send raises again, latched, no second line
        client = _budgeted("feed", "w9")
        err, old = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            km._client_send(client, "x" * 100, ("feed",))
            with self.assertRaises(OSError):
                client["send"]("y" * 100)
        finally:
            sys.stderr = old
        self.assertEqual(len(_drop_lines(err)), 1)

    def test_a_drop_on_the_view_delta_path_is_loud_and_names_its_slot(self):
        # the delta encoder's own send (the tail of _send_slot_delta) used to swallow the raise like the
        # rest; a delta client that tips the budget on a delta frame leaves the same line, slot named
        def feed(text):
            asks = [{"itemId": "%s:g%d" % (SID, i), "sid": SID, "text": text if i == 0 else "card %d " % i * 40}
                    for i in range(6)]
            return {"type": "feed", "asks": asks, "now": 1000, "sessions": [{"sid": SID, "name": "web"}], "order": [SID]}
        client = {"app": "feed", "wid": "w9", "alive": True, "qbytes": 0, "delta": True, "sock": object(),
                  "qlock": threading.Lock()}
        client["send"] = km._mk_ws_send(queue.Queue(), _Sock(), client)
        first = feed("Wire the notes-api health route")
        pre = json.dumps(first)
        km._send_slot(client, "feed", first, pre, km._dedup_sig(first, pre))
        self.assertIn("feed", client.get("dstate", {}), "the keyed full went, and the client is held as its base")
        # the backlog BEHIND the held full (the head of the queue, in flight) is past the budget: the next
        # frame — a small delta — drops it (T278: the head itself never counts as "behind")
        client["qbytes"] = client["qsizes"][0] + km.WS_QUEUE_BYTES + 10
        second = feed("Wire the notes-api health route, then its test")
        pre2 = json.dumps(second)
        err, old = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            km._send_slot(client, "feed", second, pre2, km._dedup_sig(second, pre2))
        finally:
            sys.stderr = old
        lines = _drop_lines(err)
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn("dropping feed client", lines[0]); self.assertIn("slot=feed ", lines[0])
        self.assertNotIn("view-delta feed:", err.getvalue(), "the drop is a drop, not an encoder failure")
        self.assertFalse(client["alive"])
        self.assertIn("The Feed pane's", km._WS_DROPS[-1]["text"])

    def test_the_bell_row_names_the_pane_as_the_rail_does(self):
        # the row is read by the person at the dashboard, who knows the panes by the rail's labels; the
        # Outline pane's internal app id (the key probed below) is not one of them, and the row used to name
        # it by that id. The stderr line keeps the id — it is the log's word for the pane, and the line
        # beside it (_drop_dead_ws_client) uses it.
        old = sys.stderr; sys.stderr = io.StringIO()
        try:
            texts = {}
            for app, label in km._PANE_ORDER:
                c = self._dropping(); c["app"] = app
                km._send_client(c, ("feed",), {"type": "feed"})
                texts[app] = km._WS_DROPS[-1]["text"]
                self.assertIn("The %s pane's live connection was dropped" % label, texts[app], app)
            self.assertNotIn("fleet", texts["fleet"], "the internal id never reaches the row")
            self.assertIn("The Outline pane's", texts["fleet"])
            self.assertIn("dropping fleet client", sys.stderr.getvalue(), "…while the log line keeps it")
            c = self._dropping(); c["app"] = "shell"   # a client with no rail label: the id itself, never a blank
            km._send_client(c, ("feed",), {"type": "feed"})
            self.assertIn("The shell pane's", km._WS_DROPS[-1]["text"])
        finally:
            sys.stderr = old

    def test_drop_rows_never_crowd_a_backend_problem_out_of_the_bell(self):
        class _Be:
            def problems(self, limit=0):
                return [{"seq": 7, "t": time.time(), "text": "sdk session web crashed"}]

            def problem_seq(self):
                return 7
        saved = (list(km._WS_DROPS), km._sdk_backend)
        old = sys.stderr
        sys.stderr = io.StringIO()
        try:
            km._WS_DROPS[:] = []
            for _ in range(25):
                km._send_client(self._dropping(), ("feed",), {"type": "feed"})
            km._sdk_backend = _Be()
            rows = km._sdk_problem_rows()
            kinds = [r["sig"].split("|")[2] for r in rows]
            self.assertIn("be", kinds, "the backend problem shows beside the drops: %r" % kinds)
            self.assertLessEqual(kinds.count("ws"), km._WS_DROP_BELL_ROWS, "a few drop rows at most")
            self.assertEqual([r["t"] for r in rows], sorted(r["t"] for r in rows), "merged by time")
            for r in km._WS_DROPS:
                r["t"] -= km._WS_DROP_TTL_S + 1
            self.assertEqual([r["sig"].split("|")[2] for r in km._sdk_problem_rows()], ["be"],
                             "an hour-old drop has left the bell (the log keeps it)")
        finally:
            sys.stderr = old
            km._WS_DROPS[:] = saved[0]
            km._sdk_backend = saved[1]

    def test_every_send_path_goes_through_the_loud_helper(self):
        src = open(os.path.join(BIN, "romp-kernel"), encoding="utf-8").read()
        body = src[src.index("def _send_to_app("):src.index("def _reveal_chat_for(")]
        self.assertNotIn('except Exception:\n            c["alive"] = False', body,
                         "_send_to_app / _keepalive_all / _send_to_view no longer swallow a drop")
        self.assertEqual(body.count("_client_send(c, s)"), 3)
        delta = src[src.index("def _send_slot_delta("):src.index("def _send_client(")]
        self.assertIn("if not _client_send(c, s, key):\n        return", delta, "the delta encoder's send names its slot")
        self.assertNotIn('c["send"](s)', delta)
        per_client = src[src.index("def _send_client("):src.index("def _send_chat(")]
        self.assertIn("_client_send(c, s, key)", per_client)
        self.assertNotIn('c["send"](s)', per_client)
        raise_site = src[src.index("def _mk_ws_send("):src.index("def _ws_send(")]
        self.assertIn("_note_ws_drop(client, why, len(s))\n                raise OSError(", raise_site,
                      "the raise site logs, so every direct caller is covered")


if __name__ == "__main__":
    unittest.main()
