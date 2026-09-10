#!/usr/bin/env python3
"""The kernel notices a dead dashboard pane (2026-09-03).

The keepalive was one-way: the kernel sent a small frame every KEEPALIVE_S and expected nothing back. A
peer whose forwarder kept the devbox-side socket open after the browser pane was gone — VS Code's port
forwarder does exactly that — therefore stayed a "live dashboard" forever, and the pusher kept building
and streaming every full payload to it. Measured live: 84 client connections on one kernel, 73 silent for
over five minutes, all phantoms sharing one multiplexed channel with three real panes.

Now every beat is also a WebSocket PING (RFC 6455), which every conforming peer answers with a PONG
without application code; a peer that has not answered the oldest outstanding ping within WS_DEAD_S is
dropped — the missing pong is the event. Any inbound message counts as life too. Real TCP loopback
pairs, matching the kernel's transport. Synthetic only.
"""
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_wslive", os.path.join(BIN, "romp-kernel"))


def _mask(payload):
    """A client-side (masked) frame body for `payload` with a fixed mask — what a browser's pong looks like."""
    mask = b"\x11\x22\x33\x44"
    return mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _client_frame(opcode, payload):
    n = len(payload)
    hdr = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", n)
    return hdr + _mask(payload)


class _Peer(threading.Thread):
    """The far end of a pane's socket. `pongs=True` behaves like a browser (answers every ping with a pong
    echoing its payload); `pongs=False` is the phantom: it reads what arrives and never answers."""

    def __init__(self, sock, pongs):
        super().__init__(daemon=True)
        self.sock, self.pongs, self.pings, self.texts, self.stop = sock, pongs, [], [], False

    def run(self):
        rf = self.sock.makefile("rb")
        while not self.stop:
            try:
                op, payload, fin = km._ws_recv(rf)  # server frames are unmasked; the reader tolerates that
            except OSError:
                return                              # the kernel side shut the pair down under us (teardown)
            if op is None:
                return
            if op == 0x9:
                self.pings.append(payload)
                if self.pongs:
                    try:
                        self.sock.sendall(_client_frame(0xA, payload))
                    except OSError:
                        return
            elif op == 0x1:
                self.texts.append(payload)


class _Handler(threading.Thread):
    """The kernel side's read loop, as the connection handler runs it: pongs and messages stamp liveness."""

    def __init__(self, client, sock):
        super().__init__(daemon=True)
        self.client, self.sock, self.msgs = client, sock, []

    def run(self):
        rf = self.sock.makefile("rb")
        while self.client["alive"]:
            op, payload = km._ws_recv_message(rf, lambda p: None, on_pong=lambda p: km._note_ws_inbound(self.client))
            if op is None:
                break
            km._note_ws_inbound(self.client)
            self.msgs.append(payload)
        self.client["alive"] = False


class PhantomPanesAreDropped(unittest.TestCase):
    def setUp(self):
        self.closeables, self.threads, self.queues = [], [], []
        self._saved_clients = list(km._clients)
        km._clients[:] = []
        self.clock = [1_800_000_000.0]
        self._saved_clock, self._saved_outq = km._ws_clock, km._ws_outq
        km._ws_clock = lambda: self.clock[0]
        km._ws_outq = lambda sock: 0                  # loopback drains instantly; the drain probe has its own test

    def tearDown(self):
        km._ws_clock, km._ws_outq = self._saved_clock, self._saved_outq
        km._clients[:] = self._saved_clients
        for q in self.queues:
            q.put(None)                               # end every sender thread the test started
        for s in self.closeables:
            try:
                s.shutdown(socket.SHUT_RDWR)          # a thread parked in a read returns EOF (close alone would
            except OSError:                           # not: the reader's file object keeps the descriptor)
                pass
            try:
                s.close()
            except OSError:
                pass
        for t in self.threads:
            t.join(3.0)
            self.assertFalse(t.is_alive(), "a test thread outlived its test: %r" % t)

    def _pair(self):
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0)); srv.listen(1)
        peer = socket.create_connection(srv.getsockname())
        kern, _ = srv.accept()
        srv.close()
        self.closeables += [peer, kern]
        return kern, peer

    def _connect(self, pongs, app="timeline"):
        kern, peer_sock = self._pair()
        client, q, lock = km._new_ws_client(app, "w1", kern)
        km._clients.append(client); self.queues.append(q)
        peer = _Peer(peer_sock, pongs); peer.start()
        handler = _Handler(client, kern); handler.start()
        self.threads += [peer, handler]
        return client, peer, handler

    def _settle(self, pred, timeout=3.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred():
                return True
            time.sleep(0.02)
        return pred()

    def test_a_the_beat_carries_a_ping_and_a_browser_answers_it(self):
        client, peer, _ = self._connect(pongs=True)
        t0 = 1_800_000_000.0
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1 and len(peer.texts) == 1), "one ka text + one ping per beat")
        self.assertEqual(json.loads(peer.texts[0])["type"], "ka")
        self.assertEqual(peer.pings[0], str(int(t0)).encode(), "the ping carries the beat's stamp")
        self.assertTrue(self._settle(lambda: client["pingAt"] is None), "the pong cleared the outstanding ping")
        self.assertTrue(client["alive"])

    def test_b_a_peer_that_never_answers_is_dropped_after_the_window_and_only_then(self):
        client, peer, handler = self._connect(pongs=False)
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1))
        self.assertTrue(self._settle(lambda: client["pingAt"] == t0), "the clock runs from the first unanswered ping, stamped as it left")
        self.clock[0] = t0 + km.KEEPALIVE_S
        km._keepalive_all(now=self.clock[0])                        # second beat: still inside the window
        self.assertTrue(self._settle(lambda: len(peer.pings) == 2))
        self.assertTrue(client["alive"], "one missed pong is not death")
        self.assertEqual(client["pingAt"], t0, "…and the oldest unanswered ping keeps the clock")
        self.clock[0] = t0 + km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])                        # the window has run out
        self.assertFalse(client["alive"], "no pong within WS_DEAD_S → dropped")
        handler.join(3.0)
        self.assertFalse(handler.is_alive(), "the shutdown ended the handler's blocking read")
        self.assertTrue(self._settle(lambda: not peer.is_alive()), "…and the peer saw the close")

    def test_c_any_inbound_message_is_life(self):
        client, peer, handler = self._connect(pongs=False)          # never pongs, but talks
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.pings) == 1 and client["pingAt"] == t0))
        peer.sock.sendall(_client_frame(0x1, json.dumps({"type": "activeTab", "id": "x"}).encode()))
        self.assertTrue(self._settle(lambda: client["pingAt"] is None), "a message cleared the outstanding ping")
        self.clock[0] = t0 + km.WS_DEAD_S + 1
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "a talking peer is never dropped for a missing pong")

    def test_c2_the_clock_starts_when_the_ping_leaves_not_when_the_beat_queued_it(self):
        """A slow-but-alive peer with a backlog ahead of the ping must not be judged on time the ping spent in
        the queue — the diagnosis this change answers found a 300 KB/s tunnel client rightly kept alive."""
        kern, peer_sock = self._pair()
        client, q, lock = km._new_ws_client("timeline", "w1", kern, start_sender=False)   # sender held back = backlog
        km._clients.append(client)
        t0 = self.clock[0]
        km._keepalive_all(now=t0)
        self.assertIsNone(client["pingAt"], "queued, not on the wire → no clock yet")
        self.clock[0] = t0 + 10 * km.WS_DEAD_S                       # a long backlog later…
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "…still not judged: no ping has reached the peer")
        t = threading.Thread(target=km._ws_sender, args=(q, kern, lock, client), daemon=True); t.start()   # backlog drains
        self.threads.append(t); self.queues.append(q)
        self.assertTrue(self._settle(lambda: client["pingAt"] == self.clock[0]), "stamped as the first ping left")

    def test_c3_a_peer_is_never_judged_while_its_handler_is_inside_a_dispatch(self):
        """A `ready` runs the connect push on the handler thread — tens of seconds on a cold kernel — and the
        peer's pongs wait unread in the receive buffer meanwhile. Unread is not missing (review 2026-09-03)."""
        client, peer, handler = self._connect(pongs=False)
        t0 = self.clock[0]
        client["inRead"] = False                                    # as the handler marks itself for a dispatch
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: client["pingAt"] == t0))
        self.clock[0] = t0 + 3 * km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "silence during a dispatch is the kernel's, not the peer's")
        km._note_ws_inbound(client); client["inRead"] = True        # the handler returns to its read: fresh clock
        self.assertIsNone(client["pingAt"])
        km._keepalive_all(now=self.clock[0])                        # a new ping…
        self.assertTrue(self._settle(lambda: client["pingAt"] == self.clock[0]))
        self.clock[0] += km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])                        # …judged on its own fresh window
        self.assertFalse(client["alive"], "back in the read and still silent for a whole window → dropped")

    def test_c3b_a_peer_the_kernel_declines_to_judge_still_gets_its_beat(self):
        """Not judging a client mid-dispatch must not also starve it: the ka is what the shim's own silence
        watchdog listens for, and a long build sends nothing else for tens of seconds (review 2026-09-03)."""
        client, peer, handler = self._connect(pongs=False)
        t0 = self.clock[0]
        client["inRead"] = False
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: len(peer.texts) >= 1 and client["pingAt"] == t0))
        self.clock[0] = t0 + 3 * km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])                        # ancient stamp, but the handler is busy
        self.assertTrue(client["alive"])
        self.assertTrue(self._settle(lambda: len(peer.texts) >= 2), "the beat still crossed to the peer")
        self.assertEqual(json.loads(peer.texts[-1])["type"], "ka")

    def test_c4_a_peer_still_draining_its_backlog_is_alive_and_one_that_stopped_acknowledging_is_not(self):
        client, peer, handler = self._connect(pongs=False)
        t0 = self.clock[0]
        outq = [3_000_000]
        km._ws_outq = lambda sock: outq[0]
        km._keepalive_all(now=t0)
        self.assertTrue(self._settle(lambda: client["pingAt"] == t0))
        self.clock[0] = t0 + km.WS_DEAD_S; outq[0] = 2_000_000       # the window passed, but bytes are leaving
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "unsent bytes fell since the last beat → a slow peer, not a dead one")
        self.clock[0] += km.KEEPALIVE_S; outq[0] = 1_000_000
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"])
        stuck_at = self.clock[0]                                    # from here the count never moves
        self.clock[0] += km.KEEPALIVE_S
        km._keepalive_all(now=self.clock[0])
        self.assertTrue(client["alive"], "unchanged once is not yet a verdict")
        self.clock[0] = stuck_at + km.KEEPALIVE_S + km.WS_DEAD_S
        km._keepalive_all(now=self.clock[0])
        self.assertFalse(client["alive"], "unsent bytes unmoved for a whole window → the peer stopped acknowledging")

    def test_c5_the_liveness_clock_is_monotonic(self):
        self.assertIs(self._saved_clock, time.monotonic, "a suspend or an NTP step must not read as silence")

    def test_c6_a_reconnect_of_the_same_page_retires_its_previous_socket_at_once(self):
        """The exact event behind the leak: a page reconnects (same app, same page-instance id) because its
        side of the old socket is dead, however open a forwarder keeps our side. The old socket goes now, not
        after a ping window. Keyed on the INSTANCE id, not the dashboard id: two sockets that share a
        dashboard id without an instance id (the VS Code extension's status pipe beside its feed panel), or
        with different instance ids (a duplicated browser tab, which inherits sessionStorage), are twins that
        must BOTH live — keyed on wid they retired each other every 1.5 s (review 2026-09-03)."""
        old, peer_old, h_old = self._connect(pongs=True); old["iid"] = "page-1"
        def pane(wid, iid):
            kern, _peer = self._pair()
            c, q, _ = km._new_ws_client("timeline", wid, kern); self.queues.append(q)
            if iid:
                c["iid"] = iid
            km._clients.append(c)
            return c
        dup_tab = pane("w1", "page-2")                                # same dashboard id, another page
        twin_pipe_a, twin_pipe_b = pane("w1", ""), pane("w1", "")      # the extension's shape: no instance id
        other_dash = pane("w2", "page-3")
        new_kern, _np = self._pair()
        new, nq, _ = km._new_ws_client("timeline", "w1", new_kern); self.queues.append(nq); new["iid"] = "page-1"
        km._register_ws_client(new)                                  # the same page again
        self.assertFalse(old["alive"], "the page's previous socket is retired on the reconnect itself")
        for c in (new, dup_tab, twin_pipe_a, twin_pipe_b, other_dash):
            self.assertTrue(c["alive"], "every other socket stands")
        self.assertIn(new, km._clients)
        h_old.join(3.0); self.assertFalse(h_old.is_alive(), "the old handler's read ended")
        # and the extension's shape from the other side: a second id-less socket retires nothing
        km._register_ws_client(pane("w1", ""))
        self.assertTrue(twin_pipe_a["alive"] and twin_pipe_b["alive"] and new["alive"])

    def test_d_the_ping_frame_is_well_formed(self):
        f = km._ws_ping_frame(b"1700000000")
        self.assertEqual(f[0], 0x89, "FIN + ping opcode")
        self.assertEqual(f[1], len(b"1700000000"))
        self.assertEqual(f[2:], b"1700000000")
        self.assertEqual(len(km._ws_ping_frame(b"x" * 300)), 2 + 125, "control payloads are capped at 125 bytes")

    def test_e_a_legacy_client_without_a_socket_is_never_judged(self):
        sent = []
        legacy = {"app": "feed", "wid": "", "alive": True, "send": sent.append}
        km._clients.append(legacy)
        km._keepalive_all(now=1_800_000_000.0)
        km._keepalive_all(now=1_800_000_000.0 + 10 * km.WS_DEAD_S)
        self.assertTrue(legacy["alive"])
        self.assertEqual(len(sent), 2, "it still gets the ka text and never a ping")


if __name__ == "__main__":
    unittest.main()
